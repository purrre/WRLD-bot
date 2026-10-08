import discord
from discord import ButtonStyle
from discord.ext import commands
from discord.ui import ActionRow, Button

from config import settings, emojis, colors, NOT_YOURS
from utils.components import createContainer, createView, stopParentView

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

    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.command(name="purge", aliases=["clear"], description="Mass delete messages")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def purge(self, ctx, amount: int):
        if amount < 1 or amount > 100:
            return await ctx.respond(f"{emojis.fail} Amount must be between 1 and 100.", ephemeral=True)
        deleted = await ctx.channel.purge(limit=amount + 1)
        await ctx.respond(f"{emojis.success} Purged {len(deleted) - 1} messages.", delete_after=6)

    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    @commands.command(name="slowmode", aliases=["sm"], description="Set the channel slowmode delay")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def slowmode(self, ctx, seconds: int):
        if seconds < 0 or seconds > 21600:
            return await ctx.respond(f"{emojis.fail} Slowmode must be between 0 and 21600 seconds.", ephemeral=True)
        try:
            await ctx.channel.edit(slowmode_delay=seconds)
        except Exception as e:
            return await ctx.respond(f"{emojis.fail} Failed to set slowmode:\n```py\n{e}\n```")
        await ctx.respond(f"{emojis.success} Set channel slowmode to **{seconds} seconds**.")
        

    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    @commands.command(name="nuke", description="Nuke and recreate this channel", aliases=["cnuke"])
    @commands.cooldown(1, 30, commands.BucketType.guild)
    async def nuke(self, ctx, *, message: str = None):
        old_channel = ctx.channel
        cont = createContainer(
            title="Confirm Nuke",
            description=f"Are you sure you want to nuke {old_channel.mention}?\nThis effectively clears message history and cannot be undone.",
            heading="##",
            color=colors.red,
        )
        row = ActionRow()
        confirm_btn = Button(label="Confirm", style=ButtonStyle.red)
        cancel_btn = Button(label="Cancel", style=ButtonStyle.gray)

        async def confirm_callback(interaction):
            if interaction.user.id != ctx.author.id:
                return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            stopParentView(confirm_btn)
            await interaction.response.defer()
            try:
                new_channel = await old_channel.clone(reason=f"Nuked by {ctx.author}")
                await new_channel.edit(position=old_channel.position)
                await old_channel.delete(reason=f"Nuked by {ctx.author}")
            except Exception as e:
                return await ctx.respond(f"{emojis.fail} Failed to nuke channel:\n```py\n{e}\n```")
            await new_channel.send(message or f"## FIRST\n-# channel nuked by {ctx.author.mention}")

        async def cancel_callback(interaction):
            if interaction.user.id != ctx.author.id:
                return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            stopParentView(cancel_btn)
            new_cont = createContainer(title="Cancelled", description="The channel was not nuked.", heading="##")
            await interaction.response.edit_message(view=createView(new_cont))

        confirm_btn.callback = confirm_callback
        cancel_btn.callback = cancel_callback
        row.add_item(confirm_btn)
        row.add_item(cancel_btn)
        await ctx.respond(view=createView(cont, row))


def setup(bot):
    bot.add_cog(ServerCog(bot))
