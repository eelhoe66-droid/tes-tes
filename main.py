import os
import string
import time
import logging
import asyncio
import random

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

logging.getLogger("telethon.network.mtprotosender").setLevel(logging.WARNING)
logging.getLogger("telethon.network.telegrambarebodysender").setLevel(logging.WARNING)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError, PeerFloodError
from telegram import (
    Update, InlineQueryResultArticle, InputTextMessageContent,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, 
    InlineQueryHandler, CallbackQueryHandler, ContextTypes
)

# Configuration from Environment Variables
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Konfigurasi Proxy (Opsional, atur ke None jika tidak pakai proxy)
# Contoh jika pakai SOCKS5:
# PROXY_HOST = "123.45.67.89"
# PROXY_PORT = 1080
# PROXY_USER = None
# PROXY_PASS = None
PROXY_HOST = os.getenv("PROXY_HOST", None)
PROXY_PORT = int(os.getenv("PROXY_PORT", "0")) if os.getenv("PROXY_PORT") else None
PROXY_USER = os.getenv("PROXY_USER", None)
PROXY_PASS = os.getenv("PROXY_PASS", None)

DATA_DIR = "./" 
BAN_FILE = f"{DATA_DIR}banned.txt"
USER_FILE = f"{DATA_DIR}users.txt"

BANNED_USERS = set()
clients = []
ALL_USERS = set()
SCAN_CACHE = {}

# ================== PERSISTENCE & TRACKING ==================
def load_users():
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    uid_part = line.split("|")[0].strip()
                    if uid_part.isdigit():
                        ALL_USERS.add(int(uid_part))

async def track_user(user, context: ContextTypes.DEFAULT_TYPE):
    if not user or user.id in BANNED_USERS:
        return

    if user.id not in ALL_USERS:
        ALL_USERS.add(user.id)
        
        username_str = f"@{user.username}" if user.username else "Tanpa Username"
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        user_line = f"{user.id} | {username_str} | {full_name}\n"
        
        with open(USER_FILE, "a", encoding="utf-8") as f:
            f.write(user_line)

        if ADMIN_ID:
            try:
                admin_text = (
                    f"👤 <b>Pengguna Baru Terdeteksi!</b>\n"
                    f"• <b>ID:</b> <code>{user.id}</code>\n"
                    f"• <b>Nama:</b> {full_name}\n"
                    f"• <b>Username:</b> {username_str}\n"
                    f"• <b>Total Pengguna:</b> {len(ALL_USERS)}"
                )
                await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Gagal mengirim notifikasi admin: {e}")

def load_bans():
    if os.path.exists(BAN_FILE):
        with open(BAN_FILE, "r") as f:
            for line in f:
                if line.strip(): BANNED_USERS.add(int(line.strip()))

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
        logger.error("❌ API_ID atau API_HASH kosong di Environment Variables!")
        return

    # Debugging: Cetak semua file yang ada di folder root Railway
    try:
        files_in_dir = os.listdir(DATA_DIR)
        session_files = [f for f in files_in_dir if f.endswith('.session')]
        logger.info(f"📂 Daftar file .session yang terbaca di Railway: {session_files}")
    except Exception as e:
        logger.error(f"Gagal membaca folder: {e}")

    for i in range(1, 21):
        # Sesuaikan 'acc' jika nama file session Anda berawalan lain
        s = f"{DATA_DIR}acc{i}"
        session_path = f"{s}.session"
        
        if not os.path.exists(session_path):
            continue
            
        try:
            logger.info(f"🔄 Mencoba menghubungkan {session_path}...")
            c = TelegramClient(s, int(API_ID), API_HASH)
            await c.connect()
            
            if await c.is_user_authorized():
                clients.append(c)
                logger.info(f"✅ {session_path} BERHASIL Authorized!")
            else: 
                logger.warning(f"⚠️ {session_path} ADA, tapi TIDAK Authorized (Beda API_ID / Sesi Hangus)!")
                await c.disconnect()
        except Exception as e: 
            logger.error(f"❌ Gagal memuat {session_path}: {e}")
