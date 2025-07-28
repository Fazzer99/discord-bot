import os 
import asyncio
import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord.ext.commands import Greedy, MissingRequiredArgument, MissingPermissions

# Load .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN is None:
    raise RuntimeError(
        "Discord-Token nicht gefunden. Stelle sicher, dass .env im Arbeitsverzeichnis liegt und DISCORD_TOKEN gesetzt ist."
    )

# Bot-Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # für on_member_update benötigt
bot = commands.Bot(command_prefix="!", intents=intents)

# Speichert laufende Unlock-Timer: channel.id → Task
lock_tasks: dict[int, asyncio.Task] = {}
# Speichert originalen view_channel-Status für OG-Rollen: channel.id → {"og": bool|None, "senior": bool|None}
role_views: dict[int, dict[str, bool | None]] = {}

# IDs der OG-Rollen
OG_ROLE_ID = 1386723945583218749
SENIOR_OG_ROLE_ID = 1387936511260889158
# IDs der Rollen mit Bot-Rechten
ADMIN_ROLE_ID = 1386726424441786448
MOD_ROLE_ID   = 1386723766041706506

# IDs für Welcome-Funktion
NEWBIE_ROLE_ID        = 1388900287468535818
WELCOME_CHANNEL_ID    = 1386788177062395946
RULES_CHANNEL_ID      = 1386721701450219592
ANNOUNCEMENTS_CHANNEL_ID = 1386721701450219594
TICKET_ID = 1390380110645035030

# IDs für Abschieds-Funktion
LEAVE_CHANNEL_ID = 1394309783200464967

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, MissingRequiredArgument):
        await ctx.send(
            f"❌ Fehlendes Argument: `{error.param.name}`\n"
            "Verwendung:\n"
            "• `!lock <#Kanal…> <HH:MM> <Minuten>`\n"
            "• `!unlock <#Kanal…>`"
        )
    elif isinstance(error, MissingPermissions):
        await ctx.send("❌ Du benötigst `Manage Channels`-Rechte.")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Du hast nicht die nötigen Rechte, um diesen Befehl auszuführen.")
    else:
        raise error

