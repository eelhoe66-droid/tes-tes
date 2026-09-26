import os
import string
import time
import logging
import asyncio
import random

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Sembunyikan log internal MTProtoSender Telethon agar terminal bersih
logging.getLogger("telethon.network.mtprotosender").setLevel(logging.WARNING)
logging.getLogger("telethon.network.telegrambarebodysender").setLevel(logging.WARNING)

# Load dotenv jika dijalankan secara lokal
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telegram import (
    Update, InlineQueryResultArticle, InputTextMessageContent,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, 
    InlineQueryHandler, CallbackQueryHandler, ChosenInlineResultHandler,
    MessageHandler, filters, ContextTypes
)

# Configuration from Environment Variables
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DATA_DIR = "./" 
BAN_FILE = f"{DATA_DIR}banned.txt"
USER_FILE = f"{DATA_DIR}users.txt"

BANNED_USERS = set()
clients = []
client_cooldown = {}
client_index = 0
ALL_USERS = set()

# Cache sementara hasil scan per inline message id
SCAN_CACHE = {}

# ================== NOTIFY ADMIN ==================
async def notify_admin(context: ContextTypes.DEFAULT_TYPE, user, action_type: str, query_details: str = ""):
    if not ADMIN_ID:
        return
    
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    full_name = f"{first_name} {last_name}".strip()
    username = f"@{user.username}" if user.username else "Tidak ada username"
    user_id = user.id
    
    text = (
        f"👤 Aktivitas Pengguna\n"
        f"• Aksi: {action_type}\n"
        f"• Nama: {full_name}\n"
        f"• Username: {username}\n"
        f"• ID: `{user_id}`"
    )
    if query_details:
        text += f"\n• Query/Pesan: `{query_details}`"
        
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Gagal mengirim notifikasi ke admin: {e}")

# ================== PERSISTENCE ==================
def load_users():
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r") as f:
            for line in f:
                if line.strip(): ALL_USERS.add(int(line.strip()))

def save_user(user_id):
    if user_id not in ALL_USERS:
        ALL_USERS.add(user_id)
        with open(USER_FILE, "a") as f:
            f.write(f"{user_id}\n")

def load_bans():
    if os.path.exists(BAN_FILE):
        with open(BAN_FILE, "r") as f:
            for line in f:
                if line.strip(): BANNED_USERS.add(int(line.strip()))

def save_ban(user_id):
    BANNED_USERS.add(user_id)
    with open(BAN_FILE, "w") as f:
        for uid in BANNED_USERS:
            f.write(f"{uid}\n")

def remove_ban(user_id):
    if user_id in BANNED_USERS:
        BANNED_USERS.remove(user_id)
        with open(BAN_FILE, "w") as f:
            for uid in BANNED_USERS:
                f.write(f"{uid}\n")

# ================== GENERATORS ==================
rata, tdk_rata, vokal = "asweruiozxcvnm", "qtypdfghjklb", "aeiou"

def gen_tamhur(b): return list({b[:i] + l + b[i:] for i in range(len(b)+1) for l in string.ascii_lowercase})
def gen_tamping(b): return list({l + b for l in string.ascii_lowercase} | {b + l for l in string.ascii_lowercase})
def gen_switch(b):
    res = set()
    for i in range(len(b) - 1):
        lst = list(b); lst[i], lst[i+1] = lst[i+1], lst[i]; res.add("".join(lst))
    return list(res)
def gen_uncommon(b): return list({b[:i] + b[i] + b[i:] for i in range(len(b))}) if b else []
def gen_ganhur(b): return list({b[:i] + l + b[i+1:] for i in range(len(b)) for l in string.ascii_lowercase})
def gen_kurhur(b): return list({b[:i] + b[i+1:] for i in range(len(b))}) if len(b) > 1 else []
def gen_canon(b):
    res = {b + 's'}; m = {'i': 'l', 'l': 'i'}
    for i, char in enumerate(b):
        if char in m: res.add(b[:i] + m[char] + b[i+1:])
    return list(res)
def gen_rata(b): return list({b[:i] + l + b[i:] for i in range(len(b)+1) for l in rata})
def gen_tidakrata(b): return list({b[:i] + l + b[i:] for i in range(len(b)+1) for l in tdk_rata})
def gen_vokal(b): return list({b[:i] + l + b[i:] for i in range(len(b)+1) for l in vokal})

GENERATORS = {
    "switch": (gen_switch, "switch"),
    "tamping": (gen_tamping, "tamping"),
    "tamhur": (gen_tamhur, "tamhur"),
    "ganhur": (gen_ganhur, "ganhur"),
    "uncommon": (gen_uncommon, "uncommon"),
    "kurhur": (gen_kurhur, "kurhur"),
    "rata": (gen_rata, "rata"),
    "tidakrata": (gen_tidakrata, "tidak rata"),
    "vokal": (gen_vokal, "vokal"),
}

