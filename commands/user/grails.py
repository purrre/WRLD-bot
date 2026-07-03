import difflib
import re

import discord
from discord.ext import commands, bridge
from discord import ButtonStyle
from discord.ui import ActionRow, Button

from config import emojis, NOT_YOURS
from utils.database import db
from utils.components import createContainer, createView, createSimplePagination

class GrailCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @bridge.bridge_group(name="grail", invoke_without_command=True, aliases=["grails"])
    async def grail(self, ctx):
        if ctx.invoked_with == "grails":
            return await self.show_grails(ctx, None)
        raise commands.BadArgument("Please provide a grail.")

    @grail.command(name="list")
    @bridge.bridge_option(name="user", description="User to view grails for", required=False)
    async def list_grails(self, ctx, user: discord.User = None):
        await self.show_grails(ctx, user)

    async def show_grails(self, ctx, user):
        target = user or ctx.author
        await ctx.defer()
        grails = await db.getGrails(target.id)
        if not grails:
            desc = "You have no grails saved yet.\nAdd one with `grail add <track>`" if target.id == ctx.author.id else f"{target.name} has no grails saved yet."
            return await ctx.respond(view=createView(createContainer(title=f"{emojis.list} {target.display_name}'s Grail List", description=desc, heading="##")))

        def render(page_items, page, total_pages, user_id, extra_context):
            body = "\n".join(f"{page * 15 + i + 1}. {name}" for i, name in enumerate(page_items))
            cont = createContainer(
                title=f"{emojis.list} {target.display_name}'s Grail List\n-# Page {page + 1}/{total_pages} | {len(grails)} total",
                description=body,
                heading="##",
            )
            return cont, None

        pagination = createSimplePagination(items=grails, itemsPerPage=15, userId=ctx.author.id, commandType="grail", renderPageFunc=render)
        await pagination.show(ctx, 0)

    @grail.command(name="add")
    @bridge.bridge_option(name="grail", description="Name of the grail to add", required=True)
    async def add(self, ctx, *, grail: str):
        if len(grail) > 70 or re.search(r"(https?://|www\.)\S+", grail, re.IGNORECASE):
            cont = createContainer(title="Error", description=f"{emojis.fail} Grail string is too long or contains banned characters.")
            return await ctx.respond(view=createView(cont), ephemeral=True)
        await db.addGrail(ctx.author.id, grail)
        cont = createContainer(title=f"{emojis.plus} Grail Added", description=f"Added `{grail}` to your grail list.")
        await ctx.respond(view=createView(cont), ephemeral=True)

    @grail.command(name="clear")
    async def clear(self, ctx):
        user_grails = await db.getGrails(ctx.author.id)
        if not user_grails:
            cont = createContainer(title="Nothing to Clear", description="You don't have any grails saved.", heading="##")
            return await ctx.respond(view=createView(cont), ephemeral=True)
        cont = createContainer(title="Confirm Clear", description=f"Are you sure you want to remove all **{len(user_grails)}** grails from your list?", heading="##")
        row = ActionRow()
        confirm_btn = Button(label="Confirm", style=ButtonStyle.red)
        cancel_btn = Button(label="Cancel", style=ButtonStyle.gray)

        async def confirm_callback(interaction):
            if interaction.user.id != ctx.author.id:
                return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            await db.clearGrails(ctx.author.id)
            new_cont = createContainer(title=f"{emojis.minus} Grails Cleared", description="All grails have been removed from your list.")
            await interaction.response.edit_message(view=createView(new_cont))

        async def cancel_callback(interaction):
            if interaction.user.id != ctx.author.id:
                return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            new_cont = createContainer(title="Cancelled", description="Your grail list was not changed.", heading="##")
            await interaction.response.edit_message(view=createView(new_cont))

        confirm_btn.callback = confirm_callback
        cancel_btn.callback = cancel_callback
        row.add_item(confirm_btn)
        row.add_item(cancel_btn)
        await ctx.respond(view=createView(cont, row), ephemeral=True)

    @grail.command(name="remove", aliases=["rm", "delete"])
    @bridge.bridge_option(name="grail", description="Name of the grail to remove", required=True)
    async def remove(self, ctx, *, grail: str):
        user_grails = await db.getGrails(ctx.author.id)
        if not user_grails:
            cont = createContainer(title="Not Found", description="You don't have any grails to remove.\nAdd one with `grail add <track>`", heading="##")
            return await ctx.respond(view=createView(cont), ephemeral=True)
        match = next((g for g in user_grails if g.lower() == grail.lower()), None)
        if match:
            await db.removeGrail(ctx.author.id, match)
            cont = createContainer(title=f"{emojis.minus} Grail Removed", description=f"Removed `{match}` from your grail list.")
            return await ctx.respond(view=createView(cont), ephemeral=True)
        matches = difflib.get_close_matches(grail.lower(), [g.lower() for g in user_grails], n=1, cutoff=0.4)
        suggestion = next((g for g in user_grails if g.lower() == matches[0]), None) if matches else None
        if suggestion:
            cont = createContainer(title="Not Found", description=f"I couldn't find an exact match for `{grail}`.\nDid you mean **{suggestion}**?", heading="##")
            row = ActionRow()
            confirm_btn = Button(label=f"Confirm: {suggestion}", style=ButtonStyle.green)

            async def confirm_callback(interaction):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
                await db.removeGrail(ctx.author.id, suggestion)
                new_cont = createContainer(title=f"{emojis.minus} Grail Removed", description=f"Removed `{suggestion}` from your grail list.")
                await interaction.response.edit_message(view=createView(new_cont))

            confirm_btn.callback = confirm_callback
            row.add_item(confirm_btn)
            return await ctx.respond(view=createView(cont, row), ephemeral=True)
        cont = createContainer(title="Not Found", description=f"No grails found matching `{grail}`.", heading="##")
        await ctx.respond(view=createView(cont), ephemeral=True)


def setup(bot):
    bot.add_cog(GrailCog(bot))
