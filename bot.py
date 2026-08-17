"""
Telegram-бот "До зустрічі" — щоденний лічильник днів + романтичне повідомлення.

Що робить:
- Щодня о заданій годині сам надсилає всім, хто залогінився, повідомлення:
  "День X з Y, залишилось днів: Z" + коротке романтичне повідомлення
  (щодня інше, без повторів, поки список не закінчиться).
- Під повідомленням кнопка: клік показує ЩЕ РАЗ те саме повідомлення
  (того самого дня). Новий текст з'являється тільки наступного дня.
- На 30-й і 60-й день від старту — окремі привітання (1 і 2 місяці).
- Останні 10 днів до кінцевої дати — кожного дня окремий "фінішний" текст.
- У день кінцевої дати — фінальне повідомлення.
- Є 2 ролі доступу за паролями:
    VIEWER (viewer)  — тільки отримує повідомлення й може тиснути кнопку.
    ADMIN  (admin)   — те саме + може змінювати дату кінця, час розсилки,
                        часовий пояс і т.д. прямо командами в чаті.

=================== ЯК ЗАПУСТИТИ ===================
1. Встанови залежності:
       pip install "python-telegram-bot[job-queue]" python-dotenv

2. Створи файл ".env" поруч зі скриптом (приклад — .env.example окремим
   файлом) і заповни його своїми значеннями.

3. Запусти бота:
       python love_countdown_bot.py

4. У Telegram кожен, хто хоче отримувати повідомлення, пише боту:
       /login ПАРОЛЬ
   Якщо пароль співпадає з VIEWER_PASSWORD — людина стає "viewer".
   Якщо з ADMIN_PASSWORD — стає "admin" (і теж отримує повідомлення).

5. Адмін може міняти параметри без зупинки бота, наприклад:
       /setenddate 2026-10-15
       /setsendtime 12:00
       /settimezone Europe/Warsaw
       /settings   — показати поточні налаштування
       /help       — список команд
=====================================================
"""

import json
import logging
import os
import random
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # можна обійтись і без python-dotenv, якщо ENV задані іншим способом

# ========================= ENV / НАЛАШТУВАННЯ ЗА ЗАМОВЧУВАННЯМ =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
VIEWER_PASSWORD = os.getenv("VIEWER_PASSWORD", "")

DEFAULT_START_DATE = os.getenv("START_DATE", "2026-08-16")   # YYYY-MM-DD
DEFAULT_END_DATE = os.getenv("END_DATE", "")                  # YYYY-MM-DD, порожньо = START_DATE + 90 днів
DEFAULT_SEND_HOUR = int(os.getenv("SEND_HOUR", "12"))
DEFAULT_SEND_MINUTE = int(os.getenv("SEND_MINUTE", "0"))
DEFAULT_TIMEZONE = os.getenv("TIMEZONE", "Europe/Warsaw")

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise SystemExit("Не задано BOT_TOKEN у .env")
if not ADMIN_PASSWORD or not VIEWER_PASSWORD:
    raise SystemExit("Не задано ADMIN_PASSWORD і/або VIEWER_PASSWORD у .env")

# ========================= ТЕКСТИ =========================

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

# Останні 10 днів (index 0 = залишилось 10 днів, ..., index 9 = залишився 1 день)
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

# ========================= СТАН =========================

def default_state() -> dict:
    return {
        "settings": {
            "start_date": DEFAULT_START_DATE,
            "end_date": DEFAULT_END_DATE or None,  # None -> порахується як start+90
            "send_hour": DEFAULT_SEND_HOUR,
            "send_minute": DEFAULT_SEND_MINUTE,
            "timezone": DEFAULT_TIMEZONE,
        },
        "users": {},              # {"123456": {"role": "admin"}}
        "message_state": {
            "last_sent_date": None,
            "last_message": None,
            "used_indices": [],
        },
    }


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        base = default_state()
        base["settings"].update(state.get("settings", {}))
        base["users"].update(state.get("users", {}))
        base["message_state"].update(state.get("message_state", {}))
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


