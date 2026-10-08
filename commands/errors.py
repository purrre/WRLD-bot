import traceback

from discord.ext import commands
from discord import SeparatorSpacingSize
from discord.ui import TextDisplay

from config import colors, emojis, settings
from utils.functions import consoleLog, getCommandInfo
from utils.components import createContainer, createView, buildCommandGuide


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

    async def dispatch_error(self, ctx, error):
        # shared by prefix (command_error) and slash (application_command_error)
        if isinstance(error, (commands.CommandNotFound, commands.NotOwner)):
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            return await self.sendUsage(ctx)
        if isinstance(error, commands.CommandOnCooldown):
            minutes, seconds = divmod(int(error.retry_after), 60)
            timeText = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
            return await ctx.respond(f"Too fast. Try again in **{timeText}**", delete_after=5)
        if isinstance(error, commands.NoPrivateMessage):
            return await ctx.respond(f"{emojis.fail} This command only works in servers.", ephemeral=True)
        if isinstance(error, commands.MissingPermissions):
            missing = ", ".join(error.missing_permissions)
            return await ctx.respond(f"{emojis.fail} You need `{missing}` to do that.", ephemeral=True)
        if isinstance(error, commands.CheckFailure):
            return consoleLog("BLACKLIST", f"{ctx.author.name} ({ctx.author.id}) tried to run a command")
        await self.logError(getattr(error, "original", error), ctx=ctx)
        await ctx.respond(f"{emojis.fail} Something went wrong. The error has been reported.", delete_after=10)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        await self.dispatch_error(ctx, error)

    @commands.Cog.listener()
    async def on_application_command_error(self, ctx, error):
        await self.dispatch_error(ctx, error)

    @commands.Cog.listener()
    async def on_view_error(self, error, item, interaction):
        await self.logError(error)
        try:
            msg = f"{emojis.fail} Something went wrong. The error has been reported."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    async def sendUsage(self, ctx):
        cmd = ctx.command
        if cmd is None:
            return await ctx.respond(f"{emojis.fail} Invalid usage.", ephemeral=True)
        commandInfo = getCommandInfo()
        prefix = getattr(ctx, "prefix", None) or settings.prefix
        aliases = getattr(cmd, "aliases", None) or []
        if cmd.name in commandInfo:
            return await ctx.respond(view=buildCommandGuide(commandInfo[cmd.name], prefix, aliases))
        cont = createContainer(color=colors.main)
        cont.add_item(TextDisplay(f"## {cmd.name}"))
        cont.add_item(TextDisplay(cmd.description or "No description provided."))
        cont.add_separator(divider=True, spacing=SeparatorSpacingSize.small)
        cd = getattr(getattr(cmd, "_buckets", None), "_cooldown", None)
        cdText = f"{round(cd.per)}s" if cd else "None"
        cont.add_item(TextDisplay(
            f"**Usage:** `{prefix}{getattr(cmd, 'usage', None) or cmd.name}`\n"
            f"**Cooldown:** `{cdText}`\n"
            f"**Aliases:** `{', '.join(aliases) if aliases else 'None'}`"
        ))
        await ctx.respond(view=createView(cont))

def setup(bot):
    bot.add_cog(ErrorsCog(bot))
