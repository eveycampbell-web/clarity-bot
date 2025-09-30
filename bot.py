import os, json, random, logging, asyncio
from pathlib import Path
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher.filters import Text
from dotenv import load_dotenv
import sqlite3

# ── ЛОГИ ─────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

# ── BASE & ENV ───────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_LINK = os.getenv("TELEGRAM_CHANNEL_LINK", "https://t.me/your_channel")
OWNER_USERNAME = (os.getenv("OWNER_USERNAME", "@your_username") or "").strip()
USAGE_FILE = BASE_DIR / "usage.json"   # замок на 7 дней
DB_PATH = BASE_DIR / "subscribers.db"  # база рассылки

if not TOKEN:
    raise RuntimeError("Нет токена. Откройте .env и пропишите BOT_TOKEN=...")

# ── БОТ ──────────────────────────────────────────────────
bot = Bot(token=TOKEN, parse_mode="HTML", timeout=120)
dp = Dispatcher(bot)

# ── ДИСКЛЕЙМЕРЫ и тексты ────────────────────────────────
WELCOME = (
    "Привет! Это бот «Карта ясности» ✨\n\n"
    "Нажми «Моя тема» → выбери один из трёх вопросов → выбери карту и получи мягкую подсказку на сегодня. "
    "Одна карта доступна <b>раз в 7 дней</b>, чтобы сохранять трезвый взгляд и пользу.\n\n"
    "Важно: бот носит развлекательный и познавательный характер, не является медицинской или профессиональной консультацией. <b>18+</b>"
)

HELP_TEXT = (
    "Как пользоваться:\n"
    "• Нажми «Моя тема» и выбери: «Что он(а) думает обо мне?», «Как зарабатывать больше?», «Мой скрытый талант».\n"
    "• Выбери карту 1–5 или «🎲 Случайно».\n"
    "• Прочитай краткий ответ, сделай простой шаг — и закрепи ясность.\n\n"
    f"Канал: {CHANNEL_LINK}\nСвязь: {OWNER_USERNAME}\n\n"
    "Дисклеймер: бот носит развлекательный и познавательный характер, не является медицинской или профессиональной консультацией. <b>18+</b>"
)

PRIVACY_TEXT = (
    "Конфиденциальность:\n"
    "— Бот не запрашивает личные данные и доступ к аккаунту.\n"
    "— Технически сохраняется только дата, когда вы в последний раз получали «Карту ясности» (чтобы сработал замок на 7 дней).\n"
    "— Хотите удалить — напишите мне в личку.\n\n"
    "Дисклеймер: бот носит развлекательный и познавательный характер, не является медицинской или профессиональной консультацией. <b>18+</b>"
)

CTA_TAIL = (
    f"\n\nПодписывайся на канал: {CHANNEL_LINK}\n\n"
    f"🎁 Напиши слово «ЯСНОСТЬ» в профиль {OWNER_USERNAME} — и получи скидку <b>50%</b> на первый разбор. Действует для новых клиентов. <b>18+</b>"
)

LOCK_TEXT = (
    "«Карта ясности» доступна <b>1 раз в неделю</b> — чтобы не зациклиться на переспрашивании и сохранить ценность первого взгляда. "
    "Пока идёт ожидание, в канале тебя уже ждут расклады, короткие практики и разборы — они помогают держать курс каждый день.\n\n"
    f"Загляни: {CHANNEL_LINK}\n\n"
    f"🎁 Не хочешь ждать и нужен личный разбор со <b>скидкой 50%</b>? Напиши слово «ЯСНОСТЬ» в профиль {OWNER_USERNAME}. Скидка 50% действует для новых клиентов. <b>18+</b>"
)

ABOUT_TEXT = (
    "Форматы: Таро / Нумерология / Астрология — фокус на твоих запросах.\n"
    "Что получишь: честные ответы на все волнующие тебя вопросы. Я рядом, чтобы помочь услышать, как хочешь жить именно ты 💚.\n\n"
    f"💬 Напиши «ЯСНОСТЬ» {OWNER_USERNAME} — подскажу формат и время. <b>18+</b>"
)

TOPIC_LABELS = {
    "think": "Что он(а) думает обо мне?",
    "money": "Как зарабатывать больше?",
    "talent": "Мой скрытый талант",
}

