# ============================================================
# 🎬 KINO YORDAMCHI BOT
# Python 3.13/3.14
# python-telegram-bot 21.1.1
# Render + Polling + aiohttp health server
# ============================================================

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from aiohttp import web

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ============================================================
# ⚙️ CONFIG
# ============================================================

BOT_TOKEN = os.getenv("8998476657:AAFlqY444CFw5IsVCoAgavHajLasZsRkX_c", "").strip()

try:
    OWNER_ID = int(os.getenv("6975146118", "0"))
except ValueError:
    OWNER_ID = 0

try:
    GROUP_ID = int(os.getenv("-1003535011408", "0"))
except ValueError:
    GROUP_ID = 0

PORT = int(os.getenv("PORT", "10000"))

DB_PATH = os.getenv("DB_PATH", "kino_bot.db")

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media"))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

MIN_WIDTH = 1920
MIN_HEIGHT = 1080

BOT_NAME = "🎬 Kino Yordamchi"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("KinoYordamchi")


# ============================================================
# 🗄 DATABASE
# ============================================================

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False,
)

db.row_factory = sqlite3.Row

db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA foreign_keys=ON")


def init_database():
    cursor = db.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            year INTEGER,
            quality TEXT,
            audio TEXT,
            video_codec TEXT,
            container TEXT,
            file_name TEXT,
            file_id TEXT,
            file_unique_id TEXT,
            sha256 TEXT,
            topic_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            year INTEGER,
            topic_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(title, year)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            season INTEGER NOT NULL,
            UNIQUE(series_id, season),
            FOREIGN KEY(series_id)
                REFERENCES series(id)
                ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id INTEGER NOT NULL,
            season INTEGER NOT NULL,
            episode INTEGER NOT NULL,
            episode_title TEXT,
            quality TEXT,
            audio TEXT,
            video_codec TEXT,
            container TEXT,
            file_name TEXT,
            file_id TEXT,
            file_unique_id TEXT,
            sha256 TEXT,
            topic_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(series_id, season, episode),
            FOREIGN KEY(series_id)
                REFERENCES series(id)
                ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_type TEXT NOT NULL,
            content_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL,
            title TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(content_type, content_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT,
            data TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    db.commit()

    logger.info("SQLite database initialized.")


# ============================================================
# 🔐 OWNER CHECK
# ============================================================

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def owner_only(update: Update) -> bool:
    user = update.effective_user

    if not user:
        return False

    if is_owner(user.id):
        return True

    if update.callback_query:
        await update.callback_query.answer(
            "⛔ Sizda ruxsat yo‘q!",
            show_alert=True,
        )

    elif update.message:
        await update.message.reply_text(
            "⛔ <b>Ruxsat yo‘q!</b>\n\n"
            "Bu bot faqat administrator uchun.",
            parse_mode=ParseMode.HTML,
        )

    return False


# ============================================================
# 🎬 FILENAME PARSER
# ============================================================

def parse_filename(filename: str) -> dict:
    original = Path(filename).name

    stem = Path(original).stem

    result = {
        "original": original,
        "title": None,
        "year": None,
        "season": None,
        "episode": None,
        "episode_title": None,
        "quality": None,
        "audio": None,
        "video_codec": None,
        "container": Path(original).suffix.replace(".", "").upper(),
        "type": "movie",
    }

    # --------------------------------------------------------
    # Season/Episode
    # --------------------------------------------------------

    season_episode = re.search(
        r"[Ss](\d{1,2})[Ee](\d{1,3})",
        stem,
    )

    if not season_episode:
        season_episode = re.search(
            r"(\d{1,2})[xX](\d{1,3})",
            stem,
        )

    if season_episode:
        result["type"] = "series"
        result["season"] = int(season_episode.group(1))
        result["episode"] = int(season_episode.group(2))

    # --------------------------------------------------------
    # Year
    # --------------------------------------------------------

    year_match = re.search(
        r"(19\d{2}|20\d{2})",
        stem,
    )

    if year_match:
        result["year"] = int(year_match.group(1))

    # --------------------------------------------------------
    # Quality
    # --------------------------------------------------------

    quality_patterns = [
        r"2160p",
        r"1440p",
        r"1080p",
        r"720p",
        r"480p",
        r"4K",
        r"8K",
    ]

    for pattern in quality_patterns:
        match = re.search(pattern, stem, re.I)

        if match:
            result["quality"] = match.group(0).upper()
            break

    # --------------------------------------------------------
    # Audio
    # --------------------------------------------------------

    audio_patterns = [
        "TRUEHD",
        "ATMOS",
        "DTS-HD",
        "DTS",
        "DDP",
        "DD+",
        "AC3",
        "AAC",
        "FLAC",
        "MP3",
        "EAC3",
    ]

    upper_stem = stem.upper()

    for audio in audio_patterns:
        if audio in upper_stem:
            result["audio"] = audio
            break

    # --------------------------------------------------------
    # Video codec
    # --------------------------------------------------------

    codec_patterns = [
        ("H.265", ["H265", "X265", "HEVC"]),
        ("H.264", ["H264", "X264", "AVC"]),
        ("AV1", ["AV1"]),
        ("VP9", ["VP9"]),
    ]

    for codec_name, patterns in codec_patterns:
        if any(x in upper_stem for x in patterns):
            result["video_codec"] = codec_name
            break

    # --------------------------------------------------------
    # Clean title
    # --------------------------------------------------------

    title = stem

    title = re.sub(
        r"\b(19\d{2}|20\d{2})\b",
        "",
        title,
    )

    title = re.sub(
        r"[Ss]\d{1,2}[Ee]\d{1,3}",
        "",
        title,
        flags=re.I,
    )

    title = re.sub(
        r"\d{1,2}[xX]\d{1,3}",
        "",
        title,
    )

    for pattern in quality_patterns:
        title = re.sub(
            pattern,
            "",
            title,
            flags=re.I,
        )

    for audio in audio_patterns:
        title = re.sub(
            re.escape(audio),
            "",
            title,
            flags=re.I,
        )

    title = re.sub(
        r"\b(HEVC|H265|H264|X265|X264|AV1|WEB-DL|WEBRip|BluRay|BDRip|HDR|REMUX|10bit|8bit)\b",
        "",
        title,
        flags=re.I,
    )

    title = title.replace(".", " ")
    title = title.replace("_", " ")
    title = title.replace("-", " ")

    title = re.sub(r"\s+", " ", title)
    title = title.strip(" .-_")

    result["title"] = title or "Noma'lum"

    # --------------------------------------------------------
    # Episode title
    # --------------------------------------------------------

    if result["type"] == "series":
        temp = re.sub(
            r"[Ss]\d{1,2}[Ee]\d{1,3}",
            "",
            stem,
            flags=re.I,
        )

        temp = re.sub(
            r"\b(19\d{2}|20\d{2})\b",
            "",
            temp,
        )

        temp = re.sub(
            r"\b(2160p|1440p|1080p|720p|480p|4K|8K)\b",
            "",
            temp,
            flags=re.I,
        )

        temp = temp.replace(".", " ")
        temp = temp.replace("_", " ")

        parts = temp.split()

        if parts:
            # titledan keyingi qismni taxminiy episode title sifatida
            cleaned = " ".join(parts).strip()

            if cleaned.lower() != result["title"].lower():
                result["episode_title"] = cleaned

    return result


# ============================================================
# 🔍 FFPROBE
# ============================================================

async def run_ffprobe(path: str) -> Optional[dict]:
    command = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
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

        return json.loads(
            stdout.decode(errors="ignore")
        )

    except FileNotFoundError:
        logger.error(
            "ffprobe topilmadi. Serverda FFmpeg o‘rnatilganini tekshiring."
        )
        return None

    except Exception as e:
        logger.exception("ffprobe failed: %s", e)
        return None


def extract_video_metadata(data: dict) -> dict:
    result = {
        "width": 0,
        "height": 0,
        "duration": 0,
        "video_codec": None,
        "audio_codec": None,
        "audio_language": None,
        "container": None,
    }

    streams = data.get("streams", [])

    for stream in streams:
        codec_type = stream.get("codec_type")

        if codec_type == "video" and not result["width"]:
            result["width"] = int(
                stream.get("width") or 0
            )

            result["height"] = int(
                stream.get("height") or 0
            )

            result["video_codec"] = stream.get(
                "codec_name"
            )

        elif codec_type == "audio" and not result["audio_codec"]:
            result["audio_codec"] = stream.get(
                "codec_name"
            )

            tags = stream.get("tags", {})

            result["audio_language"] = (
                tags.get("language")
                or tags.get("LANGUAGE")
            )

    fmt = data.get("format", {})

    result["duration"] = float(
        fmt.get("duration") or 0
    )

    result["container"] = fmt.get(
        "format_name"
    )

    return result


def quality_from_resolution(
    width: int,
    height: int,
) -> str:

    if width >= 3840 or height >= 2160:
        return "2160p 4K"

    if width >= 2560 or height >= 1440:
        return "1440p"

    if width >= 1920 or height >= 1080:
        return "1080p"

    if width >= 1280 or height >= 720:
        return "720p"

    return f"{width}x{height}"


# ============================================================
# 📝 CAPTION GENERATOR
# ============================================================

def build_movie_caption(
    info: dict,
    media: dict,
) -> str:

    title = html.escape(
        info.get("title") or "Noma'lum"
    )

    year = info.get("year") or "—"

    quality = quality_from_resolution(
        media["width"],
        media["height"],
    )

    audio = (
        info.get("audio")
        or media.get("audio_codec")
        or "—"
    )

    codec = (
        info.get("video_codec")
        or media.get("video_codec")
        or "—"
    )

    container = (
        info.get("container")
        or media.get("container")
        or "—"
    )

    return (
        f"🎬 <b>{title}</b>\n\n"
        f"📅 <b>Yil:</b> {year}\n"
        f"🎞 <b>Sifat:</b> {quality}\n"
        f"🔊 <b>Audio:</b> {html.escape(str(audio))}\n"
        f"🎥 <b>Video:</b> {html.escape(str(codec))}\n"
        f"💿 <b>Format:</b> {html.escape(str(container))}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🍿 <b>{BOT_NAME}</b>\n"
        f"━━━━━━━━━━━━━━━━"
    )


def build_episode_caption(
    info: dict,
    media: dict,
) -> str:

    title = html.escape(
        info.get("title") or "Noma'lum"
    )

    season = info.get("season") or 1
    episode = info.get("episode") or 1

    episode_title = info.get(
        "episode_title"
    )

    quality = quality_from_resolution(
        media["width"],
        media["height"],
    )

    audio = (
        info.get("audio")
        or media.get("audio_codec")
        or "—"
    )

    codec = (
        info.get("video_codec")
        or media.get("video_codec")
        or "—"
    )

    container = (
        info.get("container")
        or media.get("container")
        or "—"
    )

    episode_line = ""

    if episode_title:
        episode_line = (
            f"🎞 <b>Qism nomi:</b> "
            f"{html.escape(episode_title)}\n"
        )

    return (
        f"📺 <b>{title}</b>\n\n"
        f"📂 <b>Fasl:</b> {season}\n"
        f"🎬 <b>Qism:</b> {episode}\n"
        f"{episode_line}"
        f"🎞 <b>Sifat:</b> {quality}\n"
        f"🔊 <b>Audio:</b> {html.escape(str(audio))}\n"
        f"🎥 <b>Video:</b> {html.escape(str(codec))}\n"
        f"💿 <b>Format:</b> {html.escape(str(container))}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🍿 <b>{BOT_NAME}</b>\n"
        f"━━━━━━━━━━━━━━━━"
    )


# ============================================================
# 🎛 INLINE MENU
# ============================================================

def main_keyboard() -> InlineKeyboardMarkup:

    keyboard = [
        [
            InlineKeyboardButton(
                "🎬 Kinolar",
                callback_data="movies",
            ),
            InlineKeyboardButton(
                "📺 Seriallar",
                callback_data="series",
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Statistika",
                callback_data="status",
            ),
            InlineKeyboardButton(
                "⚙️ Sozlamalar",
                callback_data="settings",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Yangilash",
                callback_data="reload",
            ),
            InlineKeyboardButton(
                "ℹ️ Yordam",
                callback_data="help",
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def back_keyboard() -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Orqaga",
                callback_data="home",
            )
        ]
    ])


