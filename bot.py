import os
import json
import asyncio
import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord.ext.commands import Greedy

# --- Config‐Helpers --------------------------------------------------------
CONFIG_PATH = "config.json"

def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {"guilds": {}}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def get_guild_cfg(guild_id: int) -> dict:
    cfg = load_config()
    return cfg["guilds"].setdefault(str(guild_id), {})

# --- Environment & Bot ----------------------------------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN is None:
    raise RuntimeError("Discord-Token nicht gefunden. Stelle sicher, dass .env korrekt ist.")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

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
      welcome, leave
    """
    module = module.lower()
    if module not in ("welcome", "leave"):
        return await ctx.send("❌ Unbekanntes Modul. Verfügbar: `welcome`, `leave`.")

    guild_cfg = get_guild_cfg(ctx.guild.id)

    # 1) Kanal abfragen
    await ctx.send(f"❓ Bitte erwähne den Kanal für **{module}**-Nachrichten.")
    def check_chan(m: discord.Message):
        return m.author == ctx.author and m.channel == ctx.channel and m.channel_mentions
    try:
        msg = await bot.wait_for("message", check=check_chan, timeout=60)
    except asyncio.TimeoutError:
        return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup` neu ausführen.")
    channel = msg.channel_mentions[0]
    guild_cfg[f"{module}_channel"] = channel.id

    # für welcome: Rolle abfragen
    if module == "welcome":
        await ctx.send("❓ Bitte erwähne die Rolle, die die Willkommens-Nachricht triggern soll.")
        def check_role(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.role_mentions
        try:
            msgr = await bot.wait_for("message", check=check_role, timeout=60)
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup welcome` neu ausführen.")
        role = msgr.role_mentions[0]
        guild_cfg["welcome_role"] = role.id

    # 2) Template abfragen
    await ctx.send(
        f"✅ Kanal gesetzt auf {channel.mention}. Jetzt den Nachrichtentext eingeben.\n"
        "Verwende Platzhalter:\n"
        "`{member}` → Member-Mention\n"
        "`{guild}`  → Server-Name\n"
        ( "`{role}` → erwähnte Rolle\n" if module=="welcome" else "" )
    )
    def check_txt(m: discord.Message):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.strip()
    try:
        msg2 = await bot.wait_for("message", check=check_txt, timeout=300)
    except asyncio.TimeoutError:
        return await ctx.send("⏰ Zeit abgelaufen. Bitte `!setup` neu ausführen.")
    guild_cfg.setdefault("templates", {})[module] = msg2.content

    save_config(load_config())
    await ctx.send(f"🎉 **{module}**-Setup abgeschlossen!")

# --- Lock / Unlock --------------------------------------------------------
lock_tasks: dict[int, asyncio.Task] = {}

@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
async def lock(ctx, channels: Greedy[discord.abc.GuildChannel], start_time: str, duration: int):
    """
    Sperrt Kanäle zur Uhrzeit für `duration` Minuten.
    Öffentlich: @everyone verliert send/connect.
    Privat: alle Rollen, die Sichtbarkeit haben, verlieren send/connect, bleiben sichtbar.
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")

    # Uhrzeit parsen
    try:
        hour, minute = map(int, start_time.split(":"))
    except ValueError:
        return await ctx.send("❌ Ungültiges Format. Bitte `HH:MM` im 24h-Format.")

    now    = datetime.datetime.now(tz=ZoneInfo("Europe/Berlin"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    delay = (target - now).total_seconds()
    everyone = ctx.guild.default_role

    # Template laden
    cfg  = load_config()["guilds"].get(str(ctx.guild.id), {})
    tmpl = cfg.get("templates", {}).get("lock", 
        "🔒 Kanal {channel} gesperrt um {time} für {duration} Minuten 🚫"
    )

    for channel in channels:
        # Laufenden Task abbrechen
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()

        priv_over = channel.overwrites_for(everyone)
        is_priv   = (priv_over.view_channel is False)

        # Rollen sammeln, die sichtbar sind
        private_roles = []
        if is_priv:
            for role_obj, over in channel.overwrites.items():
                if isinstance(role_obj, discord.Role) and over.view_channel:
                    private_roles.append(role_obj)

        async def _do_lock(ch, wait, dur):
            await asyncio.sleep(wait)
            # Permissions setzen
            if isinstance(ch, discord.TextChannel):
                if is_priv:
                    for r in private_roles:
                        await ch.set_permissions(r, send_messages=False, view_channel=True)
                else:
                    await ch.set_permissions(everyone, send_messages=False)
            else:
                if is_priv:
                    for r in private_roles:
                        await ch.set_permissions(r, connect=False, speak=False, view_channel=True)
                else:
                    await ch.set_permissions(everyone, connect=False, speak=False)
                for m in ch.members:
                    try: await m.move_to(None)
                    except: pass

            # Template-Nachricht
            msg = tmpl.format(channel=ch.mention, time=start_time, duration=dur, guild=ctx.guild.name)
            await ch.send(msg)

            await asyncio.sleep(dur * 60)
            # Entsperren
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

            await ch.send("🔓 Kanal automatisch entsperrt – viel Spaß! 🎉")
            await ctx.send(f"🔓 {ch.mention} wurde automatisch entsperrt.")
            lock_tasks.pop(ch.id, None)

        task = bot.loop.create_task(_do_lock(channel, delay, duration))
        lock_tasks[channel.id] = task
        await ctx.send(f"⏰ {channel.mention} wird um {start_time} Uhr für {duration} Minuten gesperrt.")

@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def unlock(ctx, channels: Greedy[discord.abc.GuildChannel]):
    """
    Hebbt Sperre sofort auf.
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")

    everyone = ctx.guild.default_role
    cfg = load_config()["guilds"].get(str(ctx.guild.id), {})
    tmpl = cfg.get("templates", {}).get("unlock", 
        "🔓 Kanal {channel} entsperrt."
    )

    for channel in channels:
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()
            lock_tasks.pop(channel.id, None)

        is_priv = channel.overwrites_for(everyone).view_channel is False
        private_roles = []
        if is_priv:
            for role_obj, over in channel.overwrites.items():
                if isinstance(role_obj, discord.Role) and over.view_channel:
                    private_roles.append(role_obj)

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

        await channel.send(tmpl.format(channel=channel.mention, guild=ctx.guild.name))

# --- Welcome & Leave ------------------------------------------------------
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    guild_cfg = load_config()["guilds"].get(str(after.guild.id), {})
    role_id   = guild_cfg.get("welcome_role")
    chan_id   = guild_cfg.get("welcome_channel")
    tmpl      = guild_cfg.get("templates", {}).get("welcome")
    if role_id and chan_id and tmpl:
        if role_id not in {r.id for r in before.roles} and role_id in {r.id for r in after.roles}:
            ch   = after.guild.get_channel(chan_id)
            text = tmpl.format(member=after.mention, guild=after.guild.name)
            await ch.send(text)

@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    now   = datetime.datetime.now(tz=ZoneInfo("Europe/Berlin"))

    # Kick-Check
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
            if entry.target.id == member.id and (now - entry.created_at).total_seconds() < 5:
                return
            break
    except discord.Forbidden:
        pass

    # Ban-Check
    try:
        await guild.fetch_ban(member)
        return
    except (discord.NotFound, discord.Forbidden):
        pass

    cfg     = load_config()["guilds"].get(str(guild.id), {})
    chan_id = cfg.get("leave_channel")
    tmpl    = cfg.get("templates", {}).get("leave")
    if chan_id and tmpl:
        ch   = guild.get_channel(chan_id)
        text = tmpl.format(member=member.mention, guild=guild.name)
        await ch.send(text)

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
async def cleanup(ctx, channels: Greedy[discord.abc.GuildChannel], days: int, minutes: int):
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
                    text = f"in {int(wm//60)} Stunde(n)" if wm >= 60 else f"in {int(wm)} Minute(n)"
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
async def cleanup_stop(ctx, channels: Greedy[discord.abc.GuildChannel]):
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

# --- Guild Join Event -----------------------------------------------------
@bot.event
async def on_guild_join(guild: discord.Guild):
    # Versuche, System-Channel oder erstes beschreibbares Text-Channel zu finden
    target = guild.system_channel or next(
        (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
        None
    )
    if not target:
        return

    part1 = (
        f"👋 **Hallo {guild.name}!** Ich bin Dein neuer Bot – hier die ausführliche Anleitung:\n\n"

        "**1️⃣ SETUP**\n"
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
        "    Beispiel: `{member} hat uns verlassen… Wir werden dich vermissen! 💔`"
    )

    part2 = (
        "\n\n**2️⃣ KANÄLE SPERREN & ENTSPERREN**\n"
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
        "– `!lock`/`!unlock`: **Manage Channels**\n"
        "– `!cleanup`/`!cleanup_stop`: **Manage Messages**\n\n"

        "**✅ Nächste Schritte**\n"
        "1. Führe `!setup welcome` aus und beantworte die Fragen\n"
        "2. Führe `!setup leave` aus und gib Dein Abschiedstemplate ein\n"
        "3. Teste `!lock` und `!cleanup`\n\n"

        "ℹ️ Bitte lösche diese Nachricht, sobald Du fertig bist.\n"
        "Viel Spaß mit Deinem neuen Bot! 🚀"
    )

    await target.send(part1)
    await target.send(part2)

# --- Bot Start ------------------------------------------------------------
bot.run(TOKEN)