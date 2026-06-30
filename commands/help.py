import platform
import time

import discord
from discord.ext import bridge, commands

from config import colors, NOT_YOURS
from utils.components import loading, buildCommandGuide
from utils.functions import getCommandInfo

ITEMS_PER_PAGE = 10

CATEGORY_OPTIONS = [
    ("Songs", "Song search and file commands", "commands.songs"),
    ("Info", "Status and data commands", "commands.info"),
    ("User", "User-specific commands", "commands.user"),
    ("Server", "Server management commands", "commands.admin.server"),
    ("Admin", "Owner and admin commands", "commands.admin"),
    ("VC", "Voice channel media player", "commands.vc"),
]


def commandCategories(bot):
    grouped = {module: [] for _, _, module in CATEGORY_OPTIONS}
    grouped["misc"] = []
    for command in sorted(bot.commands, key=lambda cmd: cmd.qualified_name):
        module = getattr(command.callback, "__module__", "") or ""
        matched = False
        for _, _, categoryModule in CATEGORY_OPTIONS:
            if categoryModule == "commands.vc":
                continue
            if module.startswith(categoryModule):
                grouped[categoryModule].append(command)
                matched = True
                break
        if not matched:
            grouped["misc"].append(command)
    return grouped


def categoryEmbed(bot, categoryLabel, commandsList, prefix, page=0):
    startIdx = page * ITEMS_PER_PAGE
    pageCommands = commandsList[startIdx:startIdx + ITEMS_PER_PAGE]
    totalPages = (len(commandsList) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    embed = discord.Embed(title=f"wrld - {categoryLabel}", color=colors.main)
    embed.description = f"Use `{prefix}help <command>` to view more information on a command"
    if totalPages > 1:
        embed.description += f"\n-# Page {page + 1}/{totalPages}"
    for command in pageCommands:
        embed.add_field(name=command.name, value=command.description or "No description", inline=False)
    if bot.user and bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, bot, user_id, prefix, parentView):
        options = [
            discord.SelectOption(label=label, description=description, value=module)
            for label, description, module in CATEGORY_OPTIONS
        ]
        super().__init__(placeholder="Select a command category...", options=options)
        self.bot = bot
        self.user_id = user_id
        self.prefix = prefix
        self.parentView = parentView

    async def callback(self, interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
        module = self.values[0]
        label = next(label for label, _, categoryModule in CATEGORY_OPTIONS if categoryModule == module)
        if module == "commands.vc":
            embed = discord.Embed(
                title="wrld - VC",
                description=(
                    "WRLD doesnt have voice channel features built in, however I built one for you that you can build and run yourself!\n\n"
                    "**Build It**\n"
                    "[GitHub Repo](https://github.com/purrre/WRLD-ext-media-player)"

                ),
                color=colors.main,
            )
            if self.bot.user and self.bot.user.display_avatar:
                embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            return await interaction.response.edit_message(embed=embed, view=self.parentView)
        grouped = commandCategories(self.bot)
        self.parentView.currentModule = module
        self.parentView.currentLabel = label
        self.parentView.currentCommands = grouped.get(module, [])
        self.parentView.currentPage = 0
        self.parentView.updatePaginationButtons()
        embed = categoryEmbed(self.bot, label, self.parentView.currentCommands, self.prefix, 0)
        await interaction.response.edit_message(embed=embed, view=self.parentView)


class HelpMenu(discord.ui.View):
    def __init__(self, bot, user_id, prefix):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = user_id
        self.prefix = prefix
        self.currentModule = None
        self.currentLabel = None
        self.currentCommands = []
        self.currentPage = 0
        self.prevButton = None
        self.nextButton = None
        self.add_item(CategorySelect(bot, user_id, prefix, self))

    def updatePaginationButtons(self):
        totalPages = max(1, (len(self.currentCommands) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        if self.prevButton:
            self.remove_item(self.prevButton)
        if self.nextButton:
            self.remove_item(self.nextButton)
        self.prevButton = discord.ui.Button(label="Previous", style=discord.ButtonStyle.gray, row=2, disabled=self.currentPage <= 0)
        self.prevButton.callback = lambda inter: self.changePage(inter, -1)
        self.add_item(self.prevButton)
        self.nextButton = discord.ui.Button(label="Next", style=discord.ButtonStyle.gray, row=2, disabled=self.currentPage >= totalPages - 1)
        self.nextButton.callback = lambda inter: self.changePage(inter, 1)
        self.add_item(self.nextButton)

    async def changePage(self, interaction, delta):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
        totalPages = (len(self.currentCommands) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        self.currentPage = max(0, min(totalPages - 1, self.currentPage + delta))
        self.updatePaginationButtons()
        embed = categoryEmbed(self.bot, self.currentLabel, self.currentCommands, self.prefix, self.currentPage)
        await interaction.response.edit_message(embed=embed, view=self)


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.startedAt = time.time()

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def wrld(self, ctx):
        await ctx.reply(f"🎸 \n-# {round(self.bot.latency * 1000)}ms")

    @bridge.bridge_command(aliases=["h"], description="Display help information")
    @bridge.bridge_option(name="command", description="Specific command to get help for", required=False)
    @commands.cooldown(1, 7, commands.BucketType.user)
    async def help(self, ctx, command: str | None = None):
        msg = await ctx.reply(embed=await loading("command"))
        prefix = getattr(ctx, "clean_prefix", ctx.prefix)
        if command:
            cmd = self.bot.get_command(command)
            if not cmd:
                return await msg.edit(content=f"Invalid command. Run `{prefix}help` for a list of commands", embed=None)
            commandInfo = getCommandInfo()
            if cmd.name in commandInfo:
                return await msg.edit(content=None, embed=None, view=buildCommandGuide(commandInfo[cmd.name], prefix, cmd.aliases))
            aliases = ", ".join(cmd.aliases) if cmd.aliases else "None"
            cooldown = getattr(getattr(cmd, "_buckets", None), "_cooldown", None)
            cooldownText = f"{round(cooldown.per)} second(s)" if cooldown else "None"
            embed = discord.Embed(title=f"wrld - {cmd.name}", description=cmd.description or "No description", color=colors.main)
            embed.add_field(name="Usage", value=f"{prefix}{cmd.usage}" if getattr(cmd, "usage", None) else f"{prefix}{cmd.qualified_name}", inline=False)
            embed.add_field(name="Cooldown", value=cooldownText, inline=False)
            embed.add_field(name="Aliases", value=aliases, inline=False)
            return await msg.edit(embed=embed)
        embed = discord.Embed(
            title="wrld",
            description=f"Welcome to the help menu\n\nRun `{prefix}help <command>` for more information on a specific command\n\nSelect a category below to list commands",
            color=colors.main,
        )
        if self.bot.user and self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        await msg.edit(embed=embed, view=HelpMenu(self.bot, ctx.author.id, prefix))

    @bridge.bridge_command(aliases=["invite", "git"], description="Get information about WRLD")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def about(self, ctx):
        embed = discord.Embed(
            title="WRLD",
            description="Open-source Discord bot for Juice WRLD music and information. Free, forever.",
            color=colors.main,
        )
        elapsed = int(time.time() - self.startedAt)
        embed.add_field(
            name="Stats",
            value=(
                f"`{len(self.bot.guilds):,}` servers\n"
                f"`{len(self.bot.users):,}` users\n"
                f"`{len(self.bot.commands)}` commands\n"
                f"`{self.bot.shard_count}` shards\n"
                f"`{elapsed // 86400}d {(elapsed % 86400) // 3600}h {(elapsed % 3600) // 60}m` uptime"
            ),
            inline=False,
        )
        embed.add_field(name="Stack", value=f"Python `{platform.python_version()}`\nPycord `{discord.__version__}`", inline=True)
        embed.add_field(
            name="(RE)sources",
            value="[juicewrldapi.com](https://juicewrldapi.com/)\n[wrld.pure0.lol/api/groupbuys](https://wrld.pure0.lol/api/groupbuys)",
            inline=True,
        )
        if self.bot.user and self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="Made with ❤️ by @purree for juice comm")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="GitHub", url="https://github.com/purrre/wrld-bot"))
        view.add_item(discord.ui.Button(label="Invite", url="https://discord.com/oauth2/authorize?client_id=1500898771306024961"))
        await ctx.respond(embed=embed, view=view)


def setup(bot):
    bot.add_cog(HelpCog(bot))
