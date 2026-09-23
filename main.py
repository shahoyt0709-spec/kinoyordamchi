# -*- coding: utf-8 -*-

"""
Professional Telegram Movie / Series Media Management Bot
----------------------------------------------------------
Single-file project: main.py
"""

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List

from telegram import Update
from telegram.constants import ParseMode, ForumIconColor
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError, BadRequest, Forbidden, RetryAfter


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("8998476657:AAFlqY444CFw5IsVCoAgavHajLasZsRkX_c", "8998476657:AAFlqY444CFw5IsVCoAgavHajLasZsRkX_c")

OWNER_ID = 6975146118
GROUP_ID = -1003535011408
SERVER_MEDIA_DIR = "./media"
DATABASE_FILE = "./media_manager.db"
TEMP_DIR = "./temp"
CHANNEL_USERNAME = "https://t.me/+UvkuWW91FsdhNDQy"

DELETE_SOURCE_AFTER_COPY = False

MIN_WIDTH = 1920
MIN_HEIGHT = 1080

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

SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("MediaManagerBot")


# ============================================================
# GLOBAL DATABASE CONNECTION
# ============================================================

db: Optional[sqlite3.Connection] = None


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
    logger.info("SQLite database initialized successfully.")


def db_execute(query: str, params=(), fetchone=False, fetchall=False):
    try:
        cursor = db.execute(query, params)
        db.commit()
        if fetchone:
            return cursor.fetchone()
        if fetchall:
            return cursor.fetchall()
        return cursor
    except sqlite3.Error as e:
        logger.error(f"Database query failed: {query} | Error: {e}")
        raise e


# ============================================================
# CONFIG CHECK
# ============================================================

def validate_config():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.warning("BOT_TOKEN is not configured via environment variables.")
    Path(SERVER_MEDIA_DIR).mkdir(parents=True, exist_ok=True)
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)


# ============================================================
# AUTHORIZATION & ACCESS CONTROL
# ============================================================