@bot.command(name="lock")
@commands.check_any(
    commands.has_permissions(manage_channels=True),
    commands.has_any_role(ADMIN_ROLE_ID, MOD_ROLE_ID)
)
async def lock(
    ctx,
    channels: Greedy[discord.abc.GuildChannel],
    start_time: str,
    duration: int
):
    """
    Sperrt Kanäle zur Uhrzeit für duration Minuten.
    • Öffentlich: @everyone verliert send/connect.
    • Privat: OG & Senior OG verlieren send/connect, Sichtbarkeit bleibt.
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")

    # Zeit parsen
    try:
        hour, minute = map(int, start_time.split(":"))
    except ValueError:
        return await ctx.send("❌ Ungültiges Format. Bitte `HH:MM` im 24h-Format.")

    # Verzögerung berechnen (Berlin)
    now = datetime.datetime.now(tz=ZoneInfo("Europe/Berlin"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    delay = (target - now).total_seconds()

    everyone = ctx.guild.default_role
    og = ctx.guild.get_role(OG_ROLE_ID)
    senior = ctx.guild.get_role(SENIOR_OG_ROLE_ID)

    for ch in channels:
        # laufenden Task abbrechen
        if ch.id in lock_tasks:
            lock_tasks[ch.id].cancel()

        # originalen view_channel-Status für OG & Senior speichern
        role_views[ch.id] = {
            "og": ch.overwrites_for(og).view_channel if og else None,
            "senior": ch.overwrites_for(senior).view_channel if senior else None
        }

        # prüfen, ob der Kanal privat ist (everyone.view_channel=False)
        private = (ch.overwrites_for(everyone).view_channel is False)

        async def _do_lock(channel, wait, dur, is_private):
            await asyncio.sleep(wait)

            # Sperre anwenden
            if isinstance(channel, discord.TextChannel):
                if is_private:
                    if og:
                        await channel.set_permissions(og, send_messages=False, view_channel=role_views[channel.id]["og"])
                    if senior:
                        await channel.set_permissions(senior, send_messages=False, view_channel=role_views[channel.id]["senior"])
                else:
                    await channel.set_permissions(everyone, send_messages=False)
            else:  # VoiceChannel
                if is_private:
                    if og:
                        await channel.set_permissions(og, connect=False, speak=False, view_channel=role_views[channel.id]["og"])
                    if senior:
                        await channel.set_permissions(senior, connect=False, speak=False, view_channel=role_views[channel.id]["senior"])
                else:
                    await channel.set_permissions(everyone, connect=False, speak=False)
                # Kicke alle, die drin sind
                for m in channel.members:
                    try: await m.move_to(None)
                    except: pass

            await channel.send(f"🔒 Kanal automatisch gesperrt um {start_time} Uhr, da Rina gerade live ist – für {dur} Minuten nicht verfügbar 🚫")

            # Warte Dauer
            await asyncio.sleep(dur * 60)

            # Entsperren
            if isinstance(channel, discord.TextChannel):
                if is_private:
                    if og:
                        await channel.set_permissions(og, send_messages=None, view_channel=role_views[channel.id]["og"])
                    if senior:
                        await channel.set_permissions(senior, send_messages=None, view_channel=role_views[channel.id]["senior"])
                else:
                    await channel.set_permissions(everyone, send_messages=None)
            else:
                if is_private:
                    if og:
                        await channel.set_permissions(og, connect=None, speak=None, view_channel=role_views[channel.id]["og"])
                    if senior:
                        await channel.set_permissions(senior, connect=None, speak=None, view_channel=role_views[channel.id]["senior"])
                else:
                    await channel.set_permissions(everyone, connect=None, speak=None)

            await channel.send("🔓 Kanal automatisch entsperrt – viel Spaß! 🎉")
            await ctx.send(f"🔓 {channel.mention} wurde automatisch entsperrt.")
            lock_tasks.pop(channel.id, None)
            role_views.pop(channel.id, None)

        # Task starten
        t = bot.loop.create_task(_do_lock(ch, delay, duration, private))
        lock_tasks[ch.id] = t

        await ctx.send(f"⏰ {ch.mention} wird um {start_time} Uhr für {duration} Min. gesperrt.")

@bot.command(name="unlock")
@commands.check_any(
    commands.has_permissions(manage_channels=True),
    commands.has_any_role(ADMIN_ROLE_ID, MOD_ROLE_ID)
)
async def unlock(ctx, channels: Greedy[discord.abc.GuildChannel]):
    """
    Hebbt Sperre sofort auf.
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")

    everyone = ctx.guild.default_role
    og = ctx.guild.get_role(OG_ROLE_ID)
    senior = ctx.guild.get_role(SENIOR_OG_ROLE_ID)

    for ch in channels:
        if ch.id in lock_tasks:
            lock_tasks[ch.id].cancel()
            lock_tasks.pop(ch.id, None)

        private = (ch.overwrites_for(everyone).view_channel is False)
        orig = role_views.pop(ch.id, {"og": None, "senior": None})

        if isinstance(ch, discord.TextChannel):
            if private:
                if og:
                    await ch.set_permissions(og, send_messages=None, view_channel=orig["og"])
                if senior:
                    await ch.set_permissions(senior, send_messages=None, view_channel=orig["senior"])
            else:
                await ch.set_permissions(everyone, send_messages=None)
        else:
            if private:
                if og:
                    await ch.set_permissions(og, connect=None, speak=None, view_channel=orig["og"])
                if senior:
                    await ch.set_permissions(senior, connect=None, speak=None, view_channel=orig["senior"])
            else:
                await ch.set_permissions(everyone, connect=None, speak=None)

        await ctx.send(f"🔓 {ch.mention} entsperrt.")

# --- Neue Willkommensfunktion für Newbie-Rolle ---
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # prüfen, ob Newbie-Rolle neu hinzugefügt wurde
    if NEWBIE_ROLE_ID not in {r.id for r in before.roles} and NEWBIE_ROLE_ID in {r.id for r in after.roles}:
        ch = after.guild.get_channel(WELCOME_CHANNEL_ID)
        if ch:
            await ch.send(
                f"📣 @everyone Ein neues Mitglied ist da: {after.mention} 🎉\n\n"
                f"Willkommen auf **{after.guild.name}** 👋\n"
                f"Mach’s dir bequem – wir freuen uns, dass du hier bist. 😄\n\n"
                f"🔓 Sammle XP durch Aktivität im Chat und steigere dein Level!"
                f"Bitte lies unsere Regeln in <#{RULES_CHANNEL_ID}> und schau in <#{ANNOUNCEMENTS_CHANNEL_ID}> für Neuigkeiten.\n\n"
                f"Bei Fragen helfen dir unsere Mods jederzeit gerne weiter! Öffne hierfür hier ein Ticket: <#{TICKET_ID}>\n— Deine Rina🐥"
            )

