# bot/cogs/guild_join.py
from __future__ import annotations
import asyncio
import discord
from discord.ext import commands

from ..utils.replies import reply_text, make_embed, send_embed
from ..services.features import load_features  # <- wie in deinem Code verwendet

SETUP_CHANNEL_NAME = "fazzers-bot-setup"

class GuildJoinCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        # 1) Features laden
        features = load_features()
        if not features:
            features_text = "Keine Features eingetragen."
        else:
            # Nur für den Fall, dass du das noch als zusammenhängenden Text brauchst
            features_text = ""
            for name, desc in features:
                features_text += f"• **{name}**\n{desc.replace('\\n', '\\n')}\n\n"

        # 2) Kanal finden oder erstellen (wie bei dir)
        setup_channel = discord.utils.get(guild.text_channels, name=SETUP_CHANNEL_NAME)
        if setup_channel is None:
            try:
                setup_channel = await guild.create_text_channel(SETUP_CHANNEL_NAME)
                await asyncio.sleep(1)  # kleine Pause, damit Kanal bereit ist
            except discord.Forbidden:
                # Fallback: System-Channel oder erster Channel, in den der Bot schreiben darf
                setup_channel = (
                    guild.system_channel
                    or next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
                )

        if not setup_channel:
            return  # gar kein sendbarer Kanal gefunden

        # 3) Intro als Embed (bilingual wie in deinem Original)
        intro_msg = (
            f"👋 Danke, dass du mich hinzugefügt hast, **{guild.name}**!\n\n"
            "🌐 Bitte **zuerst die Sprache festlegen** (nur Admins): `/setlang de` oder `/setlang en`.\n"
            "Solange das nicht passiert, sind alle anderen Befehle gesperrt.\n\n"
            "🌐 Please **choose the language first** (admins only): `/setlang de` or `/setlang en`.\n"
            "Until then, all other commands are locked.\n\n"
        )
        await reply_text(setup_channel, intro_msg, kind="info")

        # 4) Feature-Liste als Embeds
        #    - Wir packen mehrere Features als Fields in ein Embed
        #    - Hard-Limits von Discord: max. 6000 Zeichen/Embed, 25 Felder, 1024 Zeichen pro Field-Value
        #    - Wir chunk’en sauber über mehrere Embeds falls nötig
        if features:
            current_embed = make_embed(
                title="🧩 Features",
                kind="info",
            )
            field_count = 0
            total_chars = len(current_embed.title or "")  # grobe Buchhaltung

            async def _flush():
                nonlocal current_embed, field_count, total_chars
                if field_count > 0:
                    await send_embed(setup_channel, current_embed, kind="info")
                    # reset
                    current_embed = make_embed(title="🧩 Features (fortgesetzt)", kind="info")
                    field_count = 0
                    total_chars = len(current_embed.title or "")

            for name, desc in features:
                name_str = str(name)
                value_str = (desc or "").replace("\\n", "\n").strip() or "—"

                # Falls Value > 1024 Zeichen: splitten
                chunks = []
                while value_str:
                    chunk = value_str[:1024]
                    chunks.append(chunk)
                    value_str = value_str[1024:]

                for idx, chunk in enumerate(chunks):
                    field_name = name_str if idx == 0 else f"{name_str} (…)"
                    projected_chars = total_chars + len(field_name) + len(chunk)
                    # Wenn wir Limits sprengen würden, sende das aktuelle Embed und starte ein neues
                    if field_count >= 24 or projected_chars >= 5800:  # Puffer
                        await _flush()

                    current_embed.add_field(name=field_name, value=chunk, inline=False)
                    field_count += 1
                    total_chars += len(field_name) + len(chunk)

            # rest senden
            await _flush()

async def setup(bot: commands.Bot):
    await bot.add_cog(GuildJoinCog(bot))