def is_owner(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == OWNER_ID


async def owner_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not is_owner(update):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ Sizda bu botdan foydalanish huquqi yo‘q.")
        return False
    return True


# ============================================================
# FILENAME PARSER
# ============================================================

def clean_title(text: str) -> str:
    text = text.replace("_", " ").replace(".", " ")
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(
        r"\b(?:WEB[-_. ]?DL|WEB[-_. ]?RIP|BLU[-_. ]?RAY|BDRIP|HDRIP|DVDRIP|REMUX|PROPER|REPACK|LIMITED|EXTENDED|UNCUT|COMPLETE)\b",
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
        "turkish": "Turkcha",
        "tr": "Turkcha",
    }

    result = []
    for item in audio_values:
        key = item.lower().strip()
        result.append(mapping.get(key, item.capitalize()))

    unique = []
    for item in result:
        if item not in unique:
            unique.append(item)

    return ", ".join(unique)


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

    series_match = re.search(r"(?i)(?:^|[\s._-])S(\d{1,3})E(\d{1,4})(?:[\s._-]|$)", stem)
    if not series_match:
        series_match = re.search(r"(?i)(?:^|[\s._-])(\d{1,3})x(\d{1,4})(?:[\s._-]|$)", stem)

    if series_match:
        result["type"] = "series"
        result["season"] = int(series_match.group(1))
        result["episode"] = int(series_match.group(2))

        title_part = stem[:series_match.start()]
        if not title_part.strip():
            title_part = stem
        result["title"] = clean_title(title_part)
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
            val = match if isinstance(match, str) else match[0]
            if val:
                audio_values.append(val)

    result["audio"] = format_audio_name(audio_values)
    return result


# ============================================================
# FFPROBE HELPERS
# ============================================================

def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


async def run_ffprobe(path: Path) -> Optional[Dict[str, Any]]:
    if not ffprobe_available():
        return None

    def _run():
        command = [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
        return subprocess.run(command, capture_output=True, text=True, timeout=120)

    try:
        process = await asyncio.to_thread(_run)
        if process.returncode != 0:
            logger.error(f"ffprobe execution error: {process.stderr[:500]}")
            return None
        return json.loads(process.stdout)
    except Exception as e:
        logger.error(f"Failed to execute ffprobe: {e}")
        return None


def extract_media_metadata(probe: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    result = {
        "width": None,
        "height": None,
        "resolution": None,
        "audio_tracks": [],
    }
    if not probe:
        return result

    streams = probe.get("streams", [])
    for stream in streams:
        if stream.get("codec_type") == "video" and not result["width"]:
            result["width"] = stream.get("width")
            result["height"] = stream.get("height")
            if result["width"] and result["height"]:
                result["resolution"] = f"{result['width']}x{result['height']}"

        elif stream.get("codec_type") == "audio":
            tags = stream.get("tags", {})
            lang = tags.get("language") or tags.get("LANGUAGE") or tags.get("title") or tags.get("handler_name")
            if lang:
                result["audio_tracks"].append(str(lang))

    return result


def resolution_is_acceptable(width: Optional[int], height: Optional[int]) -> bool:
    if not width or not height:
        return False
    return (width >= MIN_WIDTH and height >= MIN_HEIGHT) or (width >= MIN_HEIGHT and height >= MIN_WIDTH)


# ============================================================
# DB OPERATIONS & CAPTION BUILDER
# ============================================================

def get_or_create_series(title: str, year: Optional[int] = None):
    row = db_execute("SELECT * FROM series WHERE lower(title) = lower(?)", (title,), fetchone=True)
    if row:
        return row
    cursor = db_execute("INSERT INTO series (title, year) VALUES (?, ?)", (title, year))
    return db_execute("SELECT * FROM series WHERE id = ?", (cursor.lastrowid,), fetchone=True)


def get_or_create_season(series_id: int, season_number: int):
    row = db_execute("SELECT * FROM seasons WHERE series_id = ? AND season_number = ?", (series_id, season_number), fetchone=True)
    if row:
        return row
    cursor = db_execute("INSERT INTO seasons (series_id, season_number) VALUES (?, ?)", (series_id, season_number))
    return db_execute("SELECT * FROM seasons WHERE id = ?", (cursor.lastrowid,), fetchone=True)


async def get_or_create_topic(bot, entity_type: str, entity_id: int, title: str) -> Optional[int]:
    existing = db_execute("SELECT * FROM topics WHERE entity_type = ? AND entity_id = ?", (entity_type, entity_id), fetchone=True)
    if existing:
        return existing["telegram_topic_id"]

    try:
        topic_title = html.escape(title[:128].strip())
        topic = await bot.create_forum_topic(chat_id=GROUP_ID, name=topic_title, icon_color=ForumIconColor.BLUE)
        telegram_topic_id = topic.message_thread_id
        db_execute(
            "INSERT OR REPLACE INTO topics (entity_type, entity_id, title, telegram_topic_id) VALUES (?, ?, ?, ?)",
            (entity_type, entity_id, topic_title, telegram_topic_id),
        )
        return telegram_topic_id
    except TelegramError as e:
        logger.error(f"Failed to create forum topic for {title}: {e}")
        return None


def build_caption(parsed: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None, media_meta: Optional[Dict[str, Any]] = None) -> str:
    title = html.escape(parsed.get("title") or "Noma'lum")
    lines = [f"🎬 <b>{title.upper()}</b>", ""]

    if parsed["type"] == "series":
        season = parsed.get("season")
        episode = parsed.get("episode")
        if season is not None and episode is not None:
            lines.append(f"📺 {season}-fasl | {episode}-qism")
        if parsed.get("episode_title"):
            lines.append(f"🏷 <b>{html.escape(parsed['episode_title'])}</b>")
    elif parsed.get("year"):
        lines.append(f"📅 Yil: {parsed['year']}")

    quality = parsed.get("quality") or "1080p"
    lines.append(f"🎞 Sifat: {html.escape(quality)}")

    audio = parsed.get("audio")
    if media_meta and media_meta.get("audio_tracks"):
        audio = format_audio_name(media_meta["audio_tracks"])
    lines.append(f"🔊 Audio: {html.escape(audio or 'Noma\'lum')}")
    lines.append(f"💾 Format: {html.escape(parsed.get('format') or 'MKV')}")

    if metadata:
        if metadata.get("genre"):
            lines.append(f"🎭 Janr: {html.escape(metadata['genre'])}")
        if metadata.get("country"):
            lines.append(f"🌍 Mamlakat: {html.escape(metadata['country'])}")
        if metadata.get("description"):
            lines.extend(["", f"📝 {html.escape(metadata['description'])}"])

    lines.extend(["", "━━━━━━━━━━━━━━", f"📌 Kanal: {html.escape(CHANNEL_USERNAME)}", "━━━━━━━━━━━━━━"])
    return "\n".join(lines)[:1024]


async def calculate_sha256(path: Path) -> str:
    def _calc():
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()

    return await asyncio.to_thread(_calc)


# ============================================================
# PROCESSING LOGIC
# ============================================================

async def send_local_media(bot, path: Path, topic_id: int, caption: str):
    extension = path.suffix.lower()
    with open(path, "rb") as file:
        if extension == ".mp4":
            return await bot.send_video(
                chat_id=GROUP_ID,
                video=file,
                caption=caption,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
                supports_streaming=True,
            )
        return await bot.send_document(
            chat_id=GROUP_ID,
            document=file,
            caption=caption,
            parse_mode=ParseMode.HTML,
            message_thread_id=topic_id,
        )


async def process_local_file(path: Path, bot) -> Dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Fayl topilmadi: {path}")

    if not ffprobe_available():
        raise RuntimeError("FFprobe tizimda o'rnatilmagan.")

    probe = await run_ffprobe(path)
    if not probe:
        raise RuntimeError("FFprobe faylni tahlil qila olmadi.")

    media_meta = extract_media_metadata(probe)
    if not resolution_is_acceptable(media_meta.get("width"), media_meta.get("height")):
        raise ValueError(f"Fayl resolution 1080p dan past: {media_meta.get('resolution')}")

    parsed = parse_filename(path.name)
    source_key = await calculate_sha256(path)

    if parsed["type"] == "series":
        dup = db_execute("SELECT id FROM episodes WHERE source_key = ?", (source_key,), fetchone=True)
        if dup:
            return {"status": "duplicate"}

        series = get_or_create_series(parsed["title"], parsed.get("year"))
        topic_id = await get_or_create_topic(bot, "series", series["id"], series["title"])
        season = get_or_create_season(series["id"], parsed["season"])

        dup_ep = db_execute(
            "SELECT id FROM episodes WHERE series_id = ? AND season_id = ? AND episode_number = ?",
            (series["id"], season["id"], parsed["episode"]),
            fetchone=True,
        )
        if dup_ep:
            return {"status": "duplicate"}

        meta_row = db_execute("SELECT * FROM metadata WHERE entity_type = 'series' AND entity_id = ?", (series["id"],), fetchone=True)
        metadata_dict = dict(meta_row) if meta_row else None
        caption = build_caption(parsed, metadata_dict, media_meta)

        sent = await send_local_media(bot, path, topic_id, caption)

        db_execute(
            """
            INSERT INTO episodes (series_id, season_id, episode_number, episode_title, quality, audio, format, filename, source_key, topic_id, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (series["id"], season["id"], parsed["episode"], parsed.get("episode_title"), "1080p", parsed.get("audio"), parsed.get("format"), path.name, source_key, topic_id, sent.message_id),
        )
        return {"status": "added"}
    else:
        dup = db_execute("SELECT id FROM movies WHERE source_key = ?", (source_key,), fetchone=True)
        if dup:
            return {"status": "duplicate"}

        cursor = db_execute(
            "INSERT INTO movies (title, year, quality, audio, format, filename, source_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (parsed["title"], parsed.get("year"), "1080p", parsed.get("audio"), parsed.get("format"), path.name, source_key),
        )
        movie_id = cursor.lastrowid
        topic_id = await get_or_create_topic(bot, "movie", movie_id, parsed["title"])
        db_execute("UPDATE movies SET topic_id = ? WHERE id = ?", (topic_id, movie_id))

        caption = build_caption(parsed, None, media_meta)
        sent = await send_local_media(bot, path, topic_id, caption)
        return {"status": "added"}


async def process_telegram_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    message = update.effective_message
    if not message:
        return

    document = message.document
    video = message.video
    if not document and not video:
        return

    file_obj = document or video
    file_id = file_obj.file_id
    file_unique_id = file_obj.file_unique_id
    filename = getattr(file_obj, "file_name", None) or "media.mkv"

    dup_movie = db_execute("SELECT id FROM movies WHERE telegram_file_unique_id = ?", (file_unique_id,), fetchone=True)
    dup_ep = db_execute("SELECT id FROM episodes WHERE telegram_file_unique_id = ?", (file_unique_id,), fetchone=True)

    if dup_movie or dup_ep:
        await message.reply_text("⚠️ Bu fayl avval yuborilgan (Duplicate).")
        return

    parsed = parse_filename(filename)
    media_meta = {"width": getattr(video, "width", None), "height": getattr(video, "height", None), "audio_tracks": []}

    temp_path = Path(TEMP_DIR) / f"{file_unique_id}_{filename}"
    try:
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=str(temp_path))

        if ffprobe_available():
            probe = await run_ffprobe(temp_path)
            if probe:
                media_meta = extract_media_metadata(probe)

        if not resolution_is_acceptable(media_meta.get("width"), media_meta.get("height")):
            await message.reply_text(f"❌ Fayl sifat talabiga javob bermaydi (1080p emas). Resolution: {media_meta.get('resolution')}")
            return

        if parsed["type"] == "series":
            series = get_or_create_series(parsed["title"], parsed.get("year"))
            topic_id = await get_or_create_topic(context.bot, "series", series["id"], series["title"])
            season = get_or_create_season(series["id"], parsed["season"])

            meta_row = db_execute("SELECT * FROM metadata WHERE entity_type = 'series' AND entity_id = ?", (series["id"],), fetchone=True)
            metadata_dict = dict(meta_row) if meta_row else None
            caption = build_caption(parsed, metadata_dict, media_meta)

            sent = await context.bot.copy_message(
                chat_id=GROUP_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
                message_thread_id=topic_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )

            db_execute(
                """
                INSERT INTO episodes (series_id, season_id, episode_number, episode_title, quality, audio, format, filename, telegram_file_id, telegram_file_unique_id, topic_id, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (series["id"], season["id"], parsed["episode"], parsed.get("episode_title"), "1080p", parsed.get("audio"), parsed.get("format"), filename, file_id, file_unique_id, topic_id, sent.message_id),
            )
            await message.reply_text(f"✅ Serial qismi muvaffaqiyatli saqlandi: S{parsed['season']:02d}E{parsed['episode']:02d}")
        else:
            cursor = db_execute(
                "INSERT INTO movies (title, year, quality, audio, format, filename, telegram_file_id, telegram_file_unique_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (parsed["title"], parsed.get("year"), "1080p", parsed.get("audio"), parsed.get("format"), filename, file_id, file_unique_id),
            )
            movie_id = cursor.lastrowid
            topic_id = await get_or_create_topic(context.bot, "movie", movie_id, parsed["title"])
            db_execute("UPDATE movies SET topic_id = ? WHERE id = ?", (topic_id, movie_id))

            caption = build_caption(parsed, None, media_meta)
            await context.bot.copy_message(
                chat_id=GROUP_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
                message_thread_id=topic_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            await message.reply_text("✅ Film muvaffaqiyatli saqlandi.")

    except TelegramError as te:
        logger.error(f"Telegram API Error: {te}")
        await message.reply_text(f"❌ Telegram API Xatosi: {te.message}")
    except Exception as e:
        logger.exception("Media yuklashda xatolik")
        await message.reply_text(f"❌ Xatolik yuz berdi: {e}")
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


# ============================================================
# COMMAND HANDLERS
# ============================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    await update.effective_message.reply_text("🎬 <b>Media Manager Bot</b> ishga tushdi.\n\n/help - barcha buyruqlar.", parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    text = (
        "<b>Boshqaruv buyruqlari:</b>\n"
        "/start - Botni ishga tushirish\n"
        "/status - MB holati va statistika\n"
        "/addmovie Title | Year - Film qo'shish\n"
        "/addseries Title - Serial qo'shish\n"
        "/addmetadata Title | Desc | Genre | Year | Country - Metadata kiritish\n"
        "/admin_metadata - Metadata boshqaruvi\n"
        "/list - Medialar ro'yxati\n"
        "/delete movie/series ID - O'chirish\n"
        "/rebuild - Topic va DB ni qayta sinxronlash\n"
        "/reload - DB ulanishini yangilash\n"
        "/settings - Sozlamalarni ko'rish\n"
        "/scan - Serverdagi papkani skanerlash"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    m_count = db_execute("SELECT COUNT(*) as count FROM movies", fetchone=True)["count"]
    s_count = db_execute("SELECT COUNT(*) as count FROM series", fetchone=True)["count"]
    e_count = db_execute("SELECT COUNT(*) as count FROM episodes", fetchone=True)["count"]

    await update.effective_message.reply_text(
        f"📊 <b>Statistika:</b>\n🎬 Filmlar: {m_count}\n📺 Seriallar: {s_count}\n▶️ Qismlar: {e_count}",
        parse_mode=ParseMode.HTML,
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    msg = f"<b>Sozlamalar:</b>\nOWNER_ID: <code>{OWNER_ID}</code>\nGROUP_ID: <code>{GROUP_ID}</code>\nDIR: <code>{SERVER_MEDIA_DIR}</code>"
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def addseries_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    text = update.effective_message.text.replace("/addseries", "").strip()
    if not text:
        await update.effective_message.reply_text("Format: /addseries Title")
        return
    series = get_or_create_series(text)
    topic_id = await get_or_create_topic(context.bot, "series", series["id"], series["title"])
    await update.effective_message.reply_text(f"✅ Serial yaratildi/topildi: {series['title']} (Topic ID: {topic_id})")


async def addmovie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    raw = update.effective_message.text.replace("/addmovie", "").strip()
    parts = [p.strip() for p in raw.split("|")]
    if not parts or not parts[0]:
        await update.effective_message.reply_text("Format: /addmovie Title | Year")
        return

    title = parts[0]
    year = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    cursor = db_execute("INSERT INTO movies (title, year) VALUES (?, ?)", (title, year))
    movie_id = cursor.lastrowid
    topic_id = await get_or_create_topic(context.bot, "movie", movie_id, title)
    db_execute("UPDATE movies SET topic_id = ? WHERE id = ?", (topic_id, movie_id))
    await update.effective_message.reply_text(f"✅ Film bazaga qo'shildi: {title}")


async def addmetadata_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    raw = re.sub(r"^/(addmetadata|admin_metadata)", "", update.effective_message.text).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 5:
        await update.effective_message.reply_text("Format: /addmetadata Title | Description | Genre | Year | Country")
        return

    title, desc, genre, year_str, country = parts[:5]
    year = int(year_str) if year_str.isdigit() else None

    series = get_or_create_series(title, year)
    db_execute(
        """
        INSERT INTO metadata (entity_type, entity_id, description, genre, year, country, updated_at)
        VALUES ('series', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(entity_type, entity_id) DO UPDATE SET
            description=excluded.description,
            genre=excluded.genre,
            year=excluded.year,
            country=excluded.country,
            updated_at=CURRENT_TIMESTAMP
        """,
        (series["id"], desc, genre, year, country),
    )
    await update.effective_message.reply_text(f"✅ Metadata saqlandi: {title}")


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    movies = db_execute("SELECT id, title, year FROM movies LIMIT 10", fetchall=True)
    series = db_execute("SELECT id, title, year FROM series LIMIT 10", fetchall=True)

    text = "<b>🎬 Filmlar:</b>\n" + "\n".join([f"{m['id']}. {m['title']} ({m['year'] or 'N/A'})" for m in movies])
    text += "\n\n<b>📺 Seriallar:</b>\n" + "\n".join([f"{s['id']}. {s['title']} ({s['year'] or 'N/A'})" for s in series])
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    parts = update.effective_message.text.split()
    if len(parts) < 3 or parts[1] not in ["movie", "series"]:
        await update.effective_message.reply_text("Format: /delete movie ID yoki /delete series ID")
        return

    target_type, target_id = parts[1], parts[2]
    table = "movies" if target_type == "movie" else "series"
    db_execute(f"DELETE FROM {table} WHERE id = ?", (target_id,))
    await update.effective_message.reply_text(f"✅ {target_type} ID={target_id} o'chirildi.")


async def rebuild_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    series_list = db_execute("SELECT * FROM series", fetchall=True)
    count = 0
    for s in series_list:
        topic_id = await get_or_create_topic(context.bot, "series", s["id"], s["title"])
        if topic_id:
            count += 1
    await update.effective_message.reply_text(f"✅ Topiclar tekshirildi va qayta tiklandi: {count} ta.")


async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    global db
    if db:
        db.close()
    init_database()
    await update.effective_message.reply_text("✅ DB ulanishi va sozlamalar qayta yuklandi.")


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update, context):
        return
    await update.effective_message.reply_text("🔍 Server media papkasi skanerlanmoqda...")

    media_dir = Path(SERVER_MEDIA_DIR)
    files = [f for f in media_dir.rglob("*") if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]

    added, duplicates, errors = 0, 0, 0
    for file_path in files:
        try:
            res = await process_local_file(file_path, context.bot)
            if res.get("status") == "added":
                added += 1
            elif res.get("status") == "duplicate":
                duplicates += 1
        except Exception as e:
            logger.error(f"Scan error for {file_path.name}: {e}")
            errors += 1

    await update.effective_message.reply_text(
        f"📊 <b>Skanerlash yakunlandi:</b>\n✅ Qo'shildi: {added}\n⚠️ Qayta o'tkazib yuborildi: {duplicates}\n❌ Xatoliklar: {errors}",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# MAIN APPLICATION BUILDER
# ============================================================

def build_application() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("addmovie", addmovie_command))
    application.add_handler(CommandHandler("addseries", addseries_command))
    application.add_handler(CommandHandler("addmetadata", addmetadata_command))
    application.add_handler(CommandHandler("admin_metadata", addmetadata_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("rebuild", rebuild_command))
    application.add_handler(CommandHandler("reload", reload_command))
    application.add_handler(CommandHandler("scan", scan_command))

    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, process_telegram_media))

    return application


def main():
    validate_config()
    init_database()

    application = build_application()
    logger.info("Bot starting polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
