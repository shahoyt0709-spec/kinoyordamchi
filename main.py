# -*- coding: utf-8 -*-

import asyncio
import hashlib
import html
import logging
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

from aiohttp import web

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("8998476657:AAFlqY444CFw5IsVCoAgavHajLasZsRkX_c", "").strip()
OWNER_ID = int(os.getenv("6975146118", "0"))
GROUP_ID = int(os.getenv("-1003535011408", "0"))
PORT = int(os.getenv("PORT", "10000"))

DB_PATH = os.getenv("DB_PATH", "movies.db")
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media"))

MIN_WIDTH = 1920
MIN_HEIGHT = 1080

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("KinoYordamchi")

# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False,
)

db.row_factory = sqlite3.Row

db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA foreign_keys=ON")


def init_database():
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            year INTEGER,
            quality TEXT,
            audio TEXT,
            video_codec TEXT,
            format TEXT,
            filename TEXT,
            file_unique_id TEXT,
            file_id TEXT,
            sha256 TEXT,
            topic_id INTEGER,
            telegram_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            year INTEGER,
            topic_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(title, year)
        );

        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            season_number INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(series_id, season_number),
            FOREIGN KEY(series_id)
                REFERENCES series(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            season_number INTEGER NOT NULL,
            episode_number INTEGER NOT NULL,
            episode_title TEXT,
            quality TEXT,
            audio TEXT,
            video_codec TEXT,
            format TEXT,
            filename TEXT,
            file_unique_id TEXT,
            file_id TEXT,
            sha256 TEXT,
            topic_id INTEGER,
            telegram_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(series_id, season_number, episode_number),
            FOREIGN KEY(series_id)
                REFERENCES series(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL,
            title TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(content_type, entity_id)
        );

        CREATE TABLE IF NOT EXISTS metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sha256 TEXT UNIQUE,
            filename TEXT,
            width INTEGER,
            height INTEGER,
            duration REAL,
            video_codec TEXT,
            audio_codec TEXT,
            format TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )

    db.commit()

    logger.info("SQLite database initialized successfully.")


# =========================================================
# CONFIG VALIDATION
# =========================================================

def validate_config():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN Environment Variable topilmadi."
        )

    if OWNER_ID == 0:
        raise RuntimeError(
            "OWNER_ID Environment Variable noto'g'ri."
        )

    if GROUP_ID == 0:
        raise RuntimeError(
            "GROUP_ID Environment Variable noto'g'ri."
        )


# =========================================================
# OWNER CHECK
# =========================================================

def is_owner(update: Update) -> bool:

    user = update.effective_user

    if not user:
        return False

    return user.id == OWNER_ID


async def owner_only(update: Update) -> bool:

    if is_owner(update):
        return True

    if update.effective_message:
        await update.effective_message.reply_text(
            "❌ Sizda ushbu botdan foydalanish huquqi yo'q."
        )

    return False


# =========================================================
# FILENAME PARSER
# =========================================================

