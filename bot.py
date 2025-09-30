# -*- coding: utf-8 -*-
import os
import json
import time
import csv
import io
import random
import sqlite3
from datetime import datetime, timedelta

from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.utils import executor
from aiogram.dispatcher.filters import Text

# =========================
# Загрузка окружения/настроек
# =========================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "").lstrip("@")
TELEGRAM_CHANNEL_LINK = os.getenv("TELEGRAM_CHANNEL_LINK", "").strip() or "https://t.me/annap_club"

if not BOT_TOKEN:
    raise RuntimeError("Нет токена. Откройте .env и пропишите BOT_TOKEN=...")

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

# =========================
# Хранилище/БД
# =========================
DB_PATH = str(BASE_DIR / "subscribers.db")

def _db():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = _db()
    cur = conn.cursor()
    # Пользователи
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
      user_id INTEGER PRIMARY KEY,
      username TEXT,
      first_name TEXT,
      last_name TEXT,
      first_seen_ts INTEGER,
      last_seen_ts INTEGER,
      is_subscribed INTEGER DEFAULT 0
    )
    """)
    # Подписки (явное согласие/отказ)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
      user_id INTEGER PRIMARY KEY,
      status TEXT,               -- 'active' | 'inactive'
      subscribed_ts INTEGER,
      unsubscribed_ts INTEGER
    )
    """)
    # События
    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER,
      event_type TEXT,
      ts INTEGER,
      meta TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# =========================
# Утилиты: события, пользователи
# =========================
def now_ts():
    return int(time.time())

