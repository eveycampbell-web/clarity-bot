# -*- coding: utf-8 -*-
"""
Clarity Bot — финальная версия
- aiogram 2.25.1
- Python 3.11
Функции:
  • /start с приветствием и фото
  • приглашение подписаться показывается ТОЛЬКО ОДИН РАЗ (consent_shown)
  • ручное /subscribe и /unsubscribe
  • простое меню «Моя тема» → 3 темы → 6 карт (5 фикс + случайная)
  • «замок» на 7 дней на получение карты
  • логирование событий в SQLite (users/events)
Переменные окружения в .env:
  BOT_TOKEN=...
  TELEGRAM_CHANNEL_LINK=https://t.me/annap_club
  OWNER_USERNAME=@AnnaPClub
"""

import os
import json
import random
import logging
import asyncio
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
CHANNEL_LINK = os.getenv("TELEGRAM_CHANNEL_LINK", "https://t.me/your_channel")
OWNER_USERNAME = (os.getenv("OWNER_USERNAME", "@your_username") or "").strip()
USAGE_FILE = BASE_DIR / "usage.json"          # замок на 7 дней
DB_PATH = BASE_DIR / "subscribers.db"         # база рассылки и событий

if not TOKEN:
    raise RuntimeError("Нет токена. Откройте .env и пропишите BOT_TOKEN=...")

# ---------- БОТ ----------
bot = Bot(token=TOKEN, parse_mode="HTML", timeout=120)
dp = Dispatcher(bot)

# ---------- ДИСКЛЕЙМЕРЫ и тексты ----------
WELCOME = (
    "Привет! Это бот «Карта ясности» 🌗\n\n"
    "Нажми «Моя тема» → выбери один из трёх вопросов и получи мягкую подсказку.\n"
    "Одна карта доступна <b>раз в 7 дней</b>, чтобы сохранять трезвый взгляд и пользу.\n\n"
    "Важно: бот носит развлекательный и познавательный характер, "
    "не является медицинской или профессиональной консультацией. <b>18+</b>"
)

CONSENT_TEXT = (
    "Можно иногда присылать короткие тёплые письма (до 2 в месяц): обновления карт, мини-практики, акции?\n"
    "Ты всегда сможешь отписаться командой /unsubscribe."
)

CONSENT_KB = ReplyKeyboardMarkup(resize_keyboard=True)
CONSENT_KB.add(KeyboardButton("❤️ Подписаться"), KeyboardButton("🚫 Не сейчас"))

KB_MAIN = ReplyKeyboardMarkup(resize_keyboard=True)
KB_MAIN.add(KeyboardButton("Моя тема"))

TOPICS_KB = InlineKeyboardMarkup(row_width=1)
TOPICS_KB.add(
    InlineKeyboardButton("Что он(а) думает обо мне?", callback_data="t:think"),
    InlineKeyboardButton("Как зарабатывать больше?", callback_data="t:money"),
    InlineKeyboardButton("Мой скрытый талант", callback_data="t:talent"),
)

BACK_TO_MENU_KB = InlineKeyboardMarkup().add(
    InlineKeyboardButton("Назад к темам", callback_data="t:menu")
)

# ---------- SQLite: схема и утилиты ----------

def db_init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # таблица users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            first_name    TEXT,
            last_name     TEXT,
            first_seen_ts INTEGER,
            last_seen_ts  INTEGER,
            subscribe_flag INTEGER DEFAULT 0,
            consent_shown  INTEGER DEFAULT 0
        )
    """)
    # таблица событий
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER,
            event_type TEXT,
            ts        INTEGER,
            meta      TEXT
        )
    """)
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
        args.extend([u.id])
        cur.execute(f"UPDATE users SET {sets_str} WHERE user_id=?", args)
    else:
        cur.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, first_seen_ts,
                               last_seen_ts, subscribe_flag, consent_shown)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            u.id, u.username, u.first_name, u.last_name, now, now,
            int(subscribe_flag or 0), int(consent_shown or 0)
        ))
    conn.commit()
    conn.close()

def get_user_flags(user_id: int) -> tuple[int, int]:
    """Возвращает (subscribe_flag, consent_shown)."""
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

# ---------- Генерация карт ----------

