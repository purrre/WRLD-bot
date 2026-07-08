import asyncio
import io
import os
import re
import urllib.parse

import aiohttp
import discord
from discord import ButtonStyle, SeparatorSpacingSize
from discord.ui import Section, TextDisplay, Button, ActionRow, Thumbnail

from config import NOT_YOURS, colors, emojis, endpoints
from utils.functions import (
    downloadUrl, fetchBrowseItems, stripVersionMarkers, getTitleKey,
    normalizeMatchingText as normalizeText, findClosestMatch, getEraMap,
    getSession, consoleLog, tokenizeText,
)
from utils.components import (
    notesLengthThreshold, PersistentSongView, createContainer, createView, createSongDropdown, loading,
)
from utils.database import db

LEAK_EXCLUDED_CATEGORIES = {"unsurfaced", "recording_session"}
SNIPPET_ALLOWED_CATEGORIES = {"unreleased", "released", "unsurfaced"}
ALBUM_MAPPING = getEraMap()

DEFAULT_UPLOAD_LIMIT = 8 * 1024 * 1024
COMMON_WORDS = {"juice", "wrld", "the", "and", "or", "ft", "feat", "featuring", "prod", "by", "with"}
MIN_BROWSE_LENGTH = 6


# ==============================================================================
# matching
# ==============================================================================

def dedupe_songs(songs):
    seen = set()
    unique = []
    for song in songs:
        sid = song.get("public_id")
        if sid in seen:
            continue
        seen.add(sid)
        unique.append(song)
    return unique


def fallback_matches(query):
    songs = cache_songs()
    normalized = stripVersionMarkers(query).lower()
    matches = []
    for song in songs:
        name = stripVersionMarkers(str(song.get("name", ""))).lower()
        titles = [stripVersionMarkers(str(t)).lower() for t in (song.get("track_titles") or [])]
        if normalized in name or any(normalized in t for t in titles):
            matches.append(song)
    return matches


def filter_mode_matches(songs, mode):
    filtered = []
    for song in songs:
        category = str(song.get("category", "")).lower()
        if mode in ("leak", "ogfile") and category in LEAK_EXCLUDED_CATEGORIES:
            continue
        if mode == "snippets" and category not in SNIPPET_ALLOWED_CATEGORIES:
            continue
        if mode == "session" and category != "recording_session":
            continue
        filtered.append(song)
    return dedupe_songs(filtered)


def song_matches(query, mode):
    results = findClosestMatch(query, listAll=True)
    if results and isinstance(results, dict) and "all" in results:
        matches = results["all"]
    else:
        songs = cache_songs()
        q = query.lower()
        matches = [
            s for s in songs
            if q in s.get("name", "").lower() or any(q in str(t).lower() for t in s.get("track_titles", []))
        ]
    return filter_mode_matches(matches, mode)


def is_album_query(query):
    normalized_query = normalizeText(stripVersionMarkers(query))
    query_key = getTitleKey(query)
    if not normalized_query:
        return False
    from utils.cache import cache
    albums = cache.getAlbums() or []
    for album in albums:
        album_name = str(album.get("name", "") or "").strip()
        if not album_name:
            continue
        if normalizeText(stripVersionMarkers(album_name)) == normalized_query:
            return True
        album_key = getTitleKey(album_name)
        if query_key and album_key and album_key == query_key:
            return True
    for mapped_key, mapped_value in (ALBUM_MAPPING or {}).items():
        for candidate in (mapped_key, mapped_value):
            candidate_text = str(candidate or "").strip()
            if not candidate_text:
                continue
            if normalizeText(stripVersionMarkers(candidate_text)) == normalized_query:
                return True
            candidate_key = getTitleKey(candidate_text)
            if query_key and candidate_key and candidate_key == query_key:
                return True
    return False


