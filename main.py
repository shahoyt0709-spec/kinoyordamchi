# -*- coding: utf-8 -*-

"""
Professional Telegram Movie / Series Media Management Bot
----------------------------------------------------------
Single-file project: main.py

Features:
- Owner-only administration
- Telegram Forum Topics
- SQLite database
- Movie / Series auto-detection
- Filename metadata parser
- Season / Episode detection
- Resolution validation through ffprobe
- Audio track detection through ffprobe
- Local server media scanner
- Telegram media processing
- Duplicate protection
- Local metadata without TMDB / AI
- Logging
- Async architecture
- Web server for UptimeRobot Health Checks
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List

from aiohttp import web
from telegram import Update
from telegram.constants import ParseMode, ForumIconColor
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError


# ============================================================
# CONFIGURATION
# ============================================================

# Token and Port environment variables
BOT_TOKEN = os.getenv("8998476657:AAFlqY444CFw5IsVCoAgavHajLasZsRkX_c")
PORT = int(os.getenv("PORT", 8080))

# Your Telegram numeric user ID
OWNER_ID = 6975146118

# Your forum supergroup ID
GROUP_ID = -1003535011408

# Local media folder
SERVER_MEDIA_DIR = "./media"

# SQLite database
DATABASE_FILE = "./media_manager.db"

# Temporary files
TEMP_DIR = "./temp"

# Channel shown in captions
CHANNEL_USERNAME = "https://t.me/+UvkuWW91FsdhNDQy"

# Delete original Telegram upload after successful copy?
DELETE_SOURCE_AFTER_COPY = False

# Minimum accepted resolution
MIN_WIDTH = 1920
MIN_HEIGHT = 1080

# Supported media formats
VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
    ".ts",
    ".m2ts",
}

AUDIO_EXTENSIONS = {
    ".mp3",
    ".aac",
    ".flac",
    ".wav",
    ".m4a",
}

SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("MediaManagerBot")


# ============================================================
# GLOBAL DATABASE
# ============================================================

db: Optional[sqlite3.Connection] = None


# ============================================================
# DATABASE
# ============================================================

def init_database():
    global db

    db = sqlite3.connect(
        DATABASE_FILE,
        check_same_thread=False,
    )

    db.row_factory = sqlite3.Row

    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            year INTEGER,
            quality TEXT,
            audio TEXT,
            format TEXT,
            filename TEXT,
            source_key TEXT UNIQUE,
            topic_id INTEGER,
            telegram_file_id TEXT,
            telegram_file_unique_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL UNIQUE,
            year INTEGER,
            quality TEXT,
            audio TEXT,
            format TEXT,
            topic_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            season_number INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(series_id, season_number),
            FOREIGN KEY(series_id) REFERENCES series(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            season_id INTEGER NOT NULL,
            episode_number INTEGER NOT NULL,
            episode_title TEXT,
            quality TEXT,
            audio TEXT,
            format TEXT,
            filename TEXT,
            source_key TEXT UNIQUE,
            telegram_file_id TEXT,
            telegram_file_unique_id TEXT,
            topic_id INTEGER,
            message_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(series_id, season_id, episode_number),
            FOREIGN KEY(series_id) REFERENCES series(id) ON DELETE CASCADE,
            FOREIGN KEY(season_id) REFERENCES seasons(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            telegram_topic_id INTEGER NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_type, entity_id)
        );

        CREATE TABLE IF NOT EXISTS metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            description TEXT,
            genre TEXT,
            year INTEGER,
            country TEXT,
            extra_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(entity_type, entity_id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )

    db.commit()

    logger.info("SQLite database initialized")


def db_execute(
    query: str,
    params=(),
    fetchone=False,
    fetchall=False,
):
    try:
        cursor = db.execute(query, params)
        db.commit()

        if fetchone:
            return cursor.fetchone()

        if fetchall:
            return cursor.fetchall()

        return cursor

    except sqlite3.Error:
        logger.exception("Database error")
        raise


# ============================================================
# CONFIG CHECK
# ============================================================

def validate_config():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN topilmadi. Render Environment Variables'ga "
            "BOT_TOKEN ni kiriting."
        )

    Path(SERVER_MEDIA_DIR).mkdir(
        parents=True,
        exist_ok=True,
    )

    Path(TEMP_DIR).mkdir(
        parents=True,
        exist_ok=True,
    )


# ============================================================
# AUTHORIZATION
# ============================================================

def is_owner(update: Update) -> bool:
    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


async def owner_only(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_owner(update):
        await update.effective_message.reply_text(
            "⛔ Sizda bu botdan foydalanish huquqi yo‘q."
        )
        return False

    return True


def is_allowed_group(update: Update) -> bool:
    chat = update.effective_chat

    return (
        chat is not None
        and chat.id == GROUP_ID
    )


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_title(text: str) -> str:
    text = text.replace("_", " ")
    text = text.replace(".", " ")
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)

    text = re.sub(
        r"\b(?:WEB[-_. ]?DL|WEB[-_. ]?RIP|BLU[-_. ]?RAY|"
        r"BDRIP|HDRIP|DVDRIP|REMUX|PROPER|REPACK|LIMITED|"
        r"EXTENDED|UNCUT|COMPLETE)\b",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(r"\s+", " ", text)

    return text.strip(" -_.[]")


def format_audio_name(audio_values: List[str]) -> str:
    if not audio_values:
        return "Noma'lum"

    mapping = {
        "uz": "O‘zbekcha",
        "uzb": "O‘zbekcha",
        "uzbek": "O‘zbekcha",
        "russian": "Ruscha",
        "rus": "Ruscha",
        "ru": "Ruscha",
        "english": "Inglizcha",
        "eng": "Inglizcha",
        "en": "Inglizcha",
        "ukrainian": "Ukraincha",
        "ukr": "Ukraincha",
        "tr": "Turkcha",
        "turkish": "Turkcha",
    }

    result = []

    for item in audio_values:
        key = item.lower().strip()

        if key in mapping:
            result.append(mapping[key])
        else:
            result.append(item)

    unique = []

    for item in result:
        if item not in unique:
            unique.append(item)

    return ", ".join(unique)


# ============================================================
# FILENAME PARSER
# ============================================================

def parse_filename(filename: str) -> Dict[str, Any]:
    original = Path(filename).name
    stem = Path(original).stem
    extension = Path(original).suffix.lower()

    result = {
        "type": "movie",
        "title": "",
        "year": None,
        "season": None,
        "episode": None,
        "episode_title": None,
        "quality": None,
        "audio": "Noma'lum",
        "format": extension.replace(".", "").upper() if extension else "Noma'lum",
        "filename": original,
    }

    series_match = re.search(
        r"(?i)(?:^|[\s._-])S(\d{1,3})E(\d{1,4})(?:[\s._-]|$)",
        stem,
    )

    if not series_match:
        series_match = re.search(
            r"(?i)(?:^|[\s._-])(\d{1,3})x(\d{1,4})(?:[\s._-]|$)",
            stem,
        )

    if series_match:
        result["type"] = "series"
        result["season"] = int(series_match.group(1))
        result["episode"] = int(series_match.group(2))

        title_part = stem[:series_match.start()]
        if not title_part.strip():
            title_part = stem

        result["title"] = clean_title(title_part)

        remaining = stem[series_match.end():]
        remaining = re.sub(r"^[\s._-]+", "", remaining)

        stop_pattern = re.compile(
            r"(?i)"
            r"(1080p|2160p|1440p|720p|480p|"
            r"WEB[-_. ]?DL|WEB[-_. ]?RIP|"
            r"BLU[-_. ]?RAY|"
            r"UZ|UZB|UZBEK|"
            r"RUS|RU|RUSSIAN|"
            r"ENG|EN|ENGLISH|"
            r"DUB|DUBBED|"
            r"HDR|HDR10|DV|"
            r"H264|H265|HEVC|X264|X265)",
        )

        stop_match = stop_pattern.search(remaining)
        episode_title = remaining[:stop_match.start()] if stop_match else remaining
        episode_title = clean_title(episode_title)

        if episode_title:
            result["episode_title"] = episode_title
    else:
        year_match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", stem)
        if year_match:
            result["year"] = int(year_match.group(1))
            title_part = stem[:year_match.start()]
        else:
            title_part = stem

        result["title"] = clean_title(title_part)

    if result["year"] is None:
        year_match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", stem)
        if year_match:
            result["year"] = int(year_match.group(1))

    quality_match = re.search(r"(?i)\b(2160p|1440p|1080p|720p|480p|360p)\b", stem)
    if quality_match:
        result["quality"] = quality_match.group(1).lower()

    audio_values = []
    audio_patterns = [
        r"(?i)\b(UZB?|UZBEK)\b",
        r"(?i)\b(RU|RUS|RUSSIAN)\b",
        r"(?i)\b(EN|ENG|ENGLISH)\b",
        r"(?i)\b(TR|TURKISH)\b",
    ]

    for pattern in audio_patterns:
        for match in re.findall(pattern, stem):
            if isinstance(match, tuple):
                for value in match:
                    if value:
                        audio_values.append(value)
            else:
                audio_values.append(match)

    if re.search(r"(?i)\bDUB(?:BED)?\b", stem):
        if "UZ" in stem.upper() or "UZB" in stem.upper():
            audio_values.append("UZ Dub")
        else:
            audio_values.append("Dub")

    result["audio"] = format_audio_name(audio_values)
    return result


# ============================================================
# FFMPEG / FFPROBE
# ============================================================

def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


async def run_ffprobe(path: Path) -> Optional[Dict[str, Any]]:
    if not ffprobe_available():
        return None

    def _run():
        command = [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
        )

    try:
        process = await asyncio.to_thread(_run)
        if process.returncode != 0:
            logger.error("ffprobe error: %s", process.stderr[:1000])
            return None
        return json.loads(process.stdout)
    except Exception:
        logger.exception("Unexpected ffprobe error")
        return None


def extract_media_metadata(probe: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    result = {
        "width": None,
        "height": None,
        "resolution": None,
        "audio_tracks": [],
        "duration": None,
        "format_name": None,
    }

    if not probe:
        return result

    streams = probe.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if video_streams:
        video = video_streams[0]
        width = video.get("width")
        height = video.get("height")
        if width and height:
            result["width"] = width
            result["height"] = height
            result["resolution"] = f"{width}x{height}"

    for audio in audio_streams:
        tags = audio.get("tags", {})
        language = (
            tags.get("language")
            or tags.get("LANGUAGE")
            or tags.get("title")
            or tags.get("handler_name")
        )
        if language:
            result["audio_tracks"].append(str(language))

    format_data = probe.get("format", {})
    result["duration"] = format_data.get("duration")
    result["format_name"] = format_data.get("format_name")

    return result


# ============================================================
# QUALITY VALIDATION
# ============================================================

def resolution_is_acceptable(width: Optional[int], height: Optional[int]) -> bool:
    if not width or not height:
        return False
    if width >= 1920 and height >= 1080:
        return True
    if width >= 1080 and height >= 1920:
        return True
    return False


def quality_from_resolution(width: Optional[int], height: Optional[int]) -> Optional[str]:
    if not width or not height:
        return None
    long_side = max(width, height)
    short_side = min(width, height)

    if long_side >= 3800 and short_side >= 2000:
        return "2160p"
    if long_side >= 2500 and short_side >= 1400:
        return "1440p"
    if long_side >= 1920 and short_side >= 1080:
        return "1080p"
    if long_side >= 1280 and short_side >= 720:
        return "720p"
    return f"{width}x{height}"


# ============================================================
# DATABASE - SERIES / MOVIES / TOPICS / METADATA
# ============================================================

def get_series(title: str):
    return db_execute(
        "SELECT * FROM series WHERE lower(title) = lower(?)",
        (title,),
        fetchone=True,
    )


def create_series(title: str, year: Optional[int] = None):
    existing = get_series(title)
    if existing:
        return existing

    cursor = db_execute(
        "INSERT INTO series(title, year) VALUES (?, ?)",
        (title, year),
    )
    return db_execute(
        "SELECT * FROM series WHERE id = ?",
        (cursor.lastrowid,),
        fetchone=True,
    )


def get_movie_by_source(source_key: str):
    return db_execute(
        "SELECT * FROM movies WHERE source_key = ?",
        (source_key,),
        fetchone=True,
    )


def get_episode_by_source(source_key: str):
    return db_execute(
        "SELECT * FROM episodes WHERE source_key = ?",
        (source_key,),
        fetchone=True,
    )


def get_topic(entity_type: str, entity_id: int):
    return db_execute(
        "SELECT * FROM topics WHERE entity_type = ? AND entity_id = ?",
        (entity_type, entity_id),
        fetchone=True,
    )


def save_topic(entity_type: str, entity_id: int, title: str, telegram_topic_id: int):
    db_execute(
        """
        INSERT OR REPLACE INTO topics
        (entity_type, entity_id, title, telegram_topic_id)
        VALUES (?, ?, ?, ?)
        """,
        (entity_type, entity_id, title, telegram_topic_id),
    )


def get_or_create_season(series_id: int, season_number: int):
    row = db_execute(
        "SELECT * FROM seasons WHERE series_id = ? AND season_number = ?",
        (series_id, season_number),
        fetchone=True,
    )
    if row:
        return row

    cursor = db_execute(
        "INSERT INTO seasons(series_id, season_number) VALUES (?, ?)",
        (series_id, season_number),
    )
    return db_execute(
        "SELECT * FROM seasons WHERE id = ?",
        (cursor.lastrowid,),
        fetchone=True,
    )


def episode_exists(series_id: int, season_id: int, episode_number: int):
    return db_execute(
        "SELECT * FROM episodes WHERE series_id = ? AND season_id = ? AND episode_number = ?",
        (series_id, season_id, episode_number),
        fetchone=True,
    )


def get_metadata(entity_type: str, entity_id: int):
    return db_execute(
        "SELECT * FROM metadata WHERE entity_type = ? AND entity_id = ?",
        (entity_type, entity_id),
        fetchone=True,
    )


def save_metadata(entity_type: str, entity_id: int, description: str, genre: str, year: Optional[int], country: str):
    db_execute(
        """
        INSERT INTO metadata (entity_type, entity_id, description, genre, year, country, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(entity_type, entity_id) DO UPDATE SET
            description=excluded.description,
            genre=excluded.genre,
            year=excluded.year,
            country=excluded.country,
            updated_at=CURRENT_TIMESTAMP
        """,
        (entity_type, entity_id, description, genre, year, country),
    )


# ============================================================
# TOPIC MANAGEMENT & CAPTION & HASH
# ============================================================

async def get_or_create_topic(bot, entity_type: str, entity_id: int, title: str) -> Optional[int]:
    existing = get_topic(entity_type, entity_id)
    if existing:
        return existing["telegram_topic_id"]

    try:
        topic_title = title[:128].strip()
        topic = await bot.create_forum_topic(
            chat_id=GROUP_ID,
            name=topic_title,
            icon_color=ForumIconColor.BLUE,
        )
        topic_id = topic.message_thread_id
        save_topic(entity_type, entity_id, topic_title, topic_id)
        logger.info("Created forum topic: %s -> %s", topic_title, topic_id)
        return topic_id
    except TelegramError:
        logger.exception("Forum topic creation error")
        return None


def get_metadata_for_series(series_id: int, parsed: Dict[str, Any]):
    metadata = get_metadata("series", series_id)
    if metadata:
        return {
            "description": metadata["description"],
            "genre": metadata["genre"],
            "year": metadata["year"] or parsed.get("year"),
            "country": metadata["country"],
        }
    return {
        "description": None,
        "genre": None,
        "year": parsed.get("year"),
        "country": None,
    }


def build_caption(parsed: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None, media_meta: Optional[Dict[str, Any]] = None) -> str:
    title = parsed.get("title") or "Noma'lum"
    lines = [f"🎬 <b>{title.upper()}</b>", ""]

    if parsed["type"] == "series":
        season = parsed.get("season")
        episode = parsed.get("episode")
        if season is not None and episode is not None:
            lines.append(f"📺 {season}-fasl | {episode}-qism")
        if parsed.get("episode_title"):
            lines.append(f"🏷 <b>{parsed['episode_title']}</b>")
    elif parsed.get("year"):
        lines.append(f"📅 Yil: {parsed['year']}")

    quality = parsed.get("quality")
    if media_meta:
        detected_quality = quality_from_resolution(media_meta.get("width"), media_meta.get("height"))
        if detected_quality:
            quality = detected_quality

    lines.append(f"🎞 Sifat: {quality or 'Nomalum'}")

    audio = parsed.get("audio")
    if media_meta and media_meta.get("audio_tracks"):
        audio = format_audio_name(media_meta["audio_tracks"])

    lines.append(f"🔊 Audio: {audio or 'Nomalum'}")
    lines.append(f"💾 Format: {parsed.get('format') or 'Nomalum'}")

    if metadata:
        if metadata.get("genre"):
            lines.append(f"🎭 Janr: {metadata['genre']}")
        if metadata.get("country"):
            lines.append(f"🌍 Mamlakat: {metadata['country']}")
        if metadata.get("description"):
            lines.extend(["", f"📝 {metadata['description']}"])

    lines.extend(["", "━━━━━━━━━━━━━━", f"📌 Kanal: {CHANNEL_USERNAME}", "━━━━━━━━━━━━━━"])
    return "\n".join(lines)[:1024]


async def calculate_sha256(path: Path) -> str:
    def _calculate():
        hasher = hashlib.sha256()
        with open(path, "rb") as file:
            while True:
                chunk = file.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    return await asyncio.to_thread(_calculate)


# ============================================================
# LOCAL & TELEGRAM MEDIA PROCESSING
# ============================================================

async def process_local_file(path: Path, bot) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Fayl topilmadi: {path}")
    if not path.is_file():
        raise ValueError(f"Bu fayl emas: {path}")

    extension = path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Qo'llab-quvvatlanmaydigan format: {extension}")

    parsed = parse_filename(path.name)
    source_key = await calculate_sha256(path)

    duplicate = get_episode_by_source(source_key) if parsed["type"] == "series" else get_movie_by_source(source_key)
    if duplicate:
        return {"status": "duplicate", "parsed": parsed, "path": path}

    if not ffprobe_available():
        raise RuntimeError("ffprobe topilmadi.")

    probe = await run_ffprobe(path)
    if not probe:
        raise RuntimeError("ffprobe media metadata'ni o'qiy olmadi.")

    media_meta = extract_media_metadata(probe)

    if extension in VIDEO_EXTENSIONS:
        if not resolution_is_acceptable(media_meta.get("width"), media_meta.get("height")):
            resolution = media_meta.get("resolution", "noma'lum")
            raise ValueError(f"❌ Bu fayl 1080p emas.\nResolution: {resolution}")

        detected_quality = quality_from_resolution(media_meta.get("width"), media_meta.get("height"))
        if detected_quality:
            parsed["quality"] = detected_quality

    if media_meta.get("audio_tracks"):
        parsed["audio"] = format_audio_name(media_meta["audio_tracks"])

    if parsed["type"] == "series":
        if parsed["season"] is None or parsed["episode"] is None:
            raise ValueError("Serial faylida season yoki episode aniqlanmadi.")

        series = create_series(parsed["title"], parsed.get("year"))
        topic_id = await get_or_create_topic(bot, "series", series["id"], series["title"])
        if not topic_id:
            raise RuntimeError("Serial uchun forum topic yaratib bo'lmadi.")

        season = get_or_create_season(series["id"], parsed["season"])
        if episode_exists(series["id"], season["id"], parsed["episode"]):
            return {"status": "episode_duplicate", "parsed": parsed, "path": path}

        metadata_row = get_metadata_for_series(series["id"], parsed)
        caption = build_caption(parsed, metadata_row, media_meta)

        previous_episode = db_execute(
            "SELECT * FROM episodes WHERE series_id = ? AND season_id = ? LIMIT 1",
            (series["id"], season["id"]),
            fetchone=True,
        )

        if not previous_episode:
            await bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                text=f"━━━━━━━━━━━━━━\n📁 <b>S{parsed['season']:02d}</b>\n━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML,
            )

        sent_message = await send_local_media(bot, path, topic_id, caption)

        db_execute(
            """
            INSERT INTO episodes
            (series_id, season_id, episode_number, episode_title, quality, audio, format, filename, source_key, topic_id, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                series["id"],
                season["id"],
                parsed["episode"],
                parsed.get("episode_title"),
                parsed.get("quality"),
                parsed.get("audio"),
                parsed.get("format"),
                path.name,
                source_key,
                topic_id,
                sent_message.message_id,
            ),
        )

        return {"status": "added", "type": "series", "parsed": parsed, "path": path, "topic_id": topic_id}

    movie_title = parsed["title"]
    existing_movie = db_execute(
        "SELECT * FROM movies WHERE lower(title) = lower(?) AND COALESCE(year, 0) = COALESCE(?, 0)",
        (movie_title, parsed.get("year")),
        fetchone=True,
    )

    if existing_movie:
        topic_id = existing_movie["topic_id"]
    else:
        cursor = db_execute(
            "INSERT INTO movies (title, year, quality, audio, format, filename, source_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (movie_title, parsed.get("year"), parsed.get("quality"), parsed.get("audio"), parsed.get("format"), path.name, source_key),
        )
        movie_id = cursor.lastrowid
        topic_id = await get_or_create_topic(bot, "movie", movie_id, movie_title)
        if not topic_id:
            db_execute("DELETE FROM movies WHERE id = ?", (movie_id,))
            raise RuntimeError("Film uchun forum topic yaratib bo'lmadi.")

        db_execute("UPDATE movies SET topic_id = ? WHERE id = ?", (topic_id, movie_id))

    caption = build_caption(parsed, None, media_meta)
    sent_message = await send_local_media(bot, path, topic_id, caption)

    if existing_movie:
        db_execute(
            """
            INSERT OR IGNORE INTO movies (title, year, quality, audio, format, filename, source_key, topic_id, telegram_file_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                movie_title,
                parsed.get("year"),
                parsed.get("quality"),
                parsed.get("audio"),
                parsed.get("format"),
                path.name,
                source_key,
                topic_id,
                getattr(sent_message, "document", None).file_id if getattr(sent_message, "document", None) else None,
            ),
        )

    return {"status": "added", "type": "movie", "parsed": parsed, "path": path, "topic_id": topic_id}


def get_message_filename(message) -> Optional[str]:
    if message.document:
        return message.document.file_name or "unknown.mkv"
    if message.video:
        return getattr(message.video, "file_name", None) or "unknown.mp4"
    return None


def get_message_file_ids(message):
    if message.document:
        return message.document.file_id, message.document.file_unique_id
    if message.video:
        return message.video.file_id, message.video.file_unique_id
    return None, None


async def try_download_telegram_media(bot, message, filename: str) -> Optional[Path]:
    file_id, _ = get_message_file_ids(message)
    if not file_id:
        return None

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    destination = Path(TEMP_DIR) / f"{message.message_id}_{safe_name}"

    try:
        telegram_file = await bot.get_file(file_id)
        await telegram_file.download_to_drive(custom_path=str(destination))
        return destination
    except Exception:
        logger.exception("Telegram file download failed")
        return None


async def process_telegram_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update) or not is_allowed_group(update):
        return

    message = update.effective_message
    if not message:
        return

    filename = get_message_filename(message)
    if not filename:
        return

    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        await message.reply_text(f"❌ Qo‘llab-quvvatlanmaydigan format: {extension or 'nomaʼlum'}")
        return

    parsed = parse_filename(filename)
    file_id, file_unique_id = get_message_file_ids(message)
    if not file_unique_id:
        file_unique_id = file_id

    duplicate_movie = db_execute("SELECT * FROM movies WHERE telegram_file_unique_id = ?", (file_unique_id,), fetchone=True)
    duplicate_episode = db_execute("SELECT * FROM episodes WHERE telegram_file_unique_id = ?", (file_unique_id,), fetchone=True)

    if duplicate_movie or duplicate_episode:
        await message.reply_text("⚠️ Bu fayl avval database'ga qo‘shilgan.")
        return

    media_meta = {"width": None, "height": None, "resolution": None, "audio_tracks": []}
    if message.video:
        media_meta["width"] = message.video.width
        media_meta["height"] = message.video.height
        if message.video.width and message.video.height:
            media_meta["resolution"] = f"{message.video.width}x{message.video.height}"

    temp_path = None
    try:
        temp_path = await try_download_telegram_media(context.bot, message, filename)
        if temp_path and ffprobe_available():
            probe = await run_ffprobe(temp_path)
            if probe:
                detected = extract_media_metadata(probe)
                if detected.get("width"):
                    media_meta["width"] = detected["width"]
                if detected.get("height"):
                    media_meta["height"] = detected["height"]
                media_meta["resolution"] = detected.get("resolution")
                media_meta["audio_tracks"] = detected.get("audio_tracks", [])
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    if extension in VIDEO_EXTENSIONS:
        width = media_meta.get("width")
        height = media_meta.get("height")

        if width and height:
            if not resolution_is_acceptable(width, height):
                await message.reply_text(f"❌ Bu fayl 1080p emas.\nResolution: {media_meta.get('resolution', 'nomaʼlum')}")
                return
            parsed["quality"] = quality_from_resolution(width, height)
        elif not parsed.get("quality"):
            await message.reply_text("❌ Video resolutionini aniqlab bo‘lmadi.")
            return
        elif parsed.get("quality") in {"720p", "480p", "360p"}:
            await message.reply_text("❌ Bu fayl 1080p emas.")
            return

    if media_meta.get("audio_tracks"):
        parsed["audio"] = format_audio_name(media_meta["audio_tracks"])

    if parsed["type"] == "series":
        if parsed["season"] is None or parsed["episode"] is None:
            await message.reply_text("❌ Season yoki Episode aniqlanmadi.")
            return

        series = create_series(parsed["title"], parsed.get("year"))
        topic_id = await get_or_create_topic(context.bot, "series", series["id"], series["title"])
        if not topic_id:
            await message.reply_text("❌ Serial topicini yaratib bo‘lmadi.")
            return

        season = get_or_create_season(series["id"], parsed["season"])
        if episode_exists(series["id"], season["id"], parsed["episode"]):
            await message.reply_text("⚠️ Bu serial qismi database'da mavjud.")
            return

        metadata = get_metadata_for_series(series["id"], parsed)
        caption = build_caption(parsed, metadata, media_meta)

        previous_episode = db_execute(
            "SELECT * FROM episodes WHERE series_id = ? AND season_id = ? LIMIT 1",
            (series["id"], season["id"]),
            fetchone=True,
        )

        if not previous_episode:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                message_thread_id=topic_id,
                text=f"━━━━━━━━━━━━━━\n📁 <b>S{parsed['season']:02d}</b>\n━━━━━━━━━━━━━━",
                parse_mode=ParseMode.HTML,
            )

        sent = await context.bot.copy_message(
            chat_id=GROUP_ID,
            from_chat_id=GROUP_ID,
            message_id=message.message_id,
            message_thread_id=topic_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )

        db_execute(
            """
            INSERT INTO episodes
            (series_id, season_id, episode_number, episode_title, quality, audio, format, filename, source_key, telegram_file_id, telegram_file_unique_id, topic_id, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                series["id"],
                season["id"],
                parsed["episode"],
                parsed.get("episode_title"),
                parsed.get("quality"),
                parsed.get("audio"),
                parsed.get("format"),
                filename,
                f"telegram:{file_unique_id}",
                file_id,
                file_unique_id,
                topic_id,
                sent.message_id,
            ),
        )

        if DELETE_SOURCE_AFTER_COPY:
            try:
                await context.bot.delete_message(chat_id=GROUP_ID, message_id=message.message_id)
            except TelegramError:
                pass

        await message.reply_text(
            f"✅ Serial qismi qo‘shildi.\n📁 {series['title']}\n📺 S{parsed['season']:02d}E{parsed['episode']:02d}"
        )
        return

    cursor = db_execute(
        """
        INSERT INTO movies (title, year, quality, audio, format, filename, source_key, telegram_file_id, telegram_file_unique_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            parsed["title"],
            parsed.get("year"),
            parsed.get("quality"),
            parsed.get("audio"),
            parsed.get("format"),
            filename,
            f"telegram:{file_unique_id}",
            file_id,
            file_unique_id,
        ),
    )
    movie_id = cursor.lastrowid
    topic_id = await get_or_create_topic(context.bot, "movie", movie_id, parsed["title"])

    if not topic_id:
        db_execute("DELETE FROM movies WHERE id = ?", (movie_id,))
        await message.reply_text("❌ Film topicini yaratib bo‘lmadi.")
        return

    db_execute("UPDATE movies SET topic_id = ? WHERE id = ?", (topic_id, movie_id))
    caption = build_caption(parsed, None, media_meta)

    await context.bot.copy_message(
        chat_id=GROUP_ID,
        from_chat_id=GROUP_ID,
        message_id=message.message_id,
        message_thread_id=topic_id,
        caption=caption,
        parse_mode=ParseMode.HTML,
    )

    if DELETE_SOURCE_AFTER_COPY:
        try:
            await context.bot.delete_message(chat_id=GROUP_ID, message_id=message.message_id)
        except TelegramError:
            pass

    await message.reply_text(f"✅ Film qo‘shildi.\n🎬 {parsed['title']}")


async def send_local_media(bot, path: Path, topic_id: int, caption: str):
    extension = path.suffix.lower()
    if extension == ".mp4":
        with open(path, "rb") as file:
            return await bot.send_video(
                chat_id=GROUP_ID,
                video=file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
                supports_streaming=True,
            )

    with open(path, "rb") as file:
        return await bot.send_document(
            chat_id=GROUP_ID,
            document=file,
            caption=caption,
            parse_mode=ParseMode.HTML,
            message_thread_id=topic_id,
        )


# ============================================================
# COMMAND HANDLERS
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    await update.effective_message.reply_text(
        "🎬 <b>Media Manager Bot</b>\n\n/help — komandalar\n/status — bot holati",
        parse_mode=ParseMode.HTML,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    text = "🎬 <b>MEDIA MANAGER BOT</b>\n\n/start\n/help\n/status\n/addmovie\n/addseries\n/addmetadata\n/list\n/delete\n/rebuild\n/reload\n/settings\n/scan"
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    movie_count = db_execute("SELECT COUNT(*) AS c FROM movies", fetchone=True)["c"]
    series_count = db_execute("SELECT COUNT(*) AS c FROM series", fetchone=True)["c"]
    episode_count = db_execute("SELECT COUNT(*) AS c FROM episodes", fetchone=True)["c"]
    topic_count = db_execute("SELECT COUNT(*) AS c FROM topics", fetchone=True)["c"]

    await update.effective_message.reply_text(
        f"📊 <b>BOT STATUS</b>\n\n🎬 Movies: {movie_count}\n📺 Series: {series_count}\n▶️ Episodes: {episode_count}\n💬 Topics: {topic_count}",
        parse_mode=ParseMode.HTML,
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    await update.effective_message.reply_text(
        f"⚙️ <b>SETTINGS</b>\n\nOWNER_ID: <code>{OWNER_ID}</code>\nGROUP_ID: <code>{GROUP_ID}</code>\nPORT: <code>{PORT}</code>",
        parse_mode=ParseMode.HTML,
    )


async def addseries_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    text = update.effective_message.text or ""
    args = text.split(maxsplit=1)
    if len(args) < 2:
        await update.effective_message.reply_text("Format: /addseries The Walking Dead")
        return
    title = args[1].strip()
    series = create_series(title)
    topic_id = await get_or_create_topic(context.bot, "series", series["id"], series["title"])
    await update.effective_message.reply_text(f"✅ Serial qo‘shildi: {title} (Topic ID: {topic_id})")


async def addmovie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    text = update.effective_message.text or ""
    args = text.split(maxsplit=1)
    if len(args) < 2:
        await update.effective_message.reply_text("Format: /addmovie Interstellar | 2014")
        return
    data = [x.strip() for x in args[1].split("|")]
    title = data[0]
    year = int(data[1]) if len(data) > 1 and data[1].isdigit() else None

    cursor = db_execute("INSERT INTO movies(title, year) VALUES (?, ?)", (title, year))
    movie_id = cursor.lastrowid
    topic_id = await get_or_create_topic(context.bot, "movie", movie_id, title)
    if topic_id:
        db_execute("UPDATE movies SET topic_id = ? WHERE id = ?", (topic_id, movie_id))

    await update.effective_message.reply_text(f"✅ Film qo‘shildi: {title}")


async def addmetadata_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    text = update.effective_message.text or ""
    args = text.split(maxsplit=1)
    if len(args) < 2:
        await update.effective_message.reply_text("Format: /addmetadata Title | Description | Genre | Year | Country")
        return
    parts = [x.strip() for x in args[1].split("|")]
    if len(parts) < 5:
        await update.effective_message.reply_text("❌ 5 ta qiymat kerak.")
        return

    title, description, genre, year_text, country = parts[:5]
    year = int(year_text) if year_text.isdigit() else None

    series = get_series(title) or create_series(title, year)
    save_metadata("series", series["id"], description, genre, year, country)
    await update.effective_message.reply_text(f"✅ Metadata saqlandi: {title}")


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    movies = db_execute("SELECT title, year FROM movies ORDER BY id DESC LIMIT 15", fetchall=True)
    series = db_execute("SELECT title, year FROM series ORDER BY id DESC LIMIT 15", fetchall=True)

    lines = ["📚 <b>DATABASE</b>", "", "🎬 <b>MOVIES</b>"]
    lines.extend([f"• {m['title']} ({m['year'] or '?'})" for m in movies] if movies else ["• Hozircha film yo‘q."])
    lines.extend(["", "📺 <b>SERIES</b>"])
    lines.extend([f"• {s['title']} ({s['year'] or '?'})" for s in series] if series else ["• Hozircha serial yo‘q."])

    await update.effective_message.reply_text("\n".join(lines)[:4096], parse_mode=ParseMode.HTML)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    args = (update.effective_message.text or "").split(maxsplit=2)
    if len(args) < 3 or not args[2].isdigit():
        await update.effective_message.reply_text("Format: /delete movie 123")
        return

    entity_type, entity_id = args[1].lower(), int(args[2])
    if entity_type in ["movie", "series"]:
        db_execute(f"DELETE FROM {entity_type}s WHERE id = ?", (entity_id,))
        await update.effective_message.reply_text("✅ O‘chirildi.")
    else:
        await update.effective_message.reply_text("❌ Faqat movie yoki series.")


async def rebuild_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    rows = db_execute("SELECT * FROM series", fetchall=True)
    repaired = 0
    for series in rows:
        if not get_topic("series", series["id"]):
            if await get_or_create_topic(context.bot, "series", series["id"], series["title"]):
                repaired += 1
    await update.effective_message.reply_text(f"✅ Rebuild tugadi. Topiclar tiklandi: {repaired}")


async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    global db
    if db:
        db.close()
    init_database()
    await update.effective_message.reply_text("✅ Database qayta ulandi.")


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    media_dir = Path(SERVER_MEDIA_DIR)
    media_dir.mkdir(parents=True, exist_ok=True)

    files = [p for p in media_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
    if not files:
        await update.effective_message.reply_text("📁 Fayllar topilmadi.")
        return

    added, duplicate, failed = 0, 0, 0
    for path in files:
        try:
            res = await process_local_file(path, context.bot)
            if res["status"] in {"duplicate", "episode_duplicate"}:
                duplicate += 1
            else:
                added += 1
        except Exception:
            failed += 1

    await update.effective_message.reply_text(
        f"📊 <b>SCAN NATIJASI</b>\n\n✅ Added: {added}\n⚠️ Duplicate: {duplicate}\n❌ Failed: {failed}",
        parse_mode=ParseMode.HTML,
    )


async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await process_telegram_media(update, context)
    except Exception as exc:
        logger.exception("Media handler xatosi")
        if update.effective_message:
            await update.effective_message.reply_text(f"❌ Xato: {exc}")


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled bot exception", exc_info=context.error)


# ============================================================
# AIOHTTP SERVER FOR UPTIMEROBOT
# ============================================================

async def handle_ping(request):
    return web.Response(text="Bot is alive!", status=200)


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Aiohttp web server started on port %s", PORT)


# ============================================================
# APPLICATION & MAIN ASYNC RUNNER
# ============================================================

def build_application() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("addmovie", addmovie_command))
    application.add_handler(CommandHandler("addseries", addseries_command))
    application.add_handler(CommandHandler(["addmetadata", "admin_metadata"], addmetadata_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("rebuild", rebuild_command))
    application.add_handler(CommandHandler("reload", reload_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("scan", scan_command))

    application.add_handler(MessageHandler(filters.VIDEO, media_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, media_handler))

    application.add_error_handler(global_error_handler)
    return application


async def main():
    logger.info("Starting Media Manager Bot...")

    validate_config()
    init_database()

    application = build_application()

    # Bot serverini initsializatsiya qilish
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)

    # Web serverni ishga tushirish
    await start_web_server()

    logger.info("Bot and Web Server are running...")

    # Ikkala servis doimiy ishlashini ta'minlash
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
