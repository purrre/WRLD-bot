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
    """Find both session zip and session edit paths for a song."""
    query = song.get("name") or ((song.get("track_titles") or [""])[0])
    if not query:
        return None
    search_terms = build_session_search_terms(query, song)
    if not search_terms:
        return None
    zip_path = None
    edit_path = None
    zip_best_score = 0
    edit_best_score = 0
    try:
        for term in search_terms:
            items = await fetchBrowseItems(term)
            for item in items:
                path = str(item.get("path", "")).strip()
                ext = str(item.get("extension", "")).lower()
                if not path:
                    continue
                if not zip_path and ext == ".zip" and path.startswith("Studio Sessions/"):
                    score = score_candidate(item, search_terms, require_sessions_hint=True)
                    path_lower = path.lower()
                    if "protools" in path_lower or "album deliverables" in path_lower:
                        score += 5
                    if score > zip_best_score:
                        zip_best_score = score
                        zip_path = path
                if not edit_path and path.startswith("Session Edits/"):
                    score = score_candidate(item, search_terms, require_sessions_hint=False)
                    if score > edit_best_score:
                        edit_best_score = score
                        edit_path = path
            if zip_path and edit_path:
                break
    except Exception as e:
        consoleLog("SESSION_FILE", f"error finding session file: {e}", type="error")
    if not zip_path and not edit_path:
        return None
    return {"zip_path": zip_path, "edit_path": edit_path}


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
# instrumentals
# ==============================================================================

_INST_PREFIX = re.compile(r"^instrumental(?:\s*\(\d+\))?\s*:\s*", re.IGNORECASE)


def parse_instrumentals(song):
    """Extract instrumental beat name(s) from a song's `instrumentals` field.
    Returns a list of name strings. Handles both plain strings and multi-line
    fields with 'Instrumental:', 'Instrumental (1):' etc. prefixes."""
    raw = str(song.get("instrumentals") or "").strip()
    if not raw or raw.lower() in INVALID_VALS:
        return []
    names = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _INST_PREFIX.match(line)
        if m:
            name = line[m.end():].strip()
            if name:
                names.append(name)
        elif not any(p in line.lower() for p in ("loop", "midi", "stems", "kit")):
            # ponytail: lines without "Instrumental:" prefix but also not loop/midi/stems/kit
            # are treated as the instrumental name (single-line fields like "Wonderland", "shordi")
            if not names:
                names.append(line)
    return names


def _match_instrumental_items(items, query):
    """Match browse items to an instrumental query. Tries exact stem match first,
    then falls back to substring match (query contained in item name).
    Returns exact matches first, then substring matches — caller filters by extension."""
    stem = query.rsplit(".", 1)[0].lower()
    exact = [i for i in items if str(i.get("name", "")).rsplit(".", 1)[0].lower() == stem]
    # ponytail: don't return early on exact matches — a directory named "Wasted" matches
    # exactly but the actual file "Wasted intrumental prod by Cbmix.wav" only matches
    # via substring. Include both so the caller's audio filter can pick the file.
    substring = [i for i in items if stem in str(i.get("name", "")).lower() and i not in exact]
    return exact + substring


async def _browse_instrumental(query):
    """Browse for instrumental files, trying the full query first then progressively
    shorter prefixes. The browse API does phrase matching, so multi-word queries like
    'Wasted intrumental Cbmix' return nothing while 'Wasted' returns the file.
    Returns (items, effective_query) — effective_query is what actually got results."""
    query = filter_query_words(query)
    if not query or len(query) < MIN_BROWSE_LENGTH:
        return [], None
    items = await browse_files(query)
    if items:
        return items, query
    # ponytail: browse API does phrase matching, multi-word queries often return 0;
    # try the first word as a fallback. Ceiling: if the first word is too generic
    # (e.g. "Type"), may return noise — _match_instrumental_items filters it.
    words = query.split()
    if len(words) > 1:
        first = words[0]
        if len(first) >= MIN_BROWSE_LENGTH:
            items = await browse_files(first)
            if items:
                return items, first
    return [], None


async def fetch_instrumental_urls(song):
    """Browse for instrumental files matching the parsed instrumental name(s).
    Returns (urls, ext) like fetch_urls. Prefers paths under Instrumentals/."""
    names = parse_instrumentals(song)
    if not names:
        return [], None
    found = []
    found_ext = None
    for name in names:
        query = filter_query_words(name)
        if not query or len(query) < MIN_BROWSE_LENGTH:
            continue
        items, effective_query = await _browse_instrumental(name)
        if not items:
            continue
        candidates = _match_instrumental_items(items, effective_query)
        if not candidates:
            continue
        audio = [i for i in candidates if file_ext(i) in (".mp3", ".wav") and i.get("path")]
        preferred = [i for i in audio if str(i.get("path", "")).startswith("Instrumentals/")]
        for item in (preferred or audio):
            url = downloadUrl(str(item.get("path")))
            if url and url not in found:
                found.append(url)
                found_ext = found_ext or file_ext(item)
    return found, found_ext