def match_song_for_cover(query):
    if is_album_query(query):
        return None
    normalized_query = stripVersionMarkers(query)
    normalized_key = normalizeText(normalized_query)
    primary_query_key = getTitleKey(normalized_query)
    songs = cache_songs()
    if normalized_key and songs:
        exact, partial, key_exact, key_partial = [], [], [], []
        for song in songs:
            titles = song.get("track_titles") or [song.get("name", "")]
            primary_title = stripVersionMarkers(str(titles[0] if titles else song.get("name", "")))
            primary_key = normalizeText(primary_title)
            primary_title_key = getTitleKey(primary_title)
            if not primary_key:
                continue
            if primary_key == normalized_key:
                exact.append(song)
            elif normalized_key in primary_key or primary_key in normalized_key:
                partial.append(song)
            if primary_query_key and primary_title_key:
                if primary_title_key == primary_query_key:
                    key_exact.append(song)
                elif primary_query_key in primary_title_key or primary_title_key in primary_query_key:
                    key_partial.append(song)
        if exact:
            return exact[0]
        if key_exact:
            return key_exact[0]
        if key_partial:
            return key_partial[0]
        if partial:
            return partial[0]
    results = findClosestMatch(normalized_query, listAll=True)
    if results and isinstance(results, dict):
        return results.get("best")
    fallback = fallback_matches(normalized_query)
    return fallback[0] if fallback else None


def cache_songs():
    from utils.cache import cache
    return cache.getSongs() or []


def extract_fnames(file_names_field):
    if not file_names_field:
        return []
    field_str = str(file_names_field).strip()
    if not field_str:
        return []
    if "file name" not in field_str.lower():
        return [field_str]
    matches = re.findall(r"file name\s*(?:\(\d+\))?\s*[:\s]*([^\n]+)", field_str, re.IGNORECASE)
    if matches:
        cleaned = []
        for match in matches:
            cleaned.extend([name.strip() for name in match.split(",") if name.strip()])
        return cleaned
    return [field_str]


async def find_session_file(song):
    query = song.get("name") or ((song.get("track_titles") or [""])[0])
    if not query:
        return None
    terms = [query.strip()]
    song_name = str(song.get("name", "")).strip()
    if song_name:
        terms.append(song_name)
    titles = song.get("track_titles", [])
    if isinstance(titles, list) and titles:
        terms.extend([str(t).strip() for t in titles if t])
    terms = list(set(terms))
    try:
        items = await fetchBrowseItems("sessions")
        if not items:
            return None
        best_match = None
        best_score = 0
        for item in items:
            item_name = normalizeText(str(item.get("name", "")))
            item_path = normalizeText(str(item.get("path", "")))
            combined = f"{item_name} {item_path}"
            score = 0
            for term in terms:
                term_norm = normalizeText(term)
                if term_norm in item_name:
                    score += 10
                if term_norm in item_path:
                    score += 5
                if term_norm in combined:
                    score += 3
            if score > best_score:
                best_score = score
                best_match = item
        if best_match and best_score >= 5:
            return best_match
    except Exception as e:
        consoleLog("SESSION_FILE", f"error finding session file: {e}", type="error")
    return None


def split_ext(name):
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        return stem, f".{ext.lower()}"
    return name, ""


def strip_channel_suffix(stem):
    match = re.search(r"^(.*?)[ _.\-]([lr])$", stem.strip(), re.IGNORECASE)
    if match:
        return match.group(1).strip(), match.group(2).upper()
    return stem, None


def og_file_label(path):
    ext = file_ext({"path": path})
    if not ext:
        return None, None
    basename = os.path.basename(str(path))
    name_no_ext, _ = split_ext(basename)
    _, channel = strip_channel_suffix(name_no_ext)
    ext_label = ext.lstrip(".").upper()
    label = f"OG .{ext_label} ({channel})" if channel else f"OG .{ext_label}"
    type_key = f"{ext}_{channel}" if channel else ext
    return label, type_key


