import discord
from discord.ext import commands

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"✅ Logged in as {self.bot.user} ({self.bot.user.id})")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # TODO: Portiere deine Willkommenslogik
        pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # TODO: Portiere deine Leave-Logik
        pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # TODO: Portiere deine VC-Tracking/Overrides-Logik
        pass

async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))
