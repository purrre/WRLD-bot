from datetime import datetime

import discord
from discord import ButtonStyle, SeparatorSpacingSize, Color
from discord.ui import Section, TextDisplay, Button, ActionRow, Thumbnail, Container, DesignerView, Select, button

from config import NOT_YOURS, colors
from utils.functions import getEmojiMap

notesLengthThreshold = len("Song was originally untitled.") * 2
emojiMap = getEmojiMap()


def getEraEmoji(song):
    era_field = song.get("era")
    if not era_field:
        return None
    era_key = era_field.get("name") if isinstance(era_field, dict) else str(era_field)
    if not era_key:
        return None
    return emojiMap.get(era_key.strip()) or None

class TimeoutView(DesignerView):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("timeout", 60)
        super().__init__(*args, **kwargs)
        self.message = None

    async def on_timeout(self):
        if self.message:
            try:
                for item in self.children:
                    if hasattr(item, "disabled"):
                        item.disabled = True
                await self.message.edit(view=self)
            except Exception:
                pass


class PersistentSongView(DesignerView):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("timeout", 1800)
        super().__init__(*args, **kwargs)
        self.message = None

    async def on_timeout(self):
        pass


class DropdownView(DesignerView):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("timeout", 90)
        super().__init__(*args, **kwargs)

    async def on_timeout(self):
        try:
            if getattr(self, "message", None):
                await self.message.edit(view=None)
        except Exception:
            pass


class DeleteRow(ActionRow):
    @button(label="Delete", style=discord.ButtonStyle.red, id=999)
    async def deleteCallback(self, button, interaction):
        await interaction.message.delete()


class ErrorView(TimeoutView):
    def __init__(self, errorMsg):
        super().__init__(timeout=60)
        section = Section(TextDisplay(f"### ❌ API Error\n{errorMsg}"), color=Color.red())
        self.add_item(Container(section))
        self.add_item(DeleteRow())


async def loading(method, x=None):
    return discord.Embed(description=f"Getting {method} information.. {x or ''}", color=colors.main)


def getErrorEmbed(title, description, status=None):
    embed = discord.Embed(title=f"❌ {title}", description=description, color=Color.red(), timestamp=datetime.now())
    if status:
        embed.set_footer(text=f"HTTP Status: {status}")
    return embed


def createContainer(*, title=None, description=None, color=None, fields=None, heading="###", separator=True, spacing=SeparatorSpacingSize.small):
    container = Container(color=color)
    hasHeader = bool(title)
    hasBody = description is not None or bool(fields)
    if title:
        container.add_text(f"{heading} {title}")
    if separator and hasHeader and hasBody:
        container.add_separator(divider=True, spacing=spacing)
    if description is not None:
        items = description if isinstance(description, (list, tuple)) else [description]
        for item in items:
            if item is not None:
                container.add_text(str(item))
    for field in fields or []:
        if isinstance(field, dict):
            name, value = field.get("name"), field.get("value")
        else:
            name, value = field
        if value is None:
            continue
        valueText = str(value).strip()
        if not valueText:
            continue
        nameText = str(name).strip() if name is not None else ""
        container.add_text(f"**{nameText}**\n{valueText}" if nameText else valueText)
    return container


def createView(*items, viewClass=DesignerView, **view_kwargs):
    view = viewClass(**view_kwargs)
    for item in items:
        if item is not None:
            view.add_item(item)
    return view


def createSongDropdown(songs, userId, placeholder, callbackFunc, formatType="song"):
    options = []
    seen = set()
    for song in songs[:50]:
        public_id = song.get("public_id")
        if public_id in seen:
            continue
        seen.add(public_id)
        category = song.get("category", "")
        if formatType == "gb":
            mainTitle = song.get("title", song.get("name", "Unknown"))
            alts = song.get("alternates", [])
            sub = song.get("project", "N/A")
            if isinstance(sub, dict):
                sub = sub.get("name", "N/A")
        else:
            titles = song.get("track_titles") or [song.get("name", "Unknown")]
            mainTitle = titles[0] if titles else song.get("name", "Unknown")
            alts = list(titles[1:]) if len(titles) > 1 else []
            sub = (category or "N/A").replace("_", " ").title()
        label = str(mainTitle)[:100]
        altsStr = ", ".join(str(a) for a in alts[:3])
        if len(alts) > 3:
            altsStr += f", +{len(alts) - 3}"
        desc = f"{altsStr} | {sub}" if altsStr else sub
        if len(desc) > 100:
            desc = desc[:97] + "..."
        eraEmoji = getEraEmoji(song) if formatType != "gb" else None
        options.append(discord.SelectOption(label=label, description=desc, value=str(public_id), emoji=eraEmoji))
        if len(options) >= 25:
            break
    dropdown = Select(placeholder=placeholder, options=options)

    async def defaultCallback(interaction):
        if interaction.user.id != userId:
            return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
        if callbackFunc:
            await callbackFunc(interaction, dropdown.values[0])

    dropdown.callback = defaultCallback
    return dropdown