def build_card_text(topic: str) -> str:
    """6 карт = 5 фикс + 1 случайная"""
    # Короткие, мягкие, полезные — как вы просили
    decks = {
        "think": [
            ("Сейчас к тебе много внимания.",
             "Сохрани спокойствие: задавай прямые вопросы и слушай ответы."),
            ("Человек колеблется.",
             "Побереги себя: не форсируй и дай поведению показать намерения."),
            ("Есть симпатия, но есть и сомнения.",
             "Скажи о своих границах: что тебе важно и что не ок."),
            ("Контакт важен, но есть усталость.",
             "Сделай маленькую паузу: день тишины пойдёт на пользу."),
            ("Нужна ясность.",
             "Предложи короткую встречу и один конкретный вопрос."),
        ],
        "money": [
            ("Твоя опора — ритм.",
             "Выдели 30 минут в день на одно денежное действие."),
            ("Ресурс уже рядом.",
             "Собери 3-5 прошлых клиентов/контактов и напомни о себе."),
            ("Рост через фокус.",
             "Обрежь 1 лишнее направление и усили то, что уже работает."),
            ("Запрос на структуру.",
             "Составь простой прайс из 3 вариантов — людям так легче выбрать."),
            ("Ты недооцениваешь результат.",
             "Подними цену на 10% и посмотри на обратную связь."),
        ],
        "talent": [
            ("Сила в наблюдательности.",
             "Запиши 3 вещи, которые люди часто просят тебе объяснить."),
            ("Твоя поддержка тёплая и точная.",
             "Подумай про короткие консультации на 20–30 минут."),
            ("Ты собираешь смысл из деталей.",
             "Попробуй делать мини-разборы по чек-листу из 5 пунктов."),
            ("Талант — в спокойствии.",
             "Делай дела в том темпе, где чувствуешь вкус: он и есть твой знак."),
            ("Твоя речь — инструмент.",
             "Запиши голосовую заметку другу так, как бы поддержала клиента."),
        ],
    }
    pool = decks.get(topic, decks["think"]).copy()
    # случайная карта (шестая)
    random_card = (
        "Интуиция уже знает ответ.",
        "Сделай самое маленькое действие в эту сторону сегодня."
    )
    pool.append(random_card)
    choice = random.choice(pool)
    title, action = choice
    return (
        f"<b>Короткий ответ:</b> {title}\n\n"
        f"<b>Что сделать:</b> {action}\n\n"
        "Береги себя. Путь проясняется, когда мы делаем маленькие шаги. ✨\n\n"
        f"Подпишись на канал: {CHANNEL_LINK}\n"
        "🎁 Напиши слово «ясность» в профиль — получи скидку 50% на первый разбор."
    )

# ---------- Хэндлеры ----------

@dp.message_handler(commands=['start'])
async def cmd_start(m: types.Message):
    db_init()
    # фиксируем пользователя (без автоподписки)
    upsert_user(m.from_user, subscribe_flag=0)
    # лог
    log_event(m.from_user.id, "start")
    # привет + фото
    photo_path = BASE_DIR / "welcome.jpg"
    try:
        with open(photo_path, "rb") as photo:
            await m.answer_photo(photo, caption=WELCOME, reply_markup=KB_MAIN)
    except FileNotFoundError:
        await m.answer(WELCOME, reply_markup=KB_MAIN)

    # показать приглашение — только если еще НЕ показывали
    sub_flag, consent_shown = get_user_flags(m.from_user.id)
    if consent_shown == 0:
        upsert_user(m.from_user, consent_shown=1)  # помечаем как показанное
        await m.answer(CONSENT_TEXT, reply_markup=CONSENT_KB)

@dp.message_handler(Text(equals="❤️ Подписаться"))
async def agree_subscribe(m: types.Message):
    upsert_user(m.from_user, subscribe_flag=1)
    log_event(m.from_user.id, "subscribe", "consent_button")
    await m.answer("Спасибо за доверие! Я аккуратно и редко ✨", reply_markup=KB_MAIN)

@dp.message_handler(Text(equals="🚫 Не сейчас"))
async def decline_subscribe(m: types.Message):
    # просто фиксируем отказ (флаг подписки не меняем)
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
    await m.answer("Подписка выключена. Ты в любой момент можешь включить её снова: /subscribe")

@dp.message_handler(Text(equals="Моя тема"))
async def choose_topic(m: types.Message):
    await m.answer("Выбери тему:", reply_markup=types.ReplyKeyboardRemove())
    await m.answer("Темы:", reply_markup=TOPICS_KB)

@dp.callback_query_handler(Text(startswith="t:"))
async def topic_router(c: types.CallbackQuery):
    code = c.data.split(":", 1)[1]
    if code == "menu":
        await c.message.edit_text("Темы:", reply_markup=TOPICS_KB)
        await c.answer()
        return

    # замок 7 дней
    ok, when = can_draw_card(c.from_user.id)
    if not ok:
        msg = (
            f"Карту ясности можно получать 1 раз в {LOCK_DAYS} дней.\n"
            f"Новая карта будет доступна: <b>{when}</b>\n\n"
            f"Пока тебя ждут расклады и полезности в канале: {CHANNEL_LINK}"
        )
        await c.answer()
        await c.message.answer(msg, reply_markup=BACK_TO_MENU_KB)
        return

    # выдаём карту
    text = build_card_text(code)
    mark_card_drawn(c.from_user.id)
    upsert_user(c.from_user)  # обновляем last_seen
    log_event(c.from_user.id, "card", code)

    await c.answer()
    await c.message.answer(text, reply_markup=BACK_TO_MENU_KB)

# ---------- /stats (краткая админ-статистика) ----------
@dp.message_handler(commands=['stats'])
async def cmd_stats(m: types.Message):
    if OWNER_USERNAME and f"@{m.from_user.username or ''}".lower() != OWNER_USERNAME.lower():
        await m.answer("Команда доступна владельцу.")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE subscribe_flag=1")
    subs = cur.fetchone()[0]
    # актив за 7 дней
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