# ================== CORE LOGIC ==================
async def init_clients():
    if not API_ID or not API_HASH: 
        logger.error("❌ API_ID atau API_HASH kosong!")
        return
    for i in range(1, 21):
        session_str = os.getenv(f"SESSION_{i}")
        if not session_str:
            continue
        try:
            c = TelegramClient(StringSession(session_str), int(API_ID), API_HASH)
            await c.connect()
            if await c.is_user_authorized():
                clients.append(c)
                client_cooldown[c] = 0
                logger.info(f"✅ acc{i} (StringSession) Ready")
            else: 
                await c.disconnect()
        except Exception as e: 
            logger.debug(f"Gagal memuat SESSION_{i}: {e}")

def get_available_client():
    global client_index
    now = time.time()
    available = [c for c in clients if client_cooldown[c] <= now]
    if not available: return None
    client = available[client_index % len(available)]
    client_index += 1
    return client

def chunk_results(items, chunk_size=15):
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]

def build_pagination_keyboard(current_page, total_pages, target_base, mode_key):
    if total_pages <= 1:
        return None
    
    buttons = []
    for i in range(total_pages):
        label = f"• {i+1} •" if i == current_page else f"{i+1}"
        buttons.append(InlineKeyboardButton(label, callback_data=f"page_{mode_key}_{target_base}_{i}"))
    
    return InlineKeyboardMarkup([buttons])

# ================== INLINE HANDLER ==================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    user = update.inline_query.from_user
    uid = user.id

    if uid in BANNED_USERS:
        return

    save_user(uid)

    if not query:
        results = [
            InlineQueryResultArticle(
                id="help",
                title="misal",
                description="bot ini khusus gw dan temen temen gw, selain itu gw ban",
                input_message_content=InputTextMessageContent(
                    "bot ini khusus gw dan temen temen gw, selain itu gw ban\n\n"
                    "Contoh penggunaan:\n"
                    " @sunless2bot adnan"
                )
            )
        ]
        await update.inline_query.answer(results, cache_time=1)
        return

    parts = query.split(maxsplit=1)
    
    if parts[0].lower() in GENERATORS and len(parts) > 1:
        mode_key = parts[0].lower()
        base = parts[1].replace("@", "").strip()
        mode_label = GENERATORS[mode_key][1]
    else:
        mode_key = "tamhur"
        base = query.replace("@", "").strip()
        mode_label = "tamhur"

    loading_text = f"Klik tombol di bawah untuk mulai scan @{base} ({mode_label})..."

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Mulai Scan", callback_data=f"runlive_{mode_key}_{base}")
    ]])

    results = [
        InlineQueryResultArticle(
            id=f"scan_{mode_key}_{base}_{int(time.time())}",
            title=f"Scan @{base} ({mode_label})",
            description=f"Langsung scan variasi username @{base}",
            input_message_content=InputTextMessageContent(loading_text),
            reply_markup=keyboard
        )
    ]
    
    await update.inline_query.answer(results, cache_time=1)

# Notifikasi ke admin hanya terkirim ketika user memilih/mengirim hasil inline query
async def chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.chosen_inline_result.from_user
    if user.id in BANNED_USERS:
        return
    query = update.chosen_inline_result.query
    await notify_admin(context, user, "Mengirim Inline Query", query)

