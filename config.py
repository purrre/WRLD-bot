import os

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = os.path.dirname(os.path.abspath(__file__))

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    dev_bot_token: str = ""
    dev_mode: bool = False
    dev_prefix: str = "!"
    default_prefix: str = ","

    admin_users: str = ""

    database_url: str = "sqlite+aiosqlite:///data/WRLDDB.sqlite"
    redis_url: str = "redis://localhost:6379/0"

    db_host: str = ""
    db_user: str = ""
    db_password: str = ""
    db_name: str = ""
    db_port: int = 3306

    gemini_api_key: str = ""
    ai_model: str = "gemini-2.0-flash"

    sync_enabled: bool = True
    sync_interval: int = 300
    sync_retries: int = 3
    sync_backoff: int = 2

    error_channel_id: int = 0

    @property
    def token(self):
        return self.dev_bot_token if self.dev_mode else self.bot_token

    @property
    def prefix(self):
        return self.dev_prefix if self.dev_mode else self.default_prefix

    @property
    def admin_ids(self):
        return {part.strip() for part in self.admin_users.split(",") if part.strip()}


settings = Settings()

NOT_YOURS = "<:crazy:1473823921961959631> This is not yours"
EMOJI_MAPPING_PATH = os.path.join(ROOT, "data", "emoji-mapping.json")

class endpoints:
    jwa = "https://juicewrldapi.com"
    media = "https://m.juicewrldapi.com"
    wrld = "https://wrld.pure0.lol"

    # juicewrldapi
    songs = f"{jwa}/juicewrld/songs/"
    eras = f"{jwa}/juicewrld/eras/"
    albums = f"{jwa}/juicewrld/albums/"
    plays_stats = f"{jwa}/juicewrld/plays/stats/"
    files = f"{jwa}/files/"
    browse = f"{jwa}/juicewrld/files/browse/?search="
    download = f"{jwa}/juicewrld/files/download/?path="
    cover_art = f"{jwa}/juicewrld/files/cover-art/?path="
    media_status = f"{media}/status/"
    now_playing = f"{media}/analytics/now-playing/discord/?discord_user_id="

    # my own services
    covers = f"{wrld}/api/covers/"
    covers_health = f"{wrld}/api/covers/health"
    groupbuys = f"{wrld}/api/groupbuys"


class colors:
    red = 0xBB2124
    green = 0x22BB33
    main = 0x6A0DAD
    main_suc = 0x912ADB
    main_fail = 0x4A1174


class emojis:
    # unicode fallbacks
    fail = "❌"
    success = "✅"
    loading = "⏳"
    minus = "➖"
    plus = "➕"
    list = "📋"
    crown = "👑"
    web = "🌐"
    mod = "🛡️"
    thumbsup = "👍"
    thumbsdown = "👎"
    search = "🔍"
    info = "ℹ️"
    vinyl = "💿"
    beta_check = "✔️"
    beta_hello = "👋"
    w1 = "⚪"
    w2 = "⚪"
    w3 = "⚪"
    g1 = "🟢"
    g2 = "🟢"
    g3 = "🟢"

    names = {
        "loading", "success", "fail", "minus", "plus", "list",
        "crown", "web", "mod", "thumbsup", "thumbsdown", "search",
        "info", "vinyl", "beta_check", "beta_hello",
        "w1", "w2", "w3", "g1", "g2", "g3",
    }

    @classmethod
    async def load(cls, bot):
        fetched = await bot.fetch_emojis()
        emoji_map = {e.name: e for e in fetched}
        for name in cls.names:
            emoji = emoji_map.get(name)
            if emoji is None:
                print(f"[!] emoji '{name}' not found")
            else:
                setattr(cls, name, emoji)

    @classmethod
    def get(cls, name, default=None):
        return getattr(cls, name, default)
