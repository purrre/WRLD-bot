import re
from functools import lru_cache

import discord
from dateutil import parser as date_parser
from discord import ButtonStyle
from discord.ext import bridge, commands
from discord.ui import ActionRow, Button, Select

from config import emojis, NOT_YOURS
from utils.cache import cache
from utils.components import PersistentSongView, createContainer, createView, createSimplePagination
from utils.functions import getEraMap, getEmojiMap
from utils.songs import format_song_line

ERA_MAP = getEraMap()
EMOJI_MAP = getEmojiMap()
items = 8

# thank u claude i hate regex 💖
@lru_cache(maxsize=4096)
def parse_date_clean(text, drop_prefix):
    if not text:
        return None
    value = re.compile(r"^(Recorded|Leaked|Surfaced|Previewed)\s*\n?\s*", re.IGNORECASE).sub("", text) if drop_prefix else text
    year_match = re.compile(r"\b(19|20)\d{2}\b").search(value)
    year = year_match.group(0) if year_match else None
    value = re.compile(r"(\d{1,2})-\d{1,2}").sub(r"\1", value)
    try:
        parsed = date_parser.parse(value, fuzzy=True)
        if year and str(parsed.year) != year:
            parsed = parsed.replace(year=int(year))
        return parsed.strftime("%Y-%m-%d")
    except (ValueError, TypeError, date_parser.ParserError):
        return None

def extract_date(text):
    return parse_date_clean(str(text).strip(), drop_prefix=True) if text else None

class MetadataCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.date_index_source_key = None
        self.date_indexes = {"date_leaked": {}, "record_dates": {}, "preview_date": {}, "date_leaked_sorted": []}

    @staticmethod
    def render_page(matched, title):
        def render(page_items, page, total_pages, user_id, extra_context):
            start = page * items
            lines = [format_song_line(s, start + i + 1) for i, s in enumerate(page_items)]
            return createContainer(title=f"{title}\n-# Page {page + 1}/{total_pages} | {len(matched)} total", description="\n".join(lines)), None
        return render

    def build_date_indexes(self, songs):
        source_key = (id(songs), len(songs))
        if source_key == self.date_index_source_key:
            return
        leaked_index, recorded_index, preview_index, leaked_sorted = {}, {}, {}, []
        for song in songs:
            leak_date = extract_date(str(song.get("date_leaked", "") or ""))
            if leak_date:
                leaked_index.setdefault(leak_date, []).append(song)
                leaked_sorted.append((song, leak_date))
            record_date = extract_date(str(song.get("record_dates", "") or ""))
            if record_date:
                recorded_index.setdefault(record_date, []).append(song)
            preview_date = extract_date(str(song.get("preview_date", "") or ""))
            if preview_date:
                preview_index.setdefault(preview_date, []).append(song)
        leaked_sorted.sort(key=lambda x: x[1], reverse=True)
        self.date_indexes = {
            "date_leaked": leaked_index,
            "record_dates": recorded_index,
            "preview_date": preview_index,
            "date_leaked_sorted": leaked_sorted,
        }
        self.date_index_source_key = source_key

    async def date_command(self, ctx, date, *, index_key, title_with_date, title_all=None, require_date=True):
        songs = cache.getSongs()
        if not songs:
            return await ctx.respond(f"{emojis.fail} Song database unavailable.", ephemeral=True)
        async with ctx.typing():
            self.build_date_indexes(songs)
            if date:
                parsed = parse_date_clean(str(date).strip(), drop_prefix=False) if date else None
                if not parsed:
                    return await ctx.respond(f"{emojis.fail} Invalid date format. Try: YYYY-MM-DD, MM/DD/YYYY, or Month DD, YYYY", ephemeral=True)
                matched = list(self.date_indexes[index_key].get(parsed, []))
                if not matched:
                    return await ctx.respond(f"{emojis.fail} {title_with_date(parsed, found=False)}", ephemeral=True)
                title = title_with_date(parsed, found=True)
            elif not require_date:
                dated = self.date_indexes["date_leaked_sorted"]
                if not dated:
                    return await ctx.respond(f"{emojis.fail} No songs with surface dates found.", ephemeral=True)
                matched = [s[0] for s in dated]
                title = title_all
            else:
                return await ctx.respond(f"{emojis.fail} Invalid date format.", ephemeral=True)

            await createSimplePagination(
                items=matched, items_per_page=items, user_id=ctx.author.id,
                command_type=index_key, render_page_func=self.render_page(matched, title), view_class=PersistentSongView,
            ).show(ctx, 0)

    @bridge.bridge_command(name="surfaced", aliases=["leaked"], description="View songs by surface/leak date")
    @bridge.bridge_option(name="date", description="Date to search for", required=False)
    async def surfaced(self, ctx, *, date: str = None):
        await self.date_command(
            ctx, date, index_key="date_leaked",
            title_with_date=lambda d, found: f"Songs Surfaced on {d}" if found else f"No songs found surfaced on {d}.",
            title_all="Songs by Surface Date (Recent First)", require_date=False,
        )

    @bridge.bridge_command(name="recorded", description="View songs recorded on a specific date")
    @bridge.bridge_option(name="date", description="Date to search for (ex. Nov 16 2018)", required=True)
    async def recorded(self, ctx, *, date: str):
        await self.date_command(
            ctx, date, index_key="record_dates",
            title_with_date=lambda d, found: f"Songs Recorded on {d}" if found else f"No songs found recorded on {d}.",
        )

    @bridge.bridge_command(aliases=["prev"], name="previewed", description="View songs first previewed on a specific date")
    @bridge.bridge_option(name="date", description="Date to search for (ex. Nov 16 2018)", required=True)
    async def previewed(self, ctx, *, date: str):
        await self.date_command(
            ctx, date, index_key="preview_date",
            title_with_date=lambda d, found: f"Songs First Previewed on {d}" if found else f"No songs found previewed on {d}.",
        )

    async def credit_search(self, ctx, query, *, field, command_type, not_found, title_prefix):
        songs = cache.getSongs()
        if not songs:
            return await ctx.respond(f"{emojis.fail} Song database unavailable.", ephemeral=True)
        async with ctx.typing():
            q = query.lower()
            matched, actual_name = [], None
            for song in songs:
                for part in str(song.get(field, "")).split(","):
                    part = part.strip()
                    if q in part.lower():
                        matched.append(song)
                        if not actual_name:
                            for person in part.split("&"):
                                person = person.strip()
                                if q in person.lower():
                                    actual_name = person
                                    break
                        break
            if not matched:
                return await ctx.respond(f"{emojis.fail} {not_found}", ephemeral=True)
            title = f"{title_prefix} {actual_name or query}"

            await createSimplePagination(
                items=matched, items_per_page=items, user_id=ctx.author.id,
                command_type=command_type, render_page_func=self.render_page(matched, title), view_class=PersistentSongView,
            ).show(ctx, 0)

    @bridge.bridge_command(name="producer", aliases=["prod"], description="Search songs by producer")
    @bridge.bridge_option(name="producer", description="Name of the producer to search for", required=True)
    async def producer(self, ctx, *, producer: str):
        await self.credit_search(ctx, producer, field="producers", command_type="producer",
                                  not_found=f"No songs found produced by `{producer}`.", title_prefix="Songs Produced by")

    @bridge.bridge_command(name="location", aliases=["loc"], description="Search songs by recording location")
    @bridge.bridge_option(name="location", description="Name of the recording location", required=True)
    async def location(self, ctx, *, location: str):
        await self.credit_search(ctx, location, field="recording_locations", command_type="location",
                                  not_found=f"No songs found recorded at `{location}`.", title_prefix="Songs Recorded at")

    @bridge.bridge_command(name="engineer", aliases=["eng"], description="Search songs by engineer")
    @bridge.bridge_option(name="engineer", description="Name of the engineer to search for", required=True)
    async def engineer(self, ctx, *, engineer: str):
        await self.credit_search(ctx, engineer, field="engineers", command_type="engineer",
                                  not_found=f"No songs found engineered by `{engineer}`.", title_prefix="Songs Engineered by")

    @staticmethod
    def is_unsurfaced(song):
        return str(song.get("category", "")).strip().lower().replace(" ", "_") == "unsurfaced"

    @staticmethod
    def era_name(song):
        era = song.get("era", {})
        return era.get("name", "") if isinstance(era, dict) else str(era or "")

    def get_unsurfaced_songs(self, songs, era_filter=None):
        return [s for s in songs if self.is_unsurfaced(s) and (not era_filter or self.era_name(s) == era_filter)]

    def get_unsurfaced_eras(self, songs):
        return sorted({self.era_name(s) for s in songs if self.is_unsurfaced(s) and self.era_name(s)})

    def calculate_unsurfaced_stats(self, songs):
        def is_unique(s):
            names = [str(s.get("name", "")).lower()] + [str(t).lower() for t in s.get("track_titles", []) if t]
            return not any(re.search(r"v\d+", n) for n in names) and not any(m in n for n in names for m in ("version", "ver.", "alt", "alternate", "pt.", "part"))
        def has_feature(s):
            text = str(s.get("name", "")).lower() + " " + str(s.get("credited_artists", "")).lower()
            return any(i in text for i in ("feat", "ft.", "with", " & ", " x "))

        unsurfaced = [s for s in songs if self.is_unsurfaced(s)]
        unique = [s for s in unsurfaced if is_unique(s)]
        post = lambda s: self.era_name(s) == "POST"
        return {
            "total": len(unsurfaced),
            "without_posthumous": sum(1 for s in unsurfaced if not post(s)),
            "juicethekidd": sum(1 for s in unsurfaced if "Juice WRLD" not in str(s.get("credited_artists", ""))),
            "juice_wrld": sum(1 for s in unsurfaced if "Juice WRLD" in str(s.get("credited_artists", ""))),
            "max_lord": sum(1 for s in unsurfaced if "max lord" in str(s.get("engineers", "")).lower()),
            "unique": len(unique),
            "unique_without_posthumous": sum(1 for s in unique if not post(s)),
            "without_features": sum(1 for s in unsurfaced if not has_feature(s)),
            "unique_without_features": sum(1 for s in unique if not has_feature(s)),
        }

    def era_select_options(self, era_options, current_era=None):
        options = [discord.SelectOption(label="All Eras", value="all", default=current_era is None)]
        for era_name in era_options:
            options.append(discord.SelectOption(
                label=str(ERA_MAP.get(era_name, era_name))[:100],
                value=era_name, default=current_era == era_name, emoji=EMOJI_MAP.get(era_name),
            ))
            if len(options) >= 25:
                break
        return options

    def build_unsurfaced_pagination(self, matched, title, user_id, era_options, current_era):
        def extra_items(pagination, page):
            if not era_options:
                return []
            era_select = Select(placeholder="Filter by era...", options=self.era_select_options(era_options, current_era))

            async def era_callback(interaction):
                if interaction.user.id != user_id:
                    return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
                selected = era_select.values[0]
                songs = cache.getSongs() or []
                new_era = None if selected == "all" else selected
                new_matched = self.get_unsurfaced_songs(songs, era_filter=new_era)
                if not new_matched:
                    return await interaction.response.send_message(f"{emojis.fail} No unsurfaced songs found for that era.", ephemeral=True)
                era_display = ERA_MAP.get(new_era, new_era) if new_era else None
                new_title = f"Unsurfaced Songs - {era_display}" if era_display else "Unsurfaced Songs"
                await self.build_unsurfaced_pagination(new_matched, new_title, user_id, era_options, new_era).show(interaction, 0)

            era_select.callback = era_callback
            return [ActionRow(era_select)]

        return createSimplePagination(
            items=matched, items_per_page=items, user_id=user_id,
            command_type="unsurfaced", render_page_func=self.render_page(matched, title), view_class=PersistentSongView,
            extra_items_func=extra_items,
        )

    @bridge.bridge_command(name="unsurfaced", aliases=["us"], description="View all unsurfaced songs, optionally filtered by era")
    async def unsurfaced(self, ctx):
        songs = cache.getSongs()
        if not songs:
            return await ctx.respond(f"{emojis.fail} Song database unavailable.", ephemeral=True)
        async with ctx.typing():
            matched = self.get_unsurfaced_songs(songs)
            if not matched:
                return await ctx.respond(f"{emojis.fail} No unsurfaced songs found.", ephemeral=True)
            stats = self.calculate_unsurfaced_stats(songs)
            r2 = "<:reply2:1407148805287182348>"
            r1 = "<:reply:1407148693802455120>"
            bk = "<:blank:1512827956144242688>"
            stats_text = (
                f"Total Unsurfaced: **{stats['total']:,}**\n"
                f"{r2} Minus Posthumous: **{stats['without_posthumous']:,}**\n"
                f"{r2} Without Features: **{stats['without_features']:,}**\n"
                f"{r2} JuiceTheKidd: **{stats['juicethekidd']:,}**\n"
                f"{r2} Juice WRLD: **{stats['juice_wrld']:,}**\n"
                f"{bk}{r1} Engineered by Max Lord: **{stats['max_lord']:,}**\n"
                "<:reply3:1512827072270303282>\n"
                f"{r2} Unique: **{stats['unique']:,}**\n"
                f"{bk}{r2} Unique minus Features: **{stats['unique_without_features']:,}**\n"
                f"{bk}{r1} Unique minus Posthumous: **{stats['unique_without_posthumous']:,}**\n"
            )
            cont = createContainer(title=f"{emojis.info} Unsurfaced Stats", description=stats_text)
            view = createView(cont)
            era_options = self.get_unsurfaced_eras(songs)
            if era_options:
                era_select = Select(placeholder="Filter by era...", options=self.era_select_options(era_options))

                async def era_callback(interaction):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
                    selected = era_select.values[0]
                    new_era = None if selected == "all" else selected
                    new_matched = self.get_unsurfaced_songs(songs, era_filter=new_era)
                    if not new_matched:
                        return await interaction.response.send_message(f"{emojis.fail} No unsurfaced songs found for that era.", ephemeral=True)
                    era_display = ERA_MAP.get(new_era, new_era) if new_era else None
                    title = f"Unsurfaced Songs - {era_display}" if era_display else "Unsurfaced Songs"
                    await self.build_unsurfaced_pagination(new_matched, title, ctx.author.id, era_options, new_era).show(interaction, 0)

                era_select.callback = era_callback
                view.add_item(ActionRow(era_select))
            await ctx.respond(view=view)


def setup(bot):
    bot.add_cog(MetadataCog(bot))