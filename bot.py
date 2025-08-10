import os
import base64
import requests
import json
from pathlib import Path
import asyncio
from datetime import datetime, timezone
from typing import Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # Fallback, falls nicht verfügbar

import asyncpg
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord.ext.commands import Greedy

# Deine eigene Discord-User-ID 
BOT_OWNER_ID = 693861343014551623

# --- Features -------------------------------------------------------------
FEATURES_FILE = Path(__file__).parent / "features.json"

def load_features():
    """Lädt die Features aus der features.json"""
    if FEATURES_FILE.exists():
        with open(FEATURES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def build_feature_list():
    """Gibt eine formatierte Feature-Liste als Text zurück."""
    features = load_features()
    return "\n\n".join(f"• **{name}**\n{desc}" for name, desc in features)

# --- Environment & Bot ----------------------------------------------------
load_dotenv()
TOKEN  = os.getenv("DISCORD_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

if TOKEN is None:
    raise RuntimeError("Discord-Token nicht gefunden. Stelle sicher, dass .env korrekt ist.")
if DB_URL is None:
    raise RuntimeError("DATABASE_URL nicht gefunden. Bitte PostgreSQL-Plugin in Railway prüfen.")

intents = discord.Intents.default()
intents.message_content = True
intents.members         = True  # für Member-Events benötigt
bot       = commands.Bot(command_prefix="!", intents=intents)
db_pool: asyncpg.Pool | None = None

# --- Guild-DB-Helpers -----------------------------------------------------
async def get_guild_cfg(guild_id: int) -> dict:
    """Lädt oder initialisiert die Zeile für guild_id."""
    row = await db_pool.fetchrow(
        "SELECT * FROM guild_settings WHERE guild_id = $1",
        guild_id
    )
    if row:
        # Record → normales dict
        d = dict(row)

        # templates immer als dict
        tmpl = d.get("templates")
        if isinstance(tmpl, str):
            try:
                d["templates"] = json.loads(tmpl)
            except json.JSONDecodeError:
                d["templates"] = {}
        elif tmpl is None:
            d["templates"] = {}

        # override_roles & target_roles immer als Liste
        for key in ("override_roles", "target_roles"):
            val = d.get(key)
            if isinstance(val, str):
                try:
                    d[key] = json.loads(val)
                except json.JSONDecodeError:
                    d[key] = []
            elif val is None:
                d[key] = []

        # default_role sicherstellen (immer vorhanden, sonst None)
        if "default_role" not in d or d["default_role"] is None:
            d["default_role"] = None
        return d

    # Zeile existiert noch nicht → neu anlegen
    await db_pool.execute(
        "INSERT INTO guild_settings (guild_id) VALUES ($1)",
        guild_id
    )
    return await get_guild_cfg(guild_id)


async def update_guild_cfg(guild_id: int, **fields):
    """
    Schreibt einzelne Felder zurück in die DB.
    Beispiel:
      await update_guild_cfg(gid, welcome_channel=123, templates={"welcome": "..."})
    """
    # SET-Klausel mit Platzhaltern $2, $3, …
    cols = ", ".join(f"{col} = ${i+2}" for i, col in enumerate(fields))
    # Werte-Liste: zuerst guild_id, dann alle fields
    vals = [guild_id]
    for v in fields.values():
        # JSONB-Feld (dict oder list) → JSON-String
        if isinstance(v, (dict, list)):
            vals.append(json.dumps(v))
        else:
            vals.append(v)

    await db_pool.execute(
        f"UPDATE guild_settings SET {cols} WHERE guild_id = $1",
        *vals
    )

# --- Startup --------------------------------------------------------------
@bot.event
async def on_ready():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(dsn=DB_URL)

        # 1) guild_settings-Tabelle
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
              guild_id        BIGINT PRIMARY KEY,
              welcome_channel BIGINT,
              welcome_role    BIGINT,
              leave_channel   BIGINT,
              default_role    BIGINT,
              templates       JSONB DEFAULT '{}'::jsonb
            );
        """)

        # 2) Neue vc_overrides-Tabelle für pro-Channel-Overrides
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS vc_overrides (
              guild_id       BIGINT    NOT NULL,
              channel_id     BIGINT    NOT NULL,
              override_roles JSONB     DEFAULT '[]'::jsonb,
              target_roles   JSONB     DEFAULT '[]'::jsonb,
              PRIMARY KEY (guild_id, channel_id)
            );
        """)

    print(f"✅ Bot ist ready als {bot.user} und DB-Pool initialisiert")

# --- Error Handler --------------------------------------------------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Fehlendes Argument: `{error.param.name}`")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Du hast nicht die nötigen Rechte.")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Du hast nicht die nötigen Rechte für diesen Befehl.")
    else:
        raise error

