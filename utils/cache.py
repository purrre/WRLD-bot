import asyncio
import hashlib
import json
import re
import time
from datetime import datetime

import redis.asyncio as aioredis

import config
from utils.functions import httpcall, consoleLog

keyPrefix = "WRLDDB"

store = {}
meta = {}

redisClient = None
def getRedis():
    global redisClient
    if redisClient is None:
        redisClient = aioredis.from_url(config.settings.redis_url, decode_responses=True, protocol=2)
    return redisClient

async def closeRedis():
    global redisClient
    if redisClient is not None:
        await redisClient.aclose()
        redisClient = None

def normalizeText(text):
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())

def computeHash(data):
    dumped = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

def makeKey(*parts):
    raw = ":".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{keyPrefix}:v:{digest}"

def dataKey(name):
    return f"{keyPrefix}:data:{name}"

def metaKey(name):
    return f"{keyPrefix}:meta:{name}"

class Cache:
    @staticmethod
    def getData(name, default=None):
        return store.get(name, default)

    @staticmethod
    def getMeta(name):
        return meta.get(name, {})

    @staticmethod
    def metadata(name):
        return meta.get(name, {})

    @staticmethod
    def getMetadata(name):
        return meta.get(name, {})

    @staticmethod
    def getItemId(item):
        for key in ("public_id", "id", "song_id"):
            if key in item:
                return item.get(key)
        return None

    @staticmethod
    def matchesValue(value, expected):
        if isinstance(value, str) and isinstance(expected, str):
            return value.lower() == expected.lower()
        return value == expected

    @classmethod
    def matchesId(cls, value, expected):
        if cls.matchesValue(value, expected):
            return True
        if value is None or expected is None:
            return False
        return str(value) == str(expected)

    @classmethod
    def get(cls, name, itemId=None, **filters):
        data = cls.getData(name)
        if data is None:
            return None
        if itemId is None and not filters:
            return data
        if isinstance(data, dict):
            if itemId is not None:
                return data.get(str(itemId))
            if not filters:
                return data
            results = []
            for key, value in data.items():
                if isinstance(value, dict) and all(cls.matchesValue(value.get(fk), fv) for fk, fv in filters.items()):
                    item = value.copy()
                    item.setdefault("song_id", key)
                    results.append(item)
            return results
        if not isinstance(data, list):
            return data
        results = data
        if itemId is not None:
            return next((item for item in results if cls.matchesId(cls.getItemId(item), itemId)), None)
        for fk, fv in filters.items():
            results = [item for item in results if cls.matchesValue(item.get(fk), fv)]
        return results

    @classmethod
    def search(cls, query, name="songs", field="name"):
        if not query:
            return []
        data = cls.getData(name)
        if not data:
            return []
        q = query.lower()
        if isinstance(data, dict):
            results = []
            for key, value in data.items():
                if isinstance(value, dict) and q in str(value.get(field, "")).lower():
                    item = value.copy()
                    item.setdefault("song_id", key)
                    results.append(item)
            return results
        if not isinstance(data, list):
            return []
        return [item for item in data if q in str(item.get(field, "")).lower()]

    @classmethod
    def getSongs(cls, songId=None, name=None, category=None):
        songs = cls.getData("songs")
        if songs is None:
            return None
        if songId is not None:
            return next((s for s in songs if cls.matchesId(s.get("public_id"), songId)), None)
        if name:
            return next((s for s in songs if cls.matchesValue(s.get("name", ""), name)), None)
        if category:
            return cls.get("songs", category=category)
        return songs

    @classmethod
    def getAlbums(cls, albumId=None, name=None):
        albums = cls.getData("albums")
        if albums is None:
            return None
        if albumId is not None:
            return next((a for a in albums if cls.matchesId(a.get("id"), albumId)), None)
        if name:
            return next((a for a in albums if cls.matchesValue(a.get("name", ""), name)), None)
        return albums

    @classmethod
    def getEras(cls, eraId=None, name=None):
        eras = cls.getData("eras")
        if eras is None:
            return None
        if eraId is not None:
            return next((e for e in eras if cls.matchesId(e.get("id"), eraId)), None)
        if name:
            return next((e for e in eras if cls.matchesValue(e.get("name", ""), name)), None)
        return eras

    @classmethod
    def getLyrics(cls, songId=None):
        return cls.get("lyrics", itemId=songId)

    @classmethod
    def searchLyrics(cls, songName):
        return cls.search(songName, name="lyrics")

    @classmethod
    def searchSongs(cls, query):
        return cls.search(query, name="songs")

    @classmethod
    def getTitles(cls):
        return cls.get("titles")

    @classmethod
    def getGroupbuys(cls):
        data = cls.get("groupbuys")
        return data if isinstance(data, dict) else None

    @classmethod
    def getGroupbuyEntries(cls):
        entries = (cls.getGroupbuys() or {}).get("entries", [])
        return entries if isinstance(entries, list) else []

    @classmethod
    def getGroupbuyStats(cls):
        stats = (cls.getGroupbuys() or {}).get("yearly_stats", {})
        return stats if isinstance(stats, dict) else {}

    @classmethod
    def getGroupbuyYearlyStats(cls):
        return cls.getGroupbuyStats()

    @staticmethod
    async def remember(parts, ttl, factory):
        #return cache, store, and return
        key = makeKey(*parts)
        r = getRedis()
        try:
            cached = await r.get(key)
            if cached is not None:
                return json.loads(cached)
        except Exception:
            pass
        value = await factory()
        if value is not None:
            try:
                await r.set(key, json.dumps(value), ex=ttl)
            except Exception:
                pass
        return value

    @staticmethod
    async def invalidate(*parts):
        try:
            await getRedis().delete(makeKey(*parts))
        except Exception:
            pass


