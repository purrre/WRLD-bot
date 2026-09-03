import os
import re
import urllib.parse

import discord
from discord.ext import bridge, commands

from config import emojis, endpoints
from utils.functions import downloadUrl, checkApiHealth, apiHost
from utils.database import db
from utils.components import createContainer, createView
from utils.songs import (
    song_title, build_session_asset_view, build_not_found_view, find_best_session_asset,
    SessionEditSendButton, SessionZipSendButton,
)


class SessionsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def health_check(self, ctx):
        if not await checkApiHealth(endpoints.jwa):
            await ctx.respond(
                f"{emojis.fail} {apiHost(endpoints.jwa)} is down. Please try again later.\n-# If this persists, contact `@purree`",
                ephemeral=True,
            )
            return False
        return True

    @staticmethod
    def get_all_titles(matched_song):
        if not matched_song:
            return None
        main, alts = song_title(matched_song)
        return ([main] + alts if main else alts) or None

    @bridge.bridge_command(aliases=["sessions", "rs", "studiosession"], description="Get the recording session for a track")
    @bridge.bridge_option(name="song", description="Name of the song to search for", required=True)
    async def session(self, ctx, *, song: str):
        if not await self.health_check(ctx):
            return
        async with ctx.typing():
            zip_paths, matched_song = await find_best_session_asset(song, kind="zip")
            edit_paths, _ = await find_best_session_asset(song, kind="edit")
            if not zip_paths and not edit_paths:
                return await ctx.respond(f"{emojis.fail} No session found for **{song}**.", ephemeral=True)
            await db.incrementStat("session_zips_found")
            zip_path = zip_paths[0] if zip_paths else None
            edit_path = edit_paths[0] if edit_paths else None
            buttons = []
            if zip_path:
                buttons.append(discord.ui.Button(label="📁 Session Zip", style=discord.ButtonStyle.link, url=downloadUrl(zip_path, plus=True)))
            if edit_path:
                buttons.append(SessionEditSendButton(edit_path))
                buttons.append(discord.ui.Button(label="\u200b", style=discord.ButtonStyle.link, url=downloadUrl(edit_path)))
            view = build_session_asset_view(
                title=matched_song.get("name", song) if matched_song else song,
                all_titles=self.get_all_titles(matched_song),
                category="Session",
                path=zip_path or edit_path,
                song=matched_song,
                button=buttons[0],
                extra_buttons=buttons[1:] if len(buttons) > 1 else None,
                show_file=False,
            )
            await ctx.respond(view=view)

    @bridge.bridge_command(aliases=["sz"], description="Get a recording session zip for a track")
    @bridge.bridge_option(name="song", description="Name of the song to search for", required=True)
    async def sessionzip(self, ctx, *, song: str):
        if not await self.health_check(ctx):
            return
        async with ctx.typing():
            paths, matched_song = await find_best_session_asset(song, kind="zip")
            if not paths:
                return await ctx.respond(f"{emojis.fail} No session zip found for **{song}**.", ephemeral=True)
            await db.incrementStat("session_zips_found")
            best_path = paths[0]
            file_title = re.sub(r"\s*\(protools\)\s*$", "", os.path.splitext(os.path.basename(best_path))[0], flags=re.IGNORECASE).strip()
            view = build_session_asset_view(
                title=file_title,
                all_titles=self.get_all_titles(matched_song),
                category="Session Zip",
                path=best_path,
                song=matched_song,
                button=discord.ui.Button(label="📁 Session Zip", style=discord.ButtonStyle.link, url=downloadUrl(best_path, plus=True)),
                show_file=False,
            )
            await ctx.respond(view=view)

    @bridge.bridge_command(aliases=["se"], description="Get a session edit file for a track")
    @bridge.bridge_option(name="song", description="Name of the song to search for", required=True)
    async def sessionedit(self, ctx, *, song: str):
        if not await self.health_check(ctx):
            return
        async with ctx.typing():
            paths, matched_song = await find_best_session_asset(song, kind="edit")
            if not paths:
                return await ctx.respond(f"{emojis.fail} No session edit found for **{song}**.", ephemeral=True)
            await db.incrementStat("session_edits_found")
            if len(paths) == 1:
                return await self._respond_session_edit(ctx, paths[0], matched_song)
            await self._respond_session_edit_disambiguation(ctx, song, paths, matched_song)

    @staticmethod
    def _session_edit_view(path, matched_song, all_titles):
        file_title = os.path.splitext(os.path.basename(path))[0]
        return build_session_asset_view(
            title=file_title,
            all_titles=all_titles,
            category="Session Edit",
            path=path,
            song=matched_song,
            button=SessionEditSendButton(path),
            extra_buttons=[discord.ui.Button(label="\u200b", style=discord.ButtonStyle.link, url=downloadUrl(path))],
            show_file=False,
            cover_url=endpoints.cover_art + urllib.parse.quote(str(path)),
        )

    async def _respond_session_edit(self, ctx, path, matched_song):
        view = self._session_edit_view(path, matched_song, self.get_all_titles(matched_song))
        await ctx.respond(view=view)

    async def _respond_session_edit_disambiguation(self, ctx, song, paths, matched_song):
        all_titles = self.get_all_titles(matched_song)
        pseudo_songs = []
        for i, path in enumerate(paths):
            name = os.path.splitext(os.path.basename(path))[0]
            name = re.sub(r"\s*\[Session Edit\]\s*", "", name, flags=re.IGNORECASE).strip()
            pseudo_songs.append({"public_id": str(i), "name": name, "track_titles": [name], "category": "Session Edit"})

        async def select_callback(interaction, pseudo):
            idx = int(pseudo["public_id"])
            view = self._session_edit_view(paths[idx], matched_song, all_titles)
            await interaction.edit_original_response(view=view)

        view = build_not_found_view(song, pseudo_songs, ctx.author.id, select_callback)
        await ctx.respond(view=view)


def setup(bot):
    bot.add_cog(SessionsCog(bot))