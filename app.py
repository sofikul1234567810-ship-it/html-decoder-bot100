import os
import re
import html
import base64
import asyncio
import logging
import tempfile
from urllib.parse import unquote
from threading import Thread

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "5"))
MAX_BYTES = MAX_FILE_MB * 1024 * 1024

web = Flask(__name__)

@web.get("/")
def home():
    return "HTML Decoder Bot is running."

@web.get("/health")
def health():
    return {"status": "ok"}

def run_web():
    web.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), use_reloader=False)

def decode_common(source):
    """Decode common text encodings only. Never execute uploaded code."""
    current = source
    methods = []
    for _ in range(3):
        before = current
        decoded = html.unescape(current)
        if decoded != current:
            current = decoded
            methods.append("HTML entities")
        decoded = unquote(current)
        if decoded != current:
            current = decoded
            methods.append("URL percent encoding")
        decoded = re.sub(r'\\u([0-9a-fA-F]{4})',
                         lambda m: chr(int(m.group(1), 16)), current)
        decoded = re.sub(r'\\x([0-9a-fA-F]{2})',
                         lambda m: chr(int(m.group(1), 16)), decoded)
        if decoded != current:
            current = decoded
            methods.append("JS unicode/hex escapes")

        # Decode Base64 only inside obvious atob()/base64 assignment or quoted long strings.
        pattern = r"""(?i)(?:atob\s*\(\s*|base64\s*[:=]\s*|["'])([A-Za-z0-9+/]{80,}={0,2})(?:["']\s*\)?|\s*)"""
        def decode_b64(match):
            token = match.group(1)
            try:
                raw = base64.b64decode(token + "=" * ((4 - len(token) % 4) % 4))
                text = raw.decode("utf-8")
                ratio = sum(c.isprintable() or c in "\r\n\t" for c in text) / max(1, len(text))
                if ratio > 0.92:
                    methods.append("Base64 text")
                    return text
            except Exception:
                pass
            return match.group(0)
        current = re.sub(pattern, decode_b64, current)
        if current == before:
            break
    return current, sorted(set(methods))

def beautify_text(source):
    # Basic, conservative tag formatting; this does not re-interpret/execute scripts.
    result = re.sub(r'>\s*<', '>\n<', source.strip())
    return result + ("\n" if result else "")

def analyze_text(source, filename):
    tests = [
        ("Base64 / atob references", r"atob\s*\(|base64"),
        ("Dynamic execution (eval/Function)", r"\beval\s*\(|new\s+function"),
        ("Obfuscated variable names", r"_0x[0-9a-f]{3,}|\bvar\s+_0x"),
        ("Anti-debug / automation checks", r"webdriver|headlesschrome|playwright|selenium|devtools"),
        ("Very long encoded-looking data", r"[A-Za-z0-9+/]{500,}={0,2}"),
        ("External URL or network calls", r"https?://|fetch\s*\(|xmlhttprequest"),
    ]
    found = [label for label, pattern in tests if re.search(pattern, source, re.I)]
    return (
        f"📄 File: {filename}\n"
        f"📦 Format: {os.path.splitext(filename)[1] or 'unknown'}\n"
        f"📏 Size: {len(source):,} characters\n"
        f"🔎 Markers found: {len(found)}\n\n"
        + ("\n".join("• " + x for x in found) if found else "No common markers detected.")
        + "\n\nStatic analysis only. This does not prove the file is safe or fully decoded."
    )