async def fetch_instrumental_urls_by_name(name):
    """Browse for instrumental files matching a direct instrumental name query."""
    query = filter_query_words(name)
    if not query or len(query) < MIN_BROWSE_LENGTH:
        return [], None
    items, effective_query = await _browse_instrumental(name)
    if not items:
        return [], None
    candidates = _match_instrumental_items(items, effective_query)
    if not candidates:
        return [], None
    audio = [i for i in candidates if file_ext(i) in (".mp3", ".wav") and i.get("path")]
    preferred = [i for i in audio if str(i.get("path", "")).startswith("Instrumentals/")]
    found = []
    found_ext = None
    for item in (preferred or audio):
        url = downloadUrl(str(item.get("path")))
        if url and url not in found:
            found.append(url)
            found_ext = found_ext or file_ext(item)
    return found, found_ext


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
    show_details = mode not in ("leak", "snippets", "ogfile", "session", "instrumental")
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
    elif mode == "instrumental":
        inst_names = parse_instrumentals(song)
        if inst_names:
            main_cont.add_separator(divider=True)
            btn_row = [InstButton(song)]
            inst_urls, _ = await fetch_instrumental_urls(song)
            if inst_urls:
                btn_row.append(Button(label="\u200b", style=ButtonStyle.link, url=inst_urls[0]))
            main_cont.add_item(ActionRow(*btn_row))
        else:
            main_cont.add_separator(divider=True, spacing=SeparatorSpacingSize.small)
            main_cont.add_text(f"{emojis.fail} No instrumental listed for this track.")
    elif mode == "session":
        session_file = await find_session_file(song)
        if session_file:
            buttons = []
            if session_file.get("edit_path"):
                buttons.append(SessionEditSendButton(session_file["edit_path"]))
            if session_file.get("zip_path"):
                buttons.append(SessionZipSendButton(session_file["zip_path"]))
            if buttons:
                main_cont.add_item(ActionRow(*buttons))
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
                view.stop()
                await interaction.edit_original_response(view=new_view)
        dropdown = createSongDropdown(matches, user_id, "Choose a song...", on_select)
        view.add_item(ActionRow(dropdown))
    return view


def not_found_text(mode, query):
    names = {"leak": "audio file", "snippets": "snippet files", "session": "session files", "instrumental": "instrumental", "info": "results"}
    return f"{emojis.fail} No {names.get(mode, 'results')} found for **{query}**."


def build_not_found_view(query, matches, user_id, select_callback):
    """Build the 'Not Found' disambiguation view: 'Did you mean **X**?' confirm button + dropdown.
    select_callback(interaction, song) is called after deferring — use edit_original_response or followup."""
    best = matches[0]
    best_titles = best.get("track_titles") or [best.get("name", "Unknown")]
    main_title = best_titles[0] if best_titles else best.get("name", "Unknown")
    alt_titles = best_titles[1:] if len(best_titles) > 1 else []
    cont = createContainer(title="Not Found", description=None, color=None)
    suggestion = f"I couldn't find one exact match for `{query}`.\nDid you mean **{main_title}**?"
    if alt_titles:
        suggestion += f"\n-# ({', '.join(alt_titles[:3])})"
    cont.add_text(suggestion)
    confirm_button = Button(label=f"Yes, show {main_title}", style=ButtonStyle.gray, custom_id=f"confirm_{best.get('public_id', 0)}_{user_id}")
    view = createView(cont, viewClass=PersistentSongView)

    async def confirm_cb(interaction):
        if interaction.user.id != user_id:
            return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
        await interaction.response.defer()
        view.stop()
        await select_callback(interaction, best)

    confirm_button.callback = confirm_cb
    cont.add_item(ActionRow(confirm_button))
    cont.add_text("## OR")
    cont.add_text(f"Select from **{len(matches)}** matches below")

    async def on_select(interaction, song_id):
        if interaction.user.id != user_id:
            return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
        await interaction.response.defer()
        selected = next((s for s in matches if str(s.get("public_id")) == str(song_id)), None)
        if selected:
            await select_callback(interaction, selected)
            view.stop()

    dropdown = createSongDropdown(matches, user_id, placeholder="Choose a song...", callbackFunc=on_select)
    cont.add_item(ActionRow(dropdown))
    return view


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


async def browse_files(query=None, *, path=None):
    if path is not None:
        url = f"{endpoints.browse.replace('search=', 'path=')}{urllib.parse.quote(path)}"
    else:
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


