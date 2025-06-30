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
bot = commands.Bot(command_prefix="!", intents=intents)

# Speichert laufende Unlock-Timer: channel.id → Task
lock_tasks: dict[int, asyncio.Task] = {}

# Error-Handler
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, MissingRequiredArgument):
        await ctx.send(
            f"❌ Fehlendes Argument: `{error.param.name}`\n"
            "Verwendung:\n"
            "• `!lock <#Kanal… oder ID…> <HH:MM> <Minuten>`\n"
            "• `!unlock <#Kanal… oder ID…>`"
        )
    elif isinstance(error, MissingPermissions):
        await ctx.send("❌ Du benötigst `Manage Channels`-Rechte dafür.")
    else:
        raise error

@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
async def lock(
    ctx,
    channels: Greedy[discord.abc.GuildChannel],
    start_time: str,
    duration: int
):
    """
    Sperrt beliebig viele Text- oder Sprachkanäle zur angegebenen Uhrzeit
    für `duration` Minuten.
    Usage: !lock #text1 #🔊Voice HH:MM Minuten
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")

    # Uhrzeit parsen
    try:
        hour, minute = map(int, start_time.split(":"))
    except ValueError:
        return await ctx.send("❌ Ungültiges Zeitformat. Bitte `HH:MM` im 24h-Format angeben.")

    # aktuelle Zeit in Berliner Zeitzone
    now = datetime.datetime.now(tz=ZoneInfo("Europe/Berlin"))
    # Ziel-Datetime in Berliner Zeit
    target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_dt <= now:
        target_dt += datetime.timedelta(days=1)
    delay_until_lock = (target_dt - now).total_seconds()

    role = ctx.guild.default_role

    for channel in channels:
        # evtl. bestehenden Task abbrechen
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()

        async def _scheduled_lock(ch: discord.abc.GuildChannel, delay: float, dur: int):
            # Warte bis zur Startzeit
            await asyncio.sleep(delay)

            # Sperre setzen mit beibehaltener Unsichtbarkeit
            if isinstance(ch, discord.TextChannel):
                await ch.set_permissions(
                    role,
                    view_channel=False,
                    send_messages=False
                )
            else:
                # VoiceChannel: entziehe View, Connect & Speak
                await ch.set_permissions(
                    role,
                    view_channel=False,
                    connect=False,
                    speak=False
                )
                # und kicke alle, die noch drin sind
                for member in ch.members:
                    try:
                        await member.move_to(None)
                    except Exception:
                        pass

            # Nachricht im Kanal
            await ch.send(
                f"🔒 Kanal automatisch gesperrt um {start_time} Uhr, da Rina gerade live ist – für {dur} Minuten nicht verfügbar 🚫"
            )

            # Timer bis zur Entsperrung
            await asyncio.sleep(dur * 60)

            # Permissions zurücksetzen, Anzeige-Override entfernen
            if isinstance(ch, discord.TextChannel):
                await ch.set_permissions(
                    role,
                    view_channel=None,
                    send_messages=None
                )
            else:
                await ch.set_permissions(
                    role,
                    view_channel=None,
                    connect=None,
                    speak=None
                )

            # Nachricht im entsperrten Kanal
            await ch.send("🔓 Kanal automatisch entsperrt – viel Spaß! 🎉")
            # Rückmeldung im Command-Kanal
            await ctx.send(f"🔓 {ch.mention} wurde automatisch entsperrt.")

            lock_tasks.pop(ch.id, None)

        # Task anlegen
        task = bot.loop.create_task(_scheduled_lock(channel, delay_until_lock, duration))
        lock_tasks[channel.id] = task

        # Sofortige Bestätigung im Command-Kanal
        await ctx.send(
            f"⏰ {channel.mention} wird um {start_time} Uhr für {duration} Minuten gesperrt."
        )

@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def unlock(
    ctx,
    channels: Greedy[discord.abc.GuildChannel]
):
    """
    Hebt die Sperre für beliebig viele Kanäle auf.
    Usage: !unlock #text1 #🔊Voice
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")

    role = ctx.guild.default_role

    for channel in channels:
        # Abbrechen laufender Timer
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()
            lock_tasks.pop(channel.id, None)

        # Permissions zurücksetzen, Anzeige-Override entfernen
        if isinstance(channel, discord.TextChannel):
            await channel.set_permissions(
                role,
                view_channel=None,
                send_messages=None
            )
        elif isinstance(channel, discord.VoiceChannel):
            await channel.set_permissions(
                role,
                view_channel=None,
                connect=None,
                speak=None
            )
        else:
            await ctx.send(f"⚠️ {channel.name} ist kein Text- oder Sprachkanal.")
            continue

        await ctx.send(f"🔓 {channel.mention} entsperrt.")

bot.run(TOKEN)


# python bot.py