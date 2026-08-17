import json
import logging
import os
import random
import threading
from datetime import date, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
VIEWER_PASSWORD = os.getenv("VIEWER_PASSWORD", "")

DEFAULT_START_DATE = os.getenv("START_DATE", "2026-08-16")
DEFAULT_END_DATE = os.getenv("END_DATE", "")

PORT = int(os.getenv("PORT", "10000"))

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise SystemExit("Не задано BOT_TOKEN у .env")
if not ADMIN_PASSWORD or not VIEWER_PASSWORD:
    raise SystemExit("Не задано ADMIN_PASSWORD і/або VIEWER_PASSWORD у .env")

ROMANTIC_MESSAGES = [
    "Кожен день без тебе — це день, який я рахую, щоб швидше обійняти тебе знову.",
    "Навіть за кордоном я щоранку прокидаюсь із думкою про тебе.",
    "Відстань — це просто цифра. Моє серце все одно поруч з тобою.",
    "Сьогодні знову ловив себе на думці, як сильно за тобою скучаю.",
    "Ти — та причина, чому кожен новий день тут має сенс.",
    "Найкраща частина дня — коли думаю про нашу зустріч.",
    "Тримайся, кохана. Ми вже на крок ближче одне до одного.",
    "Хочу, щоб ти знала: ти в моїх думках з ранку до вечора.",
    "Навіть найдовші дні минають швидше, коли думаю про тебе.",
    "Твоя усмішка — це те, за чим я скучаю найбільше.",
    "Кожен вечір я подумки бажаю тобі на добраніч і цілую в лоба.",
    "Я пишаюся тим, якою сильною ти є, поки ми на відстані.",
    "Скоро обійму тебе так міцно, що ти відчуєш усі ці дні розлуки в одних обіймах.",
    "Ти робиш мене кращим навіть на відстані сотень кілометрів.",
    "Дякую, що чекаєш. Це найцінніше, що в мене є.",
    "Іноді просто зупиняюсь і уявляю твій голос — і стає тепліше.",
    "Наша історія лише починається, і я хочу прожити її всю з тобою.",
    "Ти — мій дім, навіть коли я далеко.",
    "Люблю тебе більше з кожним прожитим тут днем.",
    "Одного дня ці дні розлуки стануть просто спогадом, а не реальністю.",
    "Найкраще у моєму дні — знати, що ти є.",
    "Скучаю за твоїми руками, за твоїм сміхом, за тобою всією.",
    "Хай сьогодні у тебе буде день, який гідний твоєї усмішки.",
    "Я з тобою серцем, навіть коли не поруч тілом.",
    "Час тут тягнеться повільно, але я знаю — це того варте.",
    "Твоя підтримка додає мені сил кожного дня.",
    "Уявляю нашу першу зустріч — і серце починає битися швидше.",
    "Ти найкраще, що зі мною сталося.",
    "Кожен день наближає нас до моменту, коли я знову триматиму тебе за руку.",
    "Хочу знати все про твій день, навіть найдрібніше.",
    "Ти вартуєш кожного дня очікування.",
    "Наша любов сильніша за будь-яку відстань.",
    "Сьогодні згадував, як ти смієшся — і мені стало тепло на душі.",
    "Бережи себе, кохана. Я скоро буду поруч.",
    "Ти надихаєш мене триматися і йти далі.",
    "Найтепліші думки цього дня — про тебе.",
    "Разом ми пройдемо через все, навіть через кілометри між нами.",
    "Люблю тебе не менше, а навіть більше з кожним днем.",
    "Твій образ — перше, про що я думаю, прокидаючись.",
    "Скоро всі ці дні складуться в спогад про те, як ми чекали одне одного.",
    "Дякую, що ти є в моєму житті.",
    "Кожна секунда наближає нас до зустрічі.",
    "Хочу побудувати з тобою багато нових спогадів, коли повернуся.",
    "Ти моя найкраща мотивація триматися тут.",
    "Уявляю, як міцно обійму тебе першого ж дня.",
    "Навіть на відстані ти — моя опора.",
    "Твоя любов зігріває мене навіть у чужій країні.",
    "Хочу, щоб ти знала: чекання того варте.",
    "Кожного дня закохуюсь у тебе трохи більше.",
    "Скоро ці дні стануть просто цифрою в минулому, а ми — знову разом.",
    "Щоранку перше, що роблю — думаю, скільки днів лишилось до тебе.",
    "Ти — той спокій, якого мені бракує в чужому місті.",
    "Найдовші черги й найнудніші справи тут стають легшими, бо в кінці дня є ти.",
    "Хочу вкрасти хвилинку часу і провести її поруч з тобою, хоч подумки.",
    "Твій сміх — мелодія, яку я вмикаю в голові, коли важко.",
    "Обіцяю: коли повернусь, надолужимо кожен пропущений день.",
    "Мені бракує твого тепла, навіть якщо надворі літо.",
    "Кожного разу, коли бачу захід сонця, думаю — а вона теж зараз на нього дивиться?",
    "Ти — та причина, чому я щодня тримаюсь у формі, а не опускаю руки.",
    "Хочу знову засинати, чуючи твоє дихання поруч.",
    "Пишаюсь тобою більше, ніж можу висловити словами.",
    "Кожен новий день — ще один крок ближче до нашого спільного ранку.",
    "Твоя фотографія на екрані — маленький промінь домашнього тепла тут.",
    "Хочу знову тримати тебе за руку, коли ми просто мовчки йдемо разом.",
    "Найкращий подарунок, який я собі уявляю — твої обійми на порозі.",
    "Ти навчила мене, що чекання теж може бути формою любові.",
    "Іноді просто перечитую наше листування, щоб відчути тебе ближче.",
    "Твоя віра в мене тримає мене на плаву в найважчі дні.",
    "Скучаю навіть за дрібницями — як ти морщиш ніс, коли смієшся.",
    "Хочу знову готувати з тобою вечерю і не поспішати нікуди.",
    "Дні тут інші, але думки про тебе завжди однакові — теплі.",
    "Ти — мій дім навіть тоді, коли я в іншій країні.",
    "Хочу знову засипати тебе дурними жартами, як раніше.",
    "Наша відстань тимчасова, а почуття — назавжди.",
    "Ти робиш мене хоробрішим, навіть коли сама далеко.",
    "Люблю уявляти, як одного дня розповідатиму тобі про кожен день тут.",
    "Скоро зможу дивитись тобі в очі, а не в екран телефону.",
    "Твоя любов — мій найнадійніший компас у чужій країні.",
    "Хочу знову разом планувати найзвичайніші вихідні.",
    "Кожен прожитий тут день — це і мій, і твій маленький подвиг.",
    "Дякую, що не даєш мені почуватись самотнім, навіть на відстані.",
    "Хочу знову йти з тобою за руку без жодного плану, куди саме.",
    "Твій голос по телефону — найкраща частина будь-якого дня.",
    "Скучаю за нашими дурними суперечками про те, що дивитись увечері.",
    "Ти — той дім, куди я завжди повертаюсь думками.",
    "Хочу знову бачити, як ти прокидаєшся вранці, ще сонна й тепла.",
    "Наша любов росте навіть на відстані, а не в'яне.",
    "Кожен твій день, навіть найзвичайніший, важливий для мене.",
    "Скоро всі ці смс замінять справжні обійми.",
    "Ти єдина, з ким я хочу ділитись і поганими, і хорошими днями.",
    "Хочу знову разом сміятись до сліз через якусь дурницю.",
    "Твоя терплячість — це те, за що я тебе безмежно ціную.",
    "Кожен день тут я стаю трохи ближчим до дому — до тебе.",
    "Хочу знову цілувати тебе на прощання, знаючи, що бачимось ввечері.",
    "Скучаю за запахом твого волосся і твоїми теплими руками.",
    "Ти надихаєш мене не здаватись навіть у найважчі моменти.",
    "Хочу знову ділити з тобою одну каву на двох вранці.",
    "Наша зустріч буде варта кожного дня цього очікування.",
    "Ти — та людина, з якою я хочу ділити всі майбутні роки.",
    "Кожен день наближає мене до твоїх обіймів, і це мене тримає.",
    "Скучаю навіть за твоїми звичками, які раніше здавались дрібницею.",
    "Хочу знову засинати з думкою «завтра я побачу її», а не «завтра ще один день».",
    "Твоя любов — найтепліше, що є в моєму житті прямо зараз.",
]