TEXTS = {
    "think": {
        "1": (
            "<b>Ответ:</b> интерес есть, но человек осторожничает. 😊\n"
            "<b>Шаг:</b> сделай лёгкий контакт без давления: короткое сообщение «Как ты?» — без разговоров «кто мы».\n"
            "«Когда рядом спокойно — чувства сами выбирают оставаться.»"
        ),
        "2": (
            "<b>Ответ:</b> видит в тебе опору, но боится раскрыться. 💛\n"
            "<b>Шаг:</b> скажи или напиши: «Мне тепло, когда мы общаемся чаще» и после предложи провести время вдвоём.\n"
            "«Безопасность открывает двери мягче любых слов.»"
        ),
        "3": (
            "<b>Ответ:</b> симпатия есть, но сравнивает и сомневается. 🤔\n"
            "<b>Шаг:</b> запиши 3 факта своей ценности (дела, а не ярлыки) и прояви один из них в следующем общении.\n"
            "«Ясность о себе делает чужие сомнения тише.»"
        ),
        "4": (
            "<b>Ответ:</b> восхищается твоей самостоятельностью, боится «не дотянуть». ✨\n"
            "<b>Шаг:</b> попроси о маленькой помощи по делу: «Подскажешь, как выбрать…?» — это сокращает дистанцию.\n"
            "«Сила притягивает, когда в ней есть место для другого.»"
        ),
        "5": (
            "<b>Ответ:</b> чувства есть, но сейчас перегружен(-а) делами. 🌧️\n"
            "<b>Шаг:</b> выбери формат «лёгкий контакт 48 часов»: короткие тёплые касания без серьёзных тем.\n"
            "«Иногда лучший шаг — мягкий шаг.»"
        ),
    },
    "money": {
        "1": (
            "<b>Ответ:</b> главный стоп — расфокус. 📌\n"
            "<b>Шаг:</b> один денежный шаг на сегодня: закрыть один счёт, отправить 3 отклика, созвониться по подработке — доведи до конца.\n"
            "«Фокус — ускоритель дохода.»"
        ),
        "2": (
            "<b>Ответ:</b> занижена собственная ценность. 💼\n"
            "<b>Шаг:</b> прибавь +10–15% к цене/ставке или попроси надбавку: «Готов(а) брать больше задач, прошу пересмотреть оплату до ___».\n"
            "«Деньги идут туда, где себя ценят.»"
        ),
        "3": (
            "<b>Ответ:</b> не видно твою пользу (дело не в навыках). 🔎\n"
            "<b>Шаг:</b> попроси у 2 людей конкретную обратную связь: «Что со мной особенно удобно? Что я делаю лучше всего?» — добавь это в резюме/диалоги.\n"
            "«Стань видим(ой) там, где ты уже полезен(на).»"
        ),
        "4": (
            "<b>Ответ:</b> деньги упираются в хаос. 📒\n"
            "<b>Шаг:</b> «финансовые 20 минут» сегодня: выписка доходов/расходов → один перевод/хвост → 1 план на неделю.\n"
            "«Порядок — уважение к своему потоку.»"
        ),
        "5": (
            "<b>Ответ:</b> растёшь в одиночку — потолок близко. 🤝\n"
            "<b>Шаг:</b> предложи знакомому(ой) простое взаимовыгодное дело: «Давай вместе возьмём маленький проект/смену» или обмен навыками.\n"
            "«Доход любит партнёрства.»"
        ),
    },
    "talent": {
        "1": (
            "<b>Ответ:</b> объясняешь сложное просто. 💡\n"
            "<b>Шаг:</b> выбери одну тему и объясни её близкому за 3–5 минут простыми словами; проверь, что понял(а).\n"
            "«Быть понятным — редкий дар.»"
        ),
        "2": (
            "<b>Ответ:</b> соединяешь людей и идеи. 🌉\n"
            "<b>Шаг:</b> познакомь двух знакомых, которым полезно встретиться, и кратко напиши — чем они могут помочь друг другу.\n"
            "«Там, где ты — появляются мосты.»"
        ),
        "3": (
            "<b>Ответ:</b> тонкое чувство вкуса/нюанса. 🎨\n"
            "<b>Шаг:</b> сделай «выбор дня»: одна вещь/мысль/музыка — и 2 предложения, почему это работает для тебя.\n"
            "«Чувствительность — сила, когда у неё есть форма.»"
        ),
        "4": (
            "<b>Ответ:</b> видишь стратегию и шаги. 🧭\n"
            "<b>Шаг:</b> распиши одну цель в 3 шага на 7 дней (шаги должны быть измеримы) и сделай первый сегодня.\n"
            "«Путь короче, когда виден план.»"
        ),
        "5": (
            "<b>Ответ:</b> собираешь смысл из хаоса. 🔦\n"
            "<b>Шаг:</b> выбери запутанную тему и сформулируй её суть в 5 предложениях — для себя.\n"
            "«Смысл — свет, который ты умеешь включать.»"
        ),
    },
}