# ============================================================
# 🏠 START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    user = update.effective_user

    text = (
        f"🎬 <b>{BOT_NAME}</b>\n\n"
        f"👋 Salom, <b>{html.escape(user.first_name)}</b>!\n\n"
        f"🤖 Men kinolar va seriallarni "
        f"guruhingizga tartibli joylashtiraman.\n\n"
        f"📥 Fayl yuboring →\n"
        f"🔎 metadata tekshiriladi →\n"
        f"📝 caption yaratiladi →\n"
        f"📂 topic tanlanadi →\n"
        f"🚀 guruhga yuboriladi.\n\n"
        f"🔐 <b>Bot faqat siz uchun ishlaydi.</b>"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# ℹ️ HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    text = (
        "🎬 <b>KINO YORDAMCHI — YORDAM</b>\n\n"
        "📥 <b>Kino yuklash</b>\n"
        "Botga video yoki video fayl yuboring.\n\n"
        "📺 <b>Serial</b>\n"
        "Fayl nomida <code>S01E01</code> yoki "
        "<code>1x01</code> bo‘lishi kerak.\n\n"
        "🎞 <b>Sifat</b>\n"
        "1080p va undan yuqori fayllar qabul qilinadi.\n\n"
        "📝 <b>Caption</b>\n"
        "Caption avtomatik yaratiladi.\n\n"
        "🔐 <b>Admin</b>\n"
        "Faqat OWNER_ID egasi botdan foydalanishi mumkin."
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )


