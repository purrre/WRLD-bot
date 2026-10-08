import platform
import time

import discord
from discord.ext import bridge, commands
from discord.ui import ActionRow, Button, Section, TextDisplay, Thumbnail

from config import colors, settings, emojis, NOT_YOURS
from utils.components import createContainer, createView, createSimplePagination, TimeoutView, buildCommandGuide, stopParentView
from utils.functions import getCommandInfo

ITEMS_PER_PAGE = 10

CATEGORY_OPTIONS = [
    ("Songs", "Song search and file commands", "commands.songs"),
    ("Info", "Status and data commands", "commands.info"),
    ("User", "User-specific commands", "commands.user"),
    ("Server", "Server management commands", "commands.admin.server"),
    ("Admin", "Owner and admin commands", "commands.admin"),
    ("VC", "Voice channel media player", "commands.vc"),
    ("Misc", "Everything else", "misc"),
]


def commandCategories(bot):
    grouped = {module: [] for _, _, module in CATEGORY_OPTIONS}
    for command in sorted(bot.commands, key=lambda cmd: cmd.qualified_name):
        module = getattr(command.callback, "__module__", "") or ""
        for _, _, categoryModule in CATEGORY_OPTIONS:
            if categoryModule == "commands.vc":
                continue
            if categoryModule == "misc":
                grouped["misc"].append(command)
                break
            if module.startswith(categoryModule):
                grouped[categoryModule].append(command)
                break
    return grouped


def categoryContainer(label, commandsList, prefix, page=0):
    startIdx = page * ITEMS_PER_PAGE
    pageCommands = commandsList[startIdx:startIdx + ITEMS_PER_PAGE]
    totalPages = max(1, (len(commandsList) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    cont = createContainer(title=f"WRLD - {label}", heading="###", separator=False, color=None)
    if pageCommands:
        cont.add_text("\n".join(f"`{prefix}{c.name}` - {c.description or 'No description'}" for c in pageCommands))
    else:
        cont.add_text("No commands in this category.")
    cont.add_separator(divider=True)
    cont.add_text(f"-# `{prefix}help <command>` for details - Page {page + 1}/{totalPages}")
    return cont


class CategorySelect(discord.ui.Select):
    def __init__(self, bot, user_id, prefix):
        options = [
            discord.SelectOption(label=label, description=description, value=module)
            for label, description, module in CATEGORY_OPTIONS
        ]
        super().__init__(placeholder="Select a command category...", options=options)
        self.bot = bot
        self.user_id = user_id
        self.prefix = prefix

    async def callback(self, interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
        stopParentView(self)
        module = self.values[0]
        label = next(label for label, _, categoryModule in CATEGORY_OPTIONS if categoryModule == module)
        selectRow = ActionRow(CategorySelect(self.bot, self.user_id, self.prefix))
        if module == "commands.vc":
            cont = createContainer(
                title="WRLD  VC",
                heading="##",
                separator=False,
                color=None,
                description=(
                    "WRLD doesnt have voice channel features built in, however I built one for you that you can build and run yourself!\n\n"
                    "**Build It**\n"
                    "[GitHub Repo](https://github.com/purrre/WRLD-ext-media-player)"
                ),
            )
            return await interaction.response.edit_message(view=createView(cont, selectRow, viewClass=TimeoutView))
        commandsList = commandCategories(self.bot).get(module, [])
        prefix = self.prefix

        def render(pageCommands, page, totalPages, userId, extra):
            return categoryContainer(label, commandsList, prefix, page), None

        pagination = createSimplePagination(
            commandsList, ITEMS_PER_PAGE, self.user_id, "helpcat", render,
            viewClass=TimeoutView,
            extraItemsFunc=lambda pv, page: [ActionRow(CategorySelect(self.bot, self.user_id, self.prefix))],
        )
        await pagination.show(interaction)


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.startedAt = time.time()

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def WRLD(self, ctx):
        await ctx.reply(f"🎸 \n-# {round(self.bot.latency * 1000)}ms")

    @bridge.bridge_command(aliases=["h"], description="Display help information")
    @bridge.bridge_option(name="command", description="Specific command to get help for", required=False)
    @commands.cooldown(1, 7, commands.BucketType.user)
    async def help(self, ctx, command: str | None = None):
        prefix = getattr(ctx, "clean_prefix", None) or getattr(ctx, "prefix", None) or settings.prefix
        if command:
            cmd = self.bot.get_command(command)
            if not cmd:
                return await ctx.respond(f"{emojis.fail} Invalid command. Run `{prefix}help` for a list of commands", ephemeral=True)
            info = getCommandInfo().get(cmd.name)
            if info:
                return await ctx.respond(view=buildCommandGuide(info, prefix, cmd.aliases))
            cooldown = getattr(getattr(cmd, "_buckets", None), "_cooldown", None)
            cont = createContainer(
                title=f"WRLD - {cmd.name}",
                heading="##",
                color=None,
                description=cmd.description or "No description",
                fields=[
                    ("Usage", f"{prefix}{getattr(cmd, 'usage', None) or cmd.qualified_name}"),
                    ("Cooldown", f"{round(cooldown.per)} second(s)" if cooldown else "None"),
                    ("Aliases", ", ".join(cmd.aliases) if cmd.aliases else "None"),
                ],
            )
            return await ctx.respond(view=createView(cont))
        cont = createContainer(
            title="WRLD",
            heading="##",
            separator=False,
            color=None,
            description=(
                "Welcome to the help menu\n\n"
                "Select a category below to list commands"
            ),
        )
        cont.add_separator(divider=True)
        cont.add_text(f"-# `{prefix}help <command>` for more information on a specific command")
        selectRow = ActionRow(CategorySelect(self.bot, ctx.author.id, prefix))
        await ctx.respond(view=createView(cont, selectRow, viewClass=TimeoutView))

    @bridge.bridge_command(aliases=["invite", "git"], description="Get information about WRLD")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def about(self, ctx):
        elapsed = int(time.time() - self.startedAt)
        cont = createContainer(separator=False, color=None)
        headerText = "## WRLD\nOpen source Discord bot for Juice WRLD music and information, without the spam.\nCompletely free, forever."
        if self.bot.user and self.bot.user.display_avatar:
            cont.add_item(Section(TextDisplay(headerText), accessory=Thumbnail(self.bot.user.display_avatar.url)))
        else:
            cont.add_text(headerText)
        cont.add_separator(divider=True)
        cont.add_text(
            f"### Stats\n"
            f"`{len(self.bot.guilds):,}` servers\n"
            f"`{len(self.bot.users):,}` users\n"
            f"`{len(self.bot.commands)}` commands\n"
            f"`{elapsed // 86400}d {(elapsed % 86400) // 3600}h {(elapsed % 3600) // 60}m` uptime\n\n"
            f"Python `{platform.python_version()}`\nPycord `{discord.__version__}`"
        )
        cont.add_separator(divider=True)
        cont.add_text("-# Made with ❤️ by <@527172619514937354> for juice comm")
        row = ActionRow(
            Button(label="GitHub", url="https://github.com/purrre/WRLD-bot"),
            Button(label="Invite", url="https://discord.com/oauth2/authorize?client_id=1500898771306024961"),
            Button(label="JuiceWRLDAPI", url="https://juicewrldapi.com"),
        )
        await ctx.respond(view=createView(cont, row))


def setup(bot):
    bot.add_cog(HelpCog(bot))