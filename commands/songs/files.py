from discord.ext import bridge, commands

from config import emojis, endpoints
from utils.functions import fetchJson, checkApiHealth, apiHost
from utils.database import db
from utils.components import createContainer, createView
from utils.songs import song_matches, build_song_view, fetch_og_buttons, not_found_text


class FilesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_song_result(self, ctx, query, mode):
        if not await checkApiHealth(endpoints.jwa):
            return await ctx.respond(
                f"{emojis.fail} {apiHost(endpoints.jwa)} is down. Please try again later.\n-# If this persists, contact `@juicewrldapi`",
                ephemeral=True,
            )
        async with ctx.typing():
            matches = song_matches(query, mode)
            if not matches:
                return await ctx.respond(not_found_text(mode, query), ephemeral=True)
            if mode == "leak":
                await db.incrementStat("leaks_found")
            og_buttons = await fetch_og_buttons(matches[0]) if mode == "ogfile" else None
            if mode == "ogfile" and not og_buttons:
                return await ctx.respond(f"{emojis.fail} No original files found for **{matches[0].get('name', query)}**.", ephemeral=True)
            view = await build_song_view(matches[0], ctx.author.id, mode, matches, og_buttons)
            await ctx.respond(view=view)

    @bridge.bridge_command(description="Get the audio file for a track")
    @bridge.bridge_option(name="song", description="Name of the song to search for", required=True)
    async def leak(self, ctx, *, song: str):
        await self.send_song_result(ctx, song, "leak")

    @bridge.bridge_command(aliases=["snippet", "snip"], description="Get snippet files for a track")
    @bridge.bridge_option(name="song", description="Name of the song to search for", required=True)
    async def snippets(self, ctx, *, song: str):
        await self.send_song_result(ctx, song, "snippets")

    @bridge.bridge_command(aliases=["og", "ogf"], description="Get links to the original file for a track")
    @bridge.bridge_option(name="song", description="Name of the song to search for", required=True)
    async def ogfile(self, ctx, *, song: str):
        await self.send_song_result(ctx, song, "ogfile")

    @bridge.bridge_command(description="Get a link to the compilation files")
    async def comp(self, ctx):
        if not await checkApiHealth(endpoints.media_status):
            return await ctx.respond(
                f"{emojis.fail} {apiHost(endpoints.media)} is down. Please try again later.\n-# If this persists, contact `@juicewrldapi`",
                ephemeral=True,
            )
        await ctx.defer()
        data = await fetchJson(endpoints.media_status, timeout=10)
        link_line = f"[Click here to visit the comp]({endpoints.files})"
        if isinstance(data, dict):
            total_files = data.get("total_files", "N/A")
            total_size_mb = data.get("total_size_mb", "N/A")
            size_display = f"{total_size_mb / 1024:.2f} GB" if isinstance(total_size_mb, (int, float)) else str(total_size_mb)
            try:
                files_display = f"{total_files:,}"
            except (ValueError, TypeError):
                files_display = str(total_files)
            description = f"**Total Files:** {files_display}\n**Total Size:** {size_display}\n\n{link_line}"
        else:
            description = link_line
        cont = createContainer(title="Juice WRLD Comp", description=description)
        await ctx.respond(view=createView(cont))


def setup(bot):
    bot.add_cog(FilesCog(bot))