# ================== CALLBACK QUERY HANDLER (SAFE SCAN & PAGINATION) ==================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    
    if user.id in BANNED_USERS:
        await query.answer("❌ Kamu telah dibanned dari penggunaan bot ini.", show_alert=True)
        return

    data = query.data
    inline_msg_id = query.inline_message_id

    # 1. Trigger Mulai Scan di Channel / Grup
    if data.startswith("runlive_"):
        _, mode_key, base = data.split("_", 2)
        await query.answer("Memulai scan...")
        
        # Kirim notifikasi saat tombol scan diklik
        await notify_admin(context, user, "Menjalankan Live Scan", f"Mode: {mode_key}, Base: @{base}")

        available_clients = [c for c in clients if client_cooldown[c] <= time.time()]
        if not available_clients:
            await context.bot.edit_message_text(
                inline_message_id=inline_msg_id,
                text="❌ acc gua limit"
            )
            return

        gen_func, lbl = GENERATORS.get(mode_key, (gen_tamhur, "tamhur"))
        raw_res = gen_func(base)
        if mode_key == "uncommon":
            raw_res += gen_canon(base)

        candidates = list(set(raw_res))[:100]
        found_avail = []
        last_update_time = time.time()
        
        sem = asyncio.Semaphore(max(1, len(available_clients)))

        async def worker(u):
            nonlocal last_update_time
            async with sem:
                for _ in range(2):
                    c = get_available_client()
                    if not c:
                        await asyncio.sleep(0.2)
                        continue
                    try:
                        await asyncio.sleep(random.uniform(0.2, 0.4))
                        
                        ok = await asyncio.wait_for(
                            c(functions.account.CheckUsernameRequest(u)), 
                            timeout=4.0
                        )
                        
                        if ok:
                            res_str = f"🟢 @{u}"
                            found_avail.append(res_str)

                            now = time.time()
                            if now - last_update_time > 3.0:
                                last_update_time = now
                                live_text = (
                                    f"scanning @{base} ({lbl})...\n"
                                    f"ditemukan: {len(found_avail)}\n\n" +
                                    "\n".join(found_avail[:15]) +
                                    ("\n..." if len(found_avail) > 15 else "")
                                )
                                try:
                                    await context.bot.edit_message_text(
                                        inline_message_id=inline_msg_id,
                                        text=live_text
                                    )
                                except Exception:
                                    pass
                            return res_str
                        return None
                    except FloodWaitError as e:
                        logger.warning(f"⚠️ Account terkena FloodWait {e.seconds}s. Istirahat sementara.")
                        client_cooldown[c] = time.time() + e.seconds + 5
                        continue
                    except asyncio.TimeoutError:
                        client_cooldown[c] = time.time() + 10
                        continue
                    except Exception:
                        return None
                return None

        await asyncio.gather(*(worker(u) for u in candidates))

        if not found_avail:
            await context.bot.edit_message_text(
                inline_message_id=inline_msg_id,
                text=f"❌ @{base} ({lbl}) ga ada atau akun gua limit jadi gak nemu"
            )
            return

        pages = chunk_results(found_avail, chunk_size=15)
        
        SCAN_CACHE[inline_msg_id] = {
            "pages": pages,
            "mode_label": lbl,
            "base": base,
            "mode_key": mode_key
        }

        page_text = (
            f"hasil scan untuk @{base} ({lbl})"
            f" ada {len(found_avail)} usn\n\n" + 
            "\n".join(pages[0])
        )
        
        reply_markup = build_pagination_keyboard(0, len(pages), base, mode_key)

        try:
            await context.bot.edit_message_text(
                inline_message_id=inline_msg_id,
                text=page_text,
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Gagal update hasil akhir: {e}")

    # 2. Handler Pindah Halaman (1, 2, 3...)
    elif data.startswith("page_"):
        _, mode_key, base, page_idx = data.split("_", 3)
        page_idx = int(page_idx)

        if inline_msg_id not in SCAN_CACHE:
            await query.answer("⚠️ Session scan ini sudah kadaluarsa. Silakan scan ulang.", show_alert=True)
            return

        cache_data = SCAN_CACHE[inline_msg_id]
        pages = cache_data["pages"]
        lbl = cache_data["mode_label"]

        if page_idx >= len(pages):
            await query.answer()
            return

        page_text = (
            f" hasil scan @{base} ({lbl}) ada  {sum(len(p) for p in pages)} usn\n"
            f"{page_idx + 1}/{len(pages)}\n\n" + 
            "\n".join(pages[page_idx])
        )

        reply_markup = build_pagination_keyboard(page_idx, len(pages), base, mode_key)

        try:
            await context.bot.edit_message_text(
                inline_message_id=inline_msg_id,
                text=page_text,
                reply_markup=reply_markup
            )
            await query.answer(f"{page_idx + 1}")
        except Exception:
            await query.answer()

# ================== COMMAND & MESSAGE HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in BANNED_USERS: return
    save_user(user.id)
    await notify_admin(context, user, "Menjalankan /start")
    await update.message.reply_text("P")

# Handling semua pesan masuk (PM Bot)
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in BANNED_USERS:
        return
    save_user(user.id)
    text = update.message.text
    # Notifikasi pesan pribadi ke Admin
    await notify_admin(context, user, "Pesan Pribadi", text)

# Command khusus Admin untuk Ban
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
    
    if not context.args:
        await update.message.reply_text("Penggunaan: `/ban <user_id>`", parse_mode="Markdown")
        return
    
    try:
        target_id = int(context.args[0])
        save_ban(target_id)
        await update.message.reply_text(f"✅ ID `{target_id}` telah berhasil dibanned.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ ID Pengguna tidak valid.")

# Command khusus Admin untuk Unban
async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return
    
    if not context.args:
        await update.message.reply_text("Penggunaan: `/unban <user_id>`", parse_mode="Markdown")
        return
    
    try:
        target_id = int(context.args[0])
        remove_ban(target_id)
        await update.message.reply_text(f"✅ ID `{target_id}` telah dihapus dari daftar ban.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ ID Pengguna tidak valid.")

async def post_init(application):
    logger.info("⚙️ Inisialisasi Telethon sessions...")
    await init_clients()
    logger.info(f"📊 Total akun aktif: {len(clients)} akun.")

def main():
    load_bans()
    load_users()
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN tidak ditemukan di Environment Variable!")
        return
        
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Handler pesan obrolan langsung/private chat ke bot
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_private_message))

    logger.info("🚀 Bot berjalan...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