def clean_title(text: str) -> str:

    text = re.sub(
        r"\[[^\]]+\]",
        " ",
        text,
    )

    text = re.sub(
        r"\([^)]*(?:19|20)\d{2}[^)]*\)",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\b(?:S\d{1,2}E\d{1,3})\b",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\b\d{1,2}x\d{1,3}\b",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\b(?:2160p|1440p|1080p|720p|480p)\b",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\b(?:WEB[-_. ]?DL|WEB[-_. ]?RIP|BLURAY|BLU[-_. ]?RAY|BDRIP|DVDRIP|HDTV)\b",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\b(?:x264|x265|h264|h265|HEVC|AVC|AV1)\b",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\b(?:AAC|AC3|EAC3|DTS|TRUEHD|ATMOS|DDP\d?\.\d)\b",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"\b(?:MKV|MP4|AVI|MOV)\b",
        " ",
        text,
        flags=re.I,
    )

    text = re.sub(
        r"[._]+",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


def parse_filename(filename: str):

    original = Path(filename).stem

    result = {
        "title": None,
        "year": None,
        "season": None,
        "episode": None,
        "episode_title": None,
        "quality": None,
        "audio": None,
        "video_codec": None,
        "format": Path(filename).suffix.lower().replace(".", ""),
    }

    # -----------------------------------------------------
    # SEASON / EPISODE
    # -----------------------------------------------------

    match = re.search(
        r"\bS(\d{1,2})E(\d{1,3})\b",
        original,
        re.I,
    )

    if not match:

        match = re.search(
            r"\b(\d{1,2})x(\d{1,3})\b",
            original,
            re.I,
        )

    if match:

        result["season"] = int(match.group(1))
        result["episode"] = int(match.group(2))

    # -----------------------------------------------------
    # YEAR
    # -----------------------------------------------------

    year_match = re.search(
        r"\b((?:19|20)\d{2})\b",
        original,
    )

    if year_match:
        result["year"] = int(year_match.group(1))

    # -----------------------------------------------------
    # QUALITY
    # -----------------------------------------------------

    quality_patterns = [
        r"\b2160p\b",
        r"\b1440p\b",
        r"\b1080p\b",
        r"\b720p\b",
        r"\b480p\b",
    ]

    for pattern in quality_patterns:

        match = re.search(
            pattern,
            original,
            re.I,
        )

        if match:
            result["quality"] = match.group(0).lower()
            break

    # -----------------------------------------------------
    # AUDIO
    # -----------------------------------------------------

    audio_patterns = [
        r"\bTRUEHD\b",
        r"\bATMOS\b",
        r"\bEAC3\b",
        r"\bDDP\d?\.\d\b",
        r"\bAC3\b",
        r"\bDTS\b",
        r"\bAAC\b",
    ]

    for pattern in audio_patterns:

        match = re.search(
            pattern,
            original,
            re.I,
        )

        if match:
            result["audio"] = match.group(0).upper()
            break

    # -----------------------------------------------------
    # VIDEO CODEC
    # -----------------------------------------------------

    codec_patterns = [
        r"\bH\.?265\b",
        r"\bX265\b",
        r"\bHEVC\b",
        r"\bH\.?264\b",
        r"\bX264\b",
        r"\bAVC\b",
        r"\bAV1\b",
    ]

    for pattern in codec_patterns:

        match = re.search(
            pattern,
            original,
            re.I,
        )

        if match:

            codec = match.group(0).upper()

            if codec in ("X265", "H265"):
                codec = "HEVC"

            if codec in ("X264", "H264"):
                codec = "AVC"

            result["video_codec"] = codec

            break

    # -----------------------------------------------------
    # TITLE
    # -----------------------------------------------------

    title = original

    if result["year"]:
        title = re.sub(
            rf"\b{result['year']}\b",
            " ",
            title,
        )

    if result["season"] is not None:
        title = re.sub(
            r"\bS\d{1,2}E\d{1,3}\b",
            " ",
            title,
            flags=re.I,
        )

        title = re.sub(
            r"\b\d{1,2}x\d{1,3}\b",
            " ",
            title,
            flags=re.I,
        )

    title = clean_title(title)

    result["title"] = title

    # -----------------------------------------------------
    # EPISODE TITLE
    # -----------------------------------------------------

    if result["season"] is not None:

        episode_match = re.search(
            r"(?:S\d{1,2}E\d{1,3}|\d{1,2}x\d{1,3})\s*[-._ ]+\s*(.+)",
            original,
            re.I,
        )

        if episode_match:

            episode_title = clean_title(
                episode_match.group(1)
            )

            if episode_title:
                result["episode_title"] = episode_title

    return result


# =========================================================
# FFMPEG / FFPROBE
# =========================================================

async def run_ffprobe(path: str):

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration",
        "-show_entries",
        "stream=codec_type,codec_name,width,height",
        "-of",
        "json",
        path,
    ]

    try:

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:

            logger.error(
                "ffprobe error: %s",
                stderr.decode(errors="ignore"),
            )

            return None

        import json

        return json.loads(
            stdout.decode(
                errors="ignore"
            )
        )

    except FileNotFoundError:

        logger.error(
            "ffprobe topilmadi. FFmpeg o'rnatilganligini tekshiring."
        )

        return None

    except Exception as exc:

        logger.exception(
            "ffprobe failed: %s",
            exc,
        )

        return None