MONTH_1_MESSAGE = (
    "🎉 Місяць позаду! Цілих 30 днів ми витримали цю відстань — "
    "і з кожним днем я люблю тебе тільки сильніше. Дякую, що чекаєш на мене."
)
MONTH_2_MESSAGE = (
    "🎊 Два місяці! 60 днів позаду — і це означає, що зустріч ближче, ніж будь-коли. "
    "Ти неймовірна, що витримуєш це разом зі мною."
)
FINAL_WEEK_MESSAGES = [
    "🔥 10 днів до зустрічі! Це вже можна рахувати на пальцях однієї руки (майже).",
    "9 днів. Уявляю, як відкриються двері — і там ти.",
    "8 днів. Серце вже зараз калатає, коли думаю про нашу зустріч.",
    "7 днів — рівно тиждень! Готуй найкращу усмішку, вона мені знадобиться.",
    "6 днів. Я вже подумки складаю список того, що зроблю в перші 5 хвилин зустрічі.",
    "5 днів! Стільки ж, скільки пальців на руці — і ця рука скоро триматиме твою.",
    "4 дні. Час ніби спеціально сповільнився, щоб перевірити моє терпіння.",
    "3 дні! Можна вже рахувати на пальцях однієї руки, не задіюючи другу.",
    "2 дні. Завтра-післязавтра — і ми поруч. Затримай подих, майже прийшли.",
    "1 день!!! Завтра я тебе обійму. Спи спокійно — це остання ніч розлуки.",
]
FINAL_MESSAGE = (
    "🏁 Всі дні позаду. Це було довго, але ми впорались. "
    "Дякую, що чекала, дякую, що вірила. Тепер — тільки ми, без кілометрів між нами. ❤️"
)