class PaginatedView:
    def __init__(self, items, itemsPerPage, userId, commandType, renderPageFunc, viewClass=None, extraContext=None, extraItemsFunc=None):
        self.items = items
        self.itemsPerPage = itemsPerPage
        self.userId = userId
        self.commandType = commandType
        self.renderPageFunc = renderPageFunc
        self.viewClass = viewClass or DesignerView
        self.extraContext = extraContext or {}
        self.extraItemsFunc = extraItemsFunc
        self.totalPages = max(1, (len(items) + itemsPerPage - 1) // itemsPerPage)

    def getPageItems(self, page):
        start = page * self.itemsPerPage
        return self.items[start:start + self.itemsPerPage]

    def createView(self, page):
        pageItems = self.getPageItems(page)
        container, viewClassOverride = self.renderPageFunc(pageItems, page, self.totalPages, self.userId, self.extraContext)
        view = createView(container, viewClass=viewClassOverride or self.viewClass)
        prevBtn = Button(label="Previous", style=ButtonStyle.gray, custom_id=f"{self.commandType}_prev_{page}_{self.userId}", disabled=page <= 0)
        nextBtn = Button(label="Next", style=ButtonStyle.gray, custom_id=f"{self.commandType}_next_{page}_{self.userId}", disabled=page >= self.totalPages - 1)
        buttons = [prevBtn, nextBtn]
        view.add_item(ActionRow(*buttons))
        if self.extraItemsFunc:
            for item in self.extraItemsFunc(self, page):
                view.add_item(item)
        return view, buttons

    def attachCallbacks(self, buttons, interactionOrCtx):
        for btn in buttons:
            async def btnCallback(inter, btn=btn):
                if inter.user.id != self.userId:
                    return await inter.response.send_message(NOT_YOURS, ephemeral=True)
                parts = btn.custom_id.split("_")
                direction = parts[1]
                currentPage = int(parts[2])
                newPage = currentPage - 1 if direction == "prev" else currentPage + 1
                newView, newButtons = self.createView(newPage)
                self.attachCallbacks(newButtons, inter)
                await inter.response.edit_message(view=newView)
            btn.callback = btnCallback

    async def show(self, interactionOrCtx, page=0):
        view, buttons = self.createView(page)
        self.attachCallbacks(buttons, interactionOrCtx)
        if isinstance(interactionOrCtx, discord.Interaction):
            await interactionOrCtx.response.edit_message(view=view)
        else:
            await interactionOrCtx.respond(view=view)


def createSimplePagination(items, itemsPerPage, userId, commandType, renderPageFunc, viewClass=None, extraContext=None, extraItemsFunc=None):
    return PaginatedView(items, itemsPerPage, userId, commandType, renderPageFunc, viewClass, extraContext, extraItemsFunc)


def buildCommandGuide(info, prefix, aliases):
    guide = createContainer(title=info["title"], description=f"-# {info['description']}", heading="##")
    usageLines = "\n".join(f"- {prefix}{line}" for line in info["usage"].split("\n"))
    guide.add_item(TextDisplay(f"### Commands\n{usageLines}"))
    footer = f"-# Aliases: {', '.join(aliases)}" if aliases else ""
    exampleLines = "\n".join(f"`{prefix}{line}`" for line in info["example"].split("\n"))
    guide.add_item(TextDisplay(f"### Examples\n{exampleLines}\n\n{footer}"))
    return createView(guide)