async def fetch_og_files(song):
    names = extract_fnames(song.get("file_names"))
    if not names:
        return []
    found = []
    seen_paths = set()
    for name in names:
        if not name or name.lower() == "n/a":
            continue
        if "/" in name and any(name.lower().endswith(e) for e in (".wav", ".flac", ".mp3", ".m4a")):
            if name.lower() not in seen_paths:
                seen_paths.add(name.lower())
                found.append(name)
            continue
        query = name.strip()
        if len(query) < MIN_BROWSE_LENGTH:
            continue
        items = await browse_files(query)
        if not items:
            continue
        query_key = query.lower()
        for item in items:
            path = str(item.get("path", "")).strip()
            ext = file_ext(item)
            if not path or ext not in (".wav", ".flac", ".mp3", ".m4a"):
                continue
            item_name = str(item.get("name", ""))
            name_no_ext, _ = split_ext(item_name)
            compare_stem, _ = strip_channel_suffix(name_no_ext)
            if compare_stem.strip().lower() != query_key:
                continue
            if path.lower() in seen_paths:
                continue
            seen_paths.add(path.lower())
            found.append(path)
    return found


async def fetch_og_buttons(song):
    buttons = []
    try:
        paths = await fetch_og_files(song)
        if not paths:
            return []
        seen_types = set()
        for path in paths:
            label, type_key = og_file_label(path)
            if not label or type_key in seen_types:
                continue
            seen_types.add(type_key)
            url = downloadUrl(path)
            if url:
                buttons.append(Button(label=label, style=ButtonStyle.link, url=url))
    except Exception as e:
        consoleLog("OG_BUTTONS", f"error fetching og buttons: {e}", type="error")
    return buttons


# ==============================================================================
# song view rendering
# ==============================================================================

INVALID_VALS = {"none", "n/a", "", "null"}

METADATA_MAP = [
    ("Era", "era"), ("File Name", "file_names"), ("Instrumental", "instrumentals"),
    ("Recording Location", "recording_locations"), ("Vocals", "record_dates"),
    ("Preview", "preview_date"), ("File", "date_leaked"), ("Length", "length"),
    ("Category", "category"),
]

_DATE_PREFIXES = re.compile(
    r"^(?:Recorded|Surfaced|Leaked|First Previewed|Previewed|"
    r"[A-Z][^,\n]*?(?:'s Vocals|'s Chorus.*|Chorus.*|Verse.*|Ad-Libs.*|Vocals))\s*[\r\n]+",
    re.IGNORECASE,
)


def clean_date_value(val):
    if not val:
        return val
    cleaned = _DATE_PREFIXES.sub("", str(val)).strip()
    return cleaned or val


def build_metadata_fields(song):
    fields = []
    for label, key in METADATA_MAP:
        val = song.get(key)
        if label == "Era":
            era_key = val.get("name") if isinstance(val, dict) else str(val or "")
            val_str = ALBUM_MAPPING.get(era_key.strip()) or era_key.strip() if era_key else None
        else:
            val_str = str(val.get("name") if isinstance(val, dict) else val or "").strip() or None
        if not val_str or val_str.lower() in INVALID_VALS:
            continue
        if label == "Category":
            val_str = val_str.replace("_", " ").title()
        elif key in ("record_dates", "date_leaked", "preview_date"):
            val_str = clean_date_value(val_str)
        fields.append(f"**{label}**\n{val_str}")
    return fields


def build_notes_button(song):
    details = str(song.get("additional_information") or "").strip()
    if not details or details.lower() in INVALID_VALS or len(details) <= notesLengthThreshold:
        return None
    btn = Button(label="View Notes", style=ButtonStyle.secondary)

    async def notes_callback(interaction, _details=details):
        song_name = (song.get("track_titles") or [song.get("name", "Unknown")])[0]
        cont = createContainer(title="Notes", heading="###")
        cont.add_text(f"-# {song_name}")
        cont.add_separator(divider=True)
        cont.add_text(_details)
        await interaction.response.send_message(view=createView(cont), ephemeral=True)

    btn.callback = notes_callback
    return btn