# ============================================================
# 📊 STATUS
# ============================================================

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

    text = (
        "📊 <b>BOT STATISTIKASI</b>\n\n"
        f"🎬 Kinolar: <b>{movies}</b>\n"
        f"📺 Seriallar: <b>{series}</b>\n"
        f"🎞 Qismlar: <b>{episodes}</b>\n\n"
        f"🟢 Bot: <b>ONLINE</b>"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )


# ============================================================
# 🎬 CREATE FORUM TOPIC
# ============================================================

async def create_topic(
    bot,
    title: str,
) -> Optional[int]:

    try:
        topic = await bot.create_forum_topic(
            chat_id=GROUP_ID,
            name=title[:128],
        )

        return topic.message_thread_id

    except Exception as e:
        logger.exception(
            "Topic yaratishda xato: %s",
            e,
        )

        return None


# ============================================================
# 🎬 GET/CREATE MOVIE TOPIC
# ============================================================

async def get_movie_topic(
    bot,
    title: str,
    year: Optional[int],
) -> Optional[int]:

    row = db.execute(
        """
        SELECT topic_id
        FROM movies
        WHERE title = ?
          AND (
              year = ?
              OR (year IS NULL AND ? IS NULL)
          )
        AND topic_id IS NOT NULL
        LIMIT 1
        """,
        (title, year, year),
    ).fetchone()

    if row:
        return row["topic_id"]

    topic_name = f"🎬 {title}"

    if year:
        topic_name += f" ({year})"

    topic_id = await create_topic(
        bot,
        topic_name,
    )

    return topic_id


