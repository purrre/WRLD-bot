import re
import urllib.parse

import discord
from discord.ext import bridge, commands
from discord.ui import MediaGallery, Section, TextDisplay, Thumbnail

from config import emojis, endpoints
from utils.functions import fetchJson, checkApiHealth, apiHost
from utils.database import db
from utils.components import PersistentSongView, createContainer, createSimplePagination
from utils.songs import image_url, match_song_for_cover, song_title, INVALID_VALS

COVER_PAGE_SIZE = 7

def clean_cover_query(query):
    return re.sub(r"[^a-zA-Z0-9\s]", "", str(query)).strip()

async def search_covers(query):
    cleaned = clean_cover_query(query)
    url = f"{endpoints.covers}?search={urllib.parse.quote(cleaned)}"
    data = await fetchJson(url, timeout=10)
    images = data.get("images") if isinstance(data, dict) else []
    if not isinstance(images, list):
        return []
    seen = set()
    result = []
    for image in images:
        if not image:
            continue
        fixed = str(image).strip().replace(" ", "%20")
        key = fixed.lower()
        if key not in seen:
            seen.add(key)
            result.append(fixed)
    return result[:19]


class CoversCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @bridge.bridge_command(name="cover", aliases=["coverart", "ca", "covers"], description="Search and view cover art")
    @bridge.bridge_option(name="query", description="Song or album to search for", required=True)
    async def cover(self, ctx, *, query: str):
        if not await checkApiHealth(endpoints.covers_health):
            return await ctx.respond(f"{emojis.fail} {apiHost(endpoints.covers)} is down. Please try again later.\n-# If this persists, contact `@purree`", ephemeral=True)
        async with ctx.typing():
            cleaned = clean_cover_query(query)
            image_urls = await search_covers(cleaned)
            if not image_urls:
                return await ctx.respond(f"{emojis.fail} No covers found for **{query}**.", ephemeral=True)
            matched_song = match_song_for_cover(cleaned)

            def render(page_urls, page, total_pages, user_id, extra_context):
                song = extra_context.get("matched_song")
                main_title = str(query).strip()
                alt_titles = []
                if song:
                    main_title, alt_titles = song_title(song)
                header_lines = [f"### {main_title}"]
                if alt_titles:
                    header_lines.append(f"-# AKA: {', '.join(alt_titles[:3])}")
                if song:
                    for field, label in (("producers", "Producers"), ("engineers", "Engineers")):
                        val = str(song.get(field, "")).strip()
                        if val and val.lower() not in INVALID_VALS:
                            header_lines.append(f"-# {label}: **{val}**")
                start_idx = page * COVER_PAGE_SIZE
                thumbnail_url = image_url(song) if song else (page_urls[0] if page_urls else None)
                cont = createContainer(separator=False)
                if thumbnail_url:
                    cont.add_item(Section(TextDisplay("\n".join(header_lines)), accessory=Thumbnail(thumbnail_url)))
                else:
                    cont.add_text("\n".join(header_lines))
                cont.add_separator(divider=True)
                cont.add_text(f"-# Covers {start_idx + 1}-{start_idx + len(page_urls)} of {len(image_urls)} | Page {page + 1}/{total_pages}")
                cont.add_item(MediaGallery(*[discord.MediaGalleryItem(url) for url in page_urls]))
                return cont, PersistentSongView

            pagination = createSimplePagination(
                image_urls, COVER_PAGE_SIZE, ctx.author.id, "cover", render,
                view_class=PersistentSongView,
                extra_context={"matched_song": matched_song},
            )
            await db.incrementStat("covers_found", amount=len(image_urls))
            await pagination.show(ctx)


def setup(bot):
    bot.add_cog(CoversCog(bot))