# ── Клавиатуры ───────────────────────────────────────────
KB_MAIN = ReplyKeyboardMarkup(resize_keyboard=True)
KB_MAIN.add(KeyboardButton("Моя тема"))
KB_MAIN.add(KeyboardButton("О консультации"), KeyboardButton("Канал"))

def topic_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(TOPIC_LABELS["think"], callback_data="topic:think"))
    kb.add(InlineKeyboardButton(TOPIC_LABELS["money"], callback_data="topic:money"))
    kb.add(InlineKeyboardButton(TOPIC_LABELS["talent"], callback_data="topic:talent"))
    return kb

def card_keyboard(topic_code: str):
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("1", callback_data=f"card:{topic_code}:1"),
        InlineKeyboardButton("2", callback_data=f"card:{topic_code}:2"),
        InlineKeyboardButton("3", callback_data=f"card:{topic_code}:3"),
    )
    kb.add(
        InlineKeyboardButton("4", callback_data=f"card:{topic_code}:4"),
        InlineKeyboardButton("5", callback_data=f"card:{topic_code}:5"),
        InlineKeyboardButton("🎲 Случайно", callback_data=f"card:{topic_code}:rnd"),
    )
    return kb

# ── Учёт «1 карта в 7 дней» ─────────────────────────────
def load_usage():
    if not USAGE_FILE.exists():
        return {}
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_usage(data):
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def can_draw(user_id: int) -> bool:
    data = load_usage()
    sid = str(user_id)
    last_iso = data.get(sid)
    if not last_iso:
        return True
    try:
        last_dt = datetime.fromisoformat(last_iso)
    except Exception:
        return True
    return datetime.utcnow() - last_dt >= timedelta(days=7)

def mark_draw(user_id: int):
    data = load_usage()
    data[str(user_id)] = datetime.utcnow().isoformat()
    save_usage(data)

# ── БАЗА РАССЫЛКИ ────────────────────────────────────────
def db_init():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
              user_id INTEGER PRIMARY KEY,
              username TEXT,
              is_subscribed INTEGER DEFAULT 0,
              created_at TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()

def upsert_user(user: types.User, subscribe_flag: int = None):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT user_id FROM subscribers WHERE user_id=?", (user.id,))
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO subscribers (user_id, username, is_subscribed, created_at) VALUES (?, ?, ?, ?)",
                (user.id, user.username, 1 if subscribe_flag else 0, datetime.utcnow().isoformat())
            )
        else:
            if subscribe_flag is not None:
                conn.execute(
                    "UPDATE subscribers SET is_subscribed=?, username=? WHERE user_id=?",
                    (1 if subscribe_flag else 0, user.username, user.id)
                )
            else:
                conn.execute("UPDATE subscribers SET username=? WHERE user_id=?", (user.username, user.id))
        conn.commit()
    finally:
        conn.close()

def set_subscribe(user_id: int, value: bool):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE subscribers SET is_subscribed=? WHERE user_id=?", (1 if value else 0, user_id))
        conn.commit()
    finally:
        conn.close()

def get_all_subscribed_ids():
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT user_id FROM subscribers WHERE is_subscribed=1")
        rows = [r[0] for r in cur.fetchall()]
        return rows
    finally:
        conn.close()

def is_owner(message: types.Message) -> bool:
    if not message.from_user:
        return False
    uname = (message.from_user.username or "").strip()
    return OWNER_USERNAME and uname and ("@" + uname).lower() == OWNER_USERNAME.lower()

CONSENT_KB = InlineKeyboardMarkup().add(
    InlineKeyboardButton("🔔 Получать редкие письма (до 2 в мес.)", callback_data="consent:yes"),
    InlineKeyboardButton("🔕 Не сейчас", callback_data="consent:no")
)

