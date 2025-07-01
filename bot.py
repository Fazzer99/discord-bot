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
# Speichert originalen view_channel-Status für OG-Rollen: channel.id → {"og": bool|None, "senior": bool|None}
role_views: dict[int, dict[str, bool | None]] = {}

# IDs der OG-Rollen
OG_ROLE_ID = 1386723945583218749
SENIOR_OG_ROLE_ID = 1387936511260889158

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
@commands.has_permissions(manage_channels=True)
async def unlock(ctx, channels: Greedy[discord.abc.GuildChannel]):
    """
    Hebt Sperre sofort auf.
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

bot.run(TOKEN)



# python bot.py