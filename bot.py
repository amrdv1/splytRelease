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

from openai import OpenAI

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO)

# ---------------- ENV ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

CHANNEL_ID = os.getenv("CHANNEL_ID")
if CHANNEL_ID:
    CHANNEL_ID = int(CHANNEL_ID)

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
            genre TEXT,
            mood TEXT,
            links TEXT,
            file_id TEXT,
            file_type TEXT,
            description TEXT,
            status TEXT
        )
        """)
        await db.commit()

# ---------------- STATES ----------------
class Form(StatesGroup):
    artist = State()
    track_name = State()
    genre = State()
    mood = State()
    links = State()
    media = State()

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
    await message.answer("Назва треку:")
    await state.set_state(Form.track_name)

@dp.message(Form.track_name)
async def track_step(message: Message, state: FSMContext):
    await state.update_data(track_name=message.text.strip())
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
    await message.answer("Завантаж фото або відео:")
    await state.set_state(Form.media)

# ---------------- MEDIA ----------------
@dp.message(Form.media, F.photo)
async def photo_step(message: Message, state: FSMContext):
    await handle_media(message, state, "photo", message.photo[-1].file_id)

@dp.message(Form.media, F.video)
async def video_step(message: Message, state: FSMContext):
    await handle_media(message, state, "video", message.video.file_id)

async def handle_media(message, state, media_type, file_id):
    try:
        await state.update_data(file_id=file_id, file_type=media_type)
        data = await state.get_data()

        text = await generate_full_text(data)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
            INSERT INTO tracks (artist, track_name, genre, mood, links, file_id, file_type, description, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("artist"),
                data.get("track_name"),
                data.get("genre"),
                data.get("mood"),
                data.get("links"),
                file_id,
                media_type,
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
            if media_type == "photo":
                await bot.send_photo(admin, photo=file_id,
                    caption=f"🎵 {data.get('artist')} - {data.get('track_name')}\n\n{text}",
                    reply_markup=kb)
            else:
                await bot.send_video(admin, video=file_id,
                    caption=f"🎵 {data.get('artist')} - {data.get('track_name')}\n\n{text}",
                    reply_markup=kb)

        await message.answer("✅ Відправлено на модерацію")
        await state.clear()

    except Exception as e:
        logging.error(e)
        await message.answer("❌ Помилка")

# ---------------- AI ----------------
async def generate_full_text(data):
    if not OPENAI_API_KEY:
        return "Норм трек\n\n❤️ — качає\n💔 — сиро"

    try:
        prompt = f"""
        Напиши пост для телеграм музичного каналу.

        Формат:
        1 абзац (2-3 речення)
        потім:

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
        return "Норм трек\n\n❤️ — качає\n💔 — сиро"

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
@dp.callback_query(F.data.startswith("approve_"))
async def approve(callback):
    track_id = int(callback.data.split("_")[1])

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM tracks WHERE id=?", (track_id,))
        row = await cursor.fetchone()

    artist, track = row[1], row[2]
    file_id, file_type = row[6], row[7]
    text = row[8]

    caption = f"🎵 {artist} - {track}\n\n{text}{build_links()}"

    if file_type == "photo":
        await bot.send_photo(CHANNEL_ID, photo=file_id, caption=caption, parse_mode="HTML")
    else:
        await bot.send_video(CHANNEL_ID, video=file_id, caption=caption, parse_mode="HTML")

    await callback.answer("🚀 Запощено")

# ---------------- REJECT ----------------
@dp.callback_query(F.data.startswith("reject_"))
async def reject(callback):
    await callback.answer("❌ Відхилено")

# ---------------- MAIN ----------------
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
