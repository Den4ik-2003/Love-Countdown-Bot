# -*- coding: utf-8 -*-
"""
Персональний романтичний Telegram-бот.

Стек збережено: python-telegram-bot (async + JobQueue), health-сервер для хостингу, .env.
Нове: SQLite-сховище, режими «На відстані» / «Ми разом», розділи з повідомленнями
і повноцінна адмін-панель (доступ тільки за ADMIN_ID, перевірка на сервері).
"""
import hmac
import json
import logging
import os
import random
import re
import sqlite3
import string
import threading
import time as time_module
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from content import (
    OPEN_WHEN_CATEGORIES,
    PLACEHOLDERS,
    ROMANTIC_MESSAGES,
    SECTIONS,
    SUPPORT_CATEGORIES,
    SURPRISE_MESSAGES,
    TEXT_DEFS,
    TOGETHER_MESSAGES,
)

# ═══════════════════════════ Конфігурація (.env) ═══════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
VIEWER_PASSWORD = os.getenv("VIEWER_PASSWORD", "")
DEFAULT_START_DATE = os.getenv("START_DATE", "2026-08-16")
DEFAULT_END_DATE = os.getenv("END_DATE", "")
PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "bot_data.db"))
LEGACY_STATE_FILE = os.path.join(BASE_DIR, "bot_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# httpx на рівні INFO друкує URL із токеном бота — вимикаємо
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _read_admin_id() -> int:
    raw = os.getenv("ADMIN_ID", "").strip()
    if not raw.isdigit():
        raise SystemExit(
            "Не задано ADMIN_ID у .env — це твій числовий Telegram ID "
            "(дізнатись можна, наприклад, у бота @userinfobot)."
        )
    return int(raw)


if not BOT_TOKEN:
    raise SystemExit("Не задано BOT_TOKEN у .env")
if not VIEWER_PASSWORD:
    raise SystemExit("Не задано VIEWER_PASSWORD у .env")
ADMIN_ID = _read_admin_id()
if os.getenv("ADMIN_PASSWORD"):
    logger.warning("ADMIN_PASSWORD більше не використовується: адмін визначається лише за ADMIN_ID.")

try:
    _start = date.fromisoformat(DEFAULT_START_DATE)
    _end = date.fromisoformat(DEFAULT_END_DATE) if DEFAULT_END_DATE else _start + timedelta(days=90)
except ValueError:
    raise SystemExit("START_DATE / END_DATE мають бути у форматі РРРР-ММ-ДД")

# ═══════════════════════════ Константи ═══════════════════════════
REUNION_DAYS = 3          # скільки перших днів після зустрічі показувати «після розлуки»
PAGE_SIZE = 8
MAX_TEXT_LEN = 3500
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 15 * 60

# PTB: у days 0 = неділя … 6 = субота
DAY_SETS = {
    "all": tuple(range(7)),
    "weekdays": (1, 2, 3, 4, 5),
    "weekend": (6, 0),
}
DAYS_LABEL = {"all": "щодня", "weekdays": "пн–пт", "weekend": "сб–нд"}
DAYS_ORDER = ["all", "weekdays", "weekend"]
MODE_FILTER_LABEL = {"any": "в будь-якому режимі", "apart": "лише на відстані", "together": "лише коли разом"}
MODE_ORDER = ["any", "apart", "together"]
MODE_LABEL = {"apart": "🌍 На відстані", "together": "💖 Ми разом"}
KIND_LABEL = {
    "apart": "очікування зустрічі",
    "meeting_day": "день зустрічі",
    "reunion": "перші дні після розлуки",
    "together": "ми разом",
}

# (ключ функції, назва)
FEATURES = [
    ("morning", "☀️ Ранкове повідомлення"),
    ("love", "❤️ «Я тебе люблю»"),
    ("miss", "🥺 «Скучаю»"),
    ("night", "🌙 «Добраніч»"),
    ("surprise_auto", "🎁 Автосюрпризи за розкладом"),
    ("greetings", "🎉 Привітання зі спецдат"),
    ("section_support", "💌 Розділ «Повідомлення для тебе»"),
    ("section_open_when", "📬 Розділ «Відкрий, коли…»"),
    ("section_surprise", "🎁 Кнопка «Сюрприз»"),
    ("auto_switch", "🔄 Автоперемикання на «Ми разом» після зустрічі"),
    ("admin_copy", "👀 Копії автоповідомлень мені (адміну)"),
]
# Тип автоповідомлення → (назва, функція, що вмикає)
KINDS = {
    "morning": ("☀️ Ранкове повідомлення", "morning"),
    "love": ("❤️ «Я тебе люблю»", "love"),
    "miss": ("🥺 «Скучаю»", "miss"),
    "night": ("🌙 «Добраніч»", "night"),
    "surprise": ("🎁 Випадковий сюрприз", "surprise_auto"),
}
DEFAULT_SCHEDULES = [
    ("morning", 9, 0, "all", "any"),
    ("love", 12, 0, "all", "any"),
    ("love", 16, 0, "all", "any"),
    ("love", 22, 0, "all", "any"),
    ("miss", 15, 0, "weekend", "apart"),
]

SECTION_TITLES = dict(SECTIONS)
TEXT_BY_KEY = {k: (title, default, group) for k, title, default, group in TEXT_DEFS}
TEXT_INDEX = {k: i for i, (k, _, _, _) in enumerate(TEXT_DEFS)}
GROUP_ALLOWED = {g: {n for n, _ in items} for g, items in PLACEHOLDERS.items()}

DEFAULT_SETTINGS = {
    "mode": "apart",
    "apart_since": DEFAULT_START_DATE,
    "meeting_at": f"{_end.isoformat()}T12:00",
    "together_since": "",
    "timezone": "Europe/Warsaw",
    "greet_hour": "8",
    "greet_minute": "0",
    "daily_key": "",
    "daily_text": "",
    "seeded": "0",
}
DEFAULT_SETTINGS.update({f"feat_{k}": "1" for k, _ in FEATURES})

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS texts (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY, role TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '', joined_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, section TEXT NOT NULL,
    title TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    text TEXT NOT NULL, shown INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    hour INTEGER NOT NULL, minute INTEGER NOT NULL,
    days TEXT NOT NULL DEFAULT 'all', mode TEXT NOT NULL DEFAULT 'any');
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, month INTEGER NOT NULL,
    day INTEGER NOT NULL, year INTEGER, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending (
    chat_id INTEGER PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}');
"""

DB: sqlite3.Connection = None  # type: ignore
LOGIN_FAILS: dict = {}  # chat_id -> (кількість помилок, заблоковано до)


# ═══════════════════════════ База даних ═══════════════════════════
@contextmanager
def transaction():
    DB.execute("BEGIN")
    try:
        yield
    except BaseException:
        DB.execute("ROLLBACK")
        raise
    else:
        DB.execute("COMMIT")


def get_setting(key: str) -> str:
    row = DB.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is not None:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, "")


def set_setting(key: str, value: str) -> None:
    DB.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def feature_on(key: str) -> bool:
    return get_setting(f"feat_{key}") == "1"


def get_text(key: str) -> str:
    row = DB.execute("SELECT value FROM texts WHERE key=?", (key,)).fetchone()
    return row["value"] if row else TEXT_BY_KEY[key][1]


def load_legacy():
    """Читає старий bot_state.json (якщо є), щоб перенести налаштування."""
    if not os.path.exists(LEGACY_STATE_FILE):
        return None
    try:
        with open(LEGACY_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Не вдалось прочитати %s: %s", LEGACY_STATE_FILE, e)
        return None


def seed_database() -> None:
    """Перший запуск: наповнює базу вмістом і переносить старі налаштування."""
    if get_setting("seeded") == "1":
        return
    legacy = load_legacy()
    with transaction():
        pos = 0

        def add_category(section, title, messages):
            nonlocal pos
            pos += 1
            cur = DB.execute(
                "INSERT INTO categories(section, title, position) VALUES(?,?,?)", (section, title, pos)
            )
            DB.executemany(
                "INSERT INTO messages(category_id, text) VALUES(?,?)",
                [(cur.lastrowid, m) for m in messages],
            )

        add_category("apart", "Ранкові", ROMANTIC_MESSAGES)
        add_category("together", "Ранкові", TOGETHER_MESSAGES)
        for title, msgs in SUPPORT_CATEGORIES.items():
            add_category("support", title, msgs)
        for title, msgs in OPEN_WHEN_CATEGORIES.items():
            add_category("open_when", title, msgs)
        add_category("surprise", "Сюрпризи", SURPRISE_MESSAGES)

        schedules = list(DEFAULT_SCHEDULES)
        if legacy:
            s = legacy.get("settings", {})
            if s.get("start_date"):
                set_setting("apart_since", s["start_date"])
            try:
                start = date.fromisoformat(s.get("start_date") or DEFAULT_START_DATE)
                end = date.fromisoformat(s["end_date"]) if s.get("end_date") else start + timedelta(days=90)
                set_setting("meeting_at", f"{end.isoformat()}T12:00")
            except ValueError:
                pass
            if s.get("timezone"):
                set_setting("timezone", s["timezone"])
            schedules = [(
                "morning", int(s.get("morning_hour", 9)), int(s.get("morning_minute", 0)), "all", "any"
            )]
            for lt in s.get("love_times", []):
                schedules.append(("love", int(lt["hour"]), int(lt["minute"]), "all", "any"))
            schedules.append((
                "miss", int(s.get("weekend_hour", 15)), int(s.get("weekend_minute", 0)), "weekend", "apart"
            ))
            for cid in legacy.get("users", {}):
                try:
                    chat_id = int(cid)
                except ValueError:
                    continue
                if chat_id != ADMIN_ID:
                    DB.execute(
                        "INSERT OR IGNORE INTO users(chat_id, role, joined_at) VALUES(?,?,?)",
                        (chat_id, "viewer", datetime.now().isoformat(timespec="seconds")),
                    )
            logger.info("Старі налаштування імпортовано з %s", LEGACY_STATE_FILE)
        DB.executemany(
            "INSERT INTO schedules(kind, hour, minute, days, mode) VALUES(?,?,?,?,?)", schedules
        )
        set_setting("seeded", "1")


def init_db(path: str) -> None:
    global DB
    DB = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    DB.row_factory = sqlite3.Row
    DB.execute("PRAGMA foreign_keys=ON")
    try:
        DB.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    DB.executescript(SCHEMA)
    seed_database()
    logger.info("База даних: %s", path)


# ═══════════════════════════ Утиліти ═══════════════════════════
def plural_uk(n: int, forms) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def get_tz() -> ZoneInfo:
    try:
        return ZoneInfo(get_setting("timezone"))
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("Europe/Warsaw")


def now_local() -> datetime:
    """Поточний місцевий час (за налаштованим часовим поясом) без tzinfo."""
    return datetime.now(get_tz()).replace(tzinfo=None)


def get_date_setting(key: str, default=None):
    try:
        return date.fromisoformat(get_setting(key))
    except ValueError:
        return default


def get_meeting_dt():
    try:
        return datetime.fromisoformat(get_setting("meeting_at"))
    except ValueError:
        return None


def fmt_date(d) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


def format_meeting(dt: datetime) -> str:
    return f"{dt:%d.%m.%Y} о {dt:%H:%M}"


def format_countdown(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "вже зовсім скоро"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} {plural_uk(d, ('день', 'дні', 'днів'))} {h} год"
    if h:
        return f"{h} год {m} хв"
    return f"{m} хв"


def parse_date_input(s: str):
    s = s.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_time_input(s: str):
    try:
        h_str, m_str = s.strip().replace(".", ":").split(":")
        h, m = int(h_str), int(m_str)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except ValueError:
        pass
    return None


def parse_datetime_input(s: str):
    parts = s.split()
    if not parts or len(parts) > 2:
        return None
    d = parse_date_input(parts[0])
    if not d:
        return None
    t = (12, 0) if len(parts) == 1 else parse_time_input(parts[1])
    if not t:
        return None
    return datetime(d.year, d.month, d.day, t[0], t[1])


def parse_event_date(s: str):
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", s.strip())
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else None
    try:
        date(2000 if year is None else year, month, day)
    except ValueError:
        return None
    return month, day, year


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def validate_template(text: str, allowed: set):
    """Повертає текст помилки або None, якщо шаблон коректний."""
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as e:
        return f"неправильні фігурні дужки ({e})"
    for _, field, spec, conv in parsed:
        if field is None:
            continue
        if field not in allowed or spec or conv:
            return f"невідомий плейсхолдер «{{{field}}}»"
    return None


def render_text(key: str, ctx: dict = None) -> str:
    template = get_text(key)
    if not GROUP_ALLOWED[TEXT_BY_KEY[key][2]]:
        return template
    try:
        return template.format_map(SafeDict(ctx or {}))
    except Exception:
        return template


def pick_message(section: str = None, category_id: int = None):
    """Випадкове повідомлення без повторів, поки не показано всі."""
    if category_id is not None:
        rows = DB.execute(
            "SELECT id, text, shown FROM messages WHERE category_id=?", (category_id,)
        ).fetchall()
    else:
        rows = DB.execute(
            "SELECT m.id, m.text, m.shown FROM messages m "
            "JOIN categories c ON c.id = m.category_id WHERE c.section=?",
            (section,),
        ).fetchall()
    if not rows:
        return None
    lowest = min(r["shown"] for r in rows)
    row = random.choice([r for r in rows if r["shown"] == lowest])
    DB.execute("UPDATE messages SET shown = shown + 1 WHERE id=?", (row["id"],))
    return row["text"]


# ═══════════════════════════ Ситуація: режими й тексти ═══════════════════════════
def refresh_mode() -> None:
    """Після зустрічі автоматично перемикає «На відстані» → «Ми разом»."""
    if get_setting("mode") != "apart" or not feature_on("auto_switch"):
        return
    meeting = get_meeting_dt()
    since = get_date_setting("apart_since")
    if meeting and since and meeting.date() >= since and now_local() >= meeting:
        set_setting("mode", "together")
        set_setting("together_since", meeting.date().isoformat())
        set_setting("daily_key", "")
        logger.info("Автоперемикання: «Ми разом» з %s", meeting.date())


def compute_situation() -> dict:
    refresh_mode()
    now = now_local()
    today = now.date()
    mode = get_setting("mode")
    ctx = {"day": 0, "total": 0, "days_left": 0, "countdown": "", "meeting_date": "",
           "days_together": 0, "elapsed": 0}

    if mode == "apart":
        since = get_date_setting("apart_since", today)
        meeting = get_meeting_dt() or datetime.combine(today, time(12, 0))
        elapsed = max((today - since).days, 0)
        ctx.update(day=elapsed + 1, elapsed=elapsed)
        if meeting.date() < since:  # дату нової зустрічі ще не задано
            ctx.update(countdown="ще не визначено", meeting_date="ще не визначено")
            return {"mode": mode, "kind": "apart", "selector": "pool:apart", "ctx": ctx, "today": today}
        days_left = (meeting.date() - today).days
        ctx.update(
            total=(meeting.date() - since).days,
            days_left=max(days_left, 0),
            countdown=format_countdown(meeting - now),
            meeting_date=format_meeting(meeting),
        )
        kind = "apart"
        if days_left <= 0:
            kind, selector = "meeting_day", "text:final_0"
        elif elapsed > 0 and elapsed % 30 == 0:
            selector = {30: "text:milestone_30", 60: "text:milestone_60"}.get(elapsed, "text:milestone_generic")
        elif days_left <= 10:
            selector = f"text:final_{days_left}"
        else:
            selector = "pool:apart"
        return {"mode": mode, "kind": kind, "selector": selector, "ctx": ctx, "today": today}

    since = get_date_setting("together_since", today)
    d = max((today - since).days, 0)
    days_together = d + 1
    ctx.update(days_together=days_together, day=days_together)
    kind = "reunion" if d < REUNION_DAYS else "together"
    if days_together % 100 == 0 or days_together % 365 == 0:
        selector = "text:together_milestone"
    else:
        selector = "pool:together"
    return {"mode": mode, "kind": kind, "selector": selector, "ctx": ctx, "today": today}


HEADER_KEYS = {
    "apart": "tpl_apart",
    "meeting_day": "tpl_meeting_day",
    "reunion": "tpl_reunion",
    "together": "tpl_together",
}
FALLBACK_BODY = "Люблю тебе ❤️"


def get_body(sit: dict) -> str:
    selector = sit["selector"]
    kind, name = selector.split(":", 1)
    if kind == "text":
        return render_text(name, sit["ctx"])
    # пул: повідомлення дня фіксується, щоб «Оновити» не міняло його
    key = f"{sit['today'].isoformat()}|{selector}"
    if get_setting("daily_key") == key and get_setting("daily_text"):
        return get_setting("daily_text")
    body = pick_message(section=name) or FALLBACK_BODY
    set_setting("daily_key", key)
    set_setting("daily_text", body)
    return body


def build_main_text() -> str:
    sit = compute_situation()
    return render_text(HEADER_KEYS[sit["kind"]], sit["ctx"]) + "\n\n" + get_body(sit)


def build_morning_text() -> str:
    return render_text("morning_greeting") + "\n\n" + build_main_text()


def build_scheduled(kind: str):
    """Текст і клавіатура для автоповідомлення або None, якщо слати нічого."""
    if kind == "morning":
        return build_morning_text(), kb([[btn("💌 Відкрити", "m:open")]])
    if kind == "love":
        return get_text("love_msg"), None
    if kind == "miss":
        return get_text("miss_msg"), None
    if kind == "night":
        return get_text("night_msg"), None
    if kind == "surprise":
        msg = pick_message(section="surprise")
        return (f"🎁 Маленький сюрприз\n\n{msg}", None) if msg else None
    return None


# ═══════════════════════════ Користувачі й доступ ═══════════════════════════
def get_role(chat_id: int):
    if chat_id == ADMIN_ID:
        return "admin"
    row = DB.execute("SELECT role FROM users WHERE chat_id=?", (chat_id,)).fetchone()
    return "viewer" if row and row["role"] == "viewer" else None


def is_private(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat) and chat.type == "private"


def is_admin_user(update: Update) -> bool:
    """Перевірка адміна на сервері: і користувач, і приватний чат мають збігатись з ADMIN_ID."""
    user, chat = update.effective_user, update.effective_chat
    return bool(user) and bool(chat) and user.id == ADMIN_ID and chat.id == ADMIN_ID


def recipients() -> list:
    ids = [r["chat_id"] for r in DB.execute("SELECT chat_id FROM users WHERE role='viewer'")]
    if feature_on("admin_copy") and ADMIN_ID not in ids:
        ids.append(ADMIN_ID)
    return ids


async def broadcast(bot, text: str, keyboard=None) -> None:
    for chat_id in recipients():
        try:
            await bot.send_message(chat_id=chat_id, text=text[:4096], reply_markup=keyboard)
        except Exception as e:
            logger.error("Не вдалось надіслати %s: %s", chat_id, e)


def set_pending(chat_id: int, action: str, payload: dict = None) -> None:
    DB.execute(
        "INSERT INTO pending(chat_id, action, payload) VALUES(?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET action=excluded.action, payload=excluded.payload",
        (chat_id, action, json.dumps(payload or {})),
    )


def get_pending(chat_id: int):
    row = DB.execute("SELECT action, payload FROM pending WHERE chat_id=?", (chat_id,)).fetchone()
    if not row:
        return None
    try:
        return row["action"], json.loads(row["payload"])
    except ValueError:
        return row["action"], {}


def clear_pending(chat_id: int) -> None:
    DB.execute("DELETE FROM pending WHERE chat_id=?", (chat_id,))


# ═══════════════════════════ Розклад ═══════════════════════════
async def job_scheduled(context: ContextTypes.DEFAULT_TYPE) -> None:
    row = DB.execute("SELECT * FROM schedules WHERE id=?", (context.job.data,)).fetchone()
    if not row or row["kind"] not in KINDS:
        return
    if not feature_on(KINDS[row["kind"]][1]):
        return
    sit = compute_situation()
    if row["mode"] != "any" and row["mode"] != sit["mode"]:
        return
    built = build_scheduled(row["kind"])
    if built:
        await broadcast(context.bot, built[0], built[1])


async def job_greetings(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not feature_on("greetings"):
        return
    today = now_local().date()
    rows = DB.execute(
        "SELECT * FROM events WHERE month=? AND day=? ORDER BY id", (today.month, today.day)
    ).fetchall()
    for ev in rows:
        years = (today.year - ev["year"]) if ev["year"] else ""
        text = ev["text"]
        try:
            text = text.format_map(SafeDict(years=years))
        except Exception:
            pass
        await broadcast(context.bot, text)


def reschedule_all_jobs(application: Application) -> None:
    jq = application.job_queue
    for job in jq.jobs():
        if job.name and (job.name.startswith("sch_") or job.name == "greetings"):
            job.schedule_removal()
    tz = get_tz()
    for r in DB.execute("SELECT * FROM schedules ORDER BY id"):
        jq.run_daily(
            job_scheduled,
            time=time(hour=r["hour"], minute=r["minute"], tzinfo=tz),
            days=DAY_SETS.get(r["days"], DAY_SETS["all"]),
            data=r["id"],
            name=f"sch_{r['id']}",
        )
    jq.run_daily(
        job_greetings,
        time=time(hour=int(get_setting("greet_hour")), minute=int(get_setting("greet_minute")), tzinfo=tz),
        name="greetings",
    )
    logger.info("Завдання переплановані.")


# ═══════════════════════════ Інтерфейс: хелпери ═══════════════════════════
def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def cancel_kb():
    return kb([[btn("❌ Скасувати", "cancel")]])


def short(text: str, n: int = 38) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


async def show(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, keyboard=None, new: bool = False):
    """Редагує повідомлення з кнопкою (меню без засмічення чату) або надсилає нове."""
    text = text[:4096]
    query = update.callback_query
    if query and not new:
        try:
            await query.edit_message_text(text, reply_markup=keyboard)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=keyboard)


# ═══════════════════════════ Екрани глядача ═══════════════════════════
def main_keyboard(role: str):
    rows = []
    if feature_on("section_support"):
        rows.append([btn(get_text("btn_support"), "u:sup")])
    if feature_on("section_open_when"):
        rows.append([btn(get_text("btn_open_when"), "u:ow")])
    if feature_on("section_surprise"):
        rows.append([btn(get_text("btn_surprise"), "u:s")])
    rows.append([btn("🔄 Оновити", "m:home")])
    if role == "admin":
        rows.append([btn("⚙️ Адмін-панель", "a:menu")])
    return kb(rows)


async def show_home(update, context, new: bool = False):
    role = get_role(update.effective_chat.id)
    if role is None:
        await show(update, context, get_text("welcome_guest"), kb([[btn("🔑 Увійти", "login")]]), new)
    else:
        await show(update, context, build_main_text(), main_keyboard(role), new)


SECTION_FEATURE = {"support": "section_support", "open_when": "section_open_when"}


def screen_user_categories(section: str):
    """Список категорій розділу для глядача: (текст, клавіатура) або None, якщо розділ вимкнено."""
    if not feature_on(SECTION_FEATURE[section]):
        return None
    cats = DB.execute(
        "SELECT c.id, c.title FROM categories c "
        "WHERE c.section=? AND EXISTS (SELECT 1 FROM messages m WHERE m.category_id=c.id) "
        "ORDER BY c.position, c.id",
        (section,),
    ).fetchall()
    intro = get_text("support_intro" if section == "support" else "open_when_intro")
    if not cats:
        return "Тут поки порожньо 💛", kb([[btn("⬅️ Назад", "m:home")]])
    rows = [[btn(c["title"], f"u:c:{c['id']}")] for c in cats]
    rows.append([btn("⬅️ Назад", "m:home")])
    return intro, kb(rows)


# ═══════════════════════════ Екрани адміна ═══════════════════════════
def screen_admin_menu():
    sit = compute_situation()
    text = (
        "⚙️ Адмін-панель\n\n"
        f"Режим: {MODE_LABEL[sit['mode']]}\n"
        f"Бот зараз показує: {KIND_LABEL[sit['kind']]}\n\n"
        "Що налаштувати?"
    )
    return text, kb([
        [btn("💞 Режим і дата зустрічі", "a:mode")],
        [btn("✏️ Тексти", "a:tl:0")],
        [btn("💌 Повідомлення і категорії", "a:c")],
        [btn("⏰ Розклад автоповідомлень", "a:sc")],
        [btn("🎉 Привітання зі спецдат", "a:ev")],
        [btn("🔘 Функції (вмик/вимк)", "a:f")],
        [btn("👥 Користувачі", "a:u")],
        [btn("📊 Стан бота", "a:st")],
        [btn("⬅️ На головну", "m:home")],
    ])


def screen_mode():
    sit = compute_situation()
    mode = sit["mode"]
    meeting = get_meeting_dt()
    text = (
        "💞 Режим і дата зустрічі\n\n"
        f"Режим: {MODE_LABEL[mode]}\n"
        f"Початок розлуки: {fmt_date(get_date_setting('apart_since'))}\n"
        f"Наступна зустріч: {format_meeting(meeting) if meeting else '—'}\n"
        f"Разом з: {fmt_date(get_date_setting('together_since'))}\n"
        f"Часовий пояс: {get_setting('timezone')}\n\n"
        f"Бот зараз показує: {KIND_LABEL[sit['kind']]}"
    )
    mark = lambda m: "✅ " if mode == m else ""
    return text, kb([
        [btn(f"{mark('apart')}🌍 На відстані", "a:sm:apart"), btn(f"{mark('together')}💖 Ми разом", "a:sm:together")],
        [btn("📅 Дата й час зустрічі", "a:set:meeting")],
        [btn("🗓 Початок розлуки", "a:set:apart_since"), btn("💞 Разом з", "a:set:together_since")],
        [btn("🌍 Часовий пояс", "a:set:tz")],
        [btn("⬅️ Назад", "a:menu")],
    ])


def screen_texts(page: int):
    pages = (len(TEXT_DEFS) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, pages - 1))
    overridden = {r["key"] for r in DB.execute("SELECT key FROM texts")}
    rows = []
    for i in range(page * PAGE_SIZE, min(len(TEXT_DEFS), (page + 1) * PAGE_SIZE)):
        key, title = TEXT_DEFS[i][0], TEXT_DEFS[i][1]
        rows.append([btn(("✏️ " if key in overridden else "") + title, f"a:te:{i}")])
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"a:tl:{page - 1}"))
    nav.append(btn(f"{page + 1}/{pages}", "a:noop"))
    if page < pages - 1:
        nav.append(btn("▶️", f"a:tl:{page + 1}"))
    rows.append(nav)
    rows.append([btn("⬅️ Назад", "a:menu")])
    return "✏️ Тексти бота\n\nОбери, який текст змінити (✏️ — вже змінено):", kb(rows)


def screen_text_view(idx: int):
    key, title, _default, group = TEXT_DEFS[idx]
    text = f"✏️ {title}\n\nПоточний текст:\n──────────\n{get_text(key)}\n──────────"
    ph = PLACEHOLDERS[group]
    if ph:
        text += "\n\nМожна використовувати:\n" + "\n".join(f"{{{n}}} — {d}" for n, d in ph)
    return text, kb([
        [btn("✏️ Змінити", f"a:tw:{idx}")],
        [btn("↩️ Скинути до стандартного", f"a:tr:{idx}")],
        [btn("⬅️ Назад", f"a:tl:{idx // PAGE_SIZE}")],
    ])


def screen_content():
    rows = []
    for code, title in SECTIONS:
        n = DB.execute(
            "SELECT COUNT(*) AS n FROM messages m JOIN categories c ON c.id=m.category_id WHERE c.section=?",
            (code,),
        ).fetchone()["n"]
        rows.append([btn(f"{title} ({n})", f"a:sec:{code}")])
    rows.append([btn("⬅️ Назад", "a:menu")])
    return "💌 Повідомлення і категорії\n\nОбери розділ:", kb(rows)


def screen_section(code: str):
    cats = DB.execute(
        "SELECT c.id, c.title, COUNT(m.id) AS n FROM categories c "
        "LEFT JOIN messages m ON m.category_id=c.id WHERE c.section=? "
        "GROUP BY c.id ORDER BY c.position, c.id",
        (code,),
    ).fetchall()
    rows = [[btn(f"{c['title']} ({c['n']})", f"a:ml:{c['id']}:0")] for c in cats]
    rows.append([btn("➕ Нова категорія", f"a:cadd:{code}")])
    rows.append([btn("⬅️ Назад", "a:c")])
    text = f"{SECTION_TITLES[code]}\n\nКатегорій: {len(cats)}. Обери категорію або створи нову:"
    return text, kb(rows)


def screen_category(cat_id: int, page: int = 0):
    cat = DB.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
    if not cat:
        return screen_content()
    msgs = DB.execute("SELECT id, text FROM messages WHERE category_id=? ORDER BY id", (cat_id,)).fetchall()
    pages = max((len(msgs) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = max(0, min(page, pages - 1))
    rows = []
    for i in range(page * PAGE_SIZE, min(len(msgs), (page + 1) * PAGE_SIZE)):
        rows.append([btn(f"{i + 1}. {short(msgs[i]['text'])}", f"a:m:{msgs[i]['id']}")])
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"a:ml:{cat_id}:{page - 1}"))
    if pages > 1:
        nav.append(btn(f"{page + 1}/{pages}", "a:noop"))
    if page < pages - 1:
        nav.append(btn("▶️", f"a:ml:{cat_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("➕ Додати повідомлення", f"a:madd:{cat_id}")])
    rows.append([btn("✏️ Перейменувати", f"a:cren:{cat_id}"), btn("🗑 Видалити категорію", f"a:x:cat:{cat_id}")])
    rows.append([btn("⬅️ Назад", f"a:sec:{cat['section']}")])
    text = f"{SECTION_TITLES[cat['section']]} → {cat['title']}\n\nПовідомлень: {len(msgs)}"
    return text, kb(rows)


def screen_message(mid: int):
    row = DB.execute(
        "SELECT m.id, m.text, m.shown, m.category_id FROM messages m WHERE m.id=?", (mid,)
    ).fetchone()
    if not row:
        return screen_content()
    text = f"📝 Повідомлення #{row['id']}\n(показувалось: {row['shown']})\n──────────\n{row['text']}\n──────────"
    return text, kb([
        [btn("✏️ Змінити", f"a:med:{mid}"), btn("🗑 Видалити", f"a:x:msg:{mid}")],
        [btn("⬅️ Назад", f"a:ml:{row['category_id']}:0")],
    ])


def schedule_label(r) -> str:
    return f"{KINDS[r['kind']][0].split(' ')[0]} {r['hour']:02d}:{r['minute']:02d} · {DAYS_LABEL[r['days']]}"


def screen_schedules():
    rows_db = DB.execute("SELECT * FROM schedules ORDER BY hour, minute, id").fetchall()
    rows = [[btn(schedule_label(r), f"a:sce:{r['id']}")] for r in rows_db]
    rows.append([btn("➕ Додати автоповідомлення", "a:sca")])
    rows.append([btn("⬅️ Назад", "a:menu")])
    text = (
        "⏰ Розклад автоповідомлень\n\n"
        f"Часовий пояс: {get_setting('timezone')}\n"
        "Обери запис, щоб змінити час, частоту або режим:"
    )
    return text, kb(rows)


def screen_schedule(sid: int):
    r = DB.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    if not r:
        return screen_schedules()
    title, feat = KINDS[r["kind"]]
    text = (
        f"⏰ {title}\n\n"
        f"Час: {r['hour']:02d}:{r['minute']:02d}\n"
        f"Частота: {DAYS_LABEL[r['days']]}\n"
        f"Коли: {MODE_FILTER_LABEL[r['mode']]}"
    )
    if not feature_on(feat):
        text += "\n\n⚠️ Ця функція зараз вимкнена в розділі «Функції» — повідомлення не надсилатимуться."
    return text, kb([
        [btn("🕐 Змінити час", f"a:sct:{sid}")],
        [btn(f"📆 Частота: {DAYS_LABEL[r['days']]}", f"a:scd:{sid}")],
        [btn(f"🌍 {MODE_FILTER_LABEL[r['mode']].capitalize()}", f"a:scm:{sid}")],
        [btn("📨 Надіслати тест мені", f"a:scs:{sid}")],
        [btn("🗑 Видалити", f"a:x:sch:{sid}")],
        [btn("⬅️ Назад", "a:sc")],
    ])


def event_label(ev) -> str:
    d = f"{ev['day']:02d}.{ev['month']:02d}" + (f".{ev['year']}" if ev["year"] else "")
    return f"{d} · {short(ev['text'], 26)}"


def screen_events():
    evs = DB.execute("SELECT * FROM events ORDER BY month, day, id").fetchall()
    rows = [[btn(event_label(e), f"a:evv:{e['id']}")] for e in evs]
    rows.append([btn("➕ Додати привітання", "a:eva")])
    rows.append([btn(f"🕐 Час привітань: {int(get_setting('greet_hour')):02d}:{int(get_setting('greet_minute')):02d}",
                     "a:set:greet_time")])
    rows.append([btn("⬅️ Назад", "a:menu")])
    text = (
        "🎉 Привітання зі спеціальних дат\n\n"
        "Річниця, день народження тощо. Надсилаються щороку в обраний день.\n"
        f"Записів: {len(evs)}"
    )
    return text, kb(rows)


def screen_event(eid: int):
    ev = DB.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not ev:
        return screen_events()
    when = f"{ev['day']:02d}.{ev['month']:02d}" + (f".{ev['year']}" if ev["year"] else " (щороку)")
    text = f"🎉 Привітання\n\nДата: {when}\n──────────\n{ev['text']}\n──────────"
    return text, kb([
        [btn("✏️ Текст", f"a:evt:{eid}"), btn("📅 Дата", f"a:evd:{eid}")],
        [btn("🗑 Видалити", f"a:x:ev:{eid}")],
        [btn("⬅️ Назад", "a:ev")],
    ])


def screen_features():
    rows = [
        [btn(f"{'✅' if feature_on(k) else '⬜️'} {title}", f"a:ft:{k}")]
        for k, title in FEATURES
    ]
    rows.append([btn("⬅️ Назад", "a:menu")])
    return "🔘 Функції\n\nНатисни, щоб увімкнути або вимкнути:", kb(rows)


def screen_users():
    users = DB.execute("SELECT * FROM users ORDER BY joined_at").fetchall()
    lines = [f"👑 Адмін: {ADMIN_ID}"]
    rows = []
    for u in users:
        label = u["name"] or str(u["chat_id"])
        if u["role"] == "viewer":
            lines.append(f"• {label} ({u['chat_id']})")
            rows.append([btn(f"🚫 Заблокувати: {short(label, 25)}", f"a:ub:{u['chat_id']}")])
        else:
            lines.append(f"• ⛔ {label} ({u['chat_id']}) — заблоковано")
            rows.append([btn(f"✅ Розблокувати: {short(label, 22)}", f"a:uu:{u['chat_id']}")])
    if not users:
        lines.append("Інших користувачів поки немає.")
    rows.append([btn("⬅️ Назад", "a:menu")])
    return "👥 Користувачі\n\n" + "\n".join(lines), kb(rows)


def screen_status(application: Application):
    sit = compute_situation()
    now = now_local()
    tz = get_tz()
    viewers = DB.execute("SELECT COUNT(*) AS n FROM users WHERE role='viewer'").fetchone()["n"]
    blocked = DB.execute("SELECT COUNT(*) AS n FROM users WHERE role='blocked'").fetchone()["n"]
    counts = []
    for code, title in SECTIONS:
        n = DB.execute(
            "SELECT COUNT(*) AS n FROM messages m JOIN categories c ON c.id=m.category_id WHERE c.section=?",
            (code,),
        ).fetchone()["n"]
        counts.append(f"  {title}: {n}")
    off = [title for k, title in FEATURES if not feature_on(k)]
    upcoming = []
    try:
        jobs = [(j.next_t, j.name) for j in application.job_queue.jobs() if j.next_t]
        jobs.sort(key=lambda x: x[0])
        for nt, name in jobs[:5]:
            label = name
            if name.startswith("sch_"):
                r = DB.execute("SELECT * FROM schedules WHERE id=?", (int(name[4:]),)).fetchone()
                label = schedule_label(r) if r else name
            elif name == "greetings":
                label = "🎉 Привітання"
            upcoming.append(f"  {nt.astimezone(tz):%d.%m %H:%M} — {label}")
    except Exception:
        pass
    meeting = get_meeting_dt()
    text = (
        "📊 Стан бота\n\n"
        f"Режим: {MODE_LABEL[sit['mode']]} ({KIND_LABEL[sit['kind']]})\n"
        f"Зустріч: {format_meeting(meeting) if meeting else '—'}\n"
        f"Час на сервері зараз: {now:%d.%m.%Y %H:%M} ({get_setting('timezone')})\n"
        f"Глядачів: {viewers}, заблоковано: {blocked}\n"
        f"Автоповідомлень у розкладі: {DB.execute('SELECT COUNT(*) AS n FROM schedules').fetchone()['n']}\n"
        f"Привітань: {DB.execute('SELECT COUNT(*) AS n FROM events').fetchone()['n']}\n\n"
        "Повідомлень у базі:\n" + "\n".join(counts) + "\n\n"
        + ("Вимкнено: " + ", ".join(off) + "\n\n" if off else "Усі функції увімкнені.\n\n")
        + ("Найближчі відправки:\n" + "\n".join(upcoming) + "\n\n" if upcoming else "")
        + f"База: {DB_PATH}"
    )
    return text, kb([[btn("🔄 Оновити", "a:st")], [btn("⬅️ Назад", "a:menu")]])


def screen_confirm(what: str, oid: int):
    texts = {
        "msg": ("це повідомлення", f"a:m:{oid}"),
        "cat": ("категорію разом з усіма її повідомленнями", f"a:ml:{oid}:0"),
        "sch": ("цей запис розкладу", f"a:sce:{oid}"),
        "ev": ("це привітання", f"a:evv:{oid}"),
    }
    label, back = texts[what]
    return f"🗑 Видалити {label}?\nЦю дію не можна скасувати.", kb([
        [btn("✅ Так, видалити", f"a:y:{what}:{oid}"), btn("❌ Ні", back)],
    ])


# ═══════════════════════════ Введення тексту адміном ═══════════════════════════
class InputError(Exception):
    pass


PROMPTS = {
    "set_meeting": "📅 Введи дату й час наступної зустрічі: ДД.ММ.РРРР ГГ:ХХ\n"
                   "Наприклад: 14.11.2026 14:30\n(Якщо час не вказати — буде 12:00)",
    "set_apart_since": "🗓 Введи дату початку розлуки (ДД.ММ.РРРР). Від неї рахується «День N з M»:",
    "set_together_since": "💞 Введи дату, з якої ви разом (ДД.ММ.РРРР):",
    "set_tz": "🌍 Введи часовий пояс, наприклад Europe/Warsaw або Europe/Kyiv:",
    "set_greet_time": "🕐 Введи час привітань зі спецдат (ГГ:ХХ):",
}


def _reset_daily() -> None:
    set_setting("daily_key", "")
    set_setting("daily_text", "")


def apply_admin_input(action: str, payload: dict, value: str, application: Application) -> dict:
    """
    Застосовує введений адміном текст. Повертає:
      {"msg": підтвердження, "screen": (текст, клавіатура)}  або
      {"msg": ..., "next": (action, payload, prompt)} для багатокрокового вводу.
    Помилка введення → InputError (стан очікування лишається).
    """
    if action == "set_meeting":
        dt = parse_datetime_input(value)
        if not dt:
            raise InputError("Невірний формат. Приклад: 14.11.2026 14:30")
        since = get_date_setting("apart_since")
        if get_setting("mode") == "apart" and since and dt.date() < since:
            raise InputError(f"Зустріч не може бути раніше початку розлуки ({fmt_date(since)}).")
        set_setting("meeting_at", dt.strftime("%Y-%m-%dT%H:%M"))
        _reset_daily()
        return {"msg": f"✅ Зустріч: {format_meeting(dt)}", "screen": screen_mode()}

    if action in ("set_apart_since", "set_together_since"):
        d = parse_date_input(value)
        if not d:
            raise InputError("Невірний формат. Приклад: 16.08.2026")
        set_setting("apart_since" if action == "set_apart_since" else "together_since", d.isoformat())
        _reset_daily()
        return {"msg": f"✅ Збережено: {fmt_date(d)}", "screen": screen_mode()}

    if action == "set_tz":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise InputError("Невідомий часовий пояс. Приклад: Europe/Warsaw")
        set_setting("timezone", value)
        reschedule_all_jobs(application)
        return {"msg": f"✅ Часовий пояс: {value}", "screen": screen_mode()}

    if action == "set_greet_time":
        t = parse_time_input(value)
        if not t:
            raise InputError("Невірний формат. Приклад: 08:00")
        set_setting("greet_hour", str(t[0]))
        set_setting("greet_minute", str(t[1]))
        reschedule_all_jobs(application)
        return {"msg": f"✅ Час привітань: {t[0]:02d}:{t[1]:02d}", "screen": screen_events()}

    if action == "text_edit":
        key = payload["key"]
        if len(value) > MAX_TEXT_LEN:
            raise InputError(f"Занадто довгий текст (максимум {MAX_TEXT_LEN} символів).")
        allowed = GROUP_ALLOWED[TEXT_BY_KEY[key][2]]
        if allowed:
            err = validate_template(value, allowed)
            if err:
                names = ", ".join("{" + n + "}" for n in sorted(allowed))
                raise InputError(f"Помилка в шаблоні: {err}.\nДопустимі плейсхолдери: {names}")
        DB.execute(
            "INSERT INTO texts(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        return {"msg": "✅ Текст збережено", "screen": screen_text_view(TEXT_INDEX[key])}

    if action == "cat_add":
        if len(value) > 60:
            raise InputError("Назва занадто довга (до 60 символів).")
        pos = DB.execute("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM categories").fetchone()["p"]
        cur = DB.execute(
            "INSERT INTO categories(section, title, position) VALUES(?,?,?)", (payload["section"], value, pos)
        )
        return {"msg": "✅ Категорію створено", "screen": screen_category(cur.lastrowid)}

    if action == "cat_ren":
        if len(value) > 60:
            raise InputError("Назва занадто довга (до 60 символів).")
        DB.execute("UPDATE categories SET title=? WHERE id=?", (value, payload["id"]))
        return {"msg": "✅ Перейменовано", "screen": screen_category(payload["id"])}

    if action == "msg_add":
        parts = [p.strip() for p in re.split(r"\n\s*---+\s*\n", value) if p.strip()]
        if not parts:
            raise InputError("Порожній текст.")
        if any(len(p) > MAX_TEXT_LEN for p in parts):
            raise InputError(f"Одне з повідомлень задовге (максимум {MAX_TEXT_LEN} символів).")
        DB.executemany(
            "INSERT INTO messages(category_id, text) VALUES(?,?)", [(payload["cat"], p) for p in parts]
        )
        return {"msg": f"✅ Додано повідомлень: {len(parts)}", "screen": screen_category(payload["cat"])}

    if action == "msg_edit":
        if len(value) > MAX_TEXT_LEN:
            raise InputError(f"Занадто довгий текст (максимум {MAX_TEXT_LEN} символів).")
        DB.execute("UPDATE messages SET text=? WHERE id=?", (value, payload["id"]))
        return {"msg": "✅ Повідомлення оновлено", "screen": screen_message(payload["id"])}

    if action == "sc_time":
        t = parse_time_input(value)
        if not t:
            raise InputError("Невірний формат. Приклад: 09:00")
        DB.execute("UPDATE schedules SET hour=?, minute=? WHERE id=?", (t[0], t[1], payload["id"]))
        reschedule_all_jobs(application)
        return {"msg": f"✅ Час: {t[0]:02d}:{t[1]:02d}", "screen": screen_schedule(payload["id"])}

    if action == "ev_add_date":
        parsed = parse_event_date(value)
        if not parsed:
            raise InputError("Невірний формат. Приклад: 16.08 або 16.08.2024")
        month, day, year = parsed
        prompt = (
            "✏️ Тепер введи текст привітання.\n"
            + ("Можна вставити {years} — буде підставлено, скільки років минуло." if year else "")
        )
        return {"msg": "", "next": ("ev_add_text", {"m": month, "d": day, "y": year}, prompt.strip())}

    if action == "ev_add_text":
        err = validate_template(value, GROUP_ALLOWED["event"])
        if err:
            raise InputError(f"Помилка в шаблоні: {err}. Допустимо лише {{years}}.")
        if len(value) > MAX_TEXT_LEN:
            raise InputError(f"Занадто довгий текст (максимум {MAX_TEXT_LEN} символів).")
        cur = DB.execute(
            "INSERT INTO events(month, day, year, text) VALUES(?,?,?,?)",
            (payload["m"], payload["d"], payload["y"], value),
        )
        return {"msg": "✅ Привітання додано", "screen": screen_event(cur.lastrowid)}

    if action == "ev_text":
        err = validate_template(value, GROUP_ALLOWED["event"])
        if err:
            raise InputError(f"Помилка в шаблоні: {err}. Допустимо лише {{years}}.")
        DB.execute("UPDATE events SET text=? WHERE id=?", (value, payload["id"]))
        return {"msg": "✅ Текст оновлено", "screen": screen_event(payload["id"])}

    if action == "ev_date":
        parsed = parse_event_date(value)
        if not parsed:
            raise InputError("Невірний формат. Приклад: 16.08 або 16.08.2024")
        DB.execute(
            "UPDATE events SET month=?, day=?, year=? WHERE id=?", (parsed[0], parsed[1], parsed[2], payload["id"])
        )
        return {"msg": "✅ Дату оновлено", "screen": screen_event(payload["id"])}

    raise InputError("Невідома дія. Натисни /start.")


# ═══════════════════════════ Обробники Telegram ═══════════════════════════
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return
    clear_pending(update.effective_chat.id)
    await show_home(update, context, new=True)


async def handle_login(update: Update, context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    chat_id = update.effective_chat.id
    send = context.bot.send_message
    fails, locked_until = LOGIN_FAILS.get(chat_id, (0, 0.0))
    now = time_module.monotonic()
    if locked_until > now:
        minutes = int((locked_until - now) // 60) + 1
        await send(chat_id=chat_id, text=f"⏳ Забагато спроб. Спробуй за {minutes} хв.")
        return
    try:  # прибираємо повідомлення з паролем із чату
        await update.message.delete()
    except Exception:
        pass
    row = DB.execute("SELECT role FROM users WHERE chat_id=?", (chat_id,)).fetchone()
    if row and row["role"] == "blocked":
        clear_pending(chat_id)
        await send(chat_id=chat_id, text="⛔ Доступ закрито.")
        return
    if hmac.compare_digest(value.encode("utf-8"), VIEWER_PASSWORD.encode("utf-8")):
        LOGIN_FAILS.pop(chat_id, None)
        DB.execute(
            "INSERT INTO users(chat_id, role, name, joined_at) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET role='viewer', name=excluded.name",
            (chat_id, "viewer", update.effective_user.full_name or "", datetime.now().isoformat(timespec="seconds")),
        )
        clear_pending(chat_id)
        await send(chat_id=chat_id, text=get_text("login_ok"))
        await show_home(update, context, new=True)
        return
    fails += 1
    if fails >= LOGIN_MAX_FAILS:
        LOGIN_FAILS[chat_id] = (0, now + LOGIN_LOCK_SECONDS)
        await send(chat_id=chat_id, text="❌ Невірний пароль. Забагато спроб — спробуй за 15 хв.")
    else:
        LOGIN_FAILS[chat_id] = (fails, 0.0)
        await send(chat_id=chat_id, text="❌ Невірний пароль. Спробуй ще раз.", reply_markup=cancel_kb())


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update) or not update.message or update.message.text is None:
        return
    chat_id = update.effective_chat.id
    value = update.message.text.strip()
    pend = get_pending(chat_id)
    if not pend:
        await show_home(update, context, new=True)
        return
    action, payload = pend
    if action == "login":
        await handle_login(update, context, value)
        return
    if not is_admin_user(update):  # серверна перевірка: будь-яке адмін-введення лише від адміна
        clear_pending(chat_id)
        return
    try:
        result = apply_admin_input(action, payload, value, context.application)
    except InputError as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ {e}", reply_markup=cancel_kb())
        return
    if result.get("next"):
        nxt_action, nxt_payload, prompt = result["next"]
        set_pending(chat_id, nxt_action, nxt_payload)
        await context.bot.send_message(chat_id=chat_id, text=prompt, reply_markup=cancel_kb())
        return
    clear_pending(chat_id)
    await context.bot.send_message(chat_id=chat_id, text=result["msg"])
    text, keyboard = result["screen"]
    await show(update, context, text, keyboard, new=True)


async def route_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Маршрутизація колбеків. Повертає текст сповіщення (alert) або None."""
    chat_id = update.effective_chat.id
    role = get_role(chat_id)
    parts = data.split(":")

    if data == "cancel":
        clear_pending(chat_id)
        await show_home(update, context)
        return None
    if data == "login":
        if role is not None:
            await show_home(update, context)
            return None
        set_pending(chat_id, "login")
        await show(update, context, "🔑 Введи пароль:", cancel_kb())
        return None
    if role is None:
        await show_home(update, context)
        return None

    if parts[0] == "m":
        await show_home(update, context, new=(parts[1] == "open"))
        return None
    if parts[0] == "u":
        return await user_callback(update, context, parts)
    if parts[0] == "a":
        if not is_admin_user(update):
            return "⛔ Тільки для адміна"
        return await admin_callback(update, context, parts)
    return None


async def user_callback(update, context, parts):
    action = parts[1]
    if action in ("sup", "ow"):
        screen = screen_user_categories("support" if action == "sup" else "open_when")
        if screen is None:
            return "Цей розділ зараз вимкнено"
        await show(update, context, *screen)
        return None
    if action == "c":
        cat = DB.execute("SELECT * FROM categories WHERE id=?", (int(parts[2]),)).fetchone()
        if not cat or cat["section"] not in SECTION_FEATURE:
            return "Категорію не знайдено"
        if not feature_on(SECTION_FEATURE[cat["section"]]):
            return "Цей розділ зараз вимкнено"
        msg = pick_message(category_id=cat["id"])
        if not msg:
            return "У цій категорії поки порожньо"
        back = "u:sup" if cat["section"] == "support" else "u:ow"
        await show(update, context, f"{cat['title']}\n\n{msg}",
                   kb([[btn("🔁 Ще одне", f"u:c:{cat['id']}")], [btn("⬅️ Назад", back)]]))
        return None
    if action == "s":
        if not feature_on("section_surprise"):
            return "Сюрпризи зараз вимкнено"
        msg = pick_message(section="surprise")
        if not msg:
            return "Сюрпризів поки немає"
        await show(update, context, f"🎁 Сюрприз!\n\n{msg}",
                   kb([[btn("🎁 Ще сюрприз", "u:s")], [btn("⬅️ Назад", "m:home")]]))
        return None
    return None


async def admin_callback(update, context, parts):
    chat_id = update.effective_chat.id
    act = parts[1]
    app = context.application

    async def go(screen, new=False):
        await show(update, context, screen[0], screen[1], new)

    async def ask(action, payload, prompt):
        set_pending(chat_id, action, payload)
        await show(update, context, prompt, cancel_kb())

    if act == "noop":
        return None
    if act == "menu":
        clear_pending(chat_id)
        await go(screen_admin_menu())
    elif act == "mode":
        await go(screen_mode())
    elif act == "sm":
        new_mode = parts[2]
        if new_mode not in MODE_LABEL:
            return None
        if get_setting("mode") == new_mode:
            return "Цей режим уже увімкнено"
        today = now_local().date().isoformat()
        set_setting("mode", new_mode)
        set_setting("together_since" if new_mode == "together" else "apart_since", today)
        _reset_daily()
        meeting = get_meeting_dt()
        if new_mode == "apart" and (not meeting or meeting <= now_local()):
            await ask("set_meeting", {}, "🌍 Режим «На відстані» увімкнено.\n\n" + PROMPTS["set_meeting"])
        else:
            await go(screen_mode())
    elif act == "set":
        key = parts[2]
        action = f"set_{key}"
        if action not in PROMPTS:
            return None
        await ask(action, {}, PROMPTS[action])

    # ── Тексти ──
    elif act == "tl":
        await go(screen_texts(int(parts[2])))
    elif act == "te":
        await go(screen_text_view(int(parts[2])))
    elif act == "tw":
        idx = int(parts[2])
        key, title, _d, group = TEXT_DEFS[idx]
        prompt = f"✏️ {title}\n\nНадішли новий текст одним повідомленням."
        if PLACEHOLDERS[group]:
            prompt += "\n\nМожна вставляти:\n" + "\n".join(f"{{{n}}} — {d}" for n, d in PLACEHOLDERS[group])
        await ask("text_edit", {"key": key}, prompt)
    elif act == "tr":
        idx = int(parts[2])
        DB.execute("DELETE FROM texts WHERE key=?", (TEXT_DEFS[idx][0],))
        await go(screen_text_view(idx))

    # ── Повідомлення й категорії ──
    elif act == "c":
        await go(screen_content())
    elif act == "sec":
        if parts[2] in SECTION_TITLES:
            await go(screen_section(parts[2]))
    elif act == "ml":
        await go(screen_category(int(parts[2]), int(parts[3]) if len(parts) > 3 else 0))
    elif act == "m":
        await go(screen_message(int(parts[2])))
    elif act == "cadd":
        if parts[2] in SECTION_TITLES:
            await ask("cat_add", {"section": parts[2]}, "➕ Введи назву нової категорії (можна з емодзі):")
    elif act == "cren":
        await ask("cat_ren", {"id": int(parts[2])}, "✏️ Введи нову назву категорії:")
    elif act == "madd":
        await ask("msg_add", {"cat": int(parts[2])},
                  "➕ Надішли текст повідомлення.\n\nЩоб додати кілька одразу — розділи їх рядком, "
                  "у якому лише три дефіси: ---")
    elif act == "med":
        await ask("msg_edit", {"id": int(parts[2])}, "✏️ Надішли новий текст повідомлення:")

    # ── Видалення з підтвердженням ──
    elif act == "x":
        await go(screen_confirm(parts[2], int(parts[3])))
    elif act == "y":
        what, oid = parts[2], int(parts[3])
        if what == "msg":
            row = DB.execute("SELECT category_id FROM messages WHERE id=?", (oid,)).fetchone()
            DB.execute("DELETE FROM messages WHERE id=?", (oid,))
            await go(screen_category(row["category_id"]) if row else screen_content())
        elif what == "cat":
            row = DB.execute("SELECT section FROM categories WHERE id=?", (oid,)).fetchone()
            DB.execute("DELETE FROM categories WHERE id=?", (oid,))
            _reset_daily()
            await go(screen_section(row["section"]) if row else screen_content())
        elif what == "sch":
            DB.execute("DELETE FROM schedules WHERE id=?", (oid,))
            reschedule_all_jobs(app)
            await go(screen_schedules())
        elif what == "ev":
            DB.execute("DELETE FROM events WHERE id=?", (oid,))
            await go(screen_events())

    # ── Розклад ──
    elif act == "sc":
        await go(screen_schedules())
    elif act == "sca":
        rows = [[btn(title, f"a:scn:{k}")] for k, (title, _f) in KINDS.items()]
        rows.append([btn("⬅️ Назад", "a:sc")])
        await show(update, context, "➕ Яке автоповідомлення додати?", kb(rows))
    elif act == "scn":
        if parts[2] in KINDS:
            default_mode = "apart" if parts[2] == "miss" else "any"
            cur = DB.execute(
                "INSERT INTO schedules(kind, hour, minute, days, mode) VALUES(?,?,?,?,?)",
                (parts[2], 12, 0, "all", default_mode),
            )
            reschedule_all_jobs(app)
            await go(screen_schedule(cur.lastrowid))
    elif act == "sce":
        await go(screen_schedule(int(parts[2])))
    elif act == "sct":
        await ask("sc_time", {"id": int(parts[2])}, "🕐 Введи новий час (ГГ:ХХ), наприклад 09:30:")
    elif act in ("scd", "scm"):
        sid = int(parts[2])
        r = DB.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
        if r:
            col, order = ("days", DAYS_ORDER) if act == "scd" else ("mode", MODE_ORDER)
            nxt = order[(order.index(r[col]) + 1) % len(order)]
            DB.execute(f"UPDATE schedules SET {col}=? WHERE id=?", (nxt, sid))
            reschedule_all_jobs(app)
        await go(screen_schedule(sid))
    elif act == "scs":
        r = DB.execute("SELECT * FROM schedules WHERE id=?", (int(parts[2]),)).fetchone()
        built = build_scheduled(r["kind"]) if r else None
        if not built:
            return "Для цього типу поки немає що надсилати"
        await context.bot.send_message(chat_id=chat_id, text="📨 Тест (бачиш лише ти):\n\n" + built[0][:4000],
                                       reply_markup=built[1])
        return "Тест надіслано"

    # ── Привітання ──
    elif act == "ev":
        await go(screen_events())
    elif act == "evv":
        await go(screen_event(int(parts[2])))
    elif act == "eva":
        await ask("ev_add_date", {}, "➕ Введи дату привітання: ДД.ММ (щороку) або ДД.ММ.РРРР\n"
                                      "Приклад: 16.08.2024 — тоді можна рахувати, скільки років минуло.")
    elif act == "evt":
        await ask("ev_text", {"id": int(parts[2])}, "✏️ Надішли новий текст привітання:")
    elif act == "evd":
        await ask("ev_date", {"id": int(parts[2])}, "📅 Введи нову дату: ДД.ММ або ДД.ММ.РРРР")

    # ── Функції, користувачі, стан ──
    elif act == "f":
        await go(screen_features())
    elif act == "ft":
        key = parts[2]
        if key in {k for k, _ in FEATURES}:
            set_setting(f"feat_{key}", "0" if feature_on(key) else "1")
        await go(screen_features())
    elif act == "u":
        await go(screen_users())
    elif act in ("ub", "uu"):
        uid = int(parts[2])
        if uid == ADMIN_ID:
            return None
        if act == "ub":
            DB.execute("UPDATE users SET role='blocked' WHERE chat_id=?", (uid,))
        else:
            DB.execute("DELETE FROM users WHERE chat_id=? AND role='blocked'", (uid,))
        await go(screen_users())
    elif act == "st":
        await go(screen_status(app))
    return None


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_private(update):
        return
    alert = None
    try:
        alert = await route_callback(update, context, query.data or "")
    finally:
        try:
            await query.answer(alert) if alert else await query.answer()
        except Exception:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Помилка в обробнику", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="😔 Щось пішло не так. Спробуй ще раз або натисни /start.",
            )
        except Exception:
            pass


# ═══════════════════════════ Health-сервер ═══════════════════════════
class HealthHandler(BaseHTTPRequestHandler):
    def _respond(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._respond()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health-сервер запущено на порту %s", PORT)


# ═══════════════════════════ Запуск ═══════════════════════════
def main() -> None:
    init_db(DB_PATH)
    start_health_server()

    application = Application.builder().token(BOT_TOKEN).build()
    if application.job_queue is None:
        raise SystemExit("Потрібен job-queue: pip install \"python-telegram-bot[job-queue]\"")

    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler(["start", "menu"], start_command, filters=private))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private, text_input_handler))
    application.add_error_handler(error_handler)

    reschedule_all_jobs(application)

    logger.info("Бот запущений.")
    application.run_polling()


if __name__ == "__main__":
    main()