LOVE_MESSAGE = "Я тебе люблю ❤️"
MISS_YOU_MESSAGE = "Скучаю 🥺"


def default_state() -> dict:
    return {
        "settings": {
            "start_date": DEFAULT_START_DATE,
            "end_date": DEFAULT_END_DATE or None,
            "morning_hour": 9, "morning_minute": 0,
            "love_times": [
                {"hour": 12, "minute": 0},
                {"hour": 16, "minute": 0},
                {"hour": 22, "minute": 0},
            ],
            "weekend_hour": 15, "weekend_minute": 0,
            "timezone": "Europe/Warsaw",
        },
        "users": {},
        "message_state": {
            "last_sent_date": None,
            "last_message": None,
            "used_indices": [],
        },
        "pending": {},
    }


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        base = default_state()
        base["settings"].update(state.get("settings", {}))
        base["users"].update(state.get("users", {}))
        base["message_state"].update(state.get("message_state", {}))
        base["pending"].update(state.get("pending", {}))
        return base
    return default_state()


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_start_date(settings: dict) -> date:
    return date.fromisoformat(settings["start_date"])


def get_end_date(settings: dict) -> date:
    if settings.get("end_date"):
        return date.fromisoformat(settings["end_date"])
    return get_start_date(settings) + timedelta(days=90)


def pick_romantic_message(message_state: dict) -> str:
    used = set(message_state.get("used_indices", []))
    available = [i for i in range(len(ROMANTIC_MESSAGES)) if i not in used]
    if not available:
        used = set()
        available = list(range(len(ROMANTIC_MESSAGES)))
    idx = random.choice(available)
    used.add(idx)
    message_state["used_indices"] = list(used)
    return ROMANTIC_MESSAGES[idx]


def build_morning_text(state: dict) -> str:
    settings = state["settings"]
    start_dt = get_start_date(settings)
    end_dt = get_end_date(settings)

    today = date.today()
    elapsed_days = (today - start_dt).days
    total_days = (end_dt - start_dt).days
    days_left = max((end_dt - today).days, 0)
    day_number = elapsed_days + 1

    header = (
        f"☀️ Доброго ранку!\n\n"
        f"📅 День {day_number} з {total_days}\n"
        f"⏳ Залишилось днів: {days_left}\n\n"
    )

    if days_left == 0:
        body = FINAL_MESSAGE
    elif elapsed_days == 30:
        body = MONTH_1_MESSAGE
    elif elapsed_days == 60:
        body = MONTH_2_MESSAGE
    elif 0 < days_left <= 10:
        body = FINAL_WEEK_MESSAGES[10 - days_left]
    else:
        body = pick_romantic_message(state["message_state"])

    return header + body