def extract_media_info(probe):

    if not probe:
        return {}

    streams = probe.get(
        "streams",
        [],
    )

    format_info = probe.get(
        "format",
        {},
    )

    width = None
    height = None
    video_codec = None
    audio_codec = None

    for stream in streams:

        codec_type = stream.get(
            "codec_type"
        )

        if codec_type == "video":

            width = stream.get("width")
            height = stream.get("height")
            video_codec = stream.get(
                "codec_name"
            )

        elif codec_type == "audio":

            audio_codec = stream.get(
                "codec_name"
            )

    duration = None

    try:

        duration = float(
            format_info.get(
                "duration"
            )
        )

    except Exception:
        pass

    format_name = format_info.get(
        "format_name"
    )

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "format": format_name,
    }


# =========================================================
# QUALITY
# =========================================================

def detect_quality(width, height):

    if not width or not height:
        return "Unknown"

    if width >= 3840 or height >= 2160:
        return "2160p 4K"

    if width >= 2560 or height >= 1440:
        return "1440p"

    if width >= 1920 or height >= 1080:
        return "1080p"

    return f"{height}p"


# =========================================================
# SHA256
# =========================================================

def calculate_sha256(path: Path):

    sha = hashlib.sha256()

    with path.open(
        "rb"
    ) as file:

        while True:

            chunk = file.read(
                1024 * 1024
            )

            if not chunk:
                break

            sha.update(chunk)

    return sha.hexdigest()


# =========================================================
# DATABASE HELPERS
# =========================================================

def movie_exists(
    file_unique_id=None,
    sha256=None,
):

    query = """
        SELECT *
        FROM movies
        WHERE file_unique_id = ?
           OR sha256 = ?
        LIMIT 1
    """

    row = db.execute(
        query,
        (
            file_unique_id,
            sha256,
        ),
    ).fetchone()

    return row


def episode_exists(
    file_unique_id=None,
    sha256=None,
):

    query = """
        SELECT *
        FROM episodes
        WHERE file_unique_id = ?
           OR sha256 = ?
        LIMIT 1
    """

    row = db.execute(
        query,
        (
            file_unique_id,
            sha256,
        ),
    ).fetchone()

    return row


def get_series(
    title,
    year,
):

    return db.execute(
        """
        SELECT *
        FROM series
        WHERE title = ?
          AND (
              year = ?
              OR (year IS NULL AND ? IS NULL)
          )
        LIMIT 1
        """,
        (
            title,
            year,
            year,
        ),
    ).fetchone()


def create_series(
    title,
    year,
):

    existing = get_series(
        title,
        year,
    )

    if existing:
        return existing["id"]

    cursor = db.execute(
        """
        INSERT INTO series (
            title,
            year
        )
        VALUES (?, ?)
        """,
        (
            title,
            year,
        ),
    )

    db.commit()

    return cursor.lastrowid


def get_topic(
    content_type,
    entity_id,
):

    return db.execute(
        """
        SELECT *
        FROM topics
        WHERE content_type = ?
          AND entity_id = ?
        LIMIT 1
        """,
        (
            content_type,
            entity_id,
        ),
    ).fetchone()


def save_topic(
    content_type,
    entity_id,
    topic_id,
    title,
):

    db.execute(
        """
        INSERT INTO topics (
            content_type,
            entity_id,
            topic_id,
            title
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(content_type, entity_id)
        DO UPDATE SET
            topic_id = excluded.topic_id,
            title = excluded.title
        """,
        (
            content_type,
            entity_id,
            topic_id,
            title,
        ),
    )

    db.commit()


