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

# IDs der OG-Rollen
OG_ROLE_ID = 1386723945583218749
SENIOR_OG_ROLE_ID = 1387936511260889158

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
    Sperrt Text- oder Sprachkanäle zur angegebenen Uhrzeit für `duration` Minuten.
    Öffentlich: @everyone verliert Schreib-/Connect-Rechte.
    Privat: nur OG & Senior OG verlieren Schreib-/Connect-Rechte, Sichtbarkeit bleibt.
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")

    # Uhrzeit parsen
    try:
        hour, minute = map(int, start_time.split(":"))
    except ValueError:
        return await ctx.send("❌ Ungültiges Zeitformat. Bitte `HH:MM` eingeben.")

    # Berechne Verzögerung bis zur Sperrung (Berlin-Zeit)
    now = datetime.datetime.now(tz=ZoneInfo("Europe/Berlin"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    delay = (target - now).total_seconds()

    everyone = ctx.guild.default_role
    og_role = ctx.guild.get_role(OG_ROLE_ID)
    senior_og = ctx.guild.get_role(SENIOR_OG_ROLE_ID)

    for channel in channels:
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()

        # Prüfe, ob der Kanal privat ist (everyone.view_channel=False)
        is_private = (channel.overwrites_for(everyone).view_channel is False)

        async def _do_lock(ch: discord.abc.GuildChannel, wait: float, dur: int, private: bool):
            await asyncio.sleep(wait)

            # Sperre setzen
            if isinstance(ch, discord.TextChannel):
                if private:
                    # OG & Senior OG: Schreibrecht entziehen
                    if og_role:
                        await ch.set_permissions(og_role, send_messages=False)
                    if senior_og:
                        await ch.set_permissions(senior_og, send_messages=False)
                else:
                    # öffentlich: everyone Schreibrecht entziehen
                    await ch.set_permissions(everyone, send_messages=False)
            else:
                # Voice-Channel
                if private:
                    # OG & Senior OG: Connect/Speak entziehen
                    if og_role:
                        await ch.set_permissions(og_role, connect=False, speak=False)
                    if senior_og:
                        await ch.set_permissions(senior_og, connect=False, speak=False)
                else:
                    # öffentlich: everyone Connect/Speak entziehen
                    await ch.set_permissions(everyone, connect=False, speak=False)
                # Kick alle, die noch drin sind
                for m in ch.members:
                    try:
                        await m.move_to(None)
                    except:
                        pass

            await ch.send(
                f"🔒 Kanal automatisch gesperrt um {start_time} Uhr – für {dur} Minuten nicht verfügbar 🚫"
            )

            # Warte die Dauer
            await asyncio.sleep(dur * 60)

            # Permissions zurücksetzen
            if isinstance(ch, discord.TextChannel):
                if private:
                    if og_role:
                        await ch.set_permissions(og_role, send_messages=None)
                    if senior_og:
                        await ch.set_permissions(senior_og, send_messages=None)
                else:
                    await ch.set_permissions(everyone, send_messages=None)
            else:
                if private:
                    if og_role:
                        await ch.set_permissions(og_role, connect=None, speak=None)
                    if senior_og:
                        await ch.set_permissions(senior_og, connect=None, speak=None)
                else:
                    await ch.set_permissions(everyone, connect=None, speak=None)

            await ch.send("🔓 Kanal automatisch entsperrt – viel Spaß! 🎉")
            await ctx.send(f"🔓 {ch.mention} wurde automatisch entsperrt.")
            lock_tasks.pop(ch.id, None)

        # Starte Task
        task = bot.loop.create_task(_do_lock(channel, delay, duration, is_private))
        lock_tasks[channel.id] = task

        # Rückmeldung
        await ctx.send(f"⏰ {channel.mention} wird um {start_time} Uhr für {duration} Minuten gesperrt.")

@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def unlock(
    ctx,
    channels: Greedy[discord.abc.GuildChannel]
):
    """
    Hebt die Sperre für Kanäle sofort auf.
    """
    if not channels:
        return await ctx.send("❌ Bitte mindestens einen Kanal angeben.")

    everyone = ctx.guild.default_role
    og_role = ctx.guild.get_role(OG_ROLE_ID)
    senior_og = ctx.guild.get_role(SENIOR_OG_ROLE_ID)

    for channel in channels:
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()
            lock_tasks.pop(channel.id, None)

        is_private = (channel.overwrites_for(everyone).view_channel is False)

        if isinstance(channel, discord.TextChannel):
            if is_private:
                if og_role:
                    await channel.set_permissions(og_role, send_messages=None)
                if senior_og:
                    await channel.set_permissions(senior_og, send_messages=None)
            else:
                await channel.set_permissions(everyone, send_messages=None)
        else:
            if is_private:
                if og_role:
                    await channel.set_permissions(og_role, connect=None, speak=None)
                if senior_og:
                    await channel.set_permissions(senior_og, connect=None, speak=None)
            else:
                await channel.set_permissions(everyone, connect=None, speak=None)

        await ctx.send(f"🔓 {channel.mention} entsperrt.")

bot.run(TOKEN)



# python bot.py