def get_or_build_today_message(state: dict) -> str:
    today_str = date.today().isoformat()
    ms = state["message_state"]
    if ms.get("last_sent_date") == today_str and ms.get("last_message"):
        return ms["last_message"]
    text = build_morning_text(state)
    ms["last_sent_date"] = today_str
    ms["last_message"] = text
    save_state(state)
    return text


def build_show_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💌 Показати ще раз", callback_data="show_today")]]
    )


def get_role(state: dict, chat_id: int):
    user = state["users"].get(str(chat_id))
    return user["role"] if user else None


def is_admin(state: dict, chat_id: int) -> bool:
    return get_role(state, chat_id) == "admin"


async def broadcast(context: ContextTypes.DEFAULT_TYPE, text: str, keyboard: InlineKeyboardMarkup = None) -> None:
    state = load_state()
    if not state["users"]:
        return
    for chat_id in list(state["users"].keys()):
        try:
            await context.bot.send_message(chat_id=int(chat_id), text=text, reply_markup=keyboard)
        except Exception as e:
            logger.error("Не вдалось надіслати %s: %s", chat_id, e)


async def job_morning(context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    text = get_or_build_today_message(state)
    await broadcast(context, text, build_show_keyboard())


async def job_love(context: ContextTypes.DEFAULT_TYPE) -> None:
    await broadcast(context, LOVE_MESSAGE)


async def job_weekend(context: ContextTypes.DEFAULT_TYPE) -> None:
    await broadcast(context, MISS_YOU_MESSAGE)


JOB_NAMES = ["morning", "love_0", "love_1", "love_2", "weekend"]


def reschedule_all_jobs(application: Application, state: dict) -> None:
    for name in JOB_NAMES:
        for job in application.job_queue.get_jobs_by_name(name):
            job.schedule_removal()

    settings = state["settings"]
    tz = ZoneInfo(settings["timezone"])

    application.job_queue.run_daily(
        job_morning,
        time=time(hour=settings["morning_hour"], minute=settings["morning_minute"], tzinfo=tz),
        name="morning",
    )
    for i, lt in enumerate(settings["love_times"]):
        application.job_queue.run_daily(
            job_love,
            time=time(hour=lt["hour"], minute=lt["minute"], tzinfo=tz),
            name=f"love_{i}",
        )
    application.job_queue.run_daily(
        job_weekend,
        time=time(hour=settings["weekend_hour"], minute=settings["weekend_minute"], tzinfo=tz),
        days=(5, 6),
        name="weekend",
    )
    logger.info("Завдання переплановані.")


def main_menu_keyboard(state: dict, chat_id: int) -> InlineKeyboardMarkup:
    role = get_role(state, chat_id)
    rows = []
    if role is None:
        rows.append([InlineKeyboardButton("🔑 Увійти", callback_data="login")])
    else:
        rows.append([InlineKeyboardButton("💌 Показати", callback_data="show_today")])
        if role == "admin":
            rows.append([InlineKeyboardButton("⚙️ Налаштування", callback_data="adm_menu")])
    return InlineKeyboardMarkup(rows)


def admin_menu_text(state: dict) -> str:
    s = state["settings"]
    start_dt = get_start_date(s)
    end_dt = get_end_date(s)
    love_str = ", ".join(f"{t['hour']:02d}:{t['minute']:02d}" for t in s["love_times"])
    return (
        "⚙️ Налаштування бота\n\n"
        f"Старт: {start_dt.isoformat()}\n"
        f"Кінець: {end_dt.isoformat()} (всього днів: {(end_dt - start_dt).days})\n"
        f"Ранкове повідомлення: {s['morning_hour']:02d}:{s['morning_minute']:02d}\n"
        f"«Я тебе люблю»: {love_str}\n"
        f"«Скучаю» (сб/нд): {s['weekend_hour']:02d}:{s['weekend_minute']:02d}\n"
        f"Часовий пояс: {s['timezone']}\n\n"
        "Що змінити?"
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Дата старту", callback_data="adm_set_startdate")],
        [InlineKeyboardButton("📅 Кінцева дата", callback_data="adm_set_enddate")],
        [InlineKeyboardButton("☀️ Час ранкового повідомлення", callback_data="adm_set_morning")],
        [InlineKeyboardButton("❤️ Часи 'Я тебе люблю'", callback_data="adm_love_menu")],
        [InlineKeyboardButton("🥺 Час 'Скучаю' (сб/нд)", callback_data="adm_set_weekend")],
        [InlineKeyboardButton("🌍 Часовий пояс", callback_data="adm_set_timezone")],
        [InlineKeyboardButton("👥 Користувачі", callback_data="adm_users")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])


def love_menu_keyboard(state: dict) -> InlineKeyboardMarkup:
    s = state["settings"]
    rows = []
    for i, t in enumerate(s["love_times"]):
        rows.append([InlineKeyboardButton(
            f"Час {i + 1}: {t['hour']:02d}:{t['minute']:02d}",
            callback_data=f"adm_set_love_{i}"
        )])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm_menu")])
    return InlineKeyboardMarkup(rows)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Скасувати", callback_data="cancel_input")]])


async def send_main_menu(context, chat_id, state) -> None:
    role = get_role(state, chat_id)
    if role is None:
        text = "Привіт! 💛\nЩоб отримувати повідомлення, увійди за паролем."
    elif role == "admin":
        text = "Привіт, адміне 👋"
    else:
        text = "Привіт 💛"
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=main_menu_keyboard(state, chat_id))


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    await send_main_menu(context, update.effective_chat.id, state)