# =========================================================
# FORUM TOPIC
# =========================================================

async def create_topic(
    context,
    title,
):

    topic = await context.bot.create_forum_topic(
        chat_id=GROUP_ID,
        name=title[:128],
    )

    return topic.message_thread_id


async def get_or_create_movie_topic(
    context,
    movie_id,
    title,
    year,
):

    existing = get_topic(
        "movie",
        movie_id,
    )

    if existing:

        return existing["topic_id"]

    topic_title = title

    if year:
        topic_title += f" ({year})"

    topic_id = await create_topic(
        context,
        topic_title,
    )

    save_topic(
        "movie",
        movie_id,
        topic_id,
        topic_title,
    )

    return topic_id


async def get_or_create_series_topic(
    context,
    series_id,
    title,
    year,
):

    existing = get_topic(
        "series",
        series_id,
    )

    if existing:

        return existing["topic_id"]

    topic_title = f"📺 {title}"

    if year:
        topic_title += f" ({year})"

    topic_id = await create_topic(
        context,
        topic_title,
    )

    save_topic(
        "series",
        series_id,
        topic_id,
        topic_title,
    )

    return topic_id


# =========================================================
# CAPTION GENERATOR
# =========================================================

def build_movie_caption(
    info,
    media,
):

    title = html.escape(
        info["title"] or "Noma'lum"
    )

    year = (
        str(info["year"])
        if info["year"]
        else "Noma'lum"
    )

    quality = html.escape(
        media.get(
            "quality"
        ) or info.get(
            "quality"
        ) or "Noma'lum"
    )

    audio = html.escape(
        info.get(
            "audio"
        )
        or media.get(
            "audio_codec"
        )
        or "Noma'lum"
    )

    codec = html.escape(
        info.get(
            "video_codec"
        )
        or media.get(
            "video_codec"
        )
        or "Noma'lum"
    )

    fmt = html.escape(
        info.get(
            "format"
        )
        or "Noma'lum"
    ).upper()

    return (
        f"🎬 <b>{title}</b>\n\n"
        f"📅 <b>Yil:</b> {year}\n"
        f"🎞 <b>Sifat:</b> {quality}\n"
        f"🔊 <b>Audio:</b> {audio}\n"
        f"🎥 <b>Video:</b> {codec}\n"
        f"💿 <b>Format:</b> {fmt}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎬 <b>Kino Yordamchi</b>"
    )


def build_episode_caption(
    info,
    media,
):

    title = html.escape(
        info["title"] or "Noma'lum"
    )

    year = (
        str(info["year"])
        if info["year"]
        else "Noma'lum"
    )

    season = info.get(
        "season"
    ) or 0

    episode = info.get(
        "episode"
    ) or 0

    episode_title = html.escape(
        info.get(
            "episode_title"
        )
        or "Noma'lum"
    )

    quality = html.escape(
        media.get(
            "quality"
        ) or info.get(
            "quality"
        ) or "Noma'lum"
    )

    audio = html.escape(
        info.get(
            "audio"
        )
        or media.get(
            "audio_codec"
        )
        or "Noma'lum"
    )

    codec = html.escape(
        info.get(
            "video_codec"
        )
        or media.get(
            "video_codec"
        )
        or "Noma'lum"
    )

    fmt = html.escape(
        info.get(
            "format"
        )
        or "Noma'lum"
    ).upper()

    return (
        f"📺 <b>{title}</b>\n\n"
        f"📅 <b>Yil:</b> {year}\n"
        f"🎞 <b>Fasl:</b> {season}\n"
        f"🎬 <b>Qism:</b> {episode}\n"
        f"📝 <b>Episode:</b> {episode_title}\n"
        f"🎞 <b>Sifat:</b> {quality}\n"
        f"🔊 <b>Audio:</b> {audio}\n"
        f"🎥 <b>Video:</b> {codec}\n"
        f"💿 <b>Format:</b> {fmt}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎬 <b>Kino Yordamchi</b>"
    )


