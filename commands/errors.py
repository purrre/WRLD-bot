import sys
import traceback

from discord.ext import commands
from discord import SeparatorSpacingSize
from discord.ui import TextDisplay

from config import colors, emojis, settings
from utils.functions import consoleLog, getCommandInfo
from utils.components import createContainer, createView, buildCommandGuide

COMMAND_INFO = getCommandInfo()

class ErrorsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def logError(self, error, *, ctx=None):
        consoleLog("HANDLER", error, type="error")
        channelId = settings.error_channel_id
        if not channelId:
            return
        channel = self.bot.get_channel(channelId)
        if channel is None:
            return
        tb = traceback.format_exception(type(error), error, error.__traceback__)
        tbText = "".join(tb)[-1500:]
        cont = createContainer()
        cont.add_item(TextDisplay(f"## Unhandled Error"))
        if ctx:
            cont.add_item(TextDisplay(
                f"**Command:** `{ctx.command}`\n"
                f"**User:** {ctx.author.mention} (`{ctx.author.id}`)\n"
                f"**Guild:** `{ctx.guild.name if ctx.guild else 'DM'}`"
            ))
            cont.add_separator(divider=True)
        cont.add_text(f"```py\n{tbText}\n```")
        try:
            await channel.send(view=createView(cont))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, (commands.CommandNotFound, commands.NotOwner)):
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            return await self.sendUsage(ctx)
        if isinstance(error, commands.CommandOnCooldown):
            minutes, seconds = divmod(int(error.retry_after), 60)
            timeText = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
            msg = await ctx.reply(f"Too fast. Try again in **{timeText}**")
            return await msg.delete(delay=5)
        if isinstance(error, commands.CommandInvokeError):
            await self.logError(error.original, ctx=ctx)
            return await ctx.reply(f"{emojis.fail} Something went wrong :( The error has been reported.", delete_after=10)
        if isinstance(error, commands.NoPrivateMessage):
            return await ctx.respond(f"{emojis.fail} This command only works in servers.", ephemeral=True)
        if isinstance(error, commands.MissingPermissions):
            missing = ", ".join(error.missing_permissions)
            return await ctx.respond(f"{emojis.fail} You need `{missing}` to do that.", ephemeral=True)
        if isinstance(error, commands.CheckFailure):
            return consoleLog("BLACKLIST", f"{ctx.author.name} ({ctx.author.id}) tried to run a command")
        await self.logError(error, ctx=ctx)
        await ctx.reply(f"{emojis.fail} Something went wrong. The error has been reported.", delete_after=10)

    async def sendUsage(self, ctx):
        cmdName = ctx.command.name
        if cmdName in COMMAND_INFO:
            return await ctx.respond(view=buildCommandGuide(COMMAND_INFO[cmdName], ctx.prefix, ctx.command.aliases))
        cont = createContainer(color=colors.main)
        cont.add_item(TextDisplay(f"## {ctx.command.name}"))
        cont.add_item(TextDisplay(ctx.command.description or "No description provided."))
        cont.add_separator(divider=True, spacing=SeparatorSpacingSize.small)
        cd = ctx.command._buckets._cooldown
        cdText = f"{round(cd.per)}s" if cd else "None"
        aliases = ", ".join(ctx.command.aliases) if ctx.command.aliases else "None"
        cont.add_item(TextDisplay(
            f"**Usage:** `{ctx.prefix}{ctx.command.usage or ctx.command.name}`\n"
            f"**Cooldown:** `{cdText}`\n"
            f"**Aliases:** `{aliases}`"
        ))
        await ctx.reply(view=createView(cont))

    @commands.Cog.listener()
    async def on_error(self, event_method):
        consoleLog("HANDLER", f"Error in {event_method}", type="error")
        excType, excValue, excTb = sys.exc_info()
        if excValue:
            await self.logError(excValue)

def setup(bot):
    bot.add_cog(ErrorsCog(bot))
