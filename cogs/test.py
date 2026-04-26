from discord.ext import commands


class Cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def goodmorning(self, ctx: commands.Context):
        if hasattr(self.bot, "ensure_config_bound"):
            await self.bot.ensure_config_bound()
        bind_event = getattr(self.bot, "config_bind_ready", None)
        if bind_event is not None:
            await bind_event.wait()
        return await ctx.send('Good Morning')


async def setup(bot):
    await bot.add_cog(Cog(bot))
