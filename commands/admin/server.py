import discord
from discord.ext import commands

from config import settings, emojis

class ServerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.command(name="prefix", description="Manage the servers command prefix")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def prefix(self, ctx, action: str = "", *, value: str = ""):
        if not action:
            current = self.bot.db.getPrefix(ctx.guild.id) or settings.prefix
            return await ctx.respond(f"{emojis.info} Current prefix: `{current}`\nUse `prefix set <value>` or `prefix clear` to change it.")
        action = action.lower()
        if action in ("clear", "reset"):
            await self.bot.db.clearPrefix(ctx.guild.id)
            return await ctx.respond(f"{emojis.success} Prefix reset to `{settings.prefix}`")
        if action == "set":
            if not value or not value.strip():
                return await ctx.respond(f"{emojis.fail} You must provide a value. Usage: `prefix set <value>`", ephemeral=True)
            new_prefix = value.strip()
            if len(new_prefix) > 6:
                return await ctx.respond(f"{emojis.fail} Prefix must be 6 characters or fewer.", ephemeral=True)
            await self.bot.db.setPrefix(ctx.guild.id, new_prefix)
            return await ctx.respond(f"{emojis.success} Prefix updated to `{new_prefix}`")
        await ctx.respond(f"{emojis.fail} Unknown action `{action}`. Use `prefix set <value>` or `prefix clear`.", ephemeral=True)

    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.command(name="disablecmd", aliases=["disablecommand"], description="Disable a command for the current server")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def disable(self, ctx, *, command_name: str):
        cmd = self.bot.get_command(command_name)
        if not cmd:
            return await ctx.respond(f"{emojis.fail} Command `{command_name}` not found.", ephemeral=True)
        name = cmd.qualified_name
        if self.bot.db.isCommandDisabled(ctx.guild.id, name):
            return await ctx.respond(f"{emojis.fail} Command `{name}` is already disabled.", ephemeral=True)
        await self.bot.db.disableCommand(ctx.guild.id, name)
        await ctx.respond(f"{emojis.success} Command `{name}` has been disabled in this server.")

    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.command(name="enablecmd", aliases=["enablecommand"], description="Enable a command for the current server")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def enable(self, ctx, *, command_name: str):
        cmd = self.bot.get_command(command_name)
        name = cmd.qualified_name if cmd else command_name
        if not (self.bot.db.isCommandDisabled(ctx.guild.id, command_name) or self.bot.db.isCommandDisabled(ctx.guild.id, name)):
            return await ctx.respond(f"{emojis.fail} Command `{command_name}` is not disabled.", ephemeral=True)
        await self.bot.db.enableCommand(ctx.guild.id, command_name)
        await self.bot.db.enableCommand(ctx.guild.id, name)
        await ctx.respond(f"{emojis.success} Command `{command_name}` has been enabled in this server.")

    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def disabledcmds(self, ctx):
        names = await self.bot.db.disabledCommands(ctx.guild.id)
        if not names:
            return await ctx.respond(f"{emojis.info} No commands are disabled in this server.")
        embed = discord.Embed(
            title="Disabled Commands",
            description=f"The following commands are disabled in **{ctx.guild.name}**:\n\n" + "\n".join(f"• `{c}`" for c in names),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Use 'enablecmd <command>' to re-enable a command")
        await ctx.respond(embed=embed)


def setup(bot):
    bot.add_cog(ServerCog(bot))