def make_output_file(filename, content, prefix):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(filename))[:70] or "file.html"
    ext = os.path.splitext(safe)[1] or ".html"
    root, _ = os.path.splitext(safe)
    outname = prefix + root + ext
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as handle:
        handle.write(content)
    return path, outname

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("🧩 Decode", callback_data="decode"),
         InlineKeyboardButton("✨ Beautify", callback_data="beautify")],
        [InlineKeyboardButton("🔎 Analyze", callback_data="analyze"),
         InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    await update.effective_message.reply_text(
        "🤖 HTML Decoder Bot\n\n"
        "HTML / JS / TXT / JSON / XML / CSS ফাইল পাঠাও। তারপর Decode, Beautify বা Analyze বেছে নাও।\n"
        "বট ফাইলের কোড চালায় না—শুধু টেক্সট পরীক্ষা করে।",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "ব্যবহার:\n1) /start চাপো\n2) HTML/JS ফাইল পাঠাও\n"
        "3) Decode, Beautify বা Analyze বেছে নাও\n\n"
        "সমর্থিত ফাইল: .html .htm .js .mjs .cjs .txt .json .xml .css\n"
        "Custom encryption বা VM-protected code সবসময় decode করা সম্ভব নয়।"
    )

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.effective_message.document
    filename = doc.file_name or "upload.html"
    ext = os.path.splitext(filename)[1].lower()
    allowed = {".html", ".htm", ".js", ".mjs", ".cjs", ".txt", ".json", ".xml", ".css"}
    if ext not in allowed:
        await update.effective_message.reply_text("HTML, JS, TXT, JSON, XML বা CSS ফাইল পাঠাও।")
        return
    if doc.file_size and doc.file_size > MAX_BYTES:
        await update.effective_message.reply_text(f"ফাইলের সর্বোচ্চ সাইজ {MAX_FILE_MB} MB।")
        return

    status = await update.effective_message.reply_text("⏳ ফাইল পড়ছি…")
    tg_file = await context.bot.get_file(doc.file_id)
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        await tg_file.download_to_drive(path)
        if os.path.getsize(path) > MAX_BYTES:
            await status.edit_text(f"ফাইলের সর্বোচ্চ সাইজ {MAX_FILE_MB} MB।")
            return
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            source = handle.read()
        context.user_data["source"] = source
        context.user_data["filename"] = filename
        buttons = [
            [InlineKeyboardButton("✅ Decode", callback_data="decode"),
             InlineKeyboardButton("✨ Beautify", callback_data="beautify")],
            [InlineKeyboardButton("🔎 Analyze", callback_data="analyze")]
        ]
        await status.edit_text(
            f"📄 File: {filename}\n📏 Size: {len(source):,} characters\n"
            "ফাইল প্রস্তুত—এখন একটি অপশন বেছে নাও।",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as exc:
        await status.edit_text("ফাইল পড়তে সমস্যা হয়েছে: " + type(exc).__name__)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

async def do_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode):
    message = update.effective_message
    source = context.user_data.get("source")
    filename = context.user_data.get("filename", "upload.html")
    if source is None:
        await message.reply_text("আগে HTML/JS ফাইল পাঠাও।")
        return
    if mode == "analyze":
        await message.reply_text(analyze_text(source, filename)[:4000])
        return

    if mode == "beautify":
        output = beautify_text(source)
        methods = ["basic formatting"]
        prefix = "beautified_"
    else:
        output, methods = decode_common(source)
        prefix = "decoded_"

    path, outname = make_output_file(filename, output, prefix)
    try:
        with open(path, "rb") as handle:
            await message.reply_document(
                document=handle,
                filename=outname,
                caption=(
                    f"✅ {mode.upper()} COMPLETE\n"
                    f"🧩 Changes: {', '.join(methods) if methods else 'No common encoding found'}\n"
                    f"📏 Output: {len(output):,} characters\n"
                    "Custom encryption may need its key/original source; full recovery is not guaranteed."
                )
            )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "help":
        await query.message.reply_text(
            "ফাইল পাঠিয়ে Decode / Beautify / Analyze বেছে নাও। "
            "Custom encryption সবসময় reverse করা যায় না।"
        )
    else:
        await do_mode(update, context, query.data)

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Render Environment Variables-এ BOT_TOKEN সেট করো।")
    bot = Application.builder().token(BOT_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("help", help_command))
    bot.add_handler(CommandHandler("decode", lambda u, c: do_mode(u, c, "decode")))
    bot.add_handler(CommandHandler("beautify", lambda u, c: do_mode(u, c, "beautify")))
    bot.add_handler(CommandHandler("analyze", lambda u, c: do_mode(u, c, "analyze")))
    bot.add_handler(CallbackQueryHandler(callback, pattern="^(decode|beautify|analyze|help)$"))
    bot.add_handler(MessageHandler(filters.Document.ALL, receive_file))
    await bot.initialize()
    await bot.start()
    await bot.updater.start_polling()
    logging.info("Telegram polling started")
    await asyncio.Event().wait()

if __name__ == "__main__":
    Thread(target=run_web, daemon=True).start()
    asyncio.run(main())