PENDING_PROMPTS = {
    "login": "🔑 Введи пароль:",
    "set_startdate": "Введи нову дату старту у форматі РРРР-ММ-ДД (наприклад 2026-08-16):",
    "set_enddate": "Введи нову кінцеву дату у форматі РРРР-ММ-ДД (наприклад 2026-11-14):",
    "set_morning": "Введи новий час ранкового повідомлення у форматі ГГ:ХХ (наприклад 09:00):",
    "set_weekend": "Введи новий час повідомлення «Скучаю» у форматі ГГ:ХХ (наприклад 15:00):",
    "set_timezone": "Введи новий часовий пояс (наприклад Europe/Warsaw):",
    "set_love_0": "Введи новий час 1-го «Я тебе люблю» у форматі ГГ:ХХ:",
    "set_love_1": "Введи новий час 2-го «Я тебе люблю» у форматі ГГ:ХХ:",
    "set_love_2": "Введи новий час 3-го «Я тебе люблю» у форматі ГГ:ХХ:",
}


def parse_time_str(value: str):
    try:
        h_str, m_str = value.strip().split(":")
        h, m = int(h_str), int(m_str)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return None


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    state = load_state()
    pending = state["pending"].get(str(chat_id))
    if not pending:
        return

    action = pending["action"]
    value = update.message.text.strip()

    if action == "login":
        if value == ADMIN_PASSWORD:
            role = "admin"
        elif value == VIEWER_PASSWORD:
            role = "viewer"
        else:
            await update.message.reply_text("❌ Невірний пароль. Спробуй ще раз або натисни /start.")
            return
        state["users"][str(chat_id)] = {"role": role}
        state["pending"].pop(str(chat_id), None)
        save_state(state)
        await update.message.reply_text("✅ Вхід виконано!")
        await send_main_menu(context, chat_id, state)
        return

    if not is_admin(state, chat_id):
        state["pending"].pop(str(chat_id), None)
        save_state(state)
        return

    if action in ("set_startdate", "set_enddate"):
        try:
            new_date = date.fromisoformat(value)
        except ValueError:
            await update.message.reply_text("Невірний формат. Приклад: 2026-11-14", reply_markup=cancel_keyboard())
            return
        key = "start_date" if action == "set_startdate" else "end_date"
        state["settings"][key] = new_date.isoformat()
        state["pending"].pop(str(chat_id), None)
        save_state(state)
        await update.message.reply_text(f"✅ Оновлено: {new_date.isoformat()}")

    elif action == "set_timezone":
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            await update.message.reply_text("Невідомий часовий пояс. Приклад: Europe/Warsaw", reply_markup=cancel_keyboard())
            return
        state["settings"]["timezone"] = value
        state["pending"].pop(str(chat_id), None)
        save_state(state)
        reschedule_all_jobs(context.application, state)
        await update.message.reply_text(f"✅ Часовий пояс оновлено: {value}")

    elif action in ("set_morning", "set_weekend"):
        parsed = parse_time_str(value)
        if not parsed:
            await update.message.reply_text("Невірний формат. Приклад: 09:00", reply_markup=cancel_keyboard())
            return
        h, m = parsed
        prefix = "morning" if action == "set_morning" else "weekend"
        state["settings"][f"{prefix}_hour"] = h
        state["settings"][f"{prefix}_minute"] = m
        state["pending"].pop(str(chat_id), None)
        save_state(state)
        reschedule_all_jobs(context.application, state)
        await update.message.reply_text(f"✅ Час оновлено: {h:02d}:{m:02d}")

    elif action.startswith("set_love_"):
        idx = int(action.split("_")[-1])
        parsed = parse_time_str(value)
        if not parsed:
            await update.message.reply_text("Невірний формат. Приклад: 12:00", reply_markup=cancel_keyboard())
            return
        h, m = parsed
        state["settings"]["love_times"][idx] = {"hour": h, "minute": m}
        state["pending"].pop(str(chat_id), None)
        save_state(state)
        reschedule_all_jobs(context.application, state)
        await update.message.reply_text(f"✅ Час {idx + 1} оновлено: {h:02d}:{m:02d}")

    await context.bot.send_message(
        chat_id=chat_id, text=admin_menu_text(state), reply_markup=admin_menu_keyboard()
    )


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data
    state = load_state()

    if data == "show_today":
        if get_role(state, chat_id) is None:
            await context.bot.send_message(chat_id=chat_id, text="Спочатку увійди 🔑", reply_markup=main_menu_keyboard(state, chat_id))
            return
        text = get_or_build_today_message(state)
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=build_show_keyboard())
        return

    if data == "login":
        state["pending"][str(chat_id)] = {"action": "login"}
        save_state(state)
        await context.bot.send_message(chat_id=chat_id, text=PENDING_PROMPTS["login"], reply_markup=cancel_keyboard())
        return

    if data == "back_main":
        await send_main_menu(context, chat_id, state)
        return

    if data == "cancel_input":
        state["pending"].pop(str(chat_id), None)
        save_state(state)
        await send_main_menu(context, chat_id, state)
        return

    if not is_admin(state, chat_id):
        await context.bot.send_message(chat_id=chat_id, text="⛔ Доступно тільки адміну.")
        return

    if data == "adm_menu":
        await context.bot.send_message(chat_id=chat_id, text=admin_menu_text(state), reply_markup=admin_menu_keyboard())
        return

    if data == "adm_love_menu":
        await context.bot.send_message(chat_id=chat_id, text="❤️ Обери, який час змінити:", reply_markup=love_menu_keyboard(state))
        return

    if data == "adm_users":
        if not state["users"]:
            text = "Поки що ніхто не залогінився."
        else:
            lines = [f"• {cid} — {info['role']}" for cid, info in state["users"].items()]
            text = "👥 Користувачі:\n" + "\n".join(lines)
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=admin_menu_keyboard())
        return

    action_map = {
        "adm_set_startdate": "set_startdate",
        "adm_set_enddate": "set_enddate",
        "adm_set_morning": "set_morning",
        "adm_set_weekend": "set_weekend",
        "adm_set_timezone": "set_timezone",
        "adm_set_love_0": "set_love_0",
        "adm_set_love_1": "set_love_1",
        "adm_set_love_2": "set_love_2",
    }
    if data in action_map:
        action = action_map[data]
        state["pending"][str(chat_id)] = {"action": action}
        save_state(state)
        await context.bot.send_message(chat_id=chat_id, text=PENDING_PROMPTS[action], reply_markup=cancel_keyboard())
        return


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


def main() -> None:
    start_health_server()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler))

    state = load_state()
    reschedule_all_jobs(application, state)

    logger.info("Бот запущений.")
    application.run_polling()


if __name__ == "__main__":
    main()