# ============================================================
# 📺 GET/CREATE SERIES
# ============================================================

async def get_series(
    bot,
    title: str,
    year: Optional[int],
):

    row = db.execute(
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
        (title, year, year),
    ).fetchone()

    if row:
        return dict(row)

    topic_name = f"📺 {title}"

    if year:
        topic_name += f" ({year})"

    topic_id = await create_topic(
        bot,
        topic_name,
    )

    cursor = db.execute(
        """
        INSERT INTO series
        (
            title,
            year,
            topic_id
        )
        VALUES (?, ?, ?)
        """,
        (
            title,
            year,
            topic_id,
        ),
    )

    db.commit()

    series_id = cursor.lastrowid

    return {
        "id": series_id,
        "title": title,
        "year": year,
        "topic_id": topic_id,
    }


# ============================================================
# 📥 PROCESS VIDEO
# ============================================================

async def process_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await owner_only(update):
        return

    message = update.message

    if not message:
        return

    media = message.video or message.document

    if not media:
        return

    filename = (
        getattr(media, "file_name", None)
        or f"media_{message.message_id}.mp4"
    )

    await message.reply_text(
        "🔎 <b>Fayl tekshirilmoqda...</b>\n\n"
        "⏳ Metadata o‘qilmoqda...",
        parse_mode=ParseMode.HTML,
    )

    temp_path = None

    try:

        telegram_file = await media.get_file()

        suffix = Path(filename).suffix or ".mp4"

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temp:

            temp_path = temp.name

        await telegram_file.download_to_drive(
            custom_path=temp_path
        )

        # ----------------------------------------------------
        # SHA256
        # ----------------------------------------------------

        sha256 = hashlib.sha256()

        with open(temp_path, "rb") as f:

            while True:

                chunk = f.read(1024 * 1024)

                if not chunk:
                    break

                sha256.update(chunk)

        sha256_value = sha256.hexdigest()

        # ----------------------------------------------------
        # Duplicate check
        # ----------------------------------------------------

        duplicate = db.execute(
            """
            SELECT id
            FROM movies
            WHERE sha256 = ?

            UNION

            SELECT id
            FROM episodes
            WHERE sha256 = ?

            LIMIT 1
            """,
            (
                sha256_value,
                sha256_value,
            ),
        ).fetchone()

        if duplicate:

            await message.reply_text(
                "⚠️ <b>Bu fayl allaqachon bazada mavjud.</b>",
                parse_mode=ParseMode.HTML,
            )

            return

        # ----------------------------------------------------
        # FFPROBE
        # ----------------------------------------------------

        probe = await run_ffprobe(
            temp_path
        )

        if not probe:

            await message.reply_text(
                "❌ <b>Video metadata o‘qilmadi.</b>\n"
                "FFmpeg/ffprobe tekshiring.",
                parse_mode=ParseMode.HTML,
            )

            return

        metadata = extract_video_metadata(
            probe
        )

        width = metadata["width"]
        height = metadata["height"]

        if width < MIN_WIDTH or height < MIN_HEIGHT:

            await message.reply_text(
                "❌ <b>Video sifati yetarli emas.</b>\n\n"
                f"📐 Resolution: "
                f"<code>{width}x{height}</code>\n"
                "✅ Minimum: <b>1920x1080</b>",
                parse_mode=ParseMode.HTML,
            )

            return

        # ----------------------------------------------------
        # Parse filename
        # ----------------------------------------------------

        info = parse_filename(
            filename
        )

        # actual codec
        if metadata["video_codec"]:
            info["video_codec"] = (
                metadata["video_codec"]
            )

        # ----------------------------------------------------
        # MOVIE
        # ----------------------------------------------------

        if info["type"] == "movie":

            title = info["title"]
            year = info["year"]

            topic_id = await get_movie_topic(
                context.bot,
                title,
                year,
            )

            if not topic_id:

                await message.reply_text(
                    "❌ Topic yaratilmadi.",
                )

                return

            caption = build_movie_caption(
                info,
                metadata,
            )

            # save DB first
            cursor = db.execute(
                """
                INSERT INTO movies
                (
                    title,
                    year,
                    quality,
                    audio,
                    video_codec,
                    container,
                    file_name,
                    file_id,
                    file_unique_id,
                    sha256,
                    topic_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    year,
                    quality_from_resolution(
                        width,
                        height,
                    ),
                    info.get("audio")
                    or metadata.get("audio_codec"),
                    metadata.get("video_codec"),
                    info.get("container"),
                    filename,
                    getattr(media, "file_id", None),
                    getattr(media, "file_unique_id", None),
                    sha256_value,
                    topic_id,
                ),
            )

            db.commit()

            # ------------------------------------------------
            # Send to group
            # ------------------------------------------------

            await context.bot.send_video(
                chat_id=GROUP_ID,
                video=media.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
                supports_streaming=True,
            )

            await message.reply_text(
                "✅ <b>Kino guruhga joylandi!</b>\n\n"
                f"🎬 {html.escape(title)}\n"
                f"📅 {year or '—'}\n"
                f"🎞 {quality_from_resolution(width, height)}",
                parse_mode=ParseMode.HTML,
            )

        # ----------------------------------------------------
        # SERIES
        # ----------------------------------------------------

        else:

            title = info["title"]
            year = info["year"]

            series = await get_series(
                context.bot,
                title,
                year,
            )

            series_id = series["id"]
            topic_id = series["topic_id"]

            caption = build_episode_caption(
                info,
                metadata,
            )

            # season
            db.execute(
                """
                INSERT OR IGNORE INTO seasons
                (
                    series_id,
                    season
                )
                VALUES (?, ?)
                """,
                (
                    series_id,
                    info["season"],
                ),
            )

            # episode
            db.execute(
                """
                INSERT INTO episodes
                (
                    series_id,
                    season,
                    episode,
                    episode_title,
                    quality,
                    audio,
                    video_codec,
                    container,
                    file_name,
                    file_id,
                    file_unique_id,
                    sha256,
                    topic_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    series_id,
                    info["season"],
                    info["episode"],
                    info.get("episode_title"),
                    quality_from_resolution(
                        width,
                        height,
                    ),
                    info.get("audio")
                    or metadata.get("audio_codec"),
                    metadata.get("video_codec"),
                    info.get("container"),
                    filename,
                    getattr(media, "file_id", None),
                    getattr(media, "file_unique_id", None),
                    sha256_value,
                    topic_id,
                ),
            )

            db.commit()

            await context.bot.send_video(
                chat_id=GROUP_ID,
                video=media.file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
                supports_streaming=True,
            )

            await message.reply_text(
                "✅ <b>Serial qismi guruhga joylandi!</b>\n\n"
                f"📺 {html.escape(title)}\n"
                f"📂 S{info['season']:02d}\n"
                f"🎬 E{info['episode']:02d}\n"
                f"🎞 {quality_from_resolution(width, height)}",
                parse_mode=ParseMode.HTML,
            )

    except Exception as e:

        logger.exception(
            "Video processing error: %s",
            e,
        )

        await message.reply_text(
            "❌ <b>Xatolik yuz berdi.</b>\n\n"
            f"<code>{html.escape(str(e)[:1000])}</code>",
            parse_mode=ParseMode.HTML,
        )

    finally:

        if temp_path:

            try:
                os.remove(temp_path)

            except OSError:
                pass