# ================== INLINE HANDLER ==================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    from_user = update.inline_query.from_user

    if from_user.id in BANNED_USERS:
        return

    await track_user(from_user, context)

    if not query:
        results = [
            InlineQueryResultArticle(
                id="help",
                title="Cara Penggunaan",
                description="Contoh: adnan, uncommon adnan, tamping adnan, dll",
                input_message_content=InputTextMessageContent(
                    "Contoh penggunaan:\n"
                    " @botusername adnan\n"
                    " @botusername uncommon adnan"
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

# ================== CALLBACK QUERY HANDLER ==================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    inline_msg_id = query.inline_message_id

    await track_user(query.from_user, context)

    if data.startswith("runlive_"):
        _, mode_key, base = data.split("_", 2)
        await query.answer("Memulai scan...")

        if not clients:
            await context.bot.edit_message_text(
                inline_message_id=inline_msg_id,
                text="❌ Tidak ada akun Telethon yang aktif/tersedia."
            )
            return

        gen_func, lbl = GENERATORS.get(mode_key, (gen_tamhur, "tamhur"))
        raw_res = gen_func(base)
        if mode_key == "uncommon":
            raw_res += gen_canon(base)

        # Tanpa batasan jumlah kandidat (bebas/unbound)
        candidates = list(set(raw_res))
        found_avail = []
        last_update_time = time.time()
        
        work_queue = asyncio.Queue()
        for c in candidates:
            work_queue.put_nowait(c)

        async def worker_account(client):
            nonlocal last_update_time
            while not work_queue.empty():
                try:
                    usn = work_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                try:
                    # Delay acak 2.5s - 4.5s per request per akun agar sangat aman dari ban
                    await asyncio.sleep(random.uniform(2.5, 4.5))
                    
                    ok = await asyncio.wait_for(
                        client(functions.account.CheckUsernameRequest(usn)),
                        timeout=5.0
                    )
                    
                    if ok:
                        res_str = f"🟢 @{usn}"
                        found_avail.append(res_str)

                        now = time.time()
                        # Update status live UI setiap 3.0 detik
                        if now - last_update_time > 3.0:
                            last_update_time = now
                            live_text = (
                                f"scanning @{base} ({lbl})...\n"
                                f"diproses: {len(candidates) - work_queue.qsize()}/{len(candidates)}\n"
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

                except PeerFloodError:
                    logger.warning("⚠️ Akun terkena PeerFlood. Mengistirahatkan akun selama 5 menit.")
                    await asyncio.sleep(300)
                    work_queue.put_nowait(usn)  # Kembalikan task ke antrean
                except FloodWaitError as e:
                    logger.warning(f"⚠️ Akun terkena FloodWait {e.seconds}s. Mengistirahatkan sementara.")
                    await asyncio.sleep(e.seconds + 5)
                    work_queue.put_nowait(usn)  # Kembalikan task ke antrean
                except asyncio.TimeoutError:
                    work_queue.put_nowait(usn)
                except Exception as e:
                    logger.debug(f"Error saat scanning: {e}")
                finally:
                    work_queue.task_done()

        # Jalankan worker secara asinkron terdistribusi ke seluruh akun aktif
        await asyncio.gather(*(worker_account(c) for c in clients))

        if not found_avail:
            await context.bot.edit_message_text(
                inline_message_id=inline_msg_id,
                text=f"❌ @{base} ({lbl}) ga ada atau akun gua limit jadi ga nemu"
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
            f"hasil scan untuk @{base} ({lbl})\n"
            f"ada {len(found_avail)} usn tersedia\n\n" + 
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
            f"hasil scan @{base} ({lbl}) ada {sum(len(p) for p in pages)} usn\n"
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
            await query.answer(f"Halaman {page_idx + 1}")
        except Exception:
            await query.answer()

# ================== COMMAND HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in BANNED_USERS: return
    await track_user(user, context)
    await update.message.reply_text("Bot aktif. Gunakan via inline mode!")

async def get_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    if not os.path.exists(USER_FILE):
        await update.message.reply_text("Belum ada data pengguna.")
        return

    with open(USER_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        await update.message.reply_text("Belum ada data pengguna.")
        return

    text = f"📊 <b>Total Pengguna Terdata: {len(ALL_USERS)}</b>\n\n"
    recent = lines[-50:]
    text += "\n".join(recent)

    if len(lines) > 50:
        text += f"\n\n<i>(Menampilkan 50 pengguna terakhir dari total {len(lines)})</i>"

    await update.message.reply_text(text, parse_mode="HTML")

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
    app.add_handler(CommandHandler("users", get_users_list))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("🚀 Bot berjalan...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
