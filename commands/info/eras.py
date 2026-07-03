import re
from datetime import datetime

from discord.ext import bridge, commands

from utils.cache import cache
from utils.components import PersistentSongView, createContainer, createSimplePagination
from utils.functions import getEmojiMap, getEraMap

ALBUM_EMOJIS = {full: getEmojiMap().get(code, "") for code, full in getEraMap().items()}

class ErasCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @bridge.bridge_command(aliases=["era", "eraslist"], description="List all eras in order")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def eras(self, ctx):
        def date_key(value):
            text = (value or "").strip().strip("(")
            start = re.split(r"[-–]", text, maxsplit=1)[0].strip()
            for fmt in ("%B %Y", "%b %Y", "%Y-%m-%d", "%Y-%m", "%Y"):
                try:
                    dt = datetime.strptime(start, fmt)
                    return f"{dt.year:04d}-{dt.month:02d}-{getattr(dt, 'day', 1):02d}"
                except ValueError:
                    continue
            match = re.search(r"(\d{4})", start)
            return f"{int(match.group(1)):04d}-12-31" if match else "9999-12-31"

        eras = [era for era in (cache.getEras() or []) if era.get("time_frame")]
        eras.sort(key=lambda item: date_key(item.get("time_frame")))

        def render(page_items, page, total_pages, user_id, extra_context):
            start_idx = page * 8
            lines = [f"{start_idx + i}. **{era.get('name', 'Unknown')}**\n-# {era.get('description', 'No description')} | {era.get('time_frame', 'N/A')}" for i, era in enumerate(page_items, 1)]
            return createContainer(title=f"🌌 Eras\n-# Page {page + 1}/{total_pages}", description="\n\n".join(lines)), None

        pagination = createSimplePagination(
            items=eras, itemsPerPage=8, userId=ctx.author.id,
            commandType="eras", renderPageFunc=render, viewClass=PersistentSongView,
        )
        await pagination.show(ctx, 0)

    @bridge.bridge_command(aliases=["album"], description="List all albums in order")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def albums(self, ctx):
        albums = cache.getAlbums() or []
        albums.sort(key=lambda item: item.get("release_date") or "9999-99-99")

        def render(page_items, page, total_pages, user_id, extra_context):
            start_idx = page * 5
            lines = [f"{start_idx + i}. {ALBUM_EMOJIS.get(album.get('name', album.get('title', '')), '')} **{album.get('name', album.get('title', 'Unknown'))}**\n-# {album.get('description', 'No description')} | Released: {album.get('release_date', 'N/A')}" for i, album in enumerate(page_items, 1)]
            return createContainer(title=f"💿 Albums\n-# Page {page + 1}/{total_pages}", description="\n\n".join(lines)), None

        pagination = createSimplePagination(
            items=albums, itemsPerPage=5, userId=ctx.author.id,
            commandType="albums", renderPageFunc=render, viewClass=PersistentSongView,
        )
        await pagination.show(ctx, 0)


def setup(bot):
    bot.add_cog(ErasCog(bot))
