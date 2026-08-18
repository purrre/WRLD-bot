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
    song_title, build_session_asset_view, find_best_session_asset,
    SessionEditSendButton,
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
    async def session(self, ctx, *, song: str = None):
        cont = createContainer(
            title="Session Commands",
            description=[
                "Use `sessionzip` / `sz` for recording session zip files.",
                "Use `sessionedit` / `se` for session edit files.",
            ],
        )
        await ctx.respond(view=createView(cont), ephemeral=True)

    @bridge.bridge_command(aliases=["sz"], description="Get a recording session zip for a track")
    @bridge.bridge_option(name="song", description="Name of the song to search for", required=True)
    async def sessionzip(self, ctx, *, song: str):
        if not await self.health_check(ctx):
            return
        async with ctx.typing():
            best_path, matched_song = await find_best_session_asset(song, kind="zip")
            if not best_path:
                return await ctx.respond(f"{emojis.fail} No session zip found for **{song}**.", ephemeral=True)
            await db.incrementStat("session_zips_found")
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
            best_path, matched_song = await find_best_session_asset(song, kind="edit")
            if not best_path:
                return await ctx.respond(f"{emojis.fail} No session edit found for **{song}**.", ephemeral=True)
            await db.incrementStat("session_edits_found")
            file_title = os.path.splitext(os.path.basename(best_path))[0]
            view = build_session_asset_view(
                title=file_title,
                all_titles=self.get_all_titles(matched_song),
                category="Session Edit",
                path=best_path,
                song=matched_song,
                button=SessionEditSendButton(best_path),
                extra_buttons=[discord.ui.Button(label="\u200b", style=discord.ButtonStyle.link, url=downloadUrl(best_path))],
                show_file=False,
                cover_url=endpoints.cover_art + urllib.parse.quote(str(best_path)),
            )
            await ctx.respond(view=view)


def setup(bot):
    bot.add_cog(SessionsCog(bot))