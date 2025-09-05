# bot/cogs/guild_join.py
from __future__ import annotations
import asyncio
import discord
from discord.ext import commands

from ..utils.replies import reply_text, make_embed, send_embed
from ..services.features import load_features  # <- wie in deinem Code verwendet
from ..db import fetchrow  # <- NEU: DB-Check für bot_bans

SETUP_CHANNEL_NAME = "ignix-bot-setup"
SUPPORT_INVITE_URL = "https://discord.gg/YYkpE7fnnv"


class SupportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Support",
                style=discord.ButtonStyle.link,
                url=SUPPORT_INVITE_URL,
            )
        )


class GuildJoinCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        # 0) Sofortiger Ban-Check: steht die Guild in public.bot_bans?
        try:
            banned = await fetchrow(
                "SELECT reason FROM public.bot_bans WHERE guild_id=$1",
                guild.id
            )
        except Exception:
            banned = None

        if banned:
            # Optional: leises Verlassen ohne Nachricht
            try:
                await guild.leave()
            except Exception:
                pass
            return

        # 1) Features laden
        features = load_features()
        if not features:
            features_text = "Keine Features eingetragen."
        else:
            # Nur falls du das noch als zusammenhängenden Text brauchst
            features_text = ""
            for name, desc in features:
                features_text += f"• **{name}**\n{desc.replace('\\n', '\\n')}\n\n"

        # 2) Kanal finden oder erstellen
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

        # 2a) NEU: DM an den Server-Owner (immer Englisch), mit Support-Button
        owner = guild.owner
        if owner is None and guild.owner_id:
            try:
                owner = await guild.fetch_member(guild.owner_id)
            except Exception:
                owner = None

        if owner is not None:
            bot_name = self.bot.user.name if self.bot.user else "Ignix"
            emb = make_embed(
                title=f"Thank you for choosing {bot_name}!",
                description=(
                    f"✅ **{bot_name}** has been added to **{guild.name}** successfully!\n\n"
                    "You can set up the bot using `/onboard`:\n"
                    "• `/onboard lang:de` or `/onboard lang:en`\n"
                    "• `/onboard tz:UTC+2` (quarter-hour steps supported)\n\n"
                    "If you need help or have questions, click **Support** below."
                ),
                kind="success",
            )
            try:
                await owner.send(embed=emb, view=SupportView())
            except discord.Forbidden:
                # Fallback: DM nicht möglich → Hinweis im Setup-Kanal
                try:
                    await reply_text(
                        setup_channel,
                        "I couldn't DM the server owner. If you need help, use the Support button:",
                        kind="warning",
                    )
                    # Button als separate Nachricht posten
                    await setup_channel.send(view=SupportView())
                except Exception:
                    pass

        # 3) Intro: jetzt Onboarding statt setlang
        intro_msg = (
            f"👋 Danke, dass du mich hinzugefügt hast, **{guild.name}**!\n\n"
            "🧩 **Onboarding (nur Admins):**\n"
            "1) Sprache festlegen: `/onboard lang:de` **oder** `/onboard lang:en`\n"
            "2) Zeitzone setzen: `/onboard tz:UTC+2` (Viertelschritte erlaubt: `+0.25`, `+0.5`, `+0.75`, z. B. `UTC-5.75`)\n"
            "➡️ Du kannst beides **in einem Schritt** setzen: `/onboard lang:de tz:UTC+2`\n\n"
            "🔒 Solange das Onboarding nicht abgeschlossen ist, sind alle anderen Befehle gesperrt.\n\n"
            "— — —\n"
            "🧩 **Onboarding (admins only):**\n"
            "1) Set language: `/onboard lang:de` **or** `/onboard lang:en`\n"
            "2) Set timezone: `/onboard tz:UTC+2` (quarter-hour steps supported: `+0.25`, `+0.5`, `+0.75`, e.g. `UTC-5.75`)\n"
            "➡️ You can also set **both at once**: `/onboard lang:en tz:UTC+2`\n\n"
            "🔒 Until onboarding is complete, all other commands are locked."
        )
        await reply_text(setup_channel, intro_msg, kind="info")

        # 4) Feature-Liste als Embeds (unverändert)
        #    - Max 25 Felder pro Embed, 1024 Zeichen pro Field-Value, 6000 Zeichen gesamt
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