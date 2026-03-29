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
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
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

@dp.message(Form.image, F.photo)
async def image_step(message: Message, state: FSMContext):
    try:
        photo = message.photo[-1].file_id
        await state.update_data(image_file_id=photo)

        data = await state.get_data()

        # ---------- LINKS ----------
        links = json.loads(data.get("links"))

        if not links and data.get("isrc"):
            smart = await get_feature_link(data.get("isrc"))
            if smart:
                links = [smart]

        # ---------- AI ----------
        hook = await generate_hook(data)

        # ---------- SAVE ----------
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
                hook,
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
                caption=f"🎵 {data.get('artist')} - {data.get('track_name')}\n\n{hook}",
                reply_markup=kb
            )

        await message.answer("✅ Відправлено на модерацію")
        await state.clear()

    except Exception as e:
        logging.error(e)
        await message.answer("❌ Помилка")

# ---------------- AI HOOK ----------------
async def generate_hook(data):
    if not OPENAI_API_KEY:
        return fallback_hook()

    try:
        prompt = f"""
        Напиши 2 рядки у стилі телеграм музичних постів:
        ❤️ — коротко
        💔 — протилежно
        Жанр: {data.get('genre')}
        Настрій: {data.get('mood')}
        """

        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}]
        )

        return res.choices[0].message.content.strip()

    except:
        return fallback_hook()

def fallback_hook():
    variants = [
        ("❤️ — це качає", "💔 — але не всім зайде"),
        ("❤️ — норм вайб", "💔 — але сиро"),
        ("❤️ — є потенціал", "💔 — треба допрацювати"),
    ]
    v = random.choice(variants)
    return f"{v[0]}\n{v[1]}"

# ---------------- FEATURE ----------------
async def get_feature_link(isrc):
    try:
        return f"https://feature.fm/{isrc}"
    except:
        return None

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

        await db.execute("UPDATE tracks SET status='approved' WHERE id=?", (track_id,))
        await db.commit()

    artist = row[1]
    track = row[2]
    image = row[7]
    hook = row[8]

    caption = f"🎵 {artist} - {track}\n\n{hook}{build_links()}"

    await bot.send_photo(
        CHANNEL_ID,
        photo=image,
        caption=caption,
        parse_mode="HTML"
    )

    await callback.message.edit_caption(callback.message.caption + "\n\n✅ APPROVED")
    await callback.answer("Posted 🚀")

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
