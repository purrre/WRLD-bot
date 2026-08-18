from rapidfuzz import fuzz
from discord.ext import bridge, commands
from discord.ui import ActionRow

from config import emojis, endpoints
from utils.cache import cache_manager, cache
from utils.components import createContainer, createSongDropdown, createView
from utils.functions import checkApiHealth, apiHost, normalizeMatchingText as normalizeText, extractTokens, getEraMap

PROJECT_MAP = getEraMap()

def map_project_name(project):
    raw = str(project or "").strip()
    if not raw:
        return "N/A"
    if raw in PROJECT_MAP:
        return PROJECT_MAP[raw]
    raw_upper = raw.upper()
    for key, value in PROJECT_MAP.items():
        key_upper = str(key).upper()
        if raw_upper == key_upper or raw_upper.startswith(f"{key_upper} "):
            return value
    return raw

def format_price(price):
    if isinstance(price, (int, float)):
        return f"${price:,}"
    try:
        return f"${int(price):,}"
    except (TypeError, ValueError):
        return str(price).strip() or "N/A"

def search_entries(query, entries):
    q_norm = normalizeText(query)
    q_tokens = extractTokens(query)
    scored = []
    for entry in entries:
        aliases = [entry.get("title"), *(entry.get("alternates") or []), *(entry.get("family") or [])]
        best = 0
        seen = set()
        for alias in aliases:
            text = str(alias or "").strip()
            norm = normalizeText(text)
            if not text or not norm or norm in seen:
                continue
            seen.add(norm)
            a_tokens = extractTokens(alias)
            score = fuzz.WRatio(q_norm, norm)
            if norm == q_norm:
                score += 140
            elif q_norm and q_norm in norm:
                score += 60
            if q_tokens and all(t in a_tokens for t in q_tokens):
                score += 50
                if a_tokens[:len(q_tokens)] == q_tokens:
                    score += 25
            best = max(best, int(score))
        if best >= 75:
            scored.append((best, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored]


def normalize_song_payload(entry):
    gb = entry.get("gb") or entry.get("group") or entry.get("groupbuy_info") or {}
    title = entry.get("title", "Unknown")
    return {
        "song_name": title,
        "project": map_project_name(entry.get("project", "N/A")),
        "price_display": format_price(gb.get("price")),
        "start_date": str(gb.get("start_date", "")).strip(),
        "end_date": str(gb.get("end_date", "")).strip(),
        "finished": gb.get("finished", False),
        "ogfile": gb.get("ogfile", False),
        "blind": entry.get("blind", False),
        "notes": str(entry.get("additional_info", "")).strip(),
        "family": [item for item in (entry.get("family") or []) if str(item).strip() and str(item).strip() != title],
    }


async def create_groupbuy_container(entry):
    gb = entry.get("gb") or {}
    title = entry.get("title", "Unknown")
    alts = entry.get("alternates") or []
    family = [item for item in (entry.get("family") or []) if str(item).strip() and str(item).strip() != title]
    aka = f"\n-# AKA: {', '.join(alts[:3])}" if alts else ""
    sold = f"\n-# Sold with: {', '.join(family[:8])}" if family else ""
    icon = lambda v: emojis.thumbsup if v is True else emojis.thumbsdown
    fields = [("Era", map_project_name(entry.get("project", "N/A")))]
    start = str(gb.get("start_date", "")).strip()
    end = str(gb.get("end_date", "")).strip()
    if start:
        fields.append(("Start Date", start))
    if end:
        fields.append(("End Date", end))
    fields.append((None, f"**Info**\nFinished: {icon(gb.get('finished', False))}\nOG File: {icon(gb.get('ogfile', False))}\nBlind: {icon(entry.get('blind', False))}"))
    notes = str(entry.get("additional_info", "")).strip()
    if notes and notes not in {"-", "N/A", "n/a", ""}:
        fields.append(("Notes", notes))
    return createContainer(
        title=f"{title}{aka}\n-# Price: **{format_price(gb.get('price'))}**{sold}",
        fields=fields,
    )


def get_year_container(year_data):
    total = year_data.get("total", 0)
    total_text = format_price(total)
    return createContainer(
        title=f"{emojis.crown} {year_data['year']} Groupbuy Summary",
        description=f"-# {year_data.get('label', 'Groupbuys / Personal Buys')}",
        fields=[
            ("Money Spent", total_text),
            ("Finished", year_data.get("finished", "N/A")),
            ("Surfaced as OG", year_data.get("surfaced", "N/A")),
        ],
        heading="##",
    )


class GroupbuysCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def build_dropdown(self, matches, year_option=None):
        items = []
        seen = set()
        if year_option:
            items.append({
                "title": f"⭐ {year_option['year']} Summary",
                "name": f"⭐ {year_option['year']} Summary",
                "public_id": f"YEAR_{year_option['year']}",
                "alternates": [],
                "project": year_option.get("label", "Statistics"),
            })
        for match in matches:
            title = match.get("title")
            if not title or title in seen:
                continue
            items.append({**match, "name": title, "public_id": title, "project": map_project_name(match.get("project", "N/A"))})
            seen.add(title)
            if len(items) >= 25:
                break
        return items

    def attach_dropdown(self, cont, matches, user_id, callback, year_option=None):
        items = self.build_dropdown(matches, year_option)
        if items:
            cont.add_item(ActionRow(createSongDropdown(items, user_id, placeholder="Choose a song or year...", callbackFunc=callback, formatType="gb")))

    def build_groupbuy_view(self, content, matches, user_id, year_option=None):
        async def on_select(interaction, selected_id):
            if str(selected_id).startswith("YEAR_"):
                year = str(selected_id).split("YEAR_", 1)[1]
                year_data = (cache.getGroupbuyYearlyStats() or {}).get(year)
                if not year_data:
                    return await interaction.response.send_message(f"{emojis.fail} No yearly data found.", ephemeral=True)
                next_cont = get_year_container(year_data)
                self.attach_dropdown(next_cont, matches, user_id, on_select, year_option)
                return await interaction.response.edit_message(view=createView(next_cont))
            selected = next((item for item in matches if str(item.get("title")) == str(selected_id)), None)
            if not selected:
                return await interaction.response.send_message(f"{emojis.fail} No groupbuy found.", ephemeral=True)
            next_cont = await create_groupbuy_container(selected)
            self.attach_dropdown(next_cont, matches, user_id, on_select, year_option)
            await interaction.response.edit_message(view=createView(next_cont))

        self.attach_dropdown(content, matches, user_id, on_select, year_option)
        return createView(content)

    @bridge.bridge_command(aliases=["gb"], description="Show groupbuy information")
    @bridge.bridge_option(name="query", description="Song name or year to search for", required=True)
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def groupbuy(self, ctx, *, query: str):
        if not await checkApiHealth(endpoints.groupbuys):
            return await ctx.respond(f"{emojis.fail} {apiHost(endpoints.groupbuys)} is down. Please try again later.\n-# If this persists, contact `@purree`", ephemeral=True)
        async with ctx.typing():
            data = cache.getGroupbuys()
            if not data:
                await cache_manager.syncEndpoint("groupbuys", endpoints.groupbuys, paginate=False)
                data = cache.getGroupbuys()
            if not data:
                return await ctx.respond(f"{emojis.fail} Failed to load groupbuy data.")
            yearly_stats = data.get("yearly_stats", {}) if isinstance(data, dict) else {}
            entries = data.get("entries", []) if isinstance(data, dict) else []
            year_option = yearly_stats.get(str(query).strip())
            matches = search_entries(query, entries)
            if year_option and not matches:
                return await ctx.respond(view=createView(get_year_container(year_option)))
            if not matches:
                return await ctx.respond(f"{emojis.fail} No groupbuy information found for `{query}`.")
            cont = get_year_container(year_option) if year_option else await create_groupbuy_container(matches[0])
            view = self.build_groupbuy_view(cont, matches, ctx.author.id, year_option)
            await ctx.respond(view=view)


def setup(bot):
    bot.add_cog(GroupbuysCog(bot))