# =========================================================
# SAVE MOVIE
# =========================================================

def save_movie(
    info,
    media,
    filename,
    file_unique_id,
    file_id,
    sha256,
    topic_id,
    message_id,
):

    db.execute(
        """
        INSERT INTO movies (
            title,
            year,
            quality,
            audio,
            video_codec,
            format,
            filename,
            file_unique_id,
            file_id,
            sha256,
            topic_id,
            telegram_message_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            info["title"],
            info["year"],
            media.get("quality"),
            info.get("audio")
            or media.get("audio_codec"),
            info.get("video_codec")
            or media.get("video_codec"),
            info.get("format"),
            filename,
            file_unique_id,
            file_id,
            sha256,
            topic_id,
            message_id,
        ),
    )

    db.commit()


# =========================================================
# SAVE EPISODE
# =========================================================

def save_episode(
    series_id,
    info,
    media,
    filename,
    file_unique_id,
    file_id,
    sha256,
    topic_id,
    message_id,
):

    db.execute(
        """
        INSERT OR IGNORE INTO seasons (
            series_id,
            season_number
        )
        VALUES (?, ?)
        """,
        (
            series_id,
            info["season"],
        ),
    )

    db.execute(
        """
        INSERT INTO episodes (
            series_id,
            season_number,
            episode_number,
            episode_title,
            quality,
            audio,
            video_codec,
            format,
            filename,
            file_unique_id,
            file_id,
            sha256,
            topic_id,
            telegram_message_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            series_id,
            info["season"],
            info["episode"],
            info.get("episode_title"),
            media.get("quality"),
            info.get("audio")
            or media.get("audio_codec"),
            info.get("video_codec")
            or media.get("video_codec"),
            info.get("format"),
            filename,
            file_unique_id,
            file_id,
            sha256,
            topic_id,
            message_id,
        ),
    )

    db.commit()


# =========================================================
# TELEGRAM MEDIA PROCESSOR
# =========================================================

