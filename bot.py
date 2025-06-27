import os
import asyncio
import datetime

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
intents.message_content = True  # für Prefix-Commands benötigt
bot = commands.Bot(command_prefix="!", intents=intents)

# Speichert laufende Unlock-Timer: channel.id → Task
lock_tasks: dict[int, asyncio.Task] = {}

# Error-Handler für fehlende Argumente und Permissions
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
    Sperrt beliebig viele Text- oder Sprachkanäle zur angegebenen Uhrzeit für `duration` Minuten.
    Usage: !lock #text1 #🔊Voice HH:MM Minuten
    Beispiel: !lock 123… 456… 21:30 45
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")

    # Uhrzeit parsen
    try:
        hour, minute = map(int, start_time.split(":"))
        target_time = datetime.time(hour, minute)
    except ValueError:
        return await ctx.send("❌ Ungültiges Zeitformat. Bitte `HH:MM` im 24h-Format angeben.")

    now = datetime.datetime.now()
    target_dt = datetime.datetime.combine(now.date(), target_time)
    if target_dt <= now:
        target_dt += datetime.timedelta(days=1)
    delay_until_lock = (target_dt - now).total_seconds()

    role = ctx.guild.default_role

    for channel in channels:
        # Bestehende Tasks abbrechen
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()

        # Task planen: erst sperren, dann nach duration entsperren
        async def _scheduled_lock(ch: discord.abc.GuildChannel, delay: float, dur: int):
            await asyncio.sleep(delay)
            # Sperre setzen
            if isinstance(ch, discord.TextChannel):
                await ch.set_permissions(role, send_messages=False)
            else:  # VoiceChannel
                await ch.set_permissions(role, connect=False, speak=False)
            await ch.send(
                f"🔒 Kanal automatisch gesperrt um {start_time} Uhr, da Rina gerade live ist – für {dur} Minuten nicht verfügbar 🚫"
            )
            # Warten bis Duration abgelaufen
            await asyncio.sleep(dur * 60)
            # Entsperren
            if isinstance(ch, discord.TextChannel):
                await ch.set_permissions(role, send_messages=None)
            else:
                await ch.set_permissions(role, connect=None, speak=None)
            await ch.send("🔓 Kanal automatisch entsperrt – viel Spaß! 🎉")
            await ctx.send(f"🔓 {ch.mention} wurde automatisch entsperrt.")
            lock_tasks.pop(ch.id, None)

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

        # Permissions zurücksetzen
        if isinstance(channel, discord.TextChannel):
            await channel.set_permissions(role, send_messages=None)
        elif isinstance(channel, discord.VoiceChannel):
            await channel.set_permissions(role, connect=None, speak=None)
        else:
            await ctx.send(f"⚠️ {channel.name} ist kein Text- oder Sprachkanal.")
            continue

        await ctx.send(f"🔓 {channel.mention} entsperrt.")

bot.run(TOKEN)


# python bot.py