# ========================= ЛОГІКА ДНІВ =========================

def build_daily_text(state: dict) -> str:
    settings = state["settings"]
    start_dt = get_start_date(settings)
    end_dt = get_end_date(settings)

    today = date.today()
    elapsed_days = (today - start_dt).days
    total_days = (end_dt - start_dt).days
    days_left = max((end_dt - today).days, 0)
    day_number = elapsed_days + 1

    header = f"📅 День {day_number} з {total_days}\n⏳ Залишилось днів: {days_left}\n\n"

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


def build_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💌 Показати ще раз", callback_data="show_today")]]
    )


# ========================= ДОСТУП / РОЛІ =========================

def get_role(state: dict, chat_id: int):
    user = state["users"].get(str(chat_id))
    return user["role"] if user else None


def is_admin(state: dict, chat_id: int) -> bool:
    return get_role(state, chat_id) == "admin"


# ========================= ЩОДЕННА РОЗСИЛКА =========================

async def send_daily_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if not state["users"]:
        logger.warning("Немає жодного залогіненого користувача — нікому слати.")
        return

    text = build_daily_text(state)

    for chat_id in list(state["users"].keys()):
        try:
            await context.bot.send_message(chat_id=int(chat_id), text=text, reply_markup=build_keyboard())
        except Exception as e:
            logger.error("Не вдалось надіслати повідомлення %s: %s", chat_id, e)

    state["message_state"]["last_sent_date"] = date.today().isoformat()
    state["message_state"]["last_message"] = text
    save_state(state)


def reschedule_daily_job(application: Application, state: dict) -> None:
    for job in application.job_queue.get_jobs_by_name("daily_countdown"):
        job.schedule_removal()

    settings = state["settings"]
    tz = ZoneInfo(settings["timezone"])
    application.job_queue.run_daily(
        send_daily_message,
        time=time(hour=settings["send_hour"], minute=settings["send_minute"], tzinfo=tz),
        name="daily_countdown",
    )
    logger.info(
        "Розсилка запланована на %02d:%02d (%s)",
        settings["send_hour"], settings["send_minute"], settings["timezone"],
    )


# ========================= КОМАНДИ =========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привіт! 💛\nЩоб отримувати щоденні повідомлення, введи:\n/login ПАРОЛЬ"
    )


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Введи пароль так: /login ПАРОЛЬ")
        return

    password = context.args[0]
    state = load_state()

    if password == ADMIN_PASSWORD:
        role = "admin"
    elif password == VIEWER_PASSWORD:
        role = "viewer"
    else:
        await update.message.reply_text("❌ Невірний пароль.")
        return

    state["users"][str(chat_id)] = {"role": role}
    save_state(state)

    if role == "admin":
        await update.message.reply_text(
            "✅ Вхід виконано як ADMIN. Ти отримуватимеш повідомлення і можеш "
            "змінювати налаштування.\nНабери /help, щоб побачити команди."
        )
    else:
        await update.message.reply_text("✅ Вхід виконано. Тепер ти отримуватимеш щоденні повідомлення 💛")


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    role = get_role(state, update.effective_chat.id)
    await update.message.reply_text(f"Твоя роль: {role or 'не залогінений'}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    text = (
        "/login ПАРОЛЬ — увійти й почати отримувати повідомлення\n"
        "/whoami — показати свою роль\n"
        "/settings — показати поточні налаштування\n"
    )
    if is_admin(state, update.effective_chat.id):
        text += (
            "\nАдмін-команди:\n"
            "/setenddate YYYY-MM-DD — задати кінцеву дату (наприклад /setenddate 2026-10-15)\n"
            "/setstartdate YYYY-MM-DD — задати дату старту\n"
            "/setsendtime HH:MM — час щоденної розсилки\n"
            "/settimezone Регіон/Місто — часовий пояс (наприклад Europe/Warsaw)\n"
        )
    await update.message.reply_text(text)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    settings = state["settings"]
    start_dt = get_start_date(settings)
    end_dt = get_end_date(settings)
    await update.message.reply_text(
        "⚙️ Поточні налаштування:\n"
        f"Старт: {start_dt.isoformat()}\n"
        f"Кінець: {end_dt.isoformat()}\n"
        f"Усього днів: {(end_dt - start_dt).days}\n"
        f"Час розсилки: {settings['send_hour']:02d}:{settings['send_minute']:02d}\n"
        f"Часовий пояс: {settings['timezone']}"
    )