# --- Setup Wizard ---------------------------------------------------------
@bot.command(name="setup")
@commands.has_permissions(manage_guild=True)
async def setup(ctx, module: str):
    """
    Interaktives Setup für Module:
      welcome, leave, vc_override, autorole, vc_track
    """
    module = module.lower()
    if module not in ("welcome", "leave", "vc_override", "autorole", "vc_track"):
        return await ctx.send("❌ Unbekanntes Modul. Verfügbar: `welcome`, `leave`, `vc_override`, `autorole`, `vc_track`.")

    # ─── vc_override-Setup: Kanal + Override- und Ziel-Rollen abfragen und speichern ────
    if module == "vc_override":
        # 1) Sprachkanal abfragen
        await ctx.send("❓ Bitte erwähne den **Sprachkanal**, für den das Override gelten soll.")
        def check_chan(m: discord.Message):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
                and m.channel_mentions
            )
        try:
            msg_chan = await bot.wait_for("message", check=check_chan, timeout=60)
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup vc_override` neu ausführen.")
        vc_channel = msg_chan.channel_mentions[0]
        # Verhindern, dass ein Kanal sowohl vc_override als auch vc_track hat
        exists_track = await db_pool.fetchval(
            "SELECT 1 FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
            ctx.guild.id, vc_channel.id
        )
        if exists_track:
            return await ctx.send(
                f"❌ Für {vc_channel.mention} ist bereits **vc_track** aktiv. "
                "Bitte zuerst `!disable vc_track` ausführen oder einen anderen Kanal wählen."
            )


        # 2) Override-Rollen abfragen
        await ctx.send("❓ Bitte erwähne **Override-Rollen** (z.B. `@Admin @Moderator`).")
        def check_override(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.role_mentions
        try:
            msg_o = await bot.wait_for("message", check=check_override, timeout=60)
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup vc_override` neu ausführen.")
        override_ids = [r.id for r in msg_o.role_mentions]

        # 3) Ziel-Rollen abfragen
        await ctx.send("❓ Bitte erwähne **Ziel-Rollen**, die automatisch Zugriff erhalten sollen.")
        def check_target(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.role_mentions
        try:
            msg_t = await bot.wait_for("message", check=check_target, timeout=60)
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup vc_override` neu ausführen.")
        target_ids = [r.id for r in msg_t.role_mentions]

        # 3b) (NEU) Kanal für Live-VC-Logs (vc_log_channel) abfragen
        await ctx.send("❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).")
        def check_vclog(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.channel_mentions
        try:
            msg_log = await bot.wait_for("message", check=check_vclog, timeout=60)
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup vc_override` neu ausführen.")
        vc_log_channel = msg_log.channel_mentions[0]

        # In guild_settings hinterlegen (Spalte 'vc_log_channel' ist bereits in Railway vorhanden)
        await update_guild_cfg(ctx.guild.id, vc_log_channel=vc_log_channel.id)

        # 4) In vc_overrides-Tabelle upserten
        await db_pool.execute(
            """
            INSERT INTO vc_overrides (guild_id, channel_id, override_roles, target_roles)
            VALUES ($1, $2, $3::jsonb, $4::jsonb)
            ON CONFLICT (guild_id, channel_id) DO UPDATE
              SET override_roles = EXCLUDED.override_roles,
                  target_roles   = EXCLUDED.target_roles;
            """,
            ctx.guild.id,
            vc_channel.id,
            json.dumps(override_ids),
            json.dumps(target_ids),
        )

        return await ctx.send(
            f"🎉 **vc_override**-Setup abgeschlossen für {vc_channel.mention}!\n"
            "Override-Rollen und Ziel-Rollen wurden gespeichert."
        )
    
        # ─── vc_track-Setup: normalen Sprachkanal zum Tracking registrieren ─────
    if module == "vc_track":
        await ctx.send("❓ Bitte erwähne den **Sprachkanal**, den du tracken möchtest.")
        def check_chan(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.channel_mentions
        try:
            msg_chan = await bot.wait_for("message", check=check_chan, timeout=60)
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup vc_track` neu ausführen.")
        vc_channel = msg_chan.channel_mentions[0]
        # Verhindern, dass ein Kanal sowohl vc_track als auch vc_override hat
        exists_override = await db_pool.fetchval(
            "SELECT 1 FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2",
            ctx.guild.id, vc_channel.id
        )
        if exists_override:
            return await ctx.send(
                f"❌ Für {vc_channel.mention} ist bereits **vc_override** aktiv. "
                "Bitte zuerst `!disable vc_override` (optional mit Kanal) ausführen oder einen anderen Kanal wählen."
            )


        # Sicherstellen, dass es einen Log-Kanal gibt (für Live-Embed)
        cfg = await get_guild_cfg(ctx.guild.id)
        if not cfg.get("vc_log_channel"):
            await ctx.send("❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).")
            def check_vclog(m: discord.Message):
                return m.author == ctx.author and m.channel == ctx.channel and m.channel_mentions
            try:
                msg_log = await bot.wait_for("message", check=check_vclog, timeout=60)
            except asyncio.TimeoutError:
                return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup vc_track` neu ausführen.")
            log_ch = msg_log.channel_mentions[0]
            await update_guild_cfg(ctx.guild.id, vc_log_channel=log_ch.id)

        # Da Railway kein Composite-Unique zulässt: Existenz prüfen statt ON CONFLICT
        exists = await db_pool.fetchval(
            "SELECT 1 FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
            ctx.guild.id, vc_channel.id
        )
        if exists:
            return await ctx.send(f"ℹ️ **VC-Tracking** ist für {vc_channel.mention} bereits aktiv.")

        await db_pool.execute(
            "INSERT INTO vc_tracking (guild_id, channel_id) VALUES ($1, $2)",
            ctx.guild.id, vc_channel.id
        )

        return await ctx.send(f"🎉 **vc_track**-Setup abgeschlossen für {vc_channel.mention}.")

    # ─── Autorole-Setup: Standard-Rolle abfragen und speichern ──────────────
    if module == "autorole":
        await ctx.send("❓ Bitte erwähne die Rolle, die neuen Mitgliedern automatisch zugewiesen werden soll.")
        def check_role(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.role_mentions
        try:
            msg_r = await bot.wait_for("message", check=check_role, timeout=60)
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup autorole` neu ausführen.")
        autorole = msg_r.role_mentions[0]
        await update_guild_cfg(ctx.guild.id, default_role=autorole.id)
        return await ctx.send(f"🎉 **autorole**-Setup abgeschlossen! Neue Mitglieder bekommen die Rolle {autorole.mention}.")

    # ─── Gemeinsames Setup: Kanal abfragen ────────────────────────────────────
    await ctx.send(f"❓ Bitte erwähne den Kanal für **{module}**-Nachrichten.")
    def check_chan(m: discord.Message):
        return m.author == ctx.author and m.channel == ctx.channel and m.channel_mentions
    try:
        msg = await bot.wait_for("message", check=check_chan, timeout=60)
    except asyncio.TimeoutError:
        return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup` neu ausführen.")
    channel = msg.channel_mentions[0]
    await update_guild_cfg(ctx.guild.id, **{f"{module}_channel": channel.id})

    # ─── welcome: Trigger-Rolle abfragen ──────────────────────────────────────
    if module == "welcome":
        await ctx.send("❓ Bitte erwähne die Rolle, die die Willkommens-Nachricht auslöst.")
        def check_role(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.role_mentions
        try:
            msgr = await bot.wait_for("message", check=check_role, timeout=60)
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup welcome` neu ausführen.")
        await update_guild_cfg(ctx.guild.id, welcome_role=msgr.role_mentions[0].id)

    # ─── welcome & leave: Template abfragen ───────────────────────────────────
    if module in ("welcome", "leave"):
        await ctx.send(
            f"✅ Kanal gesetzt auf {channel.mention}. Jetzt den Nachrichtentext eingeben.\n"
            "Verwende Platzhalter:\n"
            "`{member}` → Member-Erwähnung\n"
            "`{guild}`  → Server-Name"
        )
        def check_txt(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.strip()
        try:
            msg2 = await bot.wait_for("message", check=check_txt, timeout=300)
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup` neu ausführen.")

        # Aktuelles Templates-Feld aus der DB laden und updaten
        cfg = await get_guild_cfg(ctx.guild.id)
        raw = cfg.get("templates") or {}
        if isinstance(raw, str):
            try:
                current_templates = json.loads(raw)
            except json.JSONDecodeError:
                current_templates = {}
        else:
            current_templates = raw.copy()
        current_templates[module] = msg2.content
        await update_guild_cfg(ctx.guild.id, templates=current_templates)

    await ctx.send(f"🎉 **{module}**-Setup abgeschlossen!")

# --- Disable Module -------------------------------------------------------
@bot.command(name="disable")
@commands.has_permissions(manage_guild=True)
async def disable(ctx, module: str, channels: Greedy[discord.abc.GuildChannel]):
    """
    Deaktiviert ein Modul und entfernt alle zugehörigen Daten.
    Usage:
      • !disable welcome
      • !disable leave
      • !disable vc_override [#VoiceChannel1 …]
    Wenn Du bei vc_override Kanäle angibst, werden nur für diese Overrides entfernt,
    sonst für alle Channels der Guild.
    """
    module = module.lower()
    if module not in ("welcome", "leave", "vc_override", "autorole", "vc_track"):
        return await ctx.send("❌ Unbekanntes Modul. Erlaubt: `welcome`, `leave`, `vc_override`, `autorole`, `vc_track`.")

    guild_id = ctx.guild.id

    # autorole deaktivieren
    if module == "autorole":
        await update_guild_cfg(guild_id, default_role=None)
        return await ctx.send("🗑️ Modul **autorole** wurde deaktiviert. Keine Autorole mehr gesetzt.")
    
        # vc_track deaktivieren
    if module == "vc_track":
        if channels:
            removed = []
            for ch in channels:
                # Nur VoiceChannels löschen; Text-/Threads ignorieren
                if isinstance(ch, discord.VoiceChannel):
                    await db_pool.execute(
                        "DELETE FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                        guild_id, ch.id
                    )
                    removed.append(ch.mention)
            if removed:
                return await ctx.send(f"🗑️ VC-Tracking entfernt für: {', '.join(removed)}")
            return await ctx.send("ℹ️ Keine gültigen Voice-Channels angegeben.")
        else:
            await db_pool.execute("DELETE FROM vc_tracking WHERE guild_id=$1", guild_id)
            return await ctx.send("🗑️ VC-Tracking für **alle** Voice-Channels entfernt.")

    # welcome & leave: Channel und Role entfernen
    if module in ("welcome", "leave"):
        # Lade aktuelle Konfiguration
        cfg = await get_guild_cfg(guild_id)
        # Entferne channel, role und template für welcome bzw. leave
        fields = {}
        if module == "welcome":
            fields["welcome_channel"] = None
            fields["welcome_role"]    = None
        else:
            fields["leave_channel"]   = None

        # Template aus dem JSONB-Feld kicken
        tpl = cfg.get("templates", {}).copy()
        tpl.pop(module, None)
        fields["templates"] = tpl

        # Update in DB
        await update_guild_cfg(guild_id, **fields)
        return await ctx.send(f"🗑️ Modul **{module}** wurde deaktiviert und alle Einstellungen gelöscht.")

    # vc_override
    # wenn Channels angegeben: nur für diese löschen
    if channels:
        removed = []
        for ch in channels:
            await db_pool.execute(
                "DELETE FROM vc_overrides WHERE guild_id = $1 AND channel_id = $2",
                guild_id, ch.id
            )
            removed.append(ch.mention)
        return await ctx.send(
            f"🗑️ vc_override-Overrides für {' ,'.join(removed)} wurden entfernt."
        )

    # keine Channels angegeben → alles löschen
    await db_pool.execute(
        "DELETE FROM vc_overrides WHERE guild_id = $1",
        guild_id
    )
    await ctx.send("🗑️ Alle vc_override-Overrides für diese Guild wurden entfernt.")

# --- Lock / Unlock --------------------------------------------------------
lock_tasks: dict[int, asyncio.Task] = {}

@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
async def lock_cmd(ctx, channels: Greedy[discord.abc.GuildChannel], start_time: str, duration: int):
    """
    Sperrt Kanäle zur Uhrzeit für `duration` Minuten.
    Öffentlich: @everyone verliert send/connect.
    Privat: alle Rollen, die Sichtbarkeit haben, verlieren send/connect, bleiben sichtbar.
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")
    # Zeit parsen
    try:
        hour, minute = map(int, start_time.split(":"))
    except ValueError:
        return await ctx.send("❌ Ungültiges Format. Bitte `HH:MM` im 24h-Format.")
    now    = datetime.datetime.now(tz=ZoneInfo("Europe/Berlin"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    delay    = (target - now).total_seconds()
    everyone = ctx.guild.default_role

    # Template laden
    cfg = await get_guild_cfg(ctx.guild.id)
    tmpl = cfg["templates"].get("lock",
        "🔒 Kanal {channel} gesperrt um {time} für {duration} Minuten 🚫"
    )

    for ch in channels:
        if ch.id in lock_tasks:
            lock_tasks[ch.id].cancel()

        priv_over = ch.overwrites_for(everyone)
        is_priv   = (priv_over.view_channel is False)
        private_roles = []
        if is_priv:
            for role_obj, over in ch.overwrites.items():
                if isinstance(role_obj, discord.Role) and over.view_channel:
                    private_roles.append(role_obj)

        async def _do_lock(channel, wait, dur):
            await asyncio.sleep(wait)
            # Sperre setzen
            if isinstance(channel, discord.TextChannel):
                if is_priv:
                    for r in private_roles:
                        await channel.set_permissions(r, send_messages=False, view_channel=True)
                else:
                    await channel.set_permissions(everyone, send_messages=False)
            else:
                if is_priv:
                    for r in private_roles:
                        await channel.set_permissions(r, connect=False, speak=False, view_channel=True)
                else:
                    await channel.set_permissions(everyone, connect=False, speak=False)
                for m in channel.members:
                    try: await m.move_to(None)
                    except: pass

            # Nachricht senden
            msg = tmpl.format(channel=channel.mention, time=start_time, duration=dur)
            await channel.send(msg)

            # Timer und Entsperren
            await asyncio.sleep(dur * 60)
            if isinstance(channel, discord.TextChannel):
                if is_priv:
                    for r in private_roles:
                        await channel.set_permissions(r, send_messages=None, view_channel=True)
                else:
                    await channel.set_permissions(everyone, send_messages=None)
            else:
                if is_priv:
                    for r in private_roles:
                        await channel.set_permissions(r, connect=None, speak=None, view_channel=True)
                else:
                    await channel.set_permissions(everyone, connect=None, speak=None)

            await channel.send("🔓 Kanal automatisch entsperrt – viel Spaß! 🎉")
            await ctx.send(f"🔓 {channel.mention} wurde automatisch entsperrt.")
            lock_tasks.pop(channel.id, None)

        task = bot.loop.create_task(_do_lock(ch, delay, duration))
        lock_tasks[ch.id] = task
        await ctx.send(f"⏰ {ch.mention} wird um {start_time} Uhr für {duration} Minuten gesperrt.")

@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def unlock_cmd(ctx, channels: Greedy[discord.abc.GuildChannel]):
    """
    Hebbt Sperre sofort auf.
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")
    everyone = ctx.guild.default_role

    cfg = await get_guild_cfg(ctx.guild.id)
    tmpl = cfg["templates"].get("unlock", "🔓 Kanal {channel} entsperrt.")

    for ch in channels:
        if ch.id in lock_tasks:
            lock_tasks[ch.id].cancel()
            lock_tasks.pop(ch.id, None)

        is_priv = ch.overwrites_for(everyone).view_channel is False
        private_roles = []
        if is_priv:
            for role_obj, over in ch.overwrites.items():
                if isinstance(role_obj, discord.Role) and over.view_channel:
                    private_roles.append(role_obj)

        if isinstance(ch, discord.TextChannel):
            if is_priv:
                for r in private_roles:
                    await ch.set_permissions(r, send_messages=None, view_channel=True)
            else:
                await ch.set_permissions(everyone, send_messages=None)
        else:
            if is_priv:
                for r in private_roles:
                    await ch.set_permissions(r, connect=None, speak=None, view_channel=True)
            else:
                await ch.set_permissions(everyone, connect=None, speak=None)

        await ch.send(tmpl.format(channel=ch.mention))

# --- Welcome & Leave ------------------------------------------------------
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    cfg = await get_guild_cfg(after.guild.id)
    role_id    = cfg["welcome_role"]
    channel_id = cfg["welcome_channel"]
    tmpl       = cfg["templates"].get("welcome")
    if not (role_id and channel_id and tmpl):
        return
    had_before = any(r.id == role_id for r in before.roles)
    has_now    = any(r.id == role_id for r in after.roles)
    if had_before or not has_now:
        return
    channel = after.guild.get_channel(channel_id)
    if channel is None:
        return
    text = tmpl.format(member=after.mention, guild=after.guild.name)
    await channel.send(text)

@bot.event
async def on_member_remove(member: discord.Member):
    cfg = await get_guild_cfg(member.guild.id)
    leave_chan = cfg["leave_channel"]
    tmpl       = cfg["templates"].get("leave")
    if not (leave_chan and tmpl):
        return

    # Kick- und Ban-Check
    now = datetime.datetime.now(tz=ZoneInfo("Europe/Berlin"))
    kicked = False
    try:
        async for entry in member.guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
            if entry.target.id == member.id and (now - entry.created_at).total_seconds() < 5:
                kicked = True
            break
    except discord.Forbidden:
        pass
    if kicked:
        return
    try:
        await member.guild.fetch_ban(member)
        return
    except (discord.NotFound, discord.Forbidden):
        pass

    channel = member.guild.get_channel(leave_chan)
    text    = tmpl.format(member=member.mention, guild=member.guild.name)
    await channel.send(text)

# --- Chat-Cleanup ---------------------------------------------------------
cleanup_tasks: dict[int, asyncio.Task] = {}

def _compute_pre_notify(interval: float) -> float | None:
    if interval >= 3600: return interval - 3600
    if interval >= 300:  return interval - 300
    return None

def age_seconds(msg: discord.Message) -> float:
    now = datetime.datetime.now(tz=msg.created_at.tzinfo)
    return (now - msg.created_at).total_seconds()

async def _purge_all(channel: discord.TextChannel):
    cutoff = 14 * 24 * 3600
    while True:
        msgs = [m async for m in channel.history(limit=100)]
        if not msgs:
            break
        to_bulk = [m for m in msgs if age_seconds(m) < cutoff]
        for i in range(0, len(to_bulk), 100):
            await channel.delete_messages(to_bulk[i:i+100])
            await asyncio.sleep(3)
        old = [m for m in msgs if age_seconds(m) >= cutoff]
        for m in old:
            await m.delete()
            await asyncio.sleep(1)

@bot.command(name="cleanup")
@commands.has_permissions(manage_messages=True)
async def cleanup_cmd(ctx, channels: Greedy[discord.abc.GuildChannel], days: int, minutes: int):
    """
    Wiederkehrende Löschung in Kanälen.
    Usage: !cleanup <#Kanal…> <Tage> <Minuten>
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")
    interval = days * 86400 + minutes * 60
    if interval <= 0:
        return await ctx.send("❌ Ungültiges Intervall.")

    await ctx.send(
        f"🗑️ Nachrichten in {', '.join(ch.mention for ch in channels)} "
        f"werden alle {days} Tage und {minutes} Minuten gelöscht."
    )
    for ch in channels:
        if ch.id in cleanup_tasks:
            cleanup_tasks[ch.id].cancel()

        async def _loop_cleanup(channel: discord.TextChannel, interval_s: float):
            await _purge_all(channel)
            try:
                await channel.send("🗑️ Alle Nachrichten wurden automatisch gelöscht.")
            except discord.Forbidden:
                pass

            pre = _compute_pre_notify(interval_s)
            while True:
                if pre is not None:
                    await asyncio.sleep(pre)
                    wm = (interval_s - pre) / 60
                    text = (f"in {int(wm//60)} Stunde(n)" if wm >= 60 else f"in {int(wm)} Minute(n)")
                    await channel.send(f"⚠️ Achtung: {text}, dann werden alle Nachrichten gelöscht.")
                    await asyncio.sleep(interval_s - pre)
                else:
                    await asyncio.sleep(interval_s)

                await _purge_all(channel)
                try:
                    await channel.send("🗑️ Alle Nachrichten wurden automatisch gelöscht.")
                except discord.Forbidden:
                    pass

        task = bot.loop.create_task(_loop_cleanup(ch, interval))
        cleanup_tasks[ch.id] = task

@bot.command(name="cleanup_stop")
@commands.has_permissions(manage_messages=True)
async def cleanup_stop_cmd(ctx, channels: Greedy[discord.abc.GuildChannel]):
    """
    Stoppt die automatische Löschung.
    Usage: !cleanup_stop <#Kanal…>
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")
    for ch in channels:
        task = cleanup_tasks.pop(ch.id, None)
        if task:
            task.cancel()
            await ctx.send(f"🛑 Automatische Löschung in {ch.mention} gestoppt.")
        else:
            await ctx.send(f"ℹ️ Keine laufende Löschung in {ch.mention} gefunden.")

# --- VC Live Tracking / vc_log_channel (vc_override) -------------------------------------
# Laufende Sessions pro Voice-Channel
# Struktur pro VC-ID:
# {
#   'guild_id': int,
#   'channel_id': int,
#   'started_by_id': int,
#   'started_at': datetime,
#   'accum': {user_id: seconds},
#   'running': {user_id: datetime_start},
#   'message': discord.Message | None,
#   'task': asyncio.Task | None,
#   'override_ids': list[int],
# }
vc_live_sessions: dict[int, dict] = {}

def _fmt_dur(total_seconds: int) -> str:
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _now():
    # Zeitzone Berlin, falls verfügbar
    tz = ZoneInfo("Europe/Berlin") if ZoneInfo else None
    return datetime.datetime.now(tz=tz)

def _render_embed_payload(session: dict) -> discord.Embed:
    guild = bot.get_guild(session["guild_id"])
    vc = guild.get_channel(session["channel_id"]) if guild else None
    started_by = guild.get_member(session["started_by_id"]) if guild else None

    now = _now()
    lines = []
    totals = {}

    for uid, secs in session["accum"].items():
        totals[uid] = secs

    for uid, t0 in session["running"].items():
        add = int((now - t0).total_seconds())
        totals[uid] = totals.get(uid, 0) + max(0, add)

    for uid, secs in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        member = guild.get_member(uid) if guild else None
        name = member.display_name if member else f"User {uid}"
        lines.append(f"• **{name}** – `{_fmt_dur(secs)}`")

    title = "🎙️ Voice‑Session (LIVE)" if session.get("task") else "✅ Voice‑Session (Final)"
    emb = discord.Embed(title=title, color=0x5865F2)
    if vc:
        emb.add_field(name="Channel", value=vc.mention, inline=True)
    if started_by:
        emb.add_field(name="Getriggert von", value=f"{started_by.mention}", inline=True)
    started_at = session["started_at"]
    emb.add_field(
        name="Gestartet",
        value=started_at.strftime("%d.%m.%Y %H:%M:%S"),
        inline=True
    )
    emb.add_field(name="Anwesenheit", value=("\n".join(lines) if lines else "—"), inline=False)
    emb.set_footer(text="Die Liste aktualisiert sich live, solange eine Override‑Rolle im Channel ist.")
    return emb

async def _update_live_message(session: dict):
    try:
        while session.get("task") is not None:
            msg: Optional[discord.Message] = session.get("message")
            if msg:
                emb = _render_embed_payload(session)
                try:
                    await msg.edit(embed=emb)
                except discord.NotFound:
                    break
            await asyncio.sleep(5)
    finally:
        session["task"] = None

async def _start_or_attach_session(member: discord.Member, vc: discord.VoiceChannel, override_ids: list[int]):
    sid = vc.id
    now = _now()
    sess = vc_live_sessions.get(sid)

    # Log‑Kanal aus guild_settings (Spalte: vc_log_channel)
    cfg = await get_guild_cfg(member.guild.id)
    log_id = cfg.get("vc_log_channel")
    log_channel = member.guild.get_channel(log_id) if log_id else None

    if sess is None:
        sess = {
            "guild_id": member.guild.id,
            "channel_id": vc.id,
            "started_by_id": member.id,
            "started_at": now,
            "accum": {},
            "running": {},
            "message": None,
            "task": None,
            "override_ids": override_ids,
        }
        vc_live_sessions[sid] = sess

        # Zielkanal bestimmen (nie in Voice posten)
        target_channel: Optional[discord.TextChannel] = None
        if isinstance(log_channel, discord.TextChannel):
            target_channel = log_channel
        elif member.guild.system_channel:
            target_channel = member.guild.system_channel

        if target_channel is None:
            # letzter Fallback: DM an Trigger
            try:
                dm = await member.create_dm()
                msg = await dm.send(embed=_render_embed_payload(sess))
            except Exception:
                msg = None
        else:
            msg = await target_channel.send(embed=_render_embed_payload(sess))

        sess["message"] = msg
        sess["task"] = bot.loop.create_task(_update_live_message(sess))

    # Member laufend markieren (Re‑Join zählt weiter)
    if member.id not in sess["running"]:
        sess["running"][member.id] = now
    sess["accum"].setdefault(member.id, 0)

async def _handle_leave(member: discord.Member, vc: discord.VoiceChannel, override_ids: list[int]):
    sid = vc.id
    sess = vc_live_sessions.get(sid)
    if not sess:
        return

    t0 = sess["running"].pop(member.id, None)
    if t0:
        add = int((_now() - t0).total_seconds())
        if add > 0:
            sess["accum"][member.id] = sess["accum"].get(member.id, 0) + add

    # Ist noch eine Override‑Rolle im Channel?
    still_override = any(any(r.id in override_ids for r in m.roles) for m in vc.members)
    if still_override:
        if sess.get("message"):
            try:
                await sess["message"].edit(embed=_render_embed_payload(sess))
            except discord.NotFound:
                pass
        return

    # Session finalisieren: Restzeiten addieren
    now = _now()
    for uid, t0 in list(sess["running"].items()):
        add = int((now - t0).total_seconds())
        sess["accum"][uid] = sess["accum"].get(uid, 0) + max(0, add)
    sess["running"].clear()

    # Live‑Task stoppen
    task = sess.get("task")
    if task:
        task.cancel()
        sess["task"] = None

    # Finales Embed
    if sess.get("message"):
        try:
            final_emb = _render_embed_payload(sess)
            final_emb.title = "🧾 Voice‑Session (Abschluss)"
            final_emb.set_footer(text="Session beendet – letzte Override‑Rolle hat den Channel verlassen.")
            await sess["message"].edit(embed=final_emb)
        except discord.NotFound:
            pass

    vc_live_sessions.pop(sid, None)

# --- Simple Tracking: start=erste Person, stop=Channel leer -----------------
def _render_embed_payload_simple(session: dict) -> discord.Embed:
    guild = bot.get_guild(session["guild_id"])
    vc = guild.get_channel(session["channel_id"]) if guild else None
    started_by = guild.get_member(session["started_by_id"]) if guild else None

    now = _now()
    totals = {uid: secs for uid, secs in session["accum"].items()}
    for uid, t0 in session["running"].items():
        totals[uid] = totals.get(uid, 0) + max(0, int((now - t0).total_seconds()))

    lines = []
    for uid, secs in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        m = guild.get_member(uid) if guild else None
        name = m.display_name if m else f"User {uid}"
        lines.append(f"• **{name}** – `{_fmt_dur(secs)}`")

    emb = discord.Embed(
        title=("🎙️ Voice-Session (LIVE)" if session.get("task") else "✅ Voice-Session (Final)"),
        color=0x5865F2,
    )
    if vc:
        emb.add_field(name="Channel", value=vc.mention, inline=True)
    if started_by:
        emb.add_field(name="Getriggert von", value=started_by.mention, inline=True)
    emb.add_field(name="Gestartet", value=session["started_at"].strftime("%d.%m.%Y %H:%M:%S"), inline=True)
    emb.add_field(name="Anwesenheit", value=("\n".join(lines) if lines else "—"), inline=False)
    emb.set_footer(text="Die Liste aktualisiert sich live, solange Personen im Channel sind.")
    return emb

async def _update_live_message_simple(session: dict):
    try:
        while session.get("task") is not None:
            msg = session.get("message")
            if msg:
                try:
                    await msg.edit(embed=_render_embed_payload_simple(session))
                except discord.NotFound:
                    break
            await asyncio.sleep(5)
    finally:
        session["task"] = None

async def _start_or_attach_session_simple(member: discord.Member, vc: discord.VoiceChannel):
    sid = vc.id
    now = _now()
    sess = vc_live_sessions.get(sid)

    cfg = await get_guild_cfg(member.guild.id)
    log_id = cfg.get("vc_log_channel")
    log_channel = member.guild.get_channel(log_id) if log_id else None

    if sess is None:
        sess = {
            "guild_id": member.guild.id,
            "channel_id": vc.id,
            "started_by_id": member.id,  # erster Joiner
            "started_at": now,
            "accum": {},
            "running": {},
            "message": None,
            "task": None,
        }
        vc_live_sessions[sid] = sess

        target_channel = log_channel if isinstance(log_channel, discord.TextChannel) else member.guild.system_channel
        if target_channel is None:
            try:
                dm = await member.create_dm()
                msg = await dm.send(embed=_render_embed_payload_simple(sess))
            except Exception:
                msg = None
        else:
            msg = await target_channel.send(embed=_render_embed_payload_simple(sess))
        sess["message"] = msg
        sess["task"] = bot.loop.create_task(_update_live_message_simple(sess))

        # alle bereits im VC (ohne Bots) aufnehmen
        now = _now()
        for m in vc.members:
            if m.bot: 
                continue
            if m.id not in sess["running"]:
                sess["running"][m.id] = now
            sess["accum"].setdefault(m.id, 0)

    if member.id not in sess["running"]:
        sess["running"][member.id] = now
    sess["accum"].setdefault(member.id, 0)

async def _handle_leave_simple(member: discord.Member, vc: discord.VoiceChannel):
    sid = vc.id
    sess = vc_live_sessions.get(sid)
    if not sess:
        return

    t0 = sess["running"].pop(member.id, None)
    if t0:
        add = int((_now() - t0).total_seconds())
        if add > 0:
            sess["accum"][member.id] = sess["accum"].get(member.id, 0) + add

    # noch Personen im VC? (Bots ignorieren)
    if any(not m.bot for m in vc.members):
        if sess.get("message"):
            try:
                await sess["message"].edit(embed=_render_embed_payload_simple(sess))
            except discord.NotFound:
                pass
        return

    # finalisieren (Channel leer)
    now = _now()
    for uid, t0 in list(sess["running"].items()):
        sess["accum"][uid] = sess["accum"].get(uid, 0) + max(0, int((now - t0).total_seconds()))
    sess["running"].clear()

    task = sess.get("task")
    if task:
        task.cancel()
        sess["task"] = None

    if sess.get("message"):
        try:
            final = _render_embed_payload_simple(sess)
            final.title = "🧾 Voice-Session (Abschluss)"
            final.set_footer(text="Session beendet – der Channel ist jetzt leer.")
            await sess["message"].edit(embed=final)
        except discord.NotFound:
            pass

    vc_live_sessions.pop(sid, None)

# ─── Voice-Override: wenn Override-Rollen eintreten/verlassen ──────────────
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # 1) Nur weiter bei echtem Join oder Leave
    joined = before.channel is None and after.channel is not None
    left   = before.channel is not None and after.channel is None
    if not (joined or left):
        return

    # 2) Betroffenen Channel ermitteln
    vc = after.channel if joined else before.channel
    if vc is None:
        return

    # 3) Override-Config für genau diesen Channel auslesen
    row = await db_pool.fetchrow(
        """
        SELECT override_roles, target_roles
          FROM vc_overrides
         WHERE guild_id   = $1
           AND channel_id = $2
        """,
        member.guild.id,
        vc.id
    )
    if not row:
        return  # kein Override für diesen Channel

    # 4) JSONB → Python-Liste (falls String, zuerst parsen)
    raw_o = row["override_roles"]
    raw_t = row["target_roles"]
    try:
        override_ids = json.loads(raw_o) if isinstance(raw_o, str) else (raw_o or [])
    except:
        override_ids = []
    try:
        target_ids = json.loads(raw_t) if isinstance(raw_t, str) else (raw_t or [])
    except:
        target_ids = []

    if not override_ids or not target_ids:
        return  # schlechte/fehlende Konfiguration

    # 5) Prüfen, ob der Member eine Override-Rolle hat
    if not any(r.id in override_ids for r in member.roles):
        return

    # 6) Bei Join: allen Ziel-Rollen CONNECT erlauben, Sichtbarkeit übernehmen
    if joined:
        for rid in target_ids:
            role = member.guild.get_role(rid)
            if role:
                over = vc.overwrites_for(role)
                await vc.set_permissions(
                    role,
                    connect=True,
                    view_channel=over.view_channel
                )
        return

    # 7) Bei Leave: nur sperren, wenn letzte Override-Person gegangen ist
    still_override = any(
        any(r.id in override_ids for r in m.roles)
        for m in vc.members
    )
    if still_override:
        return

    for rid in target_ids:
        role = member.guild.get_role(rid)
        if role:
            over = vc.overwrites_for(role)
            await vc.set_permissions(
                role,
                connect=False,
                view_channel=over.view_channel
            )
    return

# --- Zusatz-Listener: Live-Tracking (vc_override) -----------------------
async def vc_live_tracker(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """
    Ergänzender Listener: startet Live-Session, hält Anwesenheitsliste & Zeiten
    und finalisiert, wenn die letzte Override‑Rolle den Channel verlässt.
    """
    joined = before.channel is None and after.channel is not None
    left   = before.channel is not None and after.channel is None
    if not (joined or left):
        return

    vc = after.channel if joined else before.channel
    if vc is None:
        return

    # Konfiguration aus vc_overrides holen
    row = await db_pool.fetchrow(
        """
        SELECT override_roles, target_roles
          FROM vc_overrides
         WHERE guild_id   = $1
           AND channel_id = $2
        """,
        member.guild.id,
        vc.id
    )
    if not row:
        return

    try:
        override_ids = json.loads(row["override_roles"]) if isinstance(row["override_roles"], str) else (row["override_roles"] or [])
    except Exception:
        override_ids = []
    try:
        target_ids = json.loads(row["target_roles"]) if isinstance(row["target_roles"], str) else (row["target_roles"] or [])
    except Exception:
        target_ids = []

    if not override_ids or not target_ids:
        return

    # JOIN
    if joined:
        if member.bot:
            return
        # Wenn Member eine Override‑Rolle hat, Session starten/übernehmen
        if any(r.id in override_ids for r in member.roles):
            await _start_or_attach_session(member, vc, override_ids)
        else:
            # Kein Override: nur anhängen, falls bereits Session läuft
            if vc.id in vc_live_sessions:
                sess = vc_live_sessions[vc.id]
                now = _now()
                if member.id not in sess["running"]:
                    sess["running"][member.id] = now
                sess["accum"].setdefault(member.id, 0)
                if sess.get("message"):
                    try:
                        await sess["message"].edit(embed=_render_embed_payload(sess))
                    except discord.NotFound:
                        pass
        return

    # LEAVE
    if left:
        if vc.id not in vc_live_sessions:
            return
        await _handle_leave(member, vc, override_ids)

# Listener registrieren (überschreibt nichts)
bot.add_listener(vc_live_tracker, "on_voice_state_update")

# --- Listener: Simple VC-Tracking (aktiv bei Einträgen in vc_tracking) -----
async def vc_live_tracker_simple(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    joined = before.channel is None and after.channel is not None
    left   = before.channel is not None and after.channel is None
    if not (joined or left):
        return

    vc = after.channel if joined else before.channel
    if vc is None:
        return
    # Kanal muss in vc_tracking stehen …
    row = await db_pool.fetchrow(
        "SELECT 1 FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
        member.guild.id, vc.id
    )
    if not row:
        return

    # … und darf KEIN vc_override haben (sonst übernimmt der andere Listener)
    row_override = await db_pool.fetchrow(
        "SELECT 1 FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2",
        member.guild.id, vc.id
    )
    if row_override:
        return

    if joined:
        if member.bot:
            return  # Bots starten keine Session
        await _start_or_attach_session_simple(member, vc)
        return

    if left and vc.id in vc_live_sessions:
        await _handle_leave_simple(member, vc)

# registrieren
bot.add_listener(vc_live_tracker_simple, "on_voice_state_update")

# ─── Autorole: neuen Mitgliedern automatisch die default_role geben ─────
@bot.event
async def on_member_join(member: discord.Member):
    cfg = await get_guild_cfg(member.guild.id)
    role_id = cfg.get("default_role")
    if not role_id:
        return  # keine Autorole konfiguriert
    role = member.guild.get_role(role_id)
    if role:
        try:
            await member.add_roles(role, reason="Autorole Setup")
        except discord.Forbidden:
            print(f"❗️ Kann Rolle {role_id} nicht zuweisen in Guild {member.guild.id}")

# --- Guild Join Event -----------------------------------------------------
@bot.event
async def on_guild_join(guild):
    features_text = build_feature_list()

    # Prüfen, ob der Kanal schon existiert
    setup_channel = discord.utils.get(guild.text_channels, name="fazzer´s-bot-setup")
    if setup_channel is None:
        try:
            setup_channel = await guild.create_text_channel("fazzer´s-bot-setup")
        except discord.Forbidden:
            setup_channel = guild.system_channel or next(
                (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None
            )

    if setup_channel:
        await setup_channel.send(
            f"👋 Danke, dass du mich hinzugefügt hast, **{guild.name}**!\n\n"
            f"Ich kann aktuell:\n\n{features_text}\n\n"
            "ℹ️ Nutze `!setup <feature>` um ein Feature einzurichten."
        )

# --- Feature-Liste anzeigen ---------------------------------------------------
@bot.command(name="features")
@commands.has_permissions(administrator=True)
async def list_features(ctx):
    """Zeigt die aktuelle Feature-Liste aus features.json an."""
    features_text = build_feature_list() or "Keine Features eingetragen."
    await ctx.send(f"📋 **Aktuelle Features:**\n\n{features_text}")

# Ablaufdatum des GitHub-Tokens (Format: YYYY-MM-DD) – kommt aus Railway Env
GITHUB_TOKEN_EXPIRATION = os.getenv("GITHUB_TOKEN_EXPIRATION", "2025-11-05")  # Beispiel

def days_until_token_expires():
    """Berechnet, wie viele Tage bis zum Ablauf des Tokens verbleiben."""
    try:
        exp_date = datetime.strptime(GITHUB_TOKEN_EXPIRATION, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (exp_date - datetime.now(timezone.utc)).days
    except Exception:
        return None

async def warn_if_token_expiring(ctx):
    """Sendet dem Bot-Owner eine DM, wenn das Token bald abläuft."""
    days_left = days_until_token_expires()
    if days_left is not None and days_left <= 7:
        try:
            await ctx.author.send(
                f"⚠️ Dein GitHub-Token läuft in **{days_left} Tagen** ab!\n"
                "Bitte erneuere es rechtzeitig in Railway."
            )
        except:
            pass  # Falls DMs deaktiviert sind

def commit_feature_file():
    """Commitet features.json ins GitHub-Repo."""
    repo = os.getenv("GITHUB_REPO")
    branch = os.getenv("GITHUB_BRANCH", "main")
    token = os.getenv("GITHUB_TOKEN")
    if not repo or not token:
        print("❌ GitHub Commit übersprungen: Env Vars fehlen.")
        return False, "GitHub-Einstellungen fehlen"

    try:
        with open(FEATURES_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        # Aktuellen SHA der Datei holen
        url = f"https://api.github.com/repos/{repo}/contents/{FEATURES_FILE.name}"
        headers = {"Authorization": f"token {token}"}
        params = {"ref": branch}
        r = requests.get(url, headers=headers, params=params)
        if r.status_code == 401:
            return False, "GitHub-Token ungültig oder abgelaufen"
        r.raise_for_status()
        sha = r.json()["sha"]

        # Datei committen
        message = "Update features.json via bot command"
        data = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "sha": sha,
            "branch": branch
        }
        r = requests.put(url, headers=headers, json=data)
        r.raise_for_status()
        print("✅ features.json erfolgreich zu GitHub gepusht.")
        return True, "Features erfolgreich zu GitHub gepusht."
    except Exception as e:
        print(f"❌ GitHub Commit fehlgeschlagen: {e}")
        return False, str(e)

@bot.command(name="add_feature")
async def add_feature(ctx, name: str, *, description: str):
    """Fügt ein neues Feature zur Liste hinzu (nur Bot-Owner, mit GitHub-Commit)."""
    if ctx.author.id != BOT_OWNER_ID:
        return await ctx.send("❌ Du darfst diesen Befehl nicht nutzen.")
    
    features = load_features()
    if any(f[0].lower() == name.lower() for f in features):
        return await ctx.send(f"⚠️ Feature `{name}` existiert bereits.")
    
    # Neues Feature hinzufügen
    features.append([name, description])
    save_features(features)

    # In GitHub committen
    success, message = commit_feature_file()
    if success:
        await ctx.send(f"✅ Feature `{name}` hinzugefügt.\n📤 {message}")
    else:
        await ctx.send(f"⚠️ Feature `{name}` wurde lokal gespeichert, aber nicht zu GitHub gepusht.\nGrund: {message}")

    # Warnung bei bald ablaufendem Token
    await warn_if_token_expiring(ctx)

# --- Bot Start ------------------------------------------------------------
bot.run(TOKEN)