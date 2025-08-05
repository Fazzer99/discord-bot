import os
import json
import asyncio
import datetime
from zoneinfo import ZoneInfo

import asyncpg
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord.ext.commands import Greedy

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

import json  # ganz oben in der Datei bereits importieren

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

        # 1) guild_settings-Tabelle (inkl. override_roles/target_roles als Fallback)
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
              guild_id        BIGINT PRIMARY KEY,
              welcome_channel BIGINT,
              welcome_role    BIGINT,
              leave_channel   BIGINT,
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
      welcome, leave, vc_override
    """
    module = module.lower()
    if module not in ("welcome", "leave", "vc_override"):
        return await ctx.send("❌ Unbekanntes Modul. Verfügbar: `welcome`, `leave`, `vc_override`.")

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
    if module not in ("welcome", "leave", "vc_override"):
        return await ctx.send("❌ Unbekanntes Modul. Erlaubt: `welcome`, `leave`, `vc_override`.")

    guild_id = ctx.guild.id

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
    has_override = any(r.id in override_ids for r in member.roles)
    if not has_override:
        return

    # 6) Bei Join: allen Ziel-Rollen CONNECT erlauben
    if joined:
        for rid in target_ids:
            role = member.guild.get_role(rid)
            if role:
                await vc.set_permissions(role, connect=True)
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
            await vc.set_permissions(role, connect=False)
    return

# --- Guild Join Event -----------------------------------------------------
@bot.event
async def on_guild_join(guild: discord.Guild):
    # 1️⃣ Lege einen neuen Textkanal für die Bot-Anleitung an
    try:
        info_channel = await guild.create_text_channel(
            name="fazzer´s bot-setup",
            reason="Bot-Beitritt: Einrichtungs- und Info-Kanal erstellen"
        )
    except discord.Forbidden:
        # Falls wir keine Rechte zum Anlegen haben, fallback auf system_channel
        info_channel = guild.system_channel
    if info_channel is None:
        return

    # 2️⃣ Baue die beiden Teile der Anleitung
    part1 = (
        f"👋 **Hallo {guild.name}!** Ich bin Dein neuer Bot – hier die ausführliche Anleitung:\n\n"

        "**1️⃣ SETUP-Module**\n"
        "• `!setup welcome`\n"
        "  – Danach fragt der Bot nacheinander:\n"
        "    1. Kanal erwähnen (z.B. `#welcome`)\n"
        "    2. Rolle erwähnen, die die Begrüßung auslöst (z.B. `@Newbie`)\n"
        "    3. Begrüßungstext eingeben. Platzhalter:\n"
        "       • `{member}` → Member-Mention\n"
        "       • `{guild}`  → Server-Name\n"
        "    Beispiel: `Willkommen {member} auf {guild}! Viel Spaß! 😊`\n\n"
        "• `!setup leave`\n"
        "  – Danach fragt der Bot nacheinander:\n"
        "    1. Kanal erwähnen (z.B. `#goodbye`)\n"
        "    2. Abschiedstext eingeben. Platzhalter wie oben\n"
        "    Beispiel: `{member} hat uns verlassen… Wir werden dich vermissen! 💔`\n\n"
        "• `!setup vc_override`\n"
        "  – Danach fragt der Bot nacheinander:\n"
        "    1. **Override-Rollen** erwähnen (z.B. `@Admin @Moderator`)\n"
        "    2. **Ziel-Rollen** erwähnen, die bei Beitritt einer Override-Rolle automatisch Zugriff auf gesperrte Voice-Channels erhalten\n\n"
        "• `!disable <module>`\n"
        "  – Deaktiviert ein eingerichtetes Modul und entfernt dazu alle gespeicherten Einstellungen:\n"
        "    – `!disable welcome` oder `!disable leave`\n"
        "    – `!disable vc_override [#VoiceChannel...]` (optional List von Channels)\n\n"
    )

    part2 = (
        "**2️⃣ KANÄLE SPERREN & ENTSPERREN**\n"
        "• `!lock <#Kanal1> [#Kanal2 …] <HH:MM> <Minuten>`\n"
        "  – Mindestens einen Text- oder Voice-Kanal mentionen\n"
        "  – Uhrzeit im 24-h-Format (`HH:MM`), z.B. `21:30`\n"
        "  – Dauer in Minuten, z.B. `15`\n"
        "  Beispiel: `!lock #general #Voice 21:30 15`\n\n"
        "• `!unlock <#Kanal1> [#Kanal2 …]`\n"
        "  – Hebt jede laufende Sperre sofort auf\n"
        "  Beispiel: `!unlock #general #Voice`\n\n"

        "**3️⃣ CHAT-CLEANUP**\n"
        "• `!cleanup <#Kanal…> <Tage> <Minuten>`\n"
        "  – Löscht automatisch alle Nachrichten im Abstand von Tagen+Minuten\n"
        "  – `0 10` = alle 10 Minuten, `1 0` = alle 24 Stunden\n\n"
        "• `!cleanup_stop <#Kanal…>`\n"
        "  – Stoppt die automatische Löschung\n\n"

        "**❗️ Benötigte Rechte**\n"
        "– `!setup`: **Manage Server**\n"
        "– `!setup vc_override`: **Manage Server** (zum Speichern von Override-/Ziel-Rollen)\n"
        "– `!lock`/`!unlock`: **Manage Channels**\n"
        "– `!cleanup`/`!cleanup_stop`: **Manage Messages**\n\n"

        "**✅ Nächste Schritte**\n"
        "1. Führe `!setup welcome` aus und beantworte die Fragen\n"
        "2. Führe `!setup leave` aus und gib Dein Abschiedstemplate ein\n"
        "3. Teste `!lock` und `!cleanup`\n\n"

        "ℹ️ Bitte lösche diesen Kanal **NICHT**, sondern verschiebe ihn in Deinen **Admin-Bereich** "
        "und synchronisiere dort die Kanal-Berechtigungen, sodass nur Admins ihn sehen können.\n"
        "Viel Spaß mit Deinem neuen Bot! 🚀"
    )

    # 3️⃣ Sende die beiden Nachrichtenteile
    await info_channel.send(part1)
    await info_channel.send(part2)

# --- Bot Start ------------------------------------------------------------
bot.run(TOKEN)