def image_url(song):
    img = song.get("image_url")
    if img and img.startswith("/"):
        return f"{endpoints.jwa}{img}"
    return img


def song_title(song):
    return build_song_header(song)


def build_song_header(song):
    titles = song.get("track_titles", [song.get("name", "Unknown")])
    main_title = titles[0] if titles else "Unknown"
    alt_names = titles[1:] if len(titles) > 1 else []
    return main_title, alt_names


def format_song_line(song, index):
    title = build_song_header(song)[0]
    category = (song.get("category", "N/A") or "N/A").replace("_", " ").title()
    era = song.get("era", {})
    era_name = era.get("name", "N/A") if isinstance(era, dict) else (str(era) if era else "N/A")
    path = song.get("path")
    if path:
        parts = path.split("/")
        dir_path = "/".join(parts[:-1])
        url = f"{endpoints.files}{urllib.parse.quote(dir_path, safe='')}&highlight={urllib.parse.quote(parts[-1])}" if dir_path else f"{endpoints.files}?highlight={urllib.parse.quote(parts[-1])}"
        link = f"[**{title}**]({url})"
    else:
        link = f"**{title}**"
    return f"{index}. {link}\n-# {category} | {era_name} | Prod: {song.get('producers', 'N/A') or 'N/A'} | Eng: {song.get('engineers', 'N/A') or 'N/A'}"


def build_song_details_lines(song, mode="info"):
    lines = []
    for label, key in (("Artists", "credited_artists"), ("Producers", "producers"), ("Engineers", "engineers")):
        val = str(song.get(key, "")).strip()
        if val and val.lower() not in INVALID_VALS:
            lines.append(f"-# {label}: **{val}**")
    if mode == "leak":
        length = str(song.get("length", "")).strip()
        leak_type = str(song.get("leak_type", "")).strip()
        if length and length.lower() not in INVALID_VALS:
            lines.append(f"-# Length: {length.replace(chr(10), ' ')}")
        if leak_type and leak_type.lower() not in INVALID_VALS:
            lines.append(f"-# {leak_type.replace(chr(10), ' ')}")
    elif mode == "snippets":
        preview_date = str(song.get("preview_date", "")).strip()
        if preview_date and preview_date.lower() not in INVALID_VALS:
            lines.append(f"-# {preview_date.replace(chr(10), ' ').replace(chr(13), ' ')}")
    elif mode == "session":
        record_date = str(song.get("record_dates", "") or "").strip()
        if record_date and record_date.lower() not in INVALID_VALS:
            lines.append(f"-# Recording Dates: {record_date.replace(chr(10), ' ').replace(chr(13), ' ')}")
    return lines


def build_song_container(song, include_details=True, mode="info"):
    main_title, alt_names = build_song_header(song)
    header_lines = [f"### {main_title}"]
    if alt_names:
        header_lines.append(f"-# AKA: {', '.join(alt_names)}")
    header_lines.extend(build_song_details_lines(song, mode))
    details = str(song.get("additional_information") or "").strip()
    details_valid = details and details.lower() not in INVALID_VALS
    if include_details and details_valid and len(details) <= notesLengthThreshold:
        header_lines.append(f"-# Details: `{details}`")
    full_img = image_url(song)
    accessory = Thumbnail(full_img) if full_img else None
    return Section(TextDisplay("\n".join(header_lines)), accessory=accessory)


def build_song_details_section(song):
    details = str(song.get("additional_information") or "").strip()
    details_valid = details and details.lower() not in INVALID_VALS
    if details_valid and len(details) > notesLengthThreshold:
        full_img = image_url(song)
        accessory = Thumbnail(full_img) if full_img else None
        return Section(TextDisplay(f"### Details\n{details}"), accessory=accessory)
    return None


