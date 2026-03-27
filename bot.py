import asyncio
import os
import json
import logging
import random
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import StateFilter

from openai import OpenAI

# ---------------- LOGS ----------------
logging.basicConfig(level=logging.INFO)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

CHANNEL_ID = os.getenv("CHANNEL_ID")
if CHANNEL_ID:
    CHANNEL_ID = int(CHANNEL_ID)

# ---------------- INIT ----------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
client = OpenAI(api_key=OPENAI_API_KEY)

DB_PATH = "tracks.db"

# ---------------- DB ----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist TEXT,
            track_name TEXT,
            isrc TEXT,
            genre TEXT,
            mood TEXT,
            links TEXT,
            image_file_id TEXT,
            description TEXT,
            status TEXT
        )
        """)
        await db.commit()

# ---------------- STATES ----------------
class Form(StatesGroup):
    artist = State()
    track_name = State()
    isrc = State()
    genre = State()
    mood = State()
    links = State()
    image = State()

class EditState(StatesGroup):
    text = State()

# ---------------- START ----------------
@dp.message(F.text == "/start")
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🎵 Введи виконавця:")
    await state.set_state(Form.artist)

# ---------------- FLOW ----------------
@dp.message(Form.artist)
async def artist_step(message: Message, state: FSMContext):
    await state.update_data(artist=message.text.strip())
    await message.answer("Назва треку (або -):")
    await state.set_state(Form.track_name)

@dp.message(Form.track_name)
async def track_step(message: Message, state: FSMContext):
    await state.update_data(track_name=message.text.strip())
    await message.answer("ISRC (або -):")
    await state.set_state(Form.isrc)

@dp.message(Form.isrc)
async def isrc_step(message: Message, state: FSMContext):
    data = await state.get_data()

    if message.text.strip() == "-" and data.get("track_name") == "-":
        await message.answer("❌ Потрібен ISRC або назва")
        return

    await state.update_data(isrc=None if message.text == "-" else message.text.strip())
    await message.answer("Жанр:")
    await state.set_state(Form.genre)

@dp.message(Form.genre)
async def genre_step(message: Message, state: FSMContext):
    await state.update_data(genre=message.text.strip())
    await message.answer("Настрій (або -):")
    await state.set_state(Form.mood)

@dp.message(Form.mood)
async def mood_step(message: Message, state: FSMContext):
    await state.update_data(mood=None if message.text == "-" else message.text.strip())
    await message.answer("Посилання (або -):")
    await state.set_state(Form.links)

@dp.message(Form.links)
async def links_step(message: Message, state: FSMContext):
    links = [] if message.text.strip() == "-" else [message.text.strip()]
    await state.update_data(links=json.dumps(links))
    await message.answer("Завантаж обкладинку:")
    await state.set_state(Form.image)

# ---------------- IMAGE ----------------
@dp.message(Form.image, F.photo)
async def image_step(message: Message, state: FSMContext):
    try:
        photo = message.photo[-1].file_id
        await state.update_data(image_file_id=photo)

        data = await state.get_data()

        links = json.loads(data.get("links"))

        if not links and data.get("isrc"):
            smart = await get_feature_link(data.get("isrc"))
            if smart:
                links = [smart]

        text = await generate_full_text(data)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
            INSERT INTO tracks (artist, track_name, isrc, genre, mood, links, image_file_id, description, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("artist"),
                data.get("track_name"),
                data.get("isrc"),
                data.get("genre"),
                data.get("mood"),
                json.dumps(links),
                photo,
                text,
                "pending"
            ))
            await db.commit()
            track_id = cursor.lastrowid

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{track_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{track_id}")
            ]
        ])

        for admin in ADMIN_IDS:
            await bot.send_photo(
                admin,
                photo=photo,
                caption=f"🎵 {data.get('artist')} - {data.get('track_name')}\n\n{text}",
                reply_markup=kb
            )

        await message.answer("✅ Відправлено на модерацію")
        await state.clear()

    except Exception as e:
        logging.error(e)
        await message.answer("❌ Помилка")

# ---------------- AI ----------------
async def generate_full_text(data):
    if not OPENAI_API_KEY:
        return fallback_full()

    try:
        prompt = f"""
        Напиши пост для телеграм музичного каналу.

        Формат:
        короткий опис + 2 рядки:

        ❤️ — ...
        💔 — ...

        Жанр: {data.get('genre')}
        Настрій: {data.get('mood')}
        """

        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}]
        )

        return res.choices[0].message.content.strip()

    except:
        return fallback_full()

def fallback_full():
    return "Норм трек\n\n❤️ — качає\n💔 — сиро"

# ---------------- FEATURE ----------------
async def get_feature_link(isrc):
    return f"https://feature.fm/{isrc}"

# ---------------- LINKS ----------------
def build_links():
    return (
        "\n\n"
        "<a href='https://t.me/Splyt_ch'>splyT</a> | "
        "<a href='https://t.me/splyt_chat'>Чат</a> | "
        "<a href='https://discord.gg/pdu4SSFwPN'>discord</a>\n"
        "👉 <a href='https://t.me/Splyt_ch'><b>ПІДПИСАТИСЯ</b></a>"
    )

# ---------------- APPROVE ----------------
def get_admin_kb(track_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit_{track_id}"),
            InlineKeyboardButton(text="🚀 Post", callback_data=f"post_{track_id}")
        ],
        [
            InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{track_id}")
        ]
    ])

@dp.callback_query(F.data.startswith("approve_"))
async def approve(callback):
    track_id = int(callback.data.split("_")[1])

    await callback.message.edit_reply_markup(
        reply_markup=get_admin_kb(track_id)
    )

    await callback.answer("Готово до посту")

# ---------------- EDIT ----------------
@dp.callback_query(F.data.startswith("edit_"))
async def edit(callback, state: FSMContext):
    track_id = int(callback.data.split("_")[1])
    await state.update_data(edit_id=track_id)
    await state.set_state(EditState.text)
    await callback.message.answer("✏️ Введи новий текст:")

@dp.message(EditState.text)
async def save_edit(message: Message, state: FSMContext):
    data = await state.get_data()
    track_id = data.get("edit_id")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tracks SET description=? WHERE id=?",
            (message.text, track_id)
        )
        await db.commit()

    await message.answer("✅ Оновлено")
    await state.clear()

# ---------------- POST ----------------
@dp.callback_query(F.data.startswith("post_"))
async def post(callback):
    try:
        track_id = int(callback.data.split("_")[1])

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM tracks WHERE id=?", (track_id,))
            row = await cursor.fetchone()

        artist = row[1]
        track = row[2]
        image = row[7]
        text = row[8]
        links = json.loads(row[6])

        links_text = ""
        if links:
            links_text = "\n\n" + "\n".join(links)

        caption = (
            f"🎵 {artist} - {track}\n\n"
            f"{text}"
            f"{links_text}"
            f"{build_links()}"
        )

        await bot.send_photo(
            CHANNEL_ID,
            photo=image,
            caption=caption,
            parse_mode="HTML"
        )

        await callback.answer("🚀 Запощено")

    except Exception as e:
        logging.error(e)
        await callback.answer("❌ Помилка поста")

# ---------------- REJECT ----------------
@dp.callback_query(F.data.startswith("reject_"))
async def reject(callback):
    track_id = int(callback.data.split("_")[1])

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tracks SET status='rejected' WHERE id=?", (track_id,))
        await db.commit()

    await callback.message.edit_caption(callback.message.caption + "\n\n❌ REJECTED")
    await callback.answer("Rejected")

# ---------------- MAIN ----------------
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