class CacheSync:
    async def fetchWithRetry(self, url, paginate):
        attempts = config.settings.sync_retries
        backoff = config.settings.sync_backoff
        result = (False, None)
        for attempt in range(1, attempts + 1):
            result = await httpcall(url, paginate=paginate)
            if result[0]:
                return result
            if attempt < attempts:
                wait = backoff * attempt
                consoleLog("CACHE", f"fetch failed for {url} ({attempt}/{attempts}), retrying in {wait}s", type="warning")
                await asyncio.sleep(wait)
        return result

    async def readMeta(self, name):
        try:
            raw = await getRedis().get(metaKey(name))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def writeData(self, name, data, metadata):
        r = getRedis()
        try:
            await r.set(dataKey(name), json.dumps(data, ensure_ascii=False))
            await r.set(metaKey(name), json.dumps(metadata, ensure_ascii=False))
        except Exception as e:
            consoleLog("CACHE", f"redis write failed for {name}: {e}", type="error")
        store[name] = data
        meta[name] = metadata

    async def syncEndpoint(self, name, url, paginate=True):
        consoleLog("CACHE", f"fetching {name}...")
        start = time.perf_counter()
        oldMeta = await self.readMeta(name)
        success, data = await self.fetchWithRetry(url, paginate=paginate)
        if not success:
            consoleLog("CACHE", f"failed to fetch {name}", type="error")
            return None
        dataHash = computeHash(data)
        if oldMeta and oldMeta.get("data_hash") == dataHash:
            # nothing changed
            if name not in store:
                await self.hydrateOne(name)
            consoleLog("CACHE", f"{name} unchanged")
            return data
        count = len(data) if isinstance(data, list) else 1
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            count = len(data["entries"])
        metadata = {
            "last_cached": datetime.now().isoformat(),
            "count": count,
            "url": url,
            "data_hash": dataHash,
        }
        await self.writeData(name, data, metadata)
        consoleLog("CACHE", f"saved {name} ({count} items) in {time.perf_counter() - start:.2f}s")
        return data

    def extractLyrics(self, songs):
        out = {}
        for song in songs:
            pid = song.get("public_id")
            lyrics = song.get("lyrics")
            if pid and lyrics:
                out[str(pid)] = {"name": song.get("name", "Unknown"), "lyrics": lyrics}
        return out

    def extractTitles(self, songs):
        canonicalMap = {}
        for song in songs:
            main = song.get("name")
            if not main:
                continue
            aliases = {main}
            for t in song.get("track_titles", []):
                if t:
                    aliases.add(t)
            if song.get("file_names"):
                aliases.add(song["file_names"])
            norm = normalizeText(main)
            if norm not in canonicalMap:
                canonicalMap[norm] = {"canonical": main, "aliases": list(aliases), "normalized_canonical": norm}
            else:
                merged = set(canonicalMap[norm]["aliases"])
                merged.update(aliases)
                canonicalMap[norm]["aliases"] = list(merged)
        return list(canonicalMap.values())

    async def buildDerived(self, name, builder, songs, songsHash):
        oldMeta = await self.readMeta(name)
        if oldMeta and oldMeta.get("songs_hash") == songsHash:
            if name not in store:
                await self.hydrateOne(name)
            return
        consoleLog("CACHE", f"songs changed, rebuilding {name}")
        data = builder(songs)
        metadata = {"last_cached": datetime.now().isoformat(), "count": len(data), "songs_hash": songsHash}
        await self.writeData(name, data, metadata)

    async def syncAll(self):
        consoleLog("CACHE", "starting refresh...")
        start = time.perf_counter()
        await asyncio.gather(
            self.syncEndpoint("eras", config.endpoints.eras),
            self.syncEndpoint("songs", config.endpoints.songs),
            self.syncEndpoint("albums", config.endpoints.albums),
            self.syncEndpoint("groupbuys", config.endpoints.groupbuys, paginate=False),
        )
        songs = store.get("songs")
        if not songs:
            consoleLog("CACHE", "no songs data available", type="error")
            return
        songsHash = computeHash(songs)
        await asyncio.gather(
            self.buildDerived("titles", self.extractTitles, songs, songsHash),
            self.buildDerived("lyrics", self.extractLyrics, songs, songsHash),
        )
        consoleLog("CACHE", f"refresh done in {time.perf_counter() - start:.2f}s")

    async def hydrateOne(self, name):
        try:
            raw = await getRedis().get(dataKey(name))
            if raw is None:
                return False
            store[name] = json.loads(raw)
            meta[name] = await self.readMeta(name) or {}
            return True
        except Exception as e:
            consoleLog("CACHE", f"hydrate failed for {name}: {e}", type="error")
            return False

    async def hydrate(self):
        names = ("songs", "albums", "eras", "groupbuys", "titles", "lyrics")
        await asyncio.gather(*[self.hydrateOne(n) for n in names])
        return bool(store.get("songs"))


class CachePoller:
    def __init__(self, manager, interval=None):
        self.manager = manager
        self.interval = interval if interval is not None else config.settings.sync_interval
        self.task = None

    async def pollLoop(self):
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.manager.syncAll()
            except Exception as e:
                consoleLog("CACHE", f"poll failed: {e}", type="error")

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.pollLoop())
            consoleLog("CACHE", f"auto-poll started (interval={self.interval}s)")

    def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            self.task = None


cache = Cache()
cache_manager = CacheSync()

async def syncAll():
    await cache_manager.syncAll()

async def ensureCache():
    hasData = await cache_manager.hydrate()
    if not hasData:
        consoleLog("CACHE", "redis cold, running full sync")
        await cache_manager.syncAll()


def startPoller(interval=None):
    poller = CachePoller(cache_manager, interval=interval)
    poller.start()
    return poller