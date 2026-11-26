import re
import urllib.parse

import discord
from discord.ext import bridge, commands
from discord.ui import MediaGallery, Section, TextDisplay, Thumbnail, Button
from discord import ButtonStyle

from config import emojis, endpoints
from utils.functions import fetchJson, checkApiHealth, apiHost
from utils.database import db
from utils.components import PersistentSongView, createContainer, createSimplePagination

COVER_PAGE_SIZE = 7
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def clean_cover_query(query):
    return re.sub(r"[^a-zA-Z0-9\s]", "", str(query)).strip()


def cover_url_from_path(path):
    return f"{endpoints.download}{urllib.parse.quote(str(path))}"


async def _fetch_browse(url, timeout=10):
    try:
        data = await fetchJson(url, timeout=timeout)
        if not isinstance(data, dict):
            return []
        items = data.get("items", []) or []
        return [i for i in items if i.get("type") == "file" and str(i.get("extension", "")).lower() in IMAGE_EXTS and i.get("path")]
    except Exception:
        return []


async def search_covers(query):
    cleaned = clean_cover_query(query)
    encoded = urllib.parse.quote(cleaned)
    main_items = await _fetch_browse(f"{endpoints.covers}{encoded}")
    main_urls = [cover_url_from_path(i["path"]) for i in main_items]
    if len(main_urls) >= COVER_PAGE_SIZE:
        return main_urls
    community_items = await _fetch_browse(f"{endpoints.covers_community}{encoded}")
    community_urls = [cover_url_from_path(i["path"]) for i in community_items]
    return (main_urls + community_urls)[:50]


class CoversCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @bridge.bridge_command(name="cover", aliases=["coverart", "ca", "covers"], description="Search and view cover art")
    @bridge.bridge_option(name="query", description="Song or album to search for", required=True)
    @commands.cooldown(1, 8, commands.BucketType.user)
    async def cover(self, ctx, *, query: str):
        await ctx.defer()
        if not await checkApiHealth(endpoints.covers_health):
            return await ctx.respond(f"{emojis.fail} juicewrldapi.com is down. Please try again later.\n-# If this persists, contact the juicewrldapi team", ephemeral=True)
        cleaned = clean_cover_query(query)
        image_urls = await search_covers(cleaned)
        if not image_urls:
            return await ctx.respond(f"{emojis.fail} No covers found for **{query}**.", ephemeral=True)

        def render(page_urls, page, total_pages, user_id, extra_context):
            main_title = str(query).strip()
            header_lines = [f"Results for: **{main_title}**"]
            start_idx = page * COVER_PAGE_SIZE
            thumbnail_url = page_urls[0] if page_urls else None
            cont = createContainer(separator=False)
            if thumbnail_url:
                cont.add_item(Section(TextDisplay("\n".join(header_lines)), accessory=Thumbnail(thumbnail_url)))
            else:
                cont.add_text("\n".join(header_lines))
            cont.add_separator(divider=True)
            cont.add_text(f"-# Covers {start_idx + 1}-{start_idx + len(page_urls)} of {len(image_urls)} | Page {page + 1}/{total_pages}")
            cont.add_item(MediaGallery(*[discord.MediaGalleryItem(url) for url in page_urls]))
            return cont, PersistentSongView

        def middle_button(pag, page):
            submit_btn = Button(label="Submit Covers", style=ButtonStyle.success, custom_id=f"cover_submit_{page}_{pag.userId}")
            async def submit_cb(inter):
                if inter.user.id != pag.userId:
                    return await inter.response.send_message("not yours", ephemeral=True)
                await inter.response.send_message("Cover submission is coming soon!", ephemeral=True)
            submit_btn.callback = submit_cb
            return submit_btn

        pagination = createSimplePagination(
            image_urls, COVER_PAGE_SIZE, ctx.author.id, "cover", render,
            viewClass=PersistentSongView,
            middleButtonFunc=middle_button,
        )
        await db.incrementStat("covers_found", amount=len(image_urls))
        await pagination.show(ctx)


def setup(bot):
    bot.add_cog(CoversCog(bot))