# ============================================================
# 🎬 MOVIE LIST
# ============================================================

async def movies_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not is_owner(query.from_user.id):

        await query.answer(
            "⛔ Ruxsat yo‘q!",
            show_alert=True,
        )

        return

    rows = db.execute(
        """
        SELECT title, year
        FROM movies
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()

    if not rows:

        text = (
            "🎬 <b>KINOLAR</b>\n\n"
            "Hozircha kino mavjud emas."
        )

    else:

        lines = [
            "🎬 <b>SO‘NGGI KINOLAR</b>\n"
        ]

        for index, row in enumerate(
            rows,
            start=1,
        ):

            year = (
                f" ({row['year']})"
                if row["year"]
                else ""
            )

            lines.append(
                f"{index}. 🎬 "
                f"{html.escape(row['title'])}"
                f"{year}"
            )

        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )


# ============================================================
# 📺 SERIES LIST
# ============================================================

async def series_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not is_owner(query.from_user.id):

        await query.answer(
            "⛔ Ruxsat yo‘q!",
            show_alert=True,
        )

        return

    rows = db.execute(
        """
        SELECT title, year
        FROM series
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()

    if not rows:

        text = (
            "📺 <b>SERIALLAR</b>\n\n"
            "Hozircha serial mavjud emas."
        )

    else:

        lines = [
            "📺 <b>SO‘NGGI SERIALLAR</b>\n"
        ]

        for index, row in enumerate(
            rows,
            start=1,
        ):

            year = (
                f" ({row['year']})"
                if row["year"]
                else ""
            )

            lines.append(
                f"{index}. 📺 "
                f"{html.escape(row['title'])}"
                f"{year}"
            )

        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )


# ============================================================
# ⚙️ SETTINGS
# ============================================================

async def settings_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not is_owner(query.from_user.id):

        await query.answer(
            "⛔ Ruxsat yo‘q!",
            show_alert=True,
        )

        return

    text = (
        "⚙️ <b>BOT SOZLAMALARI</b>\n\n"
        f"🎬 Bot: <b>{BOT_NAME}</b>\n"
        f"📐 Minimum sifat: <b>1080p</b>\n"
        f"🔐 Owner ID: <code>{OWNER_ID}</code>\n"
        f"👥 Group ID: <code>{GROUP_ID}</code>\n"
        f"🗄 Database: <code>{DB_PATH}</code>\n\n"
        "🟢 Polling: faol\n"
        "🟢 SQLite: faol\n"
        "🟢 FFprobe: faol"
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )


# ============================================================
# 🔄 RELOAD
# ============================================================

async def reload_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not is_owner(query.from_user.id):

        await query.answer(
            "⛔ Ruxsat yo‘q!",
            show_alert=True,
        )

        return

    await query.answer(
        "🔄 Yangilanmoqda..."
    )

    await query.edit_message_text(
        "🔄 <b>Bot ma'lumotlari yangilandi.</b>\n\n"
        "🟢 Database tekshirildi.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_keyboard(),
    )