# --- Abschieds-Funktion: wenn Mitglieder freiwillig verlassen ---
@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    now = datetime.datetime.now(tz=ZoneInfo("Europe/Berlin"))

    # 1. Kick-Check
    kicked = False
    try:
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
            if entry.target.id == member.id and (now - entry.created_at).total_seconds() < 5:
                kicked = True
            break
    except discord.Forbidden:
        kicked = False
    if kicked:
        return

    # 2. Ban-Check über fetch_ban (mit member)
    try:
        await guild.fetch_ban(member)
        return
    except discord.NotFound:
        pass
    except discord.Forbidden:
        pass

    # 3. Freiwilliges Verlassen → Abschied posten
    ch = guild.get_channel(LEAVE_CHANNEL_ID)
    if ch:
        try:
            await ch.send(f"😢 {member.mention} hat den Server verlassen. @everyone werden dich vermissen! 💔")
        except discord.Forbidden:
            print(f"Kann in Kanal {LEAVE_CHANNEL_ID} nicht schreiben.")

# --- Chat-Cleanup-Funktion ---
cleanup_tasks: dict[int, asyncio.Task] = {}

def _compute_pre_notify(interval: float) -> float | None:
    # gibt zurück, nach wie vielen Sekunden wir warnen
    if interval >= 3600:
        return interval - 3600
    if interval >= 300:
        return interval - 300
    return None

@bot.command(name="cleanup")
@commands.check_any(
    commands.has_permissions(manage_messages=True),
    commands.has_any_role(ADMIN_ROLE_ID, MOD_ROLE_ID)
)
async def cleanup(
    ctx,
    channels: Greedy[discord.abc.GuildChannel],
    days: int,
    minutes: int
):
    """
    Startet eine wiederkehrende Löschung aller Nachrichten in den angegebenen Kanälen.
    Usage: !cleanup <#Kanal…> <Tage> <Minuten>
    Beispiel: !cleanup #general 0 10    → alle 10 Minuten
              !cleanup #logs 1 0       → alle 24 Stunden
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")
    interval = days * 86400 + minutes * 60
    if interval <= 0:
        return await ctx.send("❌ Ungültige Intervalle – bitte Tage oder Minuten > 0 angeben.")

    # Rückmeldung im Command-Kanal
    await ctx.send(f"🗑️ Nachrichten in {', '.join(ch.mention for ch in channels)} "
                   f"werden alle {days} Tage und {minutes} Minuten gelöscht.")

    for ch in channels:
        # bestehenden Task abbrechen
        if ch.id in cleanup_tasks:
            cleanup_tasks[ch.id].cancel()

        async def _loop_cleanup(channel: discord.TextChannel, interval_s: float):
            pre = _compute_pre_notify(interval_s)
            while True:
                # Vorwarnung
                if pre is not None:
                    await asyncio.sleep(pre)
                    warn_minutes = (interval_s - pre) / 60
                    # Falls ≥60 Min → in Stunden ausgeben
                    if warn_minutes >= 60:
                        warn_text = f"in {int(warn_minutes//60)} Stunde(n)"
                    else:
                        warn_text = f"in {int(warn_minutes)} Minute(n)"
                    await channel.send(f"⚠️ Achtung: In {warn_text} werden alle Nachrichten gelöscht. "
                                       "Sichert bitte wichtige Infos!")
                    # dann restliche Zeit
                    await asyncio.sleep(interval_s - pre)
                else:
                    # keine Vorwarnung
                    await asyncio.sleep(interval_s)

                # cleanup
                try:
                    await channel.purge(limit=None)
                    await channel.send("🗑️ Alle Nachrichten wurden automatisch gelöscht.")
                except discord.Forbidden:
                    print(f"❗️ Kein Recht zum Löschen in {channel.id}")

        # Task anlegen
        task = bot.loop.create_task(_loop_cleanup(ch, interval))
        cleanup_tasks[ch.id] = task

@bot.command(name="cleanup_stop")
@commands.check_any(
    commands.has_permissions(manage_messages=True),
    commands.has_any_role(ADMIN_ROLE_ID, MOD_ROLE_ID)
)
async def cleanup_stop(
    ctx,
    channels: Greedy[discord.abc.GuildChannel]
):
    """
    Stoppt die automatische Löschung in den angegebenen Kanälen.
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

# Starte den Bot
bot.run(TOKEN)