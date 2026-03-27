import asyncio
import os
import json
import logging
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

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN not set")

# ---------------- INIT ----------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------- DB ----------------
DB_PATH = "tracks.db"

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

    isrc = message.text.strip()
    track = data.get("track_name", "").strip()

    if isrc == "-" and track == "-":
        await message.answer("❌ Потрібен ISRC або назва треку")
        return

    await state.update_data(isrc=None if isrc == "-" else isrc)
    await message.answer("Жанр:")
    await state.set_state(Form.genre)

@dp.message(Form.genre)
async def genre_step(message: Message, state: FSMContext):
    await state.update_data(genre=message.text.strip())
    await message.answer("Настрій / вайб (або -):")
    await state.set_state(Form.mood)

@dp.message(Form.mood)
async def mood_step(message: Message, state: FSMContext):
    await state.update_data(mood=None if message.text.strip() == "-" else message.text.strip())
    await message.answer("Посилання (або -):")
    await state.set_state(Form.links)

@dp.message(Form.links)
async def links_step(message: Message, state: FSMContext):
    text = message.text.strip()
    links = [] if text == "-" else [text]
    await state.update_data(links=json.dumps(links))
    await message.answer("Завантаж обкладинку:")
    await state.set_state(Form.image)

@dp.message(Form.image, F.photo)
async def image_step(message: Message, state: FSMContext):
    try:
        photo = message.photo[-1].file_id
        await state.update_data(image_file_id=photo)

        data = await state.get_data()

        # ---------------- AI ----------------
        text = await generate_text(data)

        # ---------------- SAVE ----------------
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
                data.get("links"),
                photo,
                text,
                "pending"
            ))
            await db.commit()
            track_id = cursor.lastrowid

        # ---------------- ADMIN BUTTONS ----------------
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{track_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{track_id}")
            ]
        ])

        # ---------------- SEND TO ADMINS ----------------
        for admin in ADMIN_IDS:
            try:
                await bot.send_photo(
                    admin,
                    photo=photo,
                    caption=f"🎵 {data.get('artist')} - {data.get('track_name')}\n\n{text}",
                    reply_markup=kb
                )
            except Exception as e:
                logging.error(f"Admin send error: {e}")

        await message.answer("✅ Відправлено на модерацію")
        await state.clear()

    except Exception as e:
        logging.error(f"Image step error: {e}")
        await message.answer("❌ Помилка. Спробуй ще раз.")

# ---------------- AI ----------------
async def generate_text(data):
    if not OPENAI_API_KEY:
        return fallback_text(data)

    try:
        prompt = f"""
        Напиши короткий маркетинговий опис треку.

        Жанр: {data.get('genre')}
        Настрій: {data.get('mood')}

        Правила:
        - 2-4 речення
        - без вигаданих фактів
        - стиль як у музичних релізів
        """

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logging.error(f"AI error: {e}")
        return fallback_text(data)

def fallback_text(data):
    return f"{data.get('artist')} презентує трек у жанрі {data.get('genre')} з атмосферою {data.get('mood')}."

# ---------------- ADMIN ----------------
@dp.callback_query(F.data.startswith("approve_"))
async def approve(callback):
    track_id = int(callback.data.split("_")[1])

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tracks SET status='approved' WHERE id=?", (track_id,))
        await db.commit()

    await callback.message.edit_caption(callback.message.caption + "\n\n✅ APPROVED")
    await callback.answer("Approved")

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
    logging.info("🚀 Bot starting...")
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.error(f"CRITICAL ERROR: {e}")