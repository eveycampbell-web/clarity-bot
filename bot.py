# -*- coding: utf-8 -*-
"""
Clarity Bot — стабильная версия с твоими текстами и фиксами
- aiogram 2.25.1
- Python 3.11

Функции:
  • /start с приветствием и фото + КНОПКИ (reply): «Моя тема», «О консультации», «Канал»
  • приглашение подписаться показывается ТОЛЬКО ОДИН РАЗ (consent_shown)
  • /subscribe и /unsubscribe
  • «Моя тема» → 3 темы → 6 вариантов: 5 твоих + 6-я «случайная из этих 5»
  • «замок» на 7 дней на получение карты (usage.json)
  • логирование событий в SQLite (users/events)
  • авто-починка недостающих колонок (subscribe_flag, consent_shown) в users

Переменные окружения в .env:
  BOT_TOKEN=...
  TELEGRAM_CHANNEL_LINK=https://t.me/annap_club
  OWNER_USERNAME=@AnnaPClub
"""

import os
import json
import random
import logging
import time
from pathlib import Path
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.dispatcher.filters import Text
from dotenv import load_dotenv
import sqlite3

# ---------- ЛОГИ ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s"
)

# ---------- BASE & ENV ----------
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_LINK = os.getenv("TELEGRAM_CHANNEL_LINK", "https://t.me/your_channel").strip()
OWNER_USERNAME = (os.getenv("OWNER_USERNAME", "@your_username") or "").strip()
USAGE_FILE = BASE_DIR / "usage.json"          # замок на 7 дней
DB_PATH = BASE_DIR / "subscribers.db"         # база рассылки и событий

if not TOKEN:
    raise RuntimeError("Нет токена. Откройте .env и пропишите BOT_TOKEN=...")

# ---------- БОТ ----------
bot = Bot(token=TOKEN, parse_mode="HTML", timeout=120)
dp = Dispatcher(bot)

# ---------- ТЕКСТЫ ----------
WELCOME = (
    "Привет! Это бот «Карта ясности» 🌗\n\n"
    "Нажми «Моя тема» → выбери один из трёх вопросов и получи мягкую подсказку.\n"
    "Одна карта доступна <b>раз в 7 дней</b>, чтобы сохранять трезвый взгляд и пользу.\n\n"
    "Важно: бот носит развлекательный и познавательный характер, не является "
    "медицинской или профессиональной консультацией. <b>18+</b>"
)

CONSENT_TEXT = (
    "Можно я буду иногда присылать короткие тёплые письма: обновления карт, мини-практики, акции?\n"
    "Ты всегда сможешь отписаться командой /unsubscribe."
)

CTA_TAIL = (
    f"\n\nПодписывайся на канал: {CHANNEL_LINK}\n\n"
    f"🎁 Напиши слово «ЯСНОСТЬ» в профиль {OWNER_USERNAME} — и получи скидку <b>50%</b> на первый разбор. "
    "Действует для новых клиентов. <b>18+</b>"
)

LOCK_TEXT = (
    "«Карта ясности» доступна <b>1 раз в неделю</b> — чтобы не зациклиться на переспрашивании и сохранить ценность первого взгляда. "
    "Пока идёт ожидание, в канале тебя уже ждут расклады, короткие практики и разборы — они помогают держать курс каждый день.\n\n"
    f"Загляни: {CHANNEL_LINK}\n\n"
    f"🎁 Не хочешь ждать и нужен личный разбор со <b>скидкой 50%</b>? Напиши слово «ЯСНОСТЬ» в профиль {OWNER_USERNAME}. "
    "Скидка 50% действует для новых клиентов. <b>18+</b>"
)

ABOUT_TEXT = (
    "Форматы: Таро / Нумерология / Астрология — фокус на твоих запросах.\n"
    "Что получишь: честные ответы на все волнующие тебя вопросы. "
    "Я рядом, чтобы помочь услышать, как хочешь жить именно ты 💚.\n\n"
    f"💬 Напиши «ЯСНОСТЬ» {OWNER_USERNAME} — подскажу формат и время. <b>18+</b>"
)