async def build_song_view(song, user_id, mode="info", matches=None, og_buttons=None):
    main_cont = createContainer()
    show_details = mode not in ("leak", "snippets", "ogfile", "session")
    main_cont.add_item(build_song_container(song, include_details=show_details, mode=mode))
    if mode == "leak":
        main_cont.add_separator(divider=True)
        btn_row = [SongButton(song)]
        path = song.get("path")
        if path:
            btn_row.append(Button(label="\u200b", style=ButtonStyle.link, url=downloadUrl(str(path))))
        main_cont.add_item(ActionRow(*btn_row))
    elif mode == "snippets":
        main_cont.add_separator(divider=True)
        btn_row = [SnipButton(song)]
        main_cont.add_item(ActionRow(*btn_row))
    elif mode == "session":
        session_file = await find_session_file(song)
        if session_file:
            main_cont.add_item(ActionRow(SessionEditSendButton(session_file.get("path")), SessionZipSendButton(session_file.get("path"))))
    if mode == "ogfile":
        if not og_buttons:
            og_buttons = await fetch_og_buttons(song)
        if og_buttons:
            main_cont.add_separator(divider=True, spacing=SeparatorSpacingSize.small)
            main_cont.add_item(ActionRow(*og_buttons))
    view = createView(main_cont, viewClass=PersistentSongView, timeout=None if mode == "leak" else 1800)
    if matches and len(matches) > 1:
        async def on_select(interaction, song_id):
            if interaction.user.id != user_id:
                return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            await interaction.response.defer()
            selected = next((m for m in matches if str(m.get("public_id")) == str(song_id)), None)
            if selected:
                new_view = await build_song_view(selected, user_id, mode, matches)
                await interaction.edit_original_response(view=new_view)
        dropdown = createSongDropdown(matches, user_id, "Choose a song...", on_select)
        view.add_item(ActionRow(dropdown))
    return view


def not_found_text(mode, query):
    names = {"leak": "audio file", "snippets": "snippet files", "session": "session files", "info": "results"}
    return f"{emojis.fail} No {names.get(mode, 'results')} found for **{query}**."


# ==============================================================================
# file fetching and streaming
# ==============================================================================

def filter_query_words(query):
    words = query.split()
    filtered = [w for w in words if w.lower() not in COMMON_WORDS]
    return " ".join(filtered) if filtered else query


def song_queries(song):
    titles = song.get("track_titles") or []
    name = song.get("name")
    values = [name, *titles]
    raw = [str(v).strip() for v in dict.fromkeys(values) if v]
    return [filter_query_words(q) for q in raw if filter_query_words(q)]


def file_ext(item):
    extension = item.get("extension")
    if extension:
        return str(extension).lower()
    path = item.get("path")
    if not path:
        return None
    basename = os.path.basename(str(path))
    if "." not in basename:
        return None
    return f".{basename.rsplit('.', 1)[1].lower()}"


async def browse_files(query):
    if len(query) < MIN_BROWSE_LENGTH:
        return []
    url = f"{endpoints.browse}{urllib.parse.quote(query)}"
    session = await getSession()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status != 200:
                return []
            data = await response.json()
            return data.get("items", []) or []
    except Exception:
        return []


def upload_limit(interaction):
    guild = interaction.guild
    if guild and getattr(guild, "filesize_limit", None):
        return guild.filesize_limit
    return DEFAULT_UPLOAD_LIMIT


