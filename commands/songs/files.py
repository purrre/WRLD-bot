import asyncio
import os
import urllib.parse

from discord.ext import bridge, commands

from config import emojis, endpoints
from utils.functions import fetchJson, checkApiHealth, apiHost
from utils.database import db
from utils.components import createContainer, createView
from utils.songs import (
    song_matches, build_song_view, build_not_found_view, fetch_og_buttons, not_found_text,
    parse_instrumentals, fetch_instrumental_urls, fetch_instrumental_urls_by_name, send_file,
)


class FilesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def has_content(song, mode):
        """Cheap sync check: does this song plausibly have content for this mode?"""
        if mode == "leak":
            return bool(song.get("path"))
        if mode == "instrumental":
            return bool(parse_instrumentals(song))
        if mode == "ogfile":
            # ponytail: file_names presence is a cheap proxy — fetch_og_files returns [] without it
            return bool(song.get("file_names") and str(song.get("file_names")).strip().lower() not in ("none", "n/a", "", "null"))
        return True  # snippets: no cheap check, keep all

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
            # filter to matches that actually have content for this mode
            available = [m for m in matches if self.has_content(m, mode)]
            if not available:
                return await ctx.respond(not_found_text(mode, query), ephemeral=True)
            if mode == "leak":
                await db.incrementStat("leaks_found")

            async def show_song(interaction, song):
                og_buttons = await fetch_og_buttons(song) if mode == "ogfile" else None
                if mode == "ogfile" and not og_buttons:
                    return await interaction.followup.send(f"{emojis.fail} No original files found for **{song.get('name', query)}**.", ephemeral=True)
                view = await build_song_view(song, ctx.author.id, mode, available, og_buttons)
                await interaction.edit_original_response(view=view)

            if len(available) == 1:
                og_buttons = await fetch_og_buttons(available[0]) if mode == "ogfile" else None
                if mode == "ogfile" and not og_buttons:
                    return await ctx.respond(f"{emojis.fail} No original files found for **{available[0].get('name', query)}**.", ephemeral=True)
                view = await build_song_view(available[0], ctx.author.id, mode, available, og_buttons)
                return await ctx.respond(view=view)
            view = build_not_found_view(query, available, ctx.author.id, show_song)
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

    @bridge.bridge_command(aliases=["inst"], description="Get the instrumental for a track or by beat name")
    @bridge.bridge_option(name="query", description="Song name or instrumental/beat name", required=True)
    async def instrumental(self, ctx, *, query: str):
        if not await checkApiHealth(endpoints.jwa):
            return await ctx.respond(
                f"{emojis.fail} {apiHost(endpoints.jwa)} is down. Please try again later.\n-# If this persists, contact `@juicewrldapi`",
                ephemeral=True,
            )
        async with ctx.typing():
            matches = song_matches(query, "leak")
            available = [m for m in matches if parse_instrumentals(m)]
            if available:
                async def show_inst(interaction, song):
                    view = await build_song_view(song, ctx.author.id, "instrumental", available)
                    await interaction.edit_original_response(view=view)
                if len(available) == 1:
                    view = await build_song_view(available[0], ctx.author.id, "instrumental", available)
                    return await ctx.respond(view=view)
                view = build_not_found_view(query, available, ctx.author.id, show_inst)
                return await ctx.respond(view=view)
            urls, found_ext = await fetch_instrumental_urls_by_name(query)
            if urls:
                from discord import ButtonStyle
                from discord.ui import Button, ActionRow
                from utils.components import PersistentSongView, createContainer, createView
                filename = os.path.basename(urllib.parse.unquote(urls[0]))
                cont = createContainer(title="Instrumental", description=f"### {filename}\n-# Found by beat name")
                inst_btn = Button(emoji="🎹", label="Instrumental", style=ButtonStyle.gray)

                async def inst_callback(interaction):
                    await interaction.response.defer(ephemeral=True, invisible=False)
                    ext = found_ext or ".mp3"
                    kind = "MP3" if ext == ".mp3" else "WAV"
                    await send_file(interaction, urls[0], filename, kind)

                inst_btn.callback = inst_callback
                link_btn = Button(label="\u200b", style=ButtonStyle.link, url=urls[0])
                cont.add_separator(divider=True)
                cont.add_item(ActionRow(inst_btn, link_btn))
                view = createView(cont, viewClass=PersistentSongView)
                return await ctx.respond(view=view)
            await ctx.respond(not_found_text("instrumental", query), ephemeral=True)

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