# ── Шаблоны рассылок ─────────────────────────────────────
TEMPLATES = [
    ("update",
     "✨ Обновление в «Карте Ясности»!\n\n"
     "Добавлены новые варианты предсказаний — можешь попробовать уже сегодня.\n"
     "Это короткие ответы + советы, которые помогут увидеть ситуацию по-новому.\n\n"
     "Загляни в бота и открой свою карту заново 🔮"),

    ("promo",
     "🟡 <b>Реклама</b>\n\n"
     "До [дата] действует акция — скидка 50% на первый разбор по любой теме:\n"
     "🔮 Таро\n🔢 Нумерология\n🌌 Астрология\n\n"
     "Чтобы воспользоваться, напиши мне в личку слово «ЯСНОСТЬ».\n"
     "Поторопись: количество мест ограничено!\n\n"
     "Отписка: /unsubscribe"),

    ("remind",
     "Привет 🌿\nКак твоя неделя?\n\n"
     "Напоминаю: в «Карте Ясности» можно получить ответ на важный вопрос — раз в 7 дней.\n"
     "Если чувствуешь, что нужна подсказка или знак — загляни в бота 🔮"),

    ("tip",
     "✨ Маленькая практика ясности:\n"
     "Спроси себя — «Что я могу отпустить сегодня, чтобы стало легче?»\n\n"
     "Ответы обычно приходят сразу, главное — довериться первому ощущению.\n\n"
     "А если хочется подтверждения или направления — открой «Карту Ясности» 🔮"),

    ("motivation",
     "Иногда достаточно одного верного шага, чтобы изменить весь маршрут 🌌\n\n"
     "Пусть эта неделя принесёт тебе ясность и уверенность в себе.\n"
     "Если захочешь подсказку от «Карты Ясности» — она ждёт тебя 🔮"),
]

# ─────────────────────────────────────────────────────────
# ХЕНДЛЕРЫ
# ─────────────────────────────────────────────────────────

@dp.message_handler(commands=["start"])
async def cmd_start(m: types.Message):
    db_init()
    upsert_user(m.from_user, subscribe_flag=0)  # фиксируем пользователя, без авто-подписки

    photo_path = BASE_DIR / "welcome.jpg"
    try:
        with open(photo_path, "rb") as photo:
            await m.answer_photo(photo, caption=WELCOME, reply_markup=KB_MAIN)
    except FileNotFoundError:
        await m.answer(WELCOME, reply_markup=KB_MAIN)

    await m.answer(
        "Можно иногда присылать короткие тёплые письма (до 2 в месяц): обновления карт, мини-практики, акции?\n"
        "Ты всегда сможешь отписаться командой /unsubscribe.",
        reply_markup=CONSENT_KB
    )

@dp.callback_query_handler(Text(startswith="consent:"))
async def on_consent(c: types.CallbackQuery):
    choice = c.data.split(":")[1]
    if choice == "yes":
        set_subscribe(c.from_user.id, True)
        await c.message.edit_text("Подписка включена. Я пишу редко и по делу 💌\nОтписка: /unsubscribe.")
    else:
        set_subscribe(c.from_user.id, False)
        await c.message.edit_text("Хорошо, без рассылок. Если захочешь — команда /subscribe.")
    await c.answer()

@dp.message_handler(commands=["subscribe"])
async def cmd_subscribe(m: types.Message):
    db_init()
    upsert_user(m.from_user)
    set_subscribe(m.from_user.id, True)
    await m.answer("🔔 Подписка включена. Сообщения — не чаще 1–2 раз в месяц.\nОтписка: /unsubscribe.")

@dp.message_handler(commands=["unsubscribe", "stop"])
async def cmd_unsubscribe(m: types.Message):
    db_init()
    upsert_user(m.from_user)
    set_subscribe(m.from_user.id, False)
    await m.answer("🔕 Подписка отключена. Спасибо, что была(и) со мной. Вернуться можно командой /subscribe.")

@dp.message_handler(commands=["help"])
async def cmd_help(m: types.Message):
    await m.answer(HELP_TEXT, disable_web_page_preview=True)

@dp.message_handler(commands=["privacy"])
async def cmd_privacy(m: types.Message):
    await m.answer(PRIVACY_TEXT)

@dp.message_handler(commands=["menu"])
async def cmd_menu(m: types.Message):
    await m.answer("Выбери тему:", reply_markup=topic_keyboard())

def _clean(text: str) -> str:
    return (text or "").strip().lower().replace("ё", "е")

@dp.message_handler(lambda m: any(w in _clean(m.text) for w in ["моя тема", "моя-тема", "мой выбор", "выбрать тему"]))
async def choose_topic(m: types.Message):
    await m.answer("Выбери тему:", reply_markup=topic_keyboard())

@dp.message_handler(lambda m: "канал" in _clean(m.text))
async def go_channel(m: types.Message):
    await m.answer(f"Наш канал: {CHANNEL_LINK}", disable_web_page_preview=True)