async def fetch_urls(song, ext=None):
    if ext == ".mp3":
        direct_path = song.get("path")
        if direct_path:
            guessed = os.path.splitext(str(direct_path))[1].lower() or ".mp3"
            return [downloadUrl(str(direct_path))], guessed
    extension_order = [ext] if ext else []
    if ext == ".mp3":
        extension_order.append(".wav")
    elif ext == ".mp4":
        extension_order.append(".mov")
    is_audio = ext in (".mp3", ".wav")
    for query in song_queries(song):
        items = await browse_files(query)
        if not items:
            continue
        if is_audio:
            stem = query.rsplit(".", 1)[0].lower()
            candidates = [item for item in items if str(item.get("name", "")).rsplit(".", 1)[0].lower() == stem]
        else:
            candidates = items
        if not candidates:
            continue
        urls = []
        found_ext = None
        for current_ext in extension_order:
            matching = []
            for item in candidates:
                if file_ext(item) == current_ext and item.get("path"):
                    matching.append(downloadUrl(str(item.get("path"))))
            if matching:
                urls.extend(matching)
                found_ext = found_ext or current_ext
        if urls:
            return list(dict.fromkeys(urls)), found_ext
    return [], None


async def send_file(interaction, url, filename, kind, session=None):
    if session is None:
        session = await getSession()
    data = io.BytesIO()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
            if response.status != 200:
                await interaction.followup.send(
                    embed=discord.Embed(description=f"Failed to fetch the {kind} file.", color=colors.red),
                    ephemeral=True,
                )
                return False
            max_size = upload_limit(interaction)
            async for chunk in response.content.iter_chunked(1024 * 256):
                data.write(chunk)
                if data.tell() > max_size:
                    container = createContainer(
                        title="File Too Large",
                        description=f"{kind} is too large to send. Download/View it below.",
                        color=colors.red,
                    )
                    view = createView(container, viewClass=PersistentSongView)
                    view.add_item(ActionRow(Button(label="Open", url=url)))
                    await interaction.followup.send(view=view, ephemeral=True)
                    return False
            data.seek(0)
            await interaction.followup.send(file=discord.File(data, filename=filename), ephemeral=True)
            return True
    except Exception as error:
        await interaction.followup.send(
            embed=discord.Embed(description=f"⚠️ Error: {error}", color=colors.red),
            ephemeral=True,
        )
        return False
    finally:
        data.close()


class SongButton(discord.ui.Button):
    def __init__(self, song):
        super().__init__(emoji="💿", label="MP3", style=discord.ButtonStyle.gray, custom_id=f"mp3_{song.get('public_id')}")
        self.song = song
        self.stream_urls = []
        self.found_ext = None

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True, invisible=False)
        if not self.stream_urls:
            urls, found_ext = await fetch_urls(self.song, ext=".mp3")
            self.stream_urls = list(dict.fromkeys(urls))
            self.found_ext = found_ext
        if not self.stream_urls:
            await interaction.followup.send(
                embed=discord.Embed(description="Couldn't find an audio file :(", color=colors.main),
                ephemeral=True,
            )
            return
        ext = self.found_ext or ".mp3"
        kind = "MP3" if ext == ".mp3" else "WAV"
        await send_file(interaction, self.stream_urls[0], f"{self.song.get('name', 'track')}{ext}", kind)
        self.stream_urls = []
        self.found_ext = None


class SnipButton(discord.ui.Button):
    def __init__(self, song):
        super().__init__(emoji="👀", label="Snippets", style=discord.ButtonStyle.gray)
        self.song = song
        self.stream_urls = []

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True, invisible=False)
        if not self.stream_urls:
            urls, found_ext = await fetch_urls(self.song, ext=".mp4")
            if urls:
                actual_ext = found_ext or ".mp4"
                self.stream_urls = [(url, actual_ext) for url in urls]
        if not self.stream_urls:
            await interaction.followup.send(
                embed=discord.Embed(description="Couldn't find any snippets :(", color=colors.main),
                ephemeral=True,
            )
            return
        suffix = " This may take a while" if len(self.stream_urls) > 5 else ""
        await interaction.followup.send(
            embed=await loading("snippet", x=f"({len(self.stream_urls)} found).{suffix}"),
            ephemeral=True,
        )
        session = await getSession()
        for index, (url, ext) in enumerate(self.stream_urls, start=1):
            filename = f"{self.song.get('name', 'track')}{ext}"
            sent = await send_file(interaction, url, filename, f"Snippet {index}", session=session)
            if sent:
                await db.incrementStat("snippets_sent")
            await asyncio.sleep(0.4)
        self.stream_urls = []