async def process_telegram_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    message = update.effective_message

    if not message:
        return

    media_file = None
    filename = None
    file_unique_id = None

    if message.video:

        media_file = message.video

        filename = (
            message.video.file_name
            or f"video_{message.video.file_unique_id}.mp4"
        )

        file_unique_id = (
            message.video.file_unique_id
        )

    elif message.document:

        mime = (
            message.document.mime_type
            or ""
        )

        if not mime.startswith("video/"):

            await message.reply_text(
                "❌ Bu video fayl emas."
            )

            return

        media_file = message.document

        filename = (
            message.document.file_name
            or f"video_{message.document.file_unique_id}"
        )

        file_unique_id = (
            message.document.file_unique_id
        )

    else:
        return

    status = await message.reply_text(
        "⏳ Video tekshirilmoqda..."
    )

    temp_path = MEDIA_DIR / (
        f"temp_{file_unique_id}_{filename}"
    )

    try:

        telegram_file = await context.bot.get_file(
            media_file.file_id
        )

        await telegram_file.download_to_drive(
            custom_path=str(temp_path)
        )

        sha256 = calculate_sha256(
            temp_path
        )

        # -------------------------------------------------
        # DUPLICATE
        # -------------------------------------------------

        duplicate_movie = movie_exists(
            file_unique_id,
            sha256,
        )

        duplicate_episode = episode_exists(
            file_unique_id,
            sha256,
        )

        if duplicate_movie or duplicate_episode:

            await status.edit_text(
                "⚠️ Bu video bazada allaqachon mavjud."
            )

            return

        # -------------------------------------------------
        # FFMPEG
        # -------------------------------------------------

        probe = await run_ffprobe(
            str(temp_path)
        )

        if not probe:

            await status.edit_text(
                "❌ Video metadata ma'lumotlarini o'qib bo'lmadi."
            )

            return

        media = extract_media_info(
            probe
        )

        width = media.get("width") or 0
        height = media.get("height") or 0

        if width < MIN_WIDTH or height < MIN_HEIGHT:

            await status.edit_text(
                "❌ Video 1080p dan past.\n\n"
                f"Topilgan: {width}x{height}\n"
                "Minimal talab: 1920x1080"
            )

            return

        media["quality"] = detect_quality(
            width,
            height,
        )

        info = parse_filename(
            filename
        )

        # -------------------------------------------------
        # SERIES
        # -------------------------------------------------

        if (
            info["season"] is not None
            and info["episode"] is not None
        ):

            series_id = create_series(
                info["title"],
                info["year"],
            )

            topic_id = await get_or_create_series_topic(
                context,
                series_id,
                info["title"],
                info["year"],
            )

            caption = build_episode_caption(
                info,
                media,
            )

            sent = await context.bot.send_document(
                chat_id=GROUP_ID,
                document=media_file.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
            )

            save_episode(
                series_id,
                info,
                media,
                filename,
                file_unique_id,
                media_file.file_id,
                sha256,
                topic_id,
                sent.message_id,
            )

        # -------------------------------------------------
        # MOVIE
        # -------------------------------------------------

        else:

            cursor = db.execute(
                """
                INSERT INTO movies (
                    title,
                    year,
                    quality,
                    audio,
                    video_codec,
                    format,
                    filename,
                    file_unique_id,
                    file_id,
                    sha256
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    info["title"],
                    info["year"],
                    media["quality"],
                    info.get("audio")
                    or media.get("audio_codec"),
                    info.get("video_codec")
                    or media.get("video_codec"),
                    info.get("format"),
                    filename,
                    file_unique_id,
                    media_file.file_id,
                    sha256,
                ),
            )

            movie_id = cursor.lastrowid

            db.commit()

            topic_id = await get_or_create_movie_topic(
                context,
                movie_id,
                info["title"],
                info["year"],
            )

            caption = build_movie_caption(
                info,
                media,
            )

            if (
                info["format"]
                and info["format"].lower() == "mp4"
            ):

                sent = await context.bot.send_video(
                    chat_id=GROUP_ID,
                    video=media_file.file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=topic_id,
                    supports_streaming=True,
                )

            else:

                sent = await context.bot.send_document(
                    chat_id=GROUP_ID,
                    document=media_file.file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=topic_id,
                )

            db.execute(
                """
                UPDATE movies
                SET topic_id = ?,
                    telegram_message_id = ?
                WHERE id = ?
                """,
                (
                    topic_id,
                    sent.message_id,
                    movie_id,
                ),
            )

            db.commit()

        await status.edit_text(
            "✅ Video muvaffaqiyatli guruhga joylandi."
        )

    except Exception as exc:

        logger.exception(
            "Media processing error: %s",
            exc,
        )

        await status.edit_text(
            f"❌ Xatolik:\n{html.escape(str(exc))}",
            parse_mode=ParseMode.HTML,
        )

    finally:

        try:

            if temp_path.exists():
                temp_path.unlink()

        except Exception:
            pass


# =========================================================
# COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    await update.message.reply_text(
        "🎬 <b>Kino Yordamchi Bot</b>\n\n"
        "Video yuboring — bot avtomatik tekshiradi,\n"
        "caption yaratadi va guruhga joylaydi.\n\n"
        "/help — yordam\n"
        "/status — bot holati\n"
        "/list — bazadagi filmlar\n",
        parse_mode=ParseMode.HTML,
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    await update.message.reply_text(
        "🎬 <b>BUYRUQLAR</b>\n\n"
        "/start — botni ishga tushirish\n"
        "/help — yordam\n"
        "/status — bot holati\n"
        "/list — filmlar ro'yxati\n"
        "/scan — media papkani tekshirish\n"
        "/reload — bazani qayta yuklash\n\n"
        "🎞 Botga video yuborsangiz,\n"
        "u avtomatik tarzda guruhga joylanadi.",
        parse_mode=ParseMode.HTML,
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    movies = db.execute(
        "SELECT COUNT(*) FROM movies"
    ).fetchone()[0]

    series = db.execute(
        "SELECT COUNT(*) FROM series"
    ).fetchone()[0]

    episodes = db.execute(
        "SELECT COUNT(*) FROM episodes"
    ).fetchone()[0]

    await update.message.reply_text(
        "📊 <b>BOT STATUS</b>\n\n"
        f"🎬 Filmlar: <b>{movies}</b>\n"
        f"📺 Seriallar: <b>{series}</b>\n"
        f"🎞 Qismlar: <b>{episodes}</b>\n\n"
        f"👤 Owner ID: <code>{OWNER_ID}</code>\n"
        f"👥 Group ID: <code>{GROUP_ID}</code>",
        parse_mode=ParseMode.HTML,
    )


async def list_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    rows = db.execute(
        """
        SELECT title, year, quality, filename
        FROM movies
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()

    if not rows:

        await update.message.reply_text(
            "📭 Bazada kino yo'q."
        )

        return

    text = "🎬 <b>OXIRGI FILMLAR</b>\n\n"

    for index, row in enumerate(
        rows,
        1,
    ):

        year = (
            f" ({row['year']})"
            if row["year"]
            else ""
        )

        text += (
            f"{index}. "
            f"<b>{html.escape(row['title'])}</b>"
            f"{year}\n"
            f"🎞 {html.escape(row['quality'] or 'N/A')}\n"
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


async def scan_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    files = []

    for path in MEDIA_DIR.rglob("*"):

        if path.is_file():

            if path.suffix.lower() in (
                ".mp4",
                ".mkv",
                ".avi",
                ".mov",
                ".webm",
            ):

                files.append(path)

    await update.message.reply_text(
        f"📁 Media papkada <b>{len(files)}</b> ta video topildi.",
        parse_mode=ParseMode.HTML,
    )


async def reload_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    db.commit()

    await update.message.reply_text(
        "♻️ Database qayta yuklandi."
    )


# =========================================================
# HEALTH SERVER
# =========================================================

async def health(
    request
):

    return web.json_response(
        {
            "status": "ok",
            "bot": "Kino Yordamchi",
        }
    )


async def start_health_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health,
    )

    app.router.add_get(
        "/health",
        health,
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    logger.info(
        "Health server started on port %s",
        PORT,
    )

    return runner


# =========================================================
# TELEGRAM APPLICATION
# =========================================================

def build_application():

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "list",
            list_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "scan",
            scan_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "reload",
            reload_command,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.VIDEO
            | filters.Document.VIDEO,
            process_telegram_media,
        )
    )

    return application


# =========================================================
# MAIN
# =========================================================

async def main():

    validate_config()

    init_database()

    application = build_application()

    health_runner = None

    try:

        logger.info(
            "Initializing Telegram application..."
        )

        await application.initialize()

        await application.start()

        if application.updater is None:

            raise RuntimeError(
                "Telegram updater mavjud emas."
            )

        await application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES
        )

        logger.info(
            "Telegram bot polling started."
        )

        health_runner = await start_health_server()

        logger.info(
            "Kino Yordamchi Bot ishga tushdi."
        )

        stop_event = asyncio.Event()

        await stop_event.wait()

    except (KeyboardInterrupt, SystemExit):

        logger.info(
            "Shutdown requested."
        )

    finally:

        logger.info(
            "Bot to'xtatilmoqda..."
        )

        if application.updater:

            if application.updater.running:

                await application.updater.stop()

        if application.running:

            await application.stop()

        await application.shutdown()

        if health_runner:

            await health_runner.cleanup()

        db.close()

        logger.info(
            "Bot to'xtatildi."
        )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
