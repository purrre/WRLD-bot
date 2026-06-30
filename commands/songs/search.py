import random
import re

import discord
from discord.ext import bridge, commands
from discord import SeparatorSpacingSize, ButtonStyle
from discord.ui import Button, ActionRow

from commands.songs.groupbuy import GroupbuysCog
from config import NOT_YOURS, emojis
from utils.functions import downloadUrl, findClosestMatch, consoleLog
from utils.database import db
from utils.cache import cache
from utils.components import PersistentSongView, createContainer, createSongDropdown, createView
from utils.songs import send_file, fetch_og_buttons, build_song_container, build_metadata_fields, build_notes_button, INVALID_VALS

class SearchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_response_msg(self, ctx, response_obj):
        if response_obj is not None and getattr(response_obj, "message", None) is not None:
            return response_obj.message
        if response_obj is not None and hasattr(response_obj, "edit"):
            return response_obj
        interaction = getattr(ctx, "interaction", None)
        if interaction:
            try:
                return await interaction.original_response()
            except Exception:
                return None
        return None

    async def send_lyrics(self, interaction, song):
        lyrics_cog = self.bot.get_cog("LyricsCog")
        if not lyrics_cog:
            return await interaction.response.send_message(f"{emojis.fail} Lyrics feature is currently unavailable.", ephemeral=True)
        views, error = lyrics_cog.get_lyrics_views(song)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        await db.incrementStat("lyrics_searched")
        await interaction.response.send_message(view=views[0], ephemeral=True)
        for view in views[1:]:
            await interaction.followup.send(view=view, ephemeral=True)

    async def send_groupbuy(self, interaction, song):
        gb_cont = await GroupbuysCog.create_groupbuy_container(song_data=song)
        if not gb_cont:
            return await interaction.response.send_message(f"{emojis.fail} No groupbuy information available for this song.", ephemeral=True)
        await interaction.response.send_message(view=createView(gb_cont, view_class=PersistentSongView), ephemeral=True)

    async def update_og_buttons(self, message_or_interaction, song, user_id, all_results=None):
        try:
            og_buttons = await fetch_og_buttons(song)
            if not og_buttons:
                return
            new_view = self.build_song_view(song, user_id, all_results, og_buttons=og_buttons)
            if isinstance(message_or_interaction, discord.Interaction):
                await message_or_interaction.edit_original_response(view=new_view)
            elif message_or_interaction is not None:
                await message_or_interaction.edit(view=new_view)
        except Exception as e:
            consoleLog("OG", f"error updating og buttons: {e}", type="error")

    async def show_song_and_update_og(self, interaction, song, user_id, all_results):
        await interaction.edit_original_response(view=self.build_song_view(song, user_id, all_results))
        self.bot.loop.create_task(self.update_og_buttons(interaction, song, user_id, all_results))

    def make_select_callback(self, user_id, all_results):
        async def on_select(interaction, song_id):
            if interaction.user.id != user_id:
                return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            await interaction.response.defer()
            selected = next((s for s in all_results if str(s.get("public_id")) == str(song_id)), None)
            if selected:
                await self.show_song_and_update_og(interaction, selected, user_id, all_results)
        return on_select

    def build_song_view(self, song, user_id, all_results=None, og_buttons=None):
        view = createView(view_class=PersistentSongView)
        main_cont = createContainer()
        main_cont.add_item(build_song_container(song))
        notes_btn = build_notes_button(song)
        if notes_btn:
            main_cont.add_item(ActionRow(notes_btn))
        metadata_fields = build_metadata_fields(song)
        if metadata_fields:
            main_cont.add_separator(divider=True, spacing=SeparatorSpacingSize.small)
            for field in metadata_fields:
                main_cont.add_text(field)
        bitrate = str(song.get("bitrate", "")).strip()
        if bitrate and bitrate.lower() not in INVALID_VALS:
            if not metadata_fields:
                main_cont.add_separator(divider=True, spacing=SeparatorSpacingSize.small)
            main_cont.add_text(f"**Bitrate**\n{bitrate}")
        if og_buttons:
            if not metadata_fields and not bitrate:
                main_cont.add_separator(divider=True, spacing=SeparatorSpacingSize.small)
            main_cont.add_item(ActionRow(*og_buttons))
        action_buttons = []
        gb = song.get("groupbuy_info") or {}
        if isinstance(gb, dict) and any(gb.values()):
            action_buttons.append(Button(label="Groupbuy Info", style=ButtonStyle.gray, custom_id=f"gb_{song.get('public_id')}_{user_id}"))
        lyrics = str(song.get("lyrics", "") or "").strip()
        if lyrics and lyrics.lower() not in INVALID_VALS:
            action_buttons.append(Button(label="View Lyrics", style=ButtonStyle.secondary, custom_id=f"lyrics_{song.get('public_id')}_{user_id}"))
        if action_buttons:
            main_cont.add_separator(divider=True, spacing=SeparatorSpacingSize.small)

            async def container_btn_callback(interaction):
                cid = interaction.data.get("custom_id", "")
                if cid.startswith("gb_"):
                    await self.send_groupbuy(interaction, song)
                elif cid.startswith("lyrics_"):
                    await self.send_lyrics(interaction, song)

            for btn in action_buttons:
                btn.callback = container_btn_callback
            main_cont.add_item(ActionRow(*action_buttons))
        view.add_item(main_cont)
        if all_results and len(all_results) > 1:
            dropdown = createSongDropdown(all_results, user_id, placeholder="Choose a song...", callback_func=self.make_select_callback(user_id, all_results))
            view.add_item(ActionRow(dropdown))
        path = song.get("path")
        if path:
            mp3_button = Button(label="MP3", emoji="💿", style=ButtonStyle.gray)

            async def mp3_callback(interaction):
                await interaction.response.defer(ephemeral=True, invisible=False)
                await send_file(interaction, downloadUrl(path), f"{song.get('name', 'Track')}.mp3", "MP3")

            mp3_button.callback = mp3_callback
            view.add_item(ActionRow(mp3_button))
        return view

    @bridge.bridge_command(name="info", aliases=["search", "track", "song"], description="Search for a track")
    @bridge.bridge_option(name="song", description="Name of the song to search for", required=True)
    async def info(self, ctx, *, song: str):
        songs_data = cache.getSongs()
        if not songs_data:
            return await ctx.respond(f"{emojis.fail} Song database is empty or unavailable.")
        async with ctx.typing():
            query = song.lower()
            results = findClosestMatch(song, list_all=True)
            if not results:
                matches = [
                    s for s in songs_data
                    if query in s.get("name", "").lower() or any(query in str(t).lower() for t in s.get("track_titles", []))
                ]
                if not matches:
                    return await ctx.respond(f"{emojis.fail} No results found for `{song}`.")
                results = {"best": matches[0], "all": matches}
            best_match = results["best"]
            all_matches = results["all"]
            await db.incrementStat("track_searches")
            if str(best_match.get("category", "")).lower() == "recording_session":
                non_session = next((s for s in all_matches if str(s.get("category", "")).lower() != "recording_session"), None)
                if non_session:
                    best_match = non_session
            non_session_matches = [m for m in all_matches if str(m.get("category", "")).lower() != "recording_session"]
            single = None
            if len(all_matches) == 1:
                single = best_match
            elif len(non_session_matches) == 1 and len(all_matches) == 2:
                single = non_session_matches[0]
            if single:
                msg = await ctx.respond(view=self.build_song_view(single, ctx.author.id))
                sent_message = await self.get_response_msg(ctx, msg)
                self.bot.loop.create_task(self.update_og_buttons(sent_message, single, ctx.author.id, all_matches))
                return
            multi_cont = createContainer(title="Not Found", description=None, color=None)
            best_titles = best_match.get("track_titles") or [best_match.get("name", "Unknown")]
            main_title = best_titles[0] if best_titles else best_match.get("name", "Unknown")
            alt_titles = best_titles[1:] if len(best_titles) > 1 else []
            suggestion_text = f"I couldn't find one exact match for `{song}`.\nDid you mean **{main_title}**? "
            if alt_titles:
                suggestion_text += f"\n-# ({', '.join(alt_titles[:3])})"
            multi_cont.add_text(suggestion_text)
            confirm_button = Button(label=f"Yes, show {main_title}", style=ButtonStyle.gray, custom_id=f"confirm_{best_match.get('public_id', 0)}_{ctx.author.id}")

            async def confirm_callback(interaction):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
                await interaction.response.defer()
                await self.show_song_and_update_og(interaction, best_match, ctx.author.id, all_matches)

            confirm_button.callback = confirm_callback
            multi_cont.add_item(ActionRow(confirm_button))
            multi_cont.add_text("## OR")
            multi_cont.add_text(f"Select from **{len(all_matches)}** matches below")
            dropdown = createSongDropdown(all_matches, ctx.author.id, placeholder="Choose a song...", callback_func=self.make_select_callback(ctx.author.id, all_matches))
            multi_cont.add_item(ActionRow(dropdown))
            view = createView(multi_cont, view_class=PersistentSongView)
            msg = await ctx.respond(view=view)
            view.message = msg

    @bridge.bridge_command(name="random", aliases=["r", "randomsong"], description="Get a random track (flags: -ns, -nu, -nr)")
    async def random(self, ctx, *, flags: str = ""):
        flag_tokens = {token.lower() for token in flags.split() if token.strip()}
        valid_flags = {"-ns", "-nu", "-nr"}
        invalid_flags = sorted(flag_tokens - valid_flags)
        if invalid_flags:
            return await ctx.respond(
                f"{emojis.fail} Invalid flag(s): {', '.join(invalid_flags)}\n"
                "Valid flags: `-ns` (no recording sessions), `-nu` (no unsurfaced), `-nr` (no released).",
                ephemeral=True,
            )
        async with ctx.typing():
            songs = cache.getSongs()
            if not songs:
                consoleLog("SEARCH", "no content in songs cache", type="error")
                return await ctx.respond(f"{emojis.fail} Failed.")
            def cat(s):
                return re.sub(r"[^a-z0-9]+", "_", str(s.get("category", "")).strip().lower()).strip("_")
            filtered = [s for s in songs if not (
                ("-ns" in flag_tokens and cat(s).startswith("recording_session")) or
                ("-nu" in flag_tokens and cat(s) == "unsurfaced") or
                ("-nr" in flag_tokens and cat(s) == "released")
            )]
            if not filtered:
                return await ctx.respond(f"{emojis.fail} No songs match those filters. Try removing `-ns`, `-nu`, or `-nr`.", ephemeral=True)
            await db.incrementStat("random_songs_found")
            random_song = random.choice(filtered)
            msg = await ctx.respond(view=self.build_song_view(random_song, ctx.author.id))
            self.bot.loop.create_task(self.update_og_buttons(msg, random_song, ctx.author.id, [random_song]))


def setup(bot):
    bot.add_cog(SearchCog(bot))