async def fetch_snippet_urls(song):
    # ponytail: browse search returns Snippets/ directories, not the .mp4/.mov inside;
    # recurse via ?path= listing. Ceiling: one extra request per Snippets/ dir per query.
    urls = []
    found_ext = None
    for query in song_queries(song):
        items = await browse_files(query)
        for item in items:
            item_path = str(item.get("path", ""))
            if not item_path.startswith("Snippets/"):
                continue
            contents = await browse_files(path=item_path) if item.get("type") == "directory" else [item]
            for f in contents:
                if file_ext(f) in (".mp4", ".mov") and f.get("path"):
                    url = downloadUrl(str(f.get("path")))
                    if url not in urls:
                        urls.append(url)
                        found_ext = found_ext or file_ext(f)
        if urls:
            return urls, found_ext
    return [], None


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
        # ponytail: total=300 gives large files room to download; sock_read=30 catches dead connections
        # without aborting a slow-but-progressing transfer. The old total=60 caused empty "Error:" on big files.
        timeout = aiohttp.ClientTimeout(total=300, sock_read=30, sock_connect=10)
        async with session.get(url, timeout=timeout) as response:
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
    except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
        await interaction.followup.send(
            embed=discord.Embed(description=f"Timed out downloading the {kind} file. Try again or use the link below.", color=colors.red),
            ephemeral=True,
        )
        return False
    except Exception as error:
        msg = str(error).strip() or type(error).__name__
        await interaction.followup.send(
            embed=discord.Embed(description=f"⚠️ Error: {msg}", color=colors.red),
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
            urls, found_ext = await fetch_snippet_urls(self.song)
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


class InstButton(discord.ui.Button):
    def __init__(self, song):
        super().__init__(emoji="🎹", label="Instrumental", style=discord.ButtonStyle.gray)
        self.song = song
        self.stream_urls = []
        self.found_ext = None

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True, invisible=False)
        if not self.stream_urls:
            urls, found_ext = await fetch_instrumental_urls(self.song)
            self.stream_urls = list(dict.fromkeys(urls))
            self.found_ext = found_ext
        if not self.stream_urls:
            await interaction.followup.send(
                embed=discord.Embed(description="Couldn't find the instrumental :(", color=colors.main),
                ephemeral=True,
            )
            return
        ext = self.found_ext or ".mp3"
        kind = "MP3" if ext == ".mp3" else "WAV"
        filename = os.path.basename(urllib.parse.unquote(self.stream_urls[0]))
        await send_file(interaction, self.stream_urls[0], filename, kind)
        self.stream_urls = []
        self.found_ext = None


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


def era_folder_matches(era_name, path):
    """Check if the era folder (2nd path component) matches the song's era."""
    if not era_name or not path:
        return False
    full_era = ALBUM_MAPPING.get(era_name.strip()) or era_name.strip()
    parts = str(path).split("/")
    if len(parts) < 2:
        return False
    return normalizeText(full_era) in normalizeText(parts[1])


async def find_best_session_asset(query, *, kind):
    matched_song = None
    results = findClosestMatch(stripVersionMarkers(query), listAll=True)
    if results and isinstance(results, dict):
        matched_song = results.get("best")
    search_terms = build_session_search_terms(query, matched_song)
    if not search_terms:
        return [], matched_song
    candidates_by_path = {}
    for term in search_terms:
        items = await fetchBrowseItems(term)
        for item in items:
            path = str(item.get("path", "")).strip()
            ext = str(item.get("extension", "")).lower()
            if not path:
                continue
            if kind == "zip":
                if ext != ".zip" or not path.startswith("Studio Sessions/"):
                    continue
                score = score_candidate(item, search_terms, require_sessions_hint=True)
                # ponytail: prefer ProTools/Album Deliverables over Multi-Track Stems/Trackouts
                path_lower = path.lower()
                if "protools" in path_lower or "album deliverables" in path_lower:
                    score += 5
            else:
                if not path.startswith("Session Edits/"):
                    continue
                score = score_candidate(item, search_terms, require_sessions_hint=False)
            existing = candidates_by_path.get(path)
            if existing is None or score > existing[0]:
                candidates_by_path[path] = (score, item)
    if not candidates_by_path:
        return [], matched_song
    # ponytail: filter by era folder when song has era info; ceiling: if era data is wrong, returns nothing
    era_name = None
    if matched_song:
        era = matched_song.get("era")
        if isinstance(era, dict):
            era_name = era.get("name")
        elif era:
            era_name = str(era)
    if era_name:
        era_filtered = {p: v for p, v in candidates_by_path.items() if era_folder_matches(era_name, p)}
        if era_filtered:
            candidates_by_path = era_filtered
    sorted_paths = [p for p, _ in sorted(candidates_by_path.items(), key=lambda e: e[1][0], reverse=True)]
    return sorted_paths, matched_song


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