class SessionEditSendButton(discord.ui.Button):
    def __init__(self, path):
        super().__init__(emoji="🎬", label="Session Edit", style=ButtonStyle.gray)
        self.path = path

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True, invisible=False)
        await send_file(interaction, downloadUrl(self.path), os.path.basename(self.path), "Session Edit")


class SessionZipSendButton(discord.ui.Button):
    def __init__(self, path):
        super().__init__(emoji="📁", label="Session Zip", style=ButtonStyle.gray)
        self.path = path

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True, invisible=False)
        await send_file(interaction, downloadUrl(self.path, plus=True), os.path.basename(self.path), "Session Zip")


def build_session_search_terms(query, song=None):
    terms = [str(query).strip()]
    if song:
        terms.append(str(song.get("name", "")).strip())
        terms.extend([str(title).strip() for title in (song.get("track_titles") or []) if str(title).strip()])
        terms.extend(extract_fnames(song.get("file_names")))
    cleaned = []
    seen = set()
    for term in terms:
        if not term:
            continue
        normalized = stripVersionMarkers(term)
        key = normalizeText(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
    return cleaned


def score_candidate(item, terms, *, require_sessions_hint=False):
    name = normalizeText(item.get("name", ""))
    path = normalizeText(item.get("path", ""))
    combined = f"{name} {path}"
    score = 0
    for term in terms:
        t = normalizeText(term)
        if not t:
            continue
        if t in name:
            score += 6
        elif t in combined:
            score += 3
        for tok in tokenizeText(t):
            if tok in combined:
                score += 1
    if require_sessions_hint and "session" in combined:
        score += 4
    return score


async def find_best_session_asset(query, *, kind):
    matched_song = None
    results = findClosestMatch(stripVersionMarkers(query), listAll=True)
    if results and isinstance(results, dict):
        matched_song = results.get("best")
    search_terms = build_session_search_terms(query, matched_song)
    if not search_terms:
        return None, matched_song
    candidates_by_path = {}
    for term in search_terms:
        items = await fetchBrowseItems(term)
        for item in items:
            path = str(item.get("path", "")).strip()
            ext = str(item.get("extension", "")).lower()
            if not path:
                continue
            if kind == "zip":
                if ext != ".zip":
                    continue
                score = score_candidate(item, search_terms, require_sessions_hint=True)
            else:
                if not path.startswith("Session Edits/"):
                    continue
                score = score_candidate(item, search_terms, require_sessions_hint=False)
            existing = candidates_by_path.get(path)
            if existing is None or score > existing[0]:
                candidates_by_path[path] = (score, item)
    if not candidates_by_path:
        return None, matched_song
    best_path, _ = max(candidates_by_path.items(), key=lambda entry: entry[1][0])
    return best_path, matched_song


def build_session_asset_view(*, title, all_titles=None, category, path, song, button, extra_buttons=None, show_file=True, cover_url=None):
    cont = createContainer()
    if song:
        cont.add_item(build_song_container(song, include_details=False, mode="session"))
    else:
        header_lines = [f"### {title}"]
        if all_titles:
            header_lines.append(f"-# AKA: {', '.join(all_titles[:4])}")
        header_lines.append(f"-# {category}")
        img = cover_url or (image_url(song) if song else None)
        accessory = Thumbnail(img) if img else None
        if accessory:
            cont.add_item(Section(TextDisplay("\n".join(header_lines)), accessory=accessory))
        else:
            cont.add_text("\n".join(header_lines))
    cont.add_separator(divider=True)
    if show_file:
        cont.add_text(f"**File**\n`{os.path.basename(path)}`")
    buttons = [button]
    if extra_buttons:
        buttons.extend(extra_buttons)
    cont.add_item(ActionRow(*buttons))
    return createView(cont, viewClass=PersistentSongView)