@dp.message_handler(lambda m: any(w in _clean(m.text) for w in ["о консультации", "консультация", "запись"]))
async def about_offer(m: types.Message):
    await m.answer(ABOUT_TEXT, disable_web_page_preview=True)

@dp.callback_query_handler(lambda c: c.data.startswith("topic:"))
async def on_topic(c: types.CallbackQuery):
    topic_code = c.data.split(":")[1]
    title = TOPIC_LABELS.get(topic_code, "Тема")
    await c.message.edit_text(
        f"<b>{title}</b>\n\nВыбери карту: 1–5 или «🎲 Случайно».",
        reply_markup=card_keyboard(topic_code)
    )
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("card:"))
async def on_card(c: types.CallbackQuery):
    _, topic_code, pick = c.data.split(":")

    if not can_draw(c.from_user.id):
        await c.answer()
        await c.message.edit_text(LOCK_TEXT, disable_web_page_preview=True)
        return

    if pick == "rnd":
        pick = random.choice(["1", "2", "3", "4", "5"])

    body = TEXTS.get(topic_code, {}).get(pick)
    if not body:
        await c.answer()
        await c.message.edit_text("Упс… этой карты пока нет. Попробуй другую. 🙂")
        return

    mark_draw(c.from_user.id)
    reply = body + CTA_TAIL
    await c.answer()
    await c.message.edit_text(reply, disable_web_page_preview=True)

# ── Рассылка: команды владельца ──────────────────────────
@dp.message_handler(commands=["templates"])
async def cmd_templates(m: types.Message):
    if not is_owner(m):
        return await m.answer("Команда доступна только владельцу.")
    lines = ["Доступные шаблоны:"]
    for i, (code, _) in enumerate(TEMPLATES, start=1):
        lines.append(f"{i}. {code}")
    lines.append("\nОтправь: /send N — чтобы разослать шаблон номер N.")
    await m.answer("\n".join(lines))

@dp.message_handler(commands=["send"])
async def cmd_send(m: types.Message):
    if not is_owner(m):
        return await m.answer("Команда доступна только владельцу.")
    parts = m.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await m.answer("Формат: /send N (номер шаблона из /templates)")
    idx = int(parts[1]) - 1
    if not (0 <= idx < len(TEMPLATES)):
        return await m.answer("Нет такого номера шаблона.")
    text = TEMPLATES[idx][1]
    db_init()
    user_ids = get_all_subscribed_ids()
    if not user_ids:
        return await m.answer("Нет подписчиков. Никому отправлять.")
    await m.answer(f"Начинаю рассылку шаблона #{idx+1}. Получателей: {len(user_ids)}")
    sent, errors = 0, 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, disable_web_page_preview=True)
            sent += 1
        except Exception as e:
            logging.warning(f"send template to {uid} failed: {e}")
            errors += 1
        await asyncio.sleep(0.05)  # ~20/сек
    await m.answer(f"Готово ✅ Успешно: {sent} | Ошибок: {errors}")

@dp.message_handler(commands=["broadcast"])
async def cmd_broadcast(m: types.Message):
    if not is_owner(m):
        return await m.answer("Команда доступна только владельцу.")
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return await m.answer("Формат: /broadcast ТЕКСТ\n"
                              "Письмо отправится только тем, кто включил подписку (/subscribe).")
    text = parts[1].strip()
    if any(w in text.lower() for w in ["скидк", "промокод", "акция"]):
        text = "🟡 <b>Реклама</b>\n" + text + "\n\nОтписка: /unsubscribe"
    db_init()
    user_ids = get_all_subscribed_ids()
    if not user_ids:
        return await m.answer("Нет подписчиков. Никому отправлять.")
    await m.answer(f"Начинаю рассылку. Получателей: {len(user_ids)}")
    sent, errors = 0, 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, disable_web_page_preview=True)
            sent += 1
        except Exception as e:
            logging.warning(f"broadcast to {uid} failed: {e}")
            errors += 1
        await asyncio.sleep(0.05)
    await m.answer(f"Готово ✅ Успешно: {sent} | Ошибок: {errors}")

# Фолбэк: если пользователь пишет что-то другое — показываем меню
@dp.message_handler(content_types=types.ContentTypes.TEXT)
async def fallback(m: types.Message):
    await m.answer("Чтобы продолжить, нажми «Моя тема» или команду /menu 🙂", reply_markup=KB_MAIN)

# ── Запуск ───────────────────────────────────────────────
if __name__ == "__main__":
    db_init()
    executor.start_polling(dp, skip_updates=True)