# ============================================================
# 🏠 HOME CALLBACK
# ============================================================

async def home_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not is_owner(query.from_user.id):

        await query.answer(
            "⛔ Ruxsat yo‘q!",
            show_alert=True,
        )

        return

    text = (
        f"🎬 <b>{BOT_NAME}</b>\n\n"
        "🤖 Boshqaruv paneli\n\n"
        "📥 Kino/serial yuboring.\n"
        "📝 Caption avtomatik yaratiladi.\n"
        "📂 Topic avtomatik tanlanadi."
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# 🔘 CALLBACK ROUTER
# ============================================================

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if data == "home":
        await home_callback(
            update,
            context,
        )

    elif data == "movies":
        await movies_callback(
            update,
            context,
        )

    elif data == "series":
        await series_callback(
            update,
            context,
        )

    elif data == "status":
        await status_command(
            update,
            context,
        )

    elif data == "settings":
        await settings_callback(
            update,
            context,
        )

    elif data == "help":
        await help_command(
            update,
            context,
        )

    elif data == "reload":
        await reload_callback(
            update,
            context,
        )


# ============================================================
# ❤️ HEALTH SERVER
# ============================================================

async def health_handler(
    request: web.Request,
):

    return web.json_response(
        {
            "status": "ok",
            "bot": "Kino Yordamchi",
            "service": "online",
        }
    )


async def start_health_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health_handler,
    )

    app.router.add_get(
        "/health",
        health_handler,
    )

    runner = web.AppRunner(app)

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


# ============================================================
# 🧩 BUILD APPLICATION
# ============================================================

def build_application() -> Application:

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler(
            "start",
            start_command,
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

    # Inline buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    # Video
    application.add_handler(
        MessageHandler(
            filters.VIDEO,
            process_video,
        )
    )

    # Documents
    application.add_handler(
        MessageHandler(
            filters.Document.VIDEO,
            process_video,
        )
    )

    return application


# ============================================================
# 🚀 MAIN
# ============================================================

async def main():

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN Environment Variable topilmadi."
        )

    if OWNER_ID == 0:

        raise RuntimeError(
            "OWNER_ID Environment Variable noto‘g‘ri."
        )

    if GROUP_ID == 0:

        raise RuntimeError(
            "GROUP_ID Environment Variable noto‘g‘ri."
        )

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    init_database()

    # --------------------------------------------------------
    # Application
    # --------------------------------------------------------

    application = build_application()

    logger.info(
        "Initializing Telegram application..."
    )

    await application.initialize()

    # --------------------------------------------------------
    # Telegram bot
    # --------------------------------------------------------

    await application.start()

    if application.updater is None:

        raise RuntimeError(
            "Telegram updater mavjud emas."
        )

    logger.info(
        "Starting Telegram polling..."
    )

    await application.updater.start_polling(
        allowed_updates=Update.ALL_TYPES
    )

    # --------------------------------------------------------
    # Health server
    # --------------------------------------------------------

    health_runner = await start_health_server()

    logger.info(
        "🎬 Kino Yordamchi ONLINE"
    )

    try:

        await asyncio.Event().wait()

    except (
        KeyboardInterrupt,
        SystemExit,
    ):

        logger.info(
            "Shutdown requested."
        )

    finally:

        logger.info(
            "Stopping bot..."
        )

        try:

            if (
                application.updater
                and application.updater.running
            ):

                await application.updater.stop()

        except Exception:

            logger.exception(
                "Updater stop error"
            )

        try:

            if application.running:

                await application.stop()

        except Exception:

            logger.exception(
                "Application stop error"
            )

        try:

            await application.shutdown()

        except Exception:

            logger.exception(
                "Application shutdown error"
            )

        try:

            await health_runner.cleanup()

        except Exception:

            logger.exception(
                "Health server cleanup error"
            )

        try:

            db.close()

        except Exception:

            pass

        logger.info(
            "🎬 Kino Yordamchi stopped."
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())