# ---------- КЛАВИАТУРЫ ----------
# Согласие/отказ (показываем ОДИН РАЗ)
CONSENT_KB = ReplyKeyboardMarkup(resize_keyboard=True)
CONSENT_KB.add(KeyboardButton("Подписаться ❤️"), KeyboardButton("🚫 Не сейчас"))

# ГЛАВНАЯ reply-клавиатура (всегда внизу)
KB_MAIN = ReplyKeyboardMarkup(resize_keyboard=True)
KB_MAIN.row(KeyboardButton("Моя тема"))
KB_MAIN.row(KeyboardButton("О консультации"), KeyboardButton("Канал"))

# Inline-кнопки с темами
TOPICS_KB = InlineKeyboardMarkup(row_width=1)
TOPICS_KB.add(
    InlineKeyboardButton("Что он(а) думает обо мне?", callback_data="t:think"),
    InlineKeyboardButton("Как зарабатывать больше?", callback_data="t:money"),
    InlineKeyboardButton("Мой скрытый талант", callback_data="t:talent"),
)

# --- Клавиатура выбора карты 1–5 + 🎲 ---
def build_cards_kb(topic: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    kb.add(
        InlineKeyboardButton("1", callback_data=f"c:{topic}:1"),
        InlineKeyboardButton("2", callback_data=f"c:{topic}:2"),
        InlineKeyboardButton("3", callback_data=f"c:{topic}:3"),
        InlineKeyboardButton("4", callback_data=f"c:{topic}:4"),
        InlineKeyboardButton("5", callback_data=f"c:{topic}:5"),
    )
    kb.add(InlineKeyboardButton("🎲 Случайная", callback_data=f"c:{topic}:rand"))
    kb.add(InlineKeyboardButton("⬅️ Назад к темам", callback_data="t:menu"))
    return kb

BACK_TO_MENU_KB = InlineKeyboardMarkup().add(
    InlineKeyboardButton("Назад к темам", callback_data="t:menu")
)

# ---------- SQLite: схема и авто-починка ----------
def db_init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Базовые таблицы
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id        INTEGER PRIMARY KEY,
            username       TEXT,
            first_name     TEXT,
            last_name      TEXT,
            first_seen_ts  INTEGER,
            last_seen_ts   INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            event_type  TEXT,
            ts          INTEGER,
            meta        TEXT
        )
    """)

    # Добавляем недостающие колонки, если база старая
    cur.execute("PRAGMA table_info(users)")
    cols = {row[1] for row in cur.fetchall()}
    if "subscribe_flag" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN subscribe_flag INTEGER DEFAULT 0")
    if "consent_shown" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN consent_shown INTEGER DEFAULT 0")

    conn.commit()
    conn.close()

def log_event(user_id: int, event_type: str, meta: str | None = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO events (user_id, event_type, ts, meta) VALUES (?,?,?,?)",
        (user_id, event_type, int(time.time()), meta)
    )
    conn.commit()
    conn.close()

def upsert_user(u: types.User, subscribe_flag: int | None = None, consent_shown: int | None = None):
    """Вставляет или обновляет пользователя + last_seen_ts."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (u.id,))
    row = cur.fetchone()
    now = int(time.time())

    if row:
        sets = ["last_seen_ts=?"]
        args = [now]
        if subscribe_flag is not None:
            sets.append("subscribe_flag=?")
            args.append(int(subscribe_flag))
        if consent_shown is not None:
            sets.append("consent_shown=?")
            args.append(int(consent_shown))
        sets_str = ", ".join(sets)
        args.append(u.id)
        cur.execute(f"UPDATE users SET {sets_str} WHERE user_id=?", args)
    else:
        cur.execute("""
            INSERT INTO users (user_id, username, first_name, last_name,
                               first_seen_ts, last_seen_ts, subscribe_flag, consent_shown)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            u.id, u.username, u.first_name, u.last_name,
            now, now,
            int(subscribe_flag or 0), int(consent_shown or 0)
        ))
    conn.commit()
    conn.close()

def get_user_flags(user_id: int) -> tuple[int, int]:
    """Возвращает (subscribe_flag, consent_shown). Если пользователя ещё нет — (0,0)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT subscribe_flag, consent_shown FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return (0, 0)
    return int(row[0] or 0), int(row[1] or 0)

# ---------- «Замок» на 7 дней ----------
LOCK_DAYS = 7

def _load_usage() -> dict:
    if not USAGE_FILE.exists():
        return {}
    try:
        return json.loads(USAGE_FILE.read_text("utf-8"))
    except Exception:
        return {}

def _save_usage(data: dict):
    USAGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

def can_draw_card(user_id: int) -> tuple[bool, str | None]:
    data = _load_usage()
    rec = data.get(str(user_id))
    if not rec:
        return True, None
    last_ts = datetime.fromisoformat(rec["last_draw"])
    if datetime.now() - last_ts >= timedelta(days=LOCK_DAYS):
        return True, None
    next_time = last_ts + timedelta(days=LOCK_DAYS)
    return False, next_time.strftime("%d.%m %H:%M")

def mark_card_drawn(user_id: int):
    data = _load_usage()
    data[str(user_id)] = {"last_draw": datetime.now().isoformat(timespec="seconds")}
    _save_usage(data)

# ---------- КОНТЕНТ КАРТ (твои тексты) ----------
DECKS: dict[str, dict[str, str]] = {
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

def build_card_text(topic: str) -> str:
    """
    Возвращает ОДНУ карту.
    6 вариантов = 5 фиксированных твоих текстов + 1 «случайная из этих же 5».
    Никаких чужих формулировок — только твои.
    """
    t = topic if topic in DECKS else "think"
    base_texts = list(DECKS[t].values())  # ровно 5

    idx = random.randrange(6)
    if idx == 5:
        chosen = random.choice(base_texts)   # «шестая» — случайная из этих же 5
    else:
        chosen = base_texts[idx]             # одна из пяти фиксированных

    return chosen + CTA_TAIL

# ---------- ХЭНДЛЕРЫ ----------
@dp.message_handler(commands=['start'])
async def cmd_start(m: types.Message):
    db_init()
    upsert_user(m.from_user, subscribe_flag=0)
    log_event(m.from_user.id, "start")

    # привет + фото
    photo_path = BASE_DIR / "welcome.jpg"
    try:
        with open(photo_path, "rb") as photo:
            await m.answer_photo(photo, caption=WELCOME, reply_markup=KB_MAIN)
    except FileNotFoundError:
        await m.answer(WELCOME, reply_markup=KB_MAIN)

    # ПРИГЛАШЕНИЕ К РАССЫЛКЕ — ТОЛЬКО ОДИН РАЗ
    sub_flag, consent_shown = get_user_flags(m.from_user.id)
    if consent_shown == 0:
        upsert_user(m.from_user, consent_shown=1)
        await m.answer(CONSENT_TEXT, reply_markup=CONSENT_KB)

@dp.message_handler(Text(equals="Подписаться ❤️"))
async def agree_subscribe(m: types.Message):
    upsert_user(m.from_user, subscribe_flag=1)
    log_event(m.from_user.id, "subscribe", "consent_button")
    await m.answer("Спасибо за доверие! Я аккуратно и редко ✨", reply_markup=KB_MAIN)

@dp.message_handler(Text(equals="🚫 Не сейчас"))
async def decline_subscribe(m: types.Message):
    log_event(m.from_user.id, "consent_decline")
    await m.answer("Хорошо. Если передумаешь — команда /subscribe.", reply_markup=KB_MAIN)

@dp.message_handler(commands=['subscribe'])
async def manual_subscribe(m: types.Message):
    upsert_user(m.from_user, subscribe_flag=1)
    log_event(m.from_user.id, "subscribe", "manual")
    await m.answer("Подписка включена. Спасибо! 🌿")

@dp.message_handler(commands=['unsubscribe'])
async def manual_unsubscribe(m: types.Message):
    upsert_user(m.from_user, subscribe_flag=0)
    log_event(m.from_user.id, "unsubscribe", "manual")
    await m.answer("Подписка выключена. В любой момент можно включить: /subscribe")

@dp.message_handler(Text(equals="О консультации"))
async def about_handler(m: types.Message):
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("Написать", url=f"https://t.me/{OWNER_USERNAME[1:]}")) if OWNER_USERNAME.startswith("@") \
        else InlineKeyboardMarkup()
    await m.answer(ABOUT_TEXT, reply_markup=kb if kb.inline_keyboard else None)

@dp.message_handler(Text(equals="Канал"))
async def channel_handler(m: types.Message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Открыть канал", url=CHANNEL_LINK))
    await m.answer("Вот ссылка на мой канал. Жду тебя 💚\n\n<b>18+</b>", reply_markup=kb)

@dp.message_handler(Text(equals="Моя тема", ignore_case=True))
async def choose_topic(m: types.Message):
    # одно сообщение, сразу с инлайн-кнопками тем
    await m.answer("Выбирай тему:", reply_markup=TOPICS_KB)

# показываем выбор карт для выбранной темы (1–5 или 🎲)
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("t:"))
async def topic_router(c: types.CallbackQuery):
    code = c.data.split(":", 1)[1]  # think | money | talent | menu
    if code == "menu":
        await c.message.edit_text("Выбирай тему:", reply_markup=TOPICS_KB)
        await c.answer()
        return

    # сразу даём выбор карт
    await c.message.edit_text("Выбери карту:", reply_markup=build_cards_kb(code))
    await c.answer()

# обработчик клика по конкретной карте 1–5 или 🎲
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("c:"))
async def card_choice(c: types.CallbackQuery):
    try:
        _, topic, key = c.data.split(":")  # topic in (think|money|talent), key in (1..5|rand)
    except ValueError:
        await c.answer("Что-то пошло не так.", show_alert=True)
        return

    # замок 7 дней
    ok, when = can_draw_card(c.from_user.id)
    if not ok:
        await c.answer()
        await c.message.answer(LOCK_TEXT, reply_markup=BACK_TO_MENU_KB)
        return

    # выбираем карту: конкретную 1..5 или случайную из имеющихся ключей
    if key == "rand":
        key = random.choice(list(DECKS.get(topic, {}).keys()))

    text = DECKS.get(topic, {}).get(key)
    if not text:
        await c.answer("Карты не нашлось 🙈", show_alert=True)
        return

    # логика после выдачи карты
    mark_card_drawn(c.from_user.id)
    upsert_user(c.from_user)  # обновить last_seen
    log_event(c.from_user.id, "card", f"{topic}:{key}")

    await c.answer()
    await c.message.answer(text + CTA_TAIL, reply_markup=BACK_TO_MENU_KB)

# ---------- /stats (краткая админ-статистика) ----------
@dp.message_handler(commands=['stats'])
async def cmd_stats(m: types.Message):
    if OWNER_USERNAME and f"@{(m.from_user.username or '').lower()}" != OWNER_USERNAME.lower():
        await m.answer("Команда доступна владельцу.")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE subscribe_flag=1")
    subs = cur.fetchone()[0]
    week_ago = int((datetime.now() - timedelta(days=7)).timestamp())
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE ts>=?", (week_ago,))
    active7 = cur.fetchone()[0]
    conn.close()
    text = (
        f"Пользователи: {total}\n"
        f"Подписка включена: {subs}\n"
        f"Актив за 7 дней: {active7}"
    )
    await m.answer(text)

# ---------- ТОЧКА ВХОДА ----------
if __name__ == "__main__":
    db_init()
    executor.start_polling(dp, skip_updates=True)