def ensure_user(u: types.User):
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (u.id,))
    row = cur.fetchone()
    if not row:
        cur.execute("""INSERT INTO users(user_id, username, first_name, last_name, first_seen_ts, last_seen_ts)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (u.id, u.username or "", u.first_name or "", u.last_name or "", now_ts(), now_ts()))
    else:
        cur.execute("UPDATE users SET username=?, first_name=?, last_name=?, last_seen_ts=? WHERE user_id=?",
                    (u.username or "", u.first_name or "", u.last_name or "", now_ts(), u.id))
    conn.commit()
    conn.close()

def log_event(user_id: int, event_type: str, meta: str = None):
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT INTO events(user_id, event_type, ts, meta) VALUES (?, ?, ?, ?)",
                (user_id, event_type, now_ts(), meta))
    conn.commit()
    conn.close()

def is_subscribed(user_id: int) -> bool:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT status FROM subscriptions WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and (row[0] == "active"))

def mark_subscribed(user_id: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO subscriptions(user_id, status, subscribed_ts, unsubscribed_ts) VALUES (?, 'active', ?, COALESCE((SELECT unsubscribed_ts FROM subscriptions WHERE user_id=?), NULL))",
                (user_id, now_ts(), user_id))
    cur.execute("UPDATE users SET is_subscribed=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def mark_unsubscribed(user_id: int):
    conn = _db()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO subscriptions(user_id, status, subscribed_ts, unsubscribed_ts) VALUES (?, 'inactive', COALESCE((SELECT subscribed_ts FROM subscriptions WHERE user_id=?), NULL), ?)",
                (user_id, user_id, now_ts()))
    cur.execute("UPDATE users SET is_subscribed=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def consent_already_shown(user_id: int) -> bool:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM events WHERE user_id=? AND event_type='consent_shown' LIMIT 1", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None

def last_draw_ts(user_id: int) -> int:
    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT ts FROM events WHERE user_id=? AND event_type='draw_card' ORDER BY ts DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0

# =========================
# Интерфейс/клавиатуры/тексты
# =========================
WELCOME = (
    "Привет! Я — «Карта Ясности» 🌓\n"
    "Помогаю быстро прояснить главное: чувства, деньги, путь и свой талант.\n\n"
    "Выбери тему — и я дам тебе компактный ответ + мягкий шаг-действие.\n"
    "🔞 18+. Ознакомительный формат. Не является публичной офертой."
)

KB_MAIN = ReplyKeyboardMarkup(resize_keyboard=True)
KB_MAIN.add(KeyboardButton("Моя тема"))
KB_MAIN.add(KeyboardButton("Перейти в канал"))
KB_MAIN.add(KeyboardButton("Подписаться на письма"), KeyboardButton("Отписаться"))

KB_TOPICS = ReplyKeyboardMarkup(resize_keyboard=True)
KB_TOPICS.add(KeyboardButton("Что он(а) думает обо мне?"))
KB_TOPICS.add(KeyboardButton("Как зарабатывать больше?"))
KB_TOPICS.add(KeyboardButton("Мой скрытый талант"))
KB_TOPICS.add(KeyboardButton("⬅️ Назад"))

CONSENT_KB = InlineKeyboardMarkup()
CONSENT_KB.add(InlineKeyboardButton("Да, присылай до 2 в месяц", callback_data="consent_yes"))
CONSENT_KB.add(InlineKeyboardButton("Не сейчас", callback_data="consent_no"))

# =========================
# Контент для ответов по темам (короткий ответ + мягкое действие + фраза)
# =========================
RESPONSES = {
    "Что он(а) думает обо мне?": [
        ("Тянется, но боится перегрузить тебя ожиданиями.",
         "Дай простую опору: одна спокойная фраза «я рядом, когда будешь готов(а)».",
         "Ты ценен(а) без доказательств."),
        ("Сейчас человек больше в своих делах, чем в чувствах.",
         "Спроси прямо, как тебе быть на ближайшую неделю — без претензий.",
         "Твоя ясность — твоя сила."),
        ("Есть интерес и симпатия, но не хватает инициативы.",
         "Сделай маленький шаг сам(а): нейтральное приглашение без давления.",
         "Мягкость — не равна слабости.")
    ],
    "Как зарабатывать больше?": [
        ("Твой потолок — это не сумма, а текущая форма заработка.",
         "Выбери одно улучшение на эту неделю: поднять цену/сократить бесплатное/ввести простой пакет.",
         "Ты имеешь право на достойную оплату."),
        ("Главная утечка — распыление на мелочи.",
         "Собери одно «деньгоделающее» действие и зафиксируй время в календаре.",
         "Фокус кормит результат."),
        ("Ты недооцениваешь навык, который для тебя «сам собой».",
         "Сделай быструю упаковку: 3 пункта пользы — и предложи двум людям.",
         "Лёгкость — тоже профессионализм.")
    ],
    "Мой скрытый талант": [
        ("Улавливать суть быстрее других.",
         "Тренируй: каждый день формулируй «одну мысль дня» в заметке.",
         "Ясность — твоя суперсила."),
        ("Собирать людей вокруг идеи.",
         "Организуй мини-встречу/чаты на одну тему — без перфекционизма.",
         "Тепло объединяет лучше правил."),
        ("Превращать хаос в систему.",
         "Возьми любой беспорядок и сделай из него простую схему из 3 шагов.",
         "Порядок = спокойствие внутри.")
    ]
}

MOTIV = [
    "Делай по-человечески, не по-идеальному.",
    "Маленький шаг сегодня лучше, чем идеальный план завтра.",
    "Ты уже ближе, чем думаешь.",
    "Мягкость — это сила под контролем.",
    "Ясность приходит в движении."
]

# =========================
# Хелперы: период /stats
# =========================
def parse_period(arg: str | None):
    """
    Возвращает (ts_from, ts_to) в UTC.
    Допустимо: None -> 7d, 'today', '7d', '30d', 'YYYY-MM-DD..YYYY-MM-DD'
    """
    ts_to = int(time.time())
    if not arg or arg.strip() == "":
        return ts_to - 7*86400, ts_to  # 7 дней по умолчанию

    s = arg.strip().lower()
    if s == "today":
        dt = datetime.utcnow().date()
        start = int(datetime(dt.year, dt.month, dt.day).timestamp())
        return start, ts_to
    if s.endswith("d") and s[:-1].isdigit():
        days = int(s[:-1])
        return ts_to - days*86400, ts_to
    if ".." in s:
        a, b = s.split("..", 1)
        try:
            start = int(datetime.fromisoformat(a).timestamp())
            end = int(datetime.fromisoformat(b).timestamp()) + 86399
            return start, end
        except Exception:
            return ts_to - 7*86400, ts_to
    return ts_to - 7*86400, ts_to

def human(n: int) -> str:
    return f"{n:,}".replace(",", " ")

# =========================
# Хэндлеры
# =========================
@dp.message_handler(commands=["start"])
async def cmd_start(m: types.Message):
    ensure_user(m.from_user)
    log_event(m.from_user.id, "start")

    # Картинка + приветствие
    photo_path = BASE_DIR / "welcome.jpg"
    if photo_path.exists():
        with open(photo_path, "rb") as p:
            await m.answer_photo(p, caption=WELCOME, reply_markup=KB_MAIN)
    else:
        await m.answer(WELCOME, reply_markup=KB_MAIN)

    # Предложение подписки — только если ещё не подписан и оффер не показывали
    if (not is_subscribed(m.from_user.id)) and (not consent_already_shown(m.from_user.id)):
        await m.answer(
            "Можно иногда присылать короткие тёплые письма (до 2 в месяц): обновления карт, мини-практики, акции?\n"
            "Отписаться можно командой /unsubscribe.",
            reply_markup=CONSENT_KB
        )
        log_event(m.from_user.id, "consent_shown")
    else:
        await m.answer("Рада видеть тебя снова 🌿", reply_markup=KB_MAIN)

@dp.callback_query_handler(Text(equals="consent_yes"))
async def cb_consent_yes(c: types.CallbackQuery):
    mark_subscribed(c.from_user.id)
    log_event(c.from_user.id, "subscribe")
    await c.message.edit_reply_markup()
    await c.message.answer("Спасибо! ✉️ Письма будут до 2 раз в месяц. /unsubscribe — чтобы отключить рассылку.")

@dp.callback_query_handler(Text(equals="consent_no"))
async def cb_consent_no(c: types.CallbackQuery):
    log_event(c.from_user.id, "subscribe_decline")
    await c.message.edit_reply_markup()
    await c.message.answer("Окей, без писем. Можно вернуться позже — команда /subscribe.")

@dp.message_handler(commands=["subscribe"])
async def cmd_subscribe(m: types.Message):
    mark_subscribed(m.from_user.id)
    log_event(m.from_user.id, "subscribe_manual")
    await m.answer("Готово! ✉️ Включила письма (до 2 в месяц). /unsubscribe — чтобы отключить.")

@dp.message_handler(commands=["unsubscribe"])
async def cmd_unsubscribe(m: types.Message):
    mark_unsubscribed(m.from_user.id)
    log_event(m.from_user.id, "unsubscribe")
    await m.answer("Письма отключены. Если передумаешь — /subscribe.")

@dp.message_handler(Text(equals="Перейти в канал"))
async def go_channel(m: types.Message):
    log_event(m.from_user.id, "click_channel")
    await m.answer(f"Наш канал: {TELEGRAM_CHANNEL_LINK}")

@dp.message_handler(Text(equals="Подписаться на письма"))
async def btn_subscribe(m: types.Message):
    if is_subscribed(m.from_user.id):
        await m.answer("Ты уже на связи ✉️. Если захочешь, можно /unsubscribe.")
    else:
        mark_subscribed(m.from_user.id)
        log_event(m.from_user.id, "subscribe_button")
        await m.answer("Подписка включена ✉️. До 2 писем в месяц — бережно и по делу.")

@dp.message_handler(Text(equals="Отписаться"))
async def btn_unsubscribe(m: types.Message):
    if not is_subscribed(m.from_user.id):
        await m.answer("Сейчас письма отключены. Можешь включить /subscribe.")
    else:
        mark_unsubscribed(m.from_user.id)
        log_event(m.from_user.id, "unsubscribe_button")
        await m.answer("Отключила письма. Возвращайся, когда захочешь 💛")

@dp.message_handler(Text(equals="Моя тема"))
async def choose_topic(m: types.Message):
    log_event(m.from_user.id, "open_topics")
    await m.answer("Выбери тему:", reply_markup=KB_TOPICS)

@dp.message_handler(Text(equals="⬅️ Назад"))
async def back_main(m: types.Message):
    await m.answer("Главное меню:", reply_markup=KB_MAIN)

# --- 7-дневный «замок» на выдачу
LOCK_DAYS = 7

async def guard_lock(m: types.Message) -> bool:
    last = last_draw_ts(m.from_user.id)
    if last and (now_ts() - last) < LOCK_DAYS * 86400:
        left = LOCK_DAYS*86400 - (now_ts() - last)
        days = max(1, left // 86400)
        log_event(m.from_user.id, "locked")
        await m.answer(
            f"«Карта Ясности» доступна раз в {LOCK_DAYS} дней.\n"
            f"До следующего вытягивания примерно {days} дн.\n\n"
            f"Пока тебя ждут разборы и полезности в канале: {TELEGRAM_CHANNEL_LINK} 🎁"
        )
        return True
    return False

def make_answer(topic: str):
    trio = random.choice(RESPONSES[topic])
    short, action, phrase = trio
    motiv = random.choice(MOTIV)
    return (
        f"<b>{topic}</b>\n\n"
        f"Ответ: {short}\n"
        f"Шаг: {action}\n\n"
        f"{phrase} • {motiv}\n\n"
        f"Переходи в канал: {TELEGRAM_CHANNEL_LINK}\n"
        f"🎁 -50% на первый разбор — напиши «ЯСНОСТЬ» @AnnaPClub"
    )

@dp.message_handler(Text(equals=list(RESPONSES.keys())))
async def topic_handler(m: types.Message):
    topic = m.text.strip()
    log_event(m.from_user.id, "choose_theme", meta=topic)

    if await guard_lock(m):
        return

    # «6 карт»: мы формируем 1 итоговый ответ, но логируем draw_card
    text = make_answer(topic)
    log_event(m.from_user.id, "draw_card", meta=topic)
    await m.answer(text, reply_markup=KB_MAIN)

# =========================
# /stats и /export (для владельца)
# =========================
def only_owner(func):
    async def wrapper(m: types.Message, *args, **kwargs):
        if (m.from_user.username or "").lower() != (OWNER_USERNAME or "").lower():
            return await m.reply("Команда недоступна.")
        return await func(m, *args, **kwargs)
    return wrapper

@dp.message_handler(commands=["stats"])
@only_owner
async def cmd_stats(m: types.Message):
    arg = m.get_args()  # текст после команды
    ts_from, ts_to = parse_period(arg)

    conn = _db()
    cur = conn.cursor()

    # Новые пользователи за период (по first_seen_ts)
    cur.execute("SELECT COUNT(*) FROM users WHERE first_seen_ts BETWEEN ? AND ?", (ts_from, ts_to))
    new_users = cur.fetchone()[0] or 0

    # Активные пользователи за период (по событиям)
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM events WHERE ts BETWEEN ? AND ?", (ts_from, ts_to))
    active_users = cur.fetchone()[0] or 0

    # Выбор тем
    topics = ["Что он(а) думает обо мне?", "Как зарабатывать больше?", "Мой скрытый талант"]
    topic_counts = {}
    for t in topics:
        cur.execute("SELECT COUNT(*) FROM events WHERE event_type='choose_theme' AND meta=? AND ts BETWEEN ? AND ?",
                    (t, ts_from, ts_to))
        topic_counts[t] = cur.fetchone()[0] or 0

    # Выдачи и замок
    cur.execute("SELECT COUNT(*) FROM events WHERE event_type='draw_card' AND ts BETWEEN ? AND ?", (ts_from, ts_to))
    draws = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM events WHERE event_type='locked' AND ts BETWEEN ? AND ?", (ts_from, ts_to))
    locked = cur.fetchone()[0] or 0

    # Подписки
    cur.execute("SELECT COUNT(*) FROM events WHERE event_type IN ('subscribe','subscribe_manual','subscribe_button') AND ts BETWEEN ? AND ?", (ts_from, ts_to))
    subs = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM events WHERE event_type IN ('unsubscribe','unsubscribe_button') AND ts BETWEEN ? AND ?", (ts_from, ts_to))
    unsubs = cur.fetchone()[0] or 0

    # Клики
    cur.execute("SELECT COUNT(*) FROM events WHERE event_type='click_channel' AND ts BETWEEN ? AND ?", (ts_from, ts_to))
    clicks_channel = cur.fetchone()[0] or 0

    conn.close()

    # Формируем ответ
    # Период для подписи:
    def fmt_ts(ts): return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    period_label = f"{fmt_ts(ts_from)}..{fmt_ts(ts_to)}"

    lines = [
        f"📊 Статистика ({period_label})",
        f"👤 Новые: {human(new_users)}",
        f"🟢 Активные: {human(active_users)}",
        "",
        "💬 Темы:"
    ]
    for t in topics:
        lines.append(f"— {t}: {human(topic_counts[t])}")
    lines += [
        "",
        f"🃏 Выдачи карт: {human(draws)}",
        f"🔒 Замок сработал: {human(locked)}",
        "",
        f"✉️ Подписки: +{human(subs)} / отписки: {human(unsubs)}",
        f"🔗 Клики «канал»: {human(clicks_channel)}",
    ]

    await m.reply("\n".join(lines))

@dp.message_handler(commands=["export"])
@only_owner
async def cmd_export(m: types.Message):
    arg = m.get_args()
    ts_from, ts_to = parse_period(arg)

    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.id, e.user_id, e.event_type, e.ts, IFNULL(e.meta,'')
        FROM events e
        WHERE e.ts BETWEEN ? AND ?
        ORDER BY e.id ASC
    """, (ts_from, ts_to))
    rows = cur.fetchall()
    conn.close()

    # Готовим CSV в памяти
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id","user_id","event_type","ts","datetime_utc","meta"])
    for r in rows:
        rid, uid, et, ts, meta = r
        dt = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        w.writerow([rid, uid, et, ts, dt, meta])
    data = buf.getvalue().encode("utf-8")

    await m.answer_document(
        types.InputFile(io.BytesIO(data), filename=f"events_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"),
        caption="Экспорт событий (UTC)."
    )

# =========================
# Точка входа
# =========================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