def admin_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = load_state()
        if not is_admin(state, update.effective_chat.id):
            await update.message.reply_text("⛔ Ця команда доступна тільки адміну.")
            return
        await handler(update, context, state)
    return wrapper


@admin_only
async def setenddate_command(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    if not context.args:
        await update.message.reply_text("Формат: /setenddate 2026-10-15")
        return
    try:
        new_date = date.fromisoformat(context.args[0])
    except ValueError:
        await update.message.reply_text("Невірний формат дати. Приклад: 2026-10-15")
        return
    state["settings"]["end_date"] = new_date.isoformat()
    save_state(state)
    await update.message.reply_text(f"✅ Кінцева дата оновлена: {new_date.isoformat()}")


@admin_only
async def setstartdate_command(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    if not context.args:
        await update.message.reply_text("Формат: /setstartdate 2026-08-16")
        return
    try:
        new_date = date.fromisoformat(context.args[0])
    except ValueError:
        await update.message.reply_text("Невірний формат дати. Приклад: 2026-08-16")
        return
    state["settings"]["start_date"] = new_date.isoformat()
    save_state(state)
    await update.message.reply_text(f"✅ Дата старту оновлена: {new_date.isoformat()}")


@admin_only
async def setsendtime_command(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    if not context.args:
        await update.message.reply_text("Формат: /setsendtime 12:00")
        return
    try:
        hour_str, minute_str = context.args[0].split(":")
        hour, minute = int(hour_str), int(minute_str)
        assert 0 <= hour <= 23 and 0 <= minute <= 59
    except Exception:
        await update.message.reply_text("Невірний формат часу. Приклад: 12:00")
        return
    state["settings"]["send_hour"] = hour
    state["settings"]["send_minute"] = minute
    save_state(state)
    reschedule_daily_job(context.application, state)
    await update.message.reply_text(f"✅ Час розсилки оновлено: {hour:02d}:{minute:02d}")


@admin_only
async def settimezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    if not context.args:
        await update.message.reply_text("Формат: /settimezone Europe/Warsaw")
        return
    tz_name = context.args[0]
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        await update.message.reply_text("Невідомий часовий пояс. Приклад: Europe/Warsaw")
        return
    state["settings"]["timezone"] = tz_name
    save_state(state)
    reschedule_daily_job(context.application, state)
    await update.message.reply_text(f"✅ Часовий пояс оновлено: {tz_name}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    state = load_state()
    chat_id = query.message.chat_id
    if get_role(state, chat_id) is None:
        await context.bot.send_message(chat_id=chat_id, text="Спочатку увійди: /login ПАРОЛЬ")
        return

    today_str = date.today().isoformat()
    ms = state["message_state"]

    if ms.get("last_sent_date") == today_str and ms.get("last_message"):
        text = ms["last_message"]
    else:
        text = build_daily_text(state)
        ms["last_sent_date"] = today_str
        ms["last_message"] = text
        save_state(state)

    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=build_keyboard())


# ========================= ЗАПУСК =========================

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("login", login_command))
    application.add_handler(CommandHandler("whoami", whoami_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("setenddate", setenddate_command))
    application.add_handler(CommandHandler("setstartdate", setstartdate_command))
    application.add_handler(CommandHandler("setsendtime", setsendtime_command))
    application.add_handler(CommandHandler("settimezone", settimezone_command))
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^show_today$"))

    state = load_state()
    reschedule_daily_job(application, state)

    logger.info("Бот запущений.")
    application.run_polling()


if __name__ == "__main__":
    main()