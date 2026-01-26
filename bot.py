
# -*- coding: utf-8 -*-

import logging
import uuid
import asyncio
import os
from threading import Thread
from flask import Flask
from pymongo import MongoClient

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ChatJoinRequestHandler,
    JobQueue
)
from telegram.error import RetryAfter
from telegram import constants
from datetime import datetime
from typing import Optional
import html
import re
# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8485721537:AAGLfMQWt3OgPAXYqUXrYY4hfHa-cqULwjk")
PHOTO_MAIN = "AgACAgUAAxkBAAMHaXT4R2F0IiaAH2H6ynObOdL-mOkAApcPaxs38qlXtMksj9_RyTIACAEAAwIAA3kABx4E"
PHOTO_ABOUT = "AgACAgUAAxkBAAMKaXT4WSd1aeBFtSPWb0J1zq_deQoAApgPaxs38qlXU33FMZI5hZAACAEAAwIAA3kABx4E"
RESTART_PHOTO_ID = "AgACAgUAAxkBAAMNaXT5WvV62BkYzQPpZaqlDTi12_wAApkPaxs38qlXR5GhBX0TOi8ACAEAAwIAA3kABx4E"
FORCE_SUB_PHOTO = "AgACAgUAAxkBAAMQaXT5Z3PN4RHtNX5AT7rLOQzWTfMAApoPaxs38qlXqnAhCJ2plWkACAEAAwIAA3kABx4E"
FLINK_END_STICKER_ID = "CAACAgUAAxkBAAKgQ2l14Z8ij8qxv-sYiNqrDiadXmGHAALIFQAConJ5VNORy6wuDv2rHgQ"
FLINK_START_STICKER_ID = "CAACAgUAAxkBAAKgSGl17aArFiuRvEVA8tcy9lTYgGSCAAIqIAACEWAhVn94wcoGhZpzHgQ"
HELP_PHOTO_ID = "AgACAgUAAxkBAAMTaXT5c8wqlmvRneK9eFpXSbp8f50AApsPaxs38qlXMHYCz2gbsG8ACAEAAwIAA3kABx4E"
OWNER_ID = int(os.getenv("OWNER_ID", "7355641270"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "-1003548938800"))
BD_CHANNEL_ID = int(os.getenv("BD_CHANNEL_ID", "-1002983564230"))        # Backup & Delivery channel
ANIME_CHANNEL_ID = int(os.getenv("ANIME_CHANNEL_ID", "-1002990773255"))    # Anime upload channel
MIN_UPLOAD_BUTTONS = 2
MAX_UPLOAD_BUTTONS = 4
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Galaxy_Network:Zlqz3sLOMyAaOlT5@cluster0.prfw2el.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = os.getenv("DB_NAME", "AUGUSTA_BOT")

mongo = MongoClient(MONGO_URI)
db = mongo[DB_NAME]

users_col = db["users"]
restart_col = db["restart"]
ban_col = db["banned"]
mods_col = db["moderators"]
links_col = db["links"]
batch_col = db["batches"]
settings_col = db["settings"]
fsub_col = db["fsub_channels"]
fsub_pending_col = db["fsub_pending"]
flink_col = db["flink_batches"]
fsub_requests_col = db["fsub_requests"]  # stores pending join requests

BAN_WAIT = set()
UNBAN_WAIT = set()
MOD_WAIT = set()
REVMOD_WAIT = set()
GENLINK_WAIT = set()
BATCH_WAIT = {}
LINK_WAIT = set()
ADD_FSUB_WAIT = set()
FLINK_WAIT = {}
UPLOAD_WAIT = {}

logging.basicConfig(level=logging.INFO)

# ---------- FLASK ----------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# ---------- HELPERS ----------

def is_owner(uid): return uid == OWNER_ID
def is_banned(uid): return ban_col.find_one({"_id": uid}) is not None
def is_moderator(uid): return mods_col.find_one({"_id": uid}) is not None
def has_permission(uid): return is_owner(uid) or is_moderator(uid)

def get_auto_delete_seconds():
    data = settings_col.find_one({"_id": "auto_delete"})
    return data["minutes"] * 60 if data else None

async def send_log(bot, user, action: str):
    username = (
        f"<a href='https://t.me/{user.username}'>@{user.username}</a>"
        if user.username
        else f"<b>{user.first_name}</b>"
    )

    text = (
        "<b>📌 BOT ACTIVITY LOG</b>\n\n"
        f"🤖 bot : Aᴜɢᴜsᴛᴀ\n"
        f"👤 User : {username}\n"
        f"🆔 User ID : <code>{user.id}</code>\n"
        f"⚙️ Action : <b>{action}</b>\n"
        f"🕒 Time : <code>{datetime.now().strftime('%d-%m-%Y %H:%M:%S')}</code>"
    )

    try:
        await bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=text,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True
        )
    except:
        pass

# -------HTML FORMATING --------
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "blockquote"}

def normalize_html_caption(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Empty caption")

    # very small sanity checks to avoid common Telegram HTML failures:
    # 1) block forbidden tags quickly
    forbidden = re.findall(r"</?([a-zA-Z0-9]+)", raw)
    for tag in forbidden:
        t = tag.lower()
        if t not in ALLOWED_TAGS:
            raise ValueError(f"Tag <{t}> is not allowed in Telegram HTML")

    # 2) quick mismatch check for <blockquote> (most common issue)
    if raw.count("<blockquote") != raw.count("</blockquote>"):
        raise ValueError("Unclosed <blockquote> tag")

    # 3) <a> must contain href=
    for m in re.finditer(r"<a([^>]*)>", raw, flags=re.IGNORECASE):
        attrs = m.group(1)
        if "href=" not in attrs.lower():
            raise ValueError("<a> tag must include href=")

    # 4) prevent broken entities like "& " (Telegram may reject)
    # this ensures entities are well-formed; it doesn't remove your HTML tags
    html.unescape(raw)

    return raw
    
# ---------- QUALITY (UNIFIED) ----------
# Treat 2160p and HDRip as SAME label:
HQ_LABEL = "HDRip"

def detect_quality_from_text(text: str | None) -> str | None:
    """
    Detect quality from a single text source.
    Returns one of: "360p","480p","720p","1080p","2160p/HDRip" or None
    """
    if not text:
        return None

    t = text.lower()

    # IMPORTANT: match more specific first
    # 2160p and HDRip treated as SAME
    if "2160p" in t or "hdrip" in t:
        return HQ_LABEL

    if "1080p" in t:
        return "1080p"
    if "720p" in t:
        return "720p"
    if "480p" in t:
        return "480p"
    if "360p" in t:
        return "360p"

    return None


def detect_quality_caption_or_filename(caption_text: str | None, filename_text: str | None) -> str | None:
    """
    Your required rule:
    - if caption exists -> detect ONLY from caption
    - else detect from filename
    """
    cap = (caption_text or "").strip()
    if cap:
        return detect_quality_from_text(cap)

    fname = (filename_text or "").strip()
    if fname:
        return detect_quality_from_text(fname)

    return None

async def get_msg_text_via_forward(
    context: ContextTypes.DEFAULT_TYPE,
    src_chat_id: int,
    msg_id: int
) -> tuple[str, str, bool]:
    """
    Forward to LOG_CHANNEL, read caption/text + filename (document/video),
    then delete that forwarded message.
    Returns: (caption_or_text, filename, is_captioned_photo)
    """
    fwd = await context.bot.forward_message(
        chat_id=LOG_CHANNEL_ID,
        from_chat_id=src_chat_id,
        message_id=msg_id,
        disable_notification=True
    )

    cap_or_text = ""
    if getattr(fwd, "caption", None):
        cap_or_text = fwd.caption
    elif getattr(fwd, "text", None):
        cap_or_text = fwd.text

    filename = ""
    try:
        if getattr(fwd, "document", None):
            filename = fwd.document.file_name or ""
        elif getattr(fwd, "video", None):
            filename = fwd.video.file_name or ""
        elif getattr(fwd, "audio", None):
            filename = fwd.audio.file_name or ""
    except:
        filename = ""

    # ✅ True only if the forwarded message is a photo AND has caption
    is_captioned_photo = bool(getattr(fwd, "photo", None)) and bool(getattr(fwd, "caption", None))

    try:
        await context.bot.delete_message(LOG_CHANNEL_ID, fwd.message_id)
    except:
        pass

    return cap_or_text or "", filename or "", is_captioned_photo


# ---------- UPLOAD BUTTONS ----------
def build_upload_buttons(links: dict):
    qualities = list(links.keys())

    if not (MIN_UPLOAD_BUTTONS <= len(qualities) <= MAX_UPLOAD_BUTTONS):
        return None

    rows = []

    if len(qualities) == 2:
        rows.append([
            InlineKeyboardButton(qualities[0], url=links[qualities[0]]),
            InlineKeyboardButton(qualities[1], url=links[qualities[1]])
        ])

    elif len(qualities) == 3:
        rows.append([
            InlineKeyboardButton(qualities[0], url=links[qualities[0]]),
            InlineKeyboardButton(qualities[1], url=links[qualities[1]])
        ])
        rows.append([
            InlineKeyboardButton(qualities[2], url=links[qualities[2]])
        ])

    elif len(qualities) == 4:
        rows.append([
            InlineKeyboardButton(qualities[0], url=links[qualities[0]]),
            InlineKeyboardButton(qualities[1], url=links[qualities[1]])
        ])
        rows.append([
            InlineKeyboardButton(qualities[2], url=links[qualities[2]]),
            InlineKeyboardButton(qualities[3], url=links[qualities[3]])
        ])

    return InlineKeyboardMarkup(rows)


def reset_upload_session(uid: int):
    if uid in UPLOAD_WAIT:
        del UPLOAD_WAIT[uid]

# ---------- FORCE SUB DB HELPERS ----------
def get_fsub_channels():
    # returns list of dicts: {id, name, url, mode}
    return list(fsub_col.find({}, {"_id": 0}))

def force_sub_keyboard():
    channels = get_fsub_channels()

    rows, row = [], []
    for ch in channels:
        row.append(InlineKeyboardButton(ch["name"], url=ch["url"]))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("‼️ CHECK JOIN", callback_data="check_fsub")])
    return InlineKeyboardMarkup(rows)

async def is_user_joined(bot, user_id):
    channels = get_fsub_channels()
    if not channels:
        return True

    for ch in channels:
        chat_id = ch["id"]

        # If join-request detected for this channel -> treat as OK
        doc_id = f"{user_id}:{chat_id}"
        if fsub_requests_col.find_one({"_id": doc_id}):
            continue

        # Otherwise require actual membership (for public channels or normal join)
        try:
            m = await bot.get_chat_member(chat_id, user_id)
            if m.status in ("left", "kicked"):
                return False
        except:
            return False

    return True

async def on_fsub_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    jr = update.chat_join_request
    if not jr:
        return

    user_id = jr.from_user.id
    chat_id = jr.chat.id

    # only track join requests for channels in FSUB list
    if not fsub_col.find_one({"id": chat_id}):
        return

    doc_id = f"{user_id}:{chat_id}"
    fsub_requests_col.update_one(
        {"_id": doc_id},
        {"$set": {"user_id": user_id, "chat_id": chat_id, "ts": datetime.utcnow()}},
        upsert=True
    )

# ---------- AUTO DELETE ----------

async def delete_messages(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    for mid in data["msg_ids"]:
        try:
            await context.bot.delete_message(data["chat_id"], mid)
        except:
            pass
    if data.get("alert_id"):
        try:
            await context.bot.delete_message(data["chat_id"], data["alert_id"])
        except:
            pass
# ---------- KEYBOARDS ----------
def start_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➥ 𝐀𝐁𝐎𝐔𝐓", callback_data="about"),
                InlineKeyboardButton("➥ 𝐍𝐄𝐓𝐖𝐎𝐑𝐊", url="https://t.me/Galaxy_Networkk")
            ],
            [InlineKeyboardButton("➥ 𝗖𝗟𝗢𝗦𝗘", callback_data="close_msg")]
        ]
    )

def about_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("« BACK", callback_data="back_to_start"),
                InlineKeyboardButton("➥ CLOSE", callback_data="close_msg")
            ]
        ]
    )

# ---------- FORCE SUB SETTINGS ----------
def is_force_sub_enabled():
    data = settings_col.find_one({"_id": "force_sub"})

    # default ON
    if data is None:
        settings_col.insert_one({"_id": "force_sub", "enabled": True})
        return True

    return data.get("enabled", True)

async def force_sub_message(update: Update):
    await update.message.reply_photo(
        photo=FORCE_SUB_PHOTO,
        caption=(
            f"<blockquote><b>◈ Hᴇʏ  {update.effective_user.mention_html()} ×\n"
            "›› ʏᴏᴜʀ ғɪʟᴇ ɪs ʀᴇᴀᴅʏ ‼️  ʟᴏᴏᴋs ʟɪᴋᴇ ʏᴏᴜ ʜᴀᴠᴇɴ'ᴛ sᴜʙsᴄʀɪʙᴇᴅ "
            "ᴛᴏ ᴏᴜʀ ᴄʜᴀɴɴᴇʟs ʏᴇᴛ, sᴜʙsᴄʀɪʙᴇ ɴᴏᴡ ᴛᴏ ɢᴇᴛ ʏᴏᴜʀ ғɪʟᴇs</b></blockquote>\n\n"
            "<blockquote><b>›› Pᴏᴡᴇʀᴇᴅ ʙʏ : @BotifyX_Pro</b></blockquote>"
        ),
        reply_markup=force_sub_keyboard(),
        parse_mode=constants.ParseMode.HTML
    )

# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id

    # save user first (IMPORTANT)
    users_col.update_one(
        {"_id": uid},
        {"$set": {"_id": uid}},
        upsert=True
    )

    # ban check
    if is_banned(uid):
        return

    # extract deep-link key (file or batch)
    key = context.args[0] if context.args else None

    # 🔒 FORCE SUB CHECK (SAVE REQUEST IF BLOCKED)
    if is_force_sub_enabled() and not await is_user_joined(context.bot, uid):
        if key:
            fsub_pending_col.update_one(
                {"_id": uid},
                {"$set": {"key": key}},
                upsert=True
            )
        await force_sub_message(update)
        return
    # ---------- FLINK QUALITY BATCH ----------
    if key and key.startswith("FLINK_"):
        doc = flink_col.find_one({"_id": key})
        if not doc:
            await context.bot.send_message(chat_id, "❌ Invalid or expired formatted link.")
            return
        
        sent_ids = []
        failed = 0
        MAX_FAILS = 25  # 🔐 safety limit

        # ✅ if a captioned-image start exists, send start sticker + that image first
        start_banner_mid = doc.get("start_message_id")
        start_sticker_id = doc.get("start_sticker_id") or FLINK_START_STICKER_ID
        # ✅ 1) banner first (photo+caption exactly)
        if start_banner_mid:
            try:
                banner = await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=doc["chat_id"],
                    message_id=start_banner_mid
                )
                sent_ids.append(banner.message_id)
            except:
                pass
            # ✅ 2) start sticker
            try:
                st = await context.bot.send_sticker(chat_id=chat_id, sticker=start_sticker_id)
                sent_ids.append(st.message_id)
            except:
                pass

        for mid in doc.get("message_ids", []):
            retries = 0
            while True:
                try:
                    m = await context.bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=doc["chat_id"],
                        message_id=mid
                    )
                    sent_ids.append(m.message_id)
                    failed = 0
                    break  # success, exit retry loop
                except RetryAfter as e:  
                    await asyncio.sleep(e.retry_after) # ✅ wait required time
                    retries += 1
                    if retries >= 5:  # safety: don't loop forever
                        failed += 1
                        break  # skip after too many waits   
                except Exception:
                    failed += 1
                    break  # skip to next message
                if failed >= MAX_FAILS:
                    break
                
        # ✅ send ending sticker (once, after batch)
        sticker_mid = None
        try:
            st = await context.bot.send_sticker(
                chat_id=chat_id,
                sticker=doc.get("sticker_id") or FLINK_END_STICKER_ID
            )
            sticker_mid = st.message_id
        except:
            pass

        # ✅ auto delete all delivered messages + sticker
        d = get_auto_delete_seconds()
        if d and (sent_ids or sticker_mid):
            msg_ids = sent_ids[:]
            if sticker_mid:
                msg_ids.append(sticker_mid)

            alert = await context.bot.send_message(
                chat_id,
                f"<b>⚠️ Dᴜᴇ ᴛᴏ Cᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs....</b>\n<blockquote>Yᴏᴜʀ ғɪʟᴇs ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ᴡɪᴛʜɪɴ {d // 60} Mɪɴᴜᴛᴇs. Sᴏ ᴘʟᴇᴀsᴇ\nғᴏʀᴡᴀʀᴅ ᴛʜᴇᴍ ᴛᴏ ᴀɴʏ ᴏᴛʜᴇʀ ᴘʟᴀᴄᴇ ғᴏʀ ғᴜᴛᴜʀᴇ ᴀᴠᴀɪʟᴀʙɪʟɪᴛʏ.</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )

            context.job_queue.run_once(
                delete_messages,
                d,
                data={
                    "chat_id": chat_id,
                    "msg_ids": msg_ids,
                    "alert_id": alert.message_id
                }
            )

        return
        
    # ---------- CHANNEL LINK ----------
    if key and key.startswith("LINK_"):
        data = links_col.find_one({"_id": key})
        if not data:
            return

        channel_name = data.get("channel_name", "Channel")
        chat_id_src = data["chat_id"]

        join_url = data.get("invite_link")

        # 🔁 CREATE INVITE LINK ONLY ONCE
        if not join_url:
            try:
                chat = await context.bot.get_chat(chat_id_src)

                # 🌐 PUBLIC CHANNEL
                if chat.username:
                    join_url = f"https://t.me/{chat.username}"

                # 🔒 PRIVATE CHANNEL (JOIN REQUEST)
                else:
                    invite = await context.bot.create_chat_invite_link(
                        chat_id=chat_id_src,
                        creates_join_request=True
                    )
                    join_url = invite.invite_link

                # ✅ STORE PERMANENTLY
                links_col.update_one(
                    {"_id": key},
                    {"$set": {"invite_link": join_url}}
                )

            except Exception:
                await context.bot.send_message(
                    chat_id,
                    "❌ Bot must be admin with invite permission in the channel."
                )
                return

        # 📩 SEND MESSAGE WITH BUTTON (EVERY TIME)
        sent = await context.bot.send_message(
            chat_id,
            f"➥ 𝐂𝐡𝐚𝐧𝐧𝐞𝐥 : <b>{channel_name}</b>\n"
            "<b>𝗖𝗟𝗜𝗖𝗞 𝗕𝗘𝗟𝗢𝗪 𝗧𝗢 𝗝𝗢𝗜𝗡 𝗧𝗛𝗘 𝗖𝗛𝗔𝗡𝗡𝗘𝗟</b>",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🍁 REQUEST TO JOIN 🍁", url=join_url)]]
            ),
            parse_mode=constants.ParseMode.HTML
        )

        # ⏱ AUTO DELETE BOT MESSAGE (NOT LINK)
        async def expire_message(context: ContextTypes.DEFAULT_TYPE):
            data = context.job.data
            try:
                await context.bot.delete_message(
                    data["chat_id"],
                    data["message_id"]
                )
            except:
                pass
            await context.bot.send_message(
                data["chat_id"],
                "» 𝐓𝐡𝐞 𝐈𝐧𝐯𝐢𝐭𝐞 𝐋𝐢𝐧𝐤 𝐢𝐬 𝐍𝐨𝐰 𝐄𝐱𝐩𝐢𝐫𝐞𝐝."
            )

        context.job_queue.run_once(
            expire_message,
            60,
            data={
                "chat_id": chat_id,
                "message_id": sent.message_id
            }
        )
        return

    # ---------- BATCH LINK ----------
    if key and key.startswith("BATCH_"):
        batch = batch_col.find_one({"_id": key})
        if not batch:
            await context.bot.send_message(chat_id, "❌ Invalid or expired batch link.")
            return

        sent_ids = []
        failed = 0
        MAX_FAILS = 15  # 🔐 safety limit


        for mid in range(batch["from_id"], batch["to_id"] + 1):
            try:
                m = await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=batch["chat_id"],
                    message_id=mid
                )
                sent_ids.append(m.message_id)
                failed = 0  # reset after success
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)

            except Exception:
                failed += 1
                if failed >= MAX_FAILS:
                    break

        if not sent_ids:
            await context.bot.send_message(
                chat_id,
                "<blockquote expandable>❌ No messages could be delivered.\n\n"
                "» This may be due to the bot not being "
                "an admin in the source channel or "
                "the messages being deleted.</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        d = get_auto_delete_seconds()
        if d:
            alert = await context.bot.send_message(
                chat_id,
                 f"<b>⚠️ Dᴜᴇ ᴛᴏ Cᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs....</b>\n<blockquote>Yᴏᴜʀ ғɪʟᴇs ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ᴡɪᴛʜɪɴ {d // 60} Mɪɴᴜᴛᴇs. Sᴏ ᴘʟᴇᴀsᴇ\nғᴏʀᴡᴀʀᴅ ᴛʜᴇᴍ ᴛᴏ ᴀɴʏ ᴏᴛʜᴇʀ ᴘʟᴀᴄᴇ ғᴏʀ ғᴜᴛᴜʀᴇ ᴀᴠᴀɪʟᴀʙɪʟɪᴛʏ.</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            context.job_queue.run_once(
                delete_messages,
                d,
                data={
                    "chat_id": chat_id,
                    "msg_ids": sent_ids,
                    "alert_id": alert.message_id
                }
            )
        return

    # ---------- SINGLE MESSAGE LINK ----------
    if key:
        data = links_col.find_one({"_id": key})
        if data:
            m = await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=data["chat_id"],
                message_id=data["message_id"]
            )

            d = get_auto_delete_seconds()
            if d:
                alert = await context.bot.send_message(
                    chat_id,
                    f"<b>⚠️ Dᴜᴇ ᴛᴏ Cᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs....</b>\n<blockquote>Yᴏᴜʀ ғɪʟᴇs ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ᴡɪᴛʜɪɴ {d // 60} Mɪɴᴜᴛᴇs. Sᴏ ᴘʟᴇᴀsᴇ\nғᴏʀᴡᴀʀᴅ ᴛʜᴇᴍ ᴛᴏ ᴀɴʏ ᴏᴛʜᴇʀ ᴘʟᴀᴄᴇ ғᴏʀ ғᴜᴛᴜʀᴇ ᴀᴠᴀɪʟᴀʙɪʟɪᴛʏ.</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                context.job_queue.run_once(
                    delete_messages,
                    d,
                    data={
                        "chat_id": chat_id,
                        "msg_ids": [m.message_id],
                        "alert_id": alert.message_id
                    }
                )
            return

    # ---------- NORMAL START ----------
    await update.message.reply_photo(
        photo=PHOTO_MAIN,
        caption=(
            f"<b>ʜᴇʏ {update.effective_user.mention_html()} ᴄᴜᴛɪᴇ</b> ,\n"
            "➖➖➖➖➖➖➖➖➖\n"
            "<blockquote><b>➩ ɪ ᴀᴍ ᴘʟᴇᴀsᴇᴅ ᴛᴏ ɪɴғᴏʀᴍ ʏᴏᴜ ᴛʜᴀᴛ ɪ ᴄᴀɴ ᴘʀᴏᴠɪᴅᴇ ʏᴏᴜ ᴡɪᴛʜ\n"
            "ᴀɴɪᴍᴇ ғɪʟᴇs ғʀᴏᴍ ʏᴏᴜʀ ғᴀᴠᴏʀɪᴛᴇ sᴇʀɪᴇs.</b></blockquote>\n"
            "➖➖➖➖➖➖➖➖➖\n"
            "<blockquote><b>➩ ʏᴏᴜ ᴡɪʟʟ ʜᴀᴠᴇ ᴛʜᴇ ᴏᴘᴛɪᴏɴ ᴛᴏ sᴇʟᴇᴄᴛ ᴛʜᴇ ғᴏʀᴍᴀᴛ ᴏғ ʏᴏᴜʀ \n"
            "ᴄʜᴏɪᴄᴇ, ᴡʜᴇᴛʜᴇʀ ɪᴛ ʙᴇ 480ᴘ, 720ᴘ, 1080ᴘ, ᴏʀ ᴀɴʏ ᴏᴛʜᴇʀ \n"
            "ᴘʀᴇғᴇʀᴇɴᴄᴇ ʏᴏᴜ ᴍᴀʏ ʜᴀᴠᴇ.</b></blockquote>\n"
            "➖➖➖➖➖➖➖➖➖\n"
            "<blockquote><b>➩ ᴡᴇ ᴀʀᴇ ʜᴇʀᴇ ᴛᴏ ᴄᴀᴛᴇʀ ᴛᴏ ʏᴏᴜʀ ᴀɴɪᴍᴇ ɴᴇᴇᴅs ᴡɪᴛʜ ᴛʜᴇ\n"
            "ᴜᴛᴍᴏsᴛ ᴘʀᴏғᴇssɪᴏɴᴀʟɪsᴍ ᴀɴᴅ ǫᴜᴀʟɪᴛʏ.</b></blockquote>"
            
        ),
        reply_markup=start_keyboard(),
        parse_mode=constants.ParseMode.HTML
    )

# ---------- LINK ----------
async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not has_permission(uid):
        await update.message.reply_text(
            "<blockquote>⛔ This command is restricted to Owner & Moderators.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    LINK_WAIT.add(uid)

    await send_log(
        context.bot,
        update.effective_user,
        "Used /link (channel link generator)"
    )

    await update.message.reply_text(
        "<blockquote>"
        "➕ Add me to the channel as <b>Admin</b>\n"
        "➥ Then forward a message from the channel"
        "</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- LINK CHANNEL LIST ----------
async def linkch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not has_permission(uid):
        await update.message.reply_text(
            "<blockquote>⛔ This command is restricted to Owner & Moderators.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    channels = links_col.distinct(
        "chat_id",
        {"type": "channel_link"}
    )

    if not channels:
        await update.message.reply_text(
            "<blockquote>No channel links found.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    buttons = []
    row = []

    for chat_id in channels:
        doc = links_col.find_one(
            {"chat_id": chat_id, "type": "channel_link"}
        )
        if not doc:
            continue

        channel_name = doc.get("channel_name", "Channel")

        row.append(
            InlineKeyboardButton(
                channel_name,
                callback_data=f"linkch_{chat_id}"
            )
        )

        if len(row) == 3:  # ✅ 3 per row
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    # navigation
    buttons.append([
        InlineKeyboardButton("⬅️", callback_data="linkch_prev"),
        InlineKeyboardButton("➡️", callback_data="linkch_next")
    ])

    # close
    buttons.append([
        InlineKeyboardButton("❌ CLOSE", callback_data="close_msg")
    ])

    await update.message.reply_text(
        "<b>Select a channel:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=constants.ParseMode.HTML
    )

# ---------- FLINK ----------
async def flink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # OWNER + MODS ONLY
    if not has_permission(uid):
        await update.message.reply_text(
            "<blockquote>⛔ This command is restricted to Owner & Moderators.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    if is_banned(uid):
        return

    FLINK_WAIT[uid] = {"step": "first"}

    await send_log(context.bot, update.effective_user, "Used /flink (formatted quality batch links)")

    await update.message.reply_text(
        "<blockquote><b>Forward the FIRST message from your channel (with forward tag).</b></blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- CANCEL UPLOAD SESSION ----------
async def cancelupload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # permission check (same as /setuploads)
    if not has_permission(uid):
        await update.message.reply_text(
            "<blockquote>⛔ This command is restricted to Owner & Moderators.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    if is_banned(uid):
        return

    # no active session?
    if uid not in UPLOAD_WAIT:
        await update.message.reply_text(
            "<blockquote>❌ No active upload session to cancel.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    # cancel session
    reset_upload_session(uid)

    await send_log(context.bot, update.effective_user, "Cancelled upload session (/cancelupload)")

    await update.message.reply_text(
        "<blockquote>✅ Upload session cancelled successfully.</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- GENLINK ----------
async def genlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # 🔒 PERMISSION CHECK
    if not has_permission(uid):
        await update.message.reply_text(
            "<blockquote>⛔ This command is restricted to Owner & Moderators.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    if is_banned(uid):
        return
    
    await send_log(
        context.bot,
        update.effective_user,
        "Used /genlink (message link generator)"
    )

    GENLINK_WAIT.add(uid)

    await update.message.reply_text(
        "<blockquote><b>Send A Message For To Get Your Shareable Link.</b></blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- BATCH ----------
async def batch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # 🔒 PERMISSION CHECK
    if not has_permission(uid):
        await update.message.reply_text(
            "<blockquote>⛔ This command is restricted to Owner & Moderators.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    if is_banned(uid):
        return
    
    await send_log(
        context.bot,
        update.effective_user,
        "Used /batch (batch link generator)"
    )

    BATCH_WAIT[uid] = {"step": "first"}

    await update.message.reply_text(
        "<blockquote><b>Forward The Batch First Message From your Batch Channel (With Forward Tag)..</b></blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- SET UPLOADS ----------
async def setuploads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not has_permission(uid):
        await update.message.reply_text(
            "<blockquote>⛔ This command is restricted to Owner & Moderators.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    if is_banned(uid):
        return

    UPLOAD_WAIT[uid] = {"step": "photo", "photo": None, "caption": None, "files": []}

    await send_log(context.bot, update.effective_user, "Started /setuploads session")

    await update.message.reply_text(
        "<blockquote>🖼 Send the POST IMAGE</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- UPLOADS ----------
async def upload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not has_permission(uid):
        await update.message.reply_text(
            "<blockquote>⛔ This command is restricted to Owner & Moderators.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    if is_banned(uid):
        return

    data = UPLOAD_WAIT.get(uid)
    if not data:
        await update.message.reply_text("❌ No active upload session.")
        return

    count = len(data["files"])
    if not (MIN_UPLOAD_BUTTONS <= count <= MAX_UPLOAD_BUTTONS):
        await update.message.reply_text("❌ File count must be between 2 and 4.")
        return

    if not data.get("photo") or not data.get("caption"):
        await update.message.reply_text("❌ Upload session incomplete (photo/caption missing).")
        return

    bot_username = "AG_Augusta_bot"
    links = {}

    # Create start links that deliver BD_CHANNEL forwarded messages
    for item in data["files"]:
        key = uuid.uuid4().hex[:12]
        links_col.insert_one({
            "_id": key,
            "chat_id": BD_CHANNEL_ID,
            "message_id": item["msg_id"]
        })
        links[item["quality"]] = f"https://t.me/{bot_username}?start={key}"

    markup = build_upload_buttons(links)
    if not markup:
        await update.message.reply_text("❌ Button layout error.")
        return

    # Post to ANIME CHANNEL
    try:
        await context.bot.send_photo(
            chat_id=ANIME_CHANNEL_ID,
            photo=data["photo"],
            caption=data["caption"],
            reply_markup=markup,
            parse_mode=constants.ParseMode.HTML
        )
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
        await context.bot.send_photo(
            chat_id=ANIME_CHANNEL_ID,
            photo=data["photo"],
            caption=data["caption"],
            reply_markup=markup,
            parse_mode=constants.ParseMode.HTML
        )

    await send_log(context.bot, update.effective_user, f"Uploaded anime post (files={count})")

    reset_upload_session(uid)

    await update.message.reply_text("✅ Anime post uploaded successfully!")
    
# ---------- BAN ----------
async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id):
        return
    BAN_WAIT.add(update.effective_user.id)
    await update.message.reply_text(
        "<blockquote>send the user id</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- UNBAN ----------
async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id):
        return
    UNBAN_WAIT.add(update.effective_user.id)
    await update.message.reply_text(
        "<blockquote>send the user id</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- MODERATOR ----------
async def moderator_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    MOD_WAIT.add(update.effective_user.id)
    await update.message.reply_text(
        "<blockquote>send the user id</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- REV MODERATOR ----------
async def revmoderator_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    REVMOD_WAIT.add(update.effective_user.id)
    await update.message.reply_text(
        "<blockquote>send the user id</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- AUTO APPROVAL WITH FORCE-SUB ----------
async def auto_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join = update.chat_join_request
    user = join.from_user
    chat = join.chat

    # ✅ OFF auto-approval for FSUB channels (do nothing for FSUB join requests)
    if fsub_col.find_one({"id": chat.id}):
        return

    if is_force_sub_enabled() and not await is_user_joined(context.bot, user.id):
        try:
            await context.bot.send_photo(
                chat_id=user.id,
                photo=FORCE_SUB_PHOTO,
                caption=(
                    f"<blockquote><b>◈ Hᴇʏ  {user.mention_html()} ×\n"
                    "›› ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟs "
                    "ʙᴇғᴏʀᴇ ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ɪs ᴀᴘᴘʀᴏᴠᴇᴅ.</b></blockquote>\n\n"
                    "<blockquote><b>›› Pᴏᴡᴇʀᴇᴅ ʙʏ : @BotifyX_Pro</b></blockquote>"
                ),
                reply_markup=force_sub_keyboard(),
                parse_mode=constants.ParseMode.HTML
            )
        except:
            pass
        return

    await context.bot.approve_chat_join_request(chat.id, user.id)

    # ✅ delete request record PER CHANNEL (since we store per channel now)
    try:
        fsub_requests_col.delete_one({"_id": f"{user.id}:{chat.id}"})
    except:
        pass

    approval_caption = (
        f"<blockquote>◈ Hᴇʏ {user.mention_html()} ×\n\n"
        f"›› ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ ᴛᴏ ᴊᴏɪɴ {chat.title} "
        "ʜᴀs ʙᴇᴇɴ ᴀᴘᴘʀᴏᴠᴇᴅ.</blockquote>\n\n"
        "<blockquote>›› Pᴏᴡᴇʀᴇᴅ ʙʏ : "
        "<a href='https://t.me/Prince_Vegeta_36'>Chrollo Lucifel</a></blockquote>"
    )

    buttons = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("➥ Support", url="https://t.me/BotifyX_support"),
            InlineKeyboardButton("➥ Developer", url="https://t.me/Prince_Vegeta_36")
        ]]
    )

    try:
        await context.bot.send_photo(
            chat_id=user.id,
            photo="AgACAgUAAxkBAAMWaXT5f9ZbZv7IWX0Muq9jadqCV7gAApwPaxs38qlXhr_riUPhAhoACAEAAwIAA3kABx4E",
            caption=approval_caption,
            reply_markup=buttons,
            parse_mode=constants.ParseMode.HTML
        )
    except:
        pass

# ---------- SET AUTO DELETE ----------
async def setdel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "<blockquote>Usage: /setdel &lt;minutes&gt;</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    minutes = int(context.args[0])
    settings_col.update_one(
        {"_id": "auto_delete"},
        {"$set": {"minutes": minutes}},
        upsert=True
    )

    await send_log( 
        context.bot,
        update.effective_user,
        f"Set auto delete time to {minutes} minute(s)"
    )

    await update.message.reply_text(
        f"<blockquote>Auto delete time set to {minutes} minute(s)</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- ADD FORCE SUB CHANNEL ----------
async def addfsub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        return

    ADD_FSUB_WAIT.add(uid)

    await update.message.reply_text(
        "<blockquote>"
        "➕ <b>Add Force-Sub Channel</b>\n\n"
        "1) Make me <b>Admin</b> in your channel\n"
        "2) Then <b>forward a message</b> from that channel here"
        "</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- DELETE FORCE SUB CHANNEL ----------
async def delfsub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_owner(uid):
        return

    channels = get_fsub_channels()
    if not channels:
        await update.message.reply_text(
            "<blockquote>❌ No Force-Sub channels found.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    buttons, row = [], []
    for ch in channels:
        row.append(InlineKeyboardButton(ch["name"], callback_data=f"fsub_pick_{ch['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("❌ Close", callback_data="close_msg")])

    await update.message.reply_text(
        "<blockquote><b>Select a channel to remove:</b></blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=constants.ParseMode.HTML
    )

# ---------- FORCE SUB TOGGLE ----------
async def fsub_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # 🔒 OWNER ONLY
    if not is_owner(uid):
        return

    if not context.args or context.args[0].lower() not in ("on", "off", "status"):
        await update.message.reply_text(
            "<blockquote>Usage:\n"
            "/fsub on — Enable force-sub\n"
            "/fsub off — Disable force-sub\n"
            "/fsub status — Show current status</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    arg = context.args[0].lower()

    # ----- STATUS -----
    if arg == "status":
        status = "✅ ENABLED" if is_force_sub_enabled() else "❌ DISABLED"
        await update.message.reply_text(
            f"<blockquote>Force Subscription is currently {status}</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    # ----- ON / OFF -----
    enabled = arg == "on"

    settings_col.update_one(
        {"_id": "force_sub"},
        {"$set": {"enabled": enabled}},
        upsert=True
    )

    await send_log(
        context.bot,
        update.effective_user,
        f"Changed force-sub setting → {arg.upper()}"
    )

    msg = "✅ Force Subscription ENABLED" if enabled else "❌ Force Subscription DISABLED"

    await update.message.reply_text(
        f"<blockquote>{msg}</blockquote>",
        parse_mode=constants.ParseMode.HTML
    )

# ---------- HELP ----------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_banned(update.effective_user.id):
        return

    help_text = (
        "<code>🤖 BOT COMMANDS GUIDE</code>\n\n"
        "<blockquote expandable>"
        "➥ <b>/start</b> — Start the bot / open main panel\n"
        "➥ <b>/help</b> — Show this help menu\n"
        "➥ <b>/genlink</b> — Generate shareable link for a file/message\n"
        "➥ <b>/batch</b> — Generate a single link for multiple messages\n"
        "➥ <b>/flink</b> — Create formatted quality-wise batch links (480p/720p/1080p)\n"
        "➥ <b>/link</b> — Create channel join/request link (Owner/Mods)\n"
        "➥ <b>/linkch</b> — List saved channel links (Owner/Mods)\n"
        "➥ <b>/setuploads</b> — Start anime post upload session (Owner/Mods)\n"
        "➥ <b>/upload</b> — Post anime to channel after setup (Owner/Mods)\n"
        "➥ <b>/cancelupload</b> — Cancel active upload session (Owner/Mods)\n"
        "➥ <b>/broadcast</b> — Broadcast a message to all users (Owner only)\n"
        "➥ <b>/check_db</b> — Show MongoDB usage/status (Owner only)\n"
        "➥ <b>/setdel</b> — Set auto delete timer in minutes (Owner only)\n"
        "➥ <b>/ban</b> — Ban a user (Owner/Mods)\n"
        "➥ <b>/unban</b> — Unban a user (Owner/Mods)\n"
        "➥ <b>/moderator</b> — Add moderator (Owner only)\n"
        "➥ <b>/revmoderator</b> — Remove moderator (Owner only)\n"
        "➥ <b>/fsub on</b> — Enable Force-Sub (Owner only)\n"
        "➥ <b>/fsub off</b> — Disable Force-Sub (Owner only)\n"
        "➥ <b>/fsub status</b> — Check Force-Sub status (Owner only)\n"
        "➥ <b>/addfsub</b> — Add Force-Sub channel (Owner only)\n"
        "➥ <b>/delfsub</b> — Remove Force-Sub channel (Owner only)\n"
        "<b>✅ Features:</b>\n"
        "• Auto-Approval for join requests\n"
        "• Force-Sub protection\n"
        "• Share links / batch links / formatted links\n"
        "• Auto-delete delivered files\n"
        "• Anime post uploader system\n"
        "</blockquote>\n"
        "<blockquote expandable><b>👑 Credits</b>\n"
        "Maintained by <b>@Prince_Vegeta_36</b>\n\n"
        "<b>⚙️ Powered by</b>\n"
        "• Python\n"
        "• python-telegram-bot\n"
        "• MongoDB\n"
        "• Render Hosting"
        "</blockquote>"
    )

    buttons = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("➥ Support", url="https://t.me/BotifyX_support"),
            InlineKeyboardButton("➥ Update Channel", url="https://t.me/BotifyX_Pro")
        ],
        [
            InlineKeyboardButton("➥ Developer", url="https://t.me/Prince_Vegeta_36"),
            InlineKeyboardButton("➥ CLOSE", callback_data="close_msg")
        ]]
    )

    # ✅ send help with photo (fallback to text if photo fails)
    try:
        await update.message.reply_photo(
            photo=HELP_PHOTO_ID,
            caption=help_text,
            reply_markup=buttons,
            parse_mode=constants.ParseMode.HTML
        )
    except Exception:
        await update.message.reply_text(
            help_text,
            reply_markup=buttons,
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True
        )

# ---------- BROADCAST (REPLY MODE) ----------
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "<blockquote>Reply to a message to broadcast it</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    await send_log(
        context.bot,
        update.effective_user,
        "Broadcasted a message to all users"
    )

    msg = update.message.reply_to_message

    total = users_col.count_documents({})
    success = 0
    blocked = 0
    deleted = 0
    failed = 0

    for user in users_col.find({}):
        try:
            await context.bot.copy_message(
                chat_id=user["_id"],
                from_chat_id=msg.chat.id,
                message_id=msg.message_id
            )
            success += 1

        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            failed += 1

        except Exception as e:
            err = str(e).lower()
            if "blocked" in err:
                blocked += 1
            elif "deleted" in err:
                deleted += 1
            else:
                failed += 1

    report = (
        "<b>Broadcast completed</b>\n\n"
        f"◇ Total Users: {total}\n"
        f"◇ Successful: {success}\n"
        f"◇ Blocked Users: {blocked}\n"
        f"◇ Deleted Accounts: {deleted}\n"
        f"◇ Unsuccessful: {failed}"
    )

    await update.message.reply_text(
        report,
        parse_mode=constants.ParseMode.HTML
    )

# ---------- CHECK DB ----------
async def check_db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    try:
        stats = db.command("dbstats")

        db_name = db.name
        data_size = stats.get("dataSize", 0) / (1024 * 1024)
        index_size = stats.get("indexSize", 0) / (1024 * 1024)
        storage_size = stats.get("storageSize", 0) / (1024 * 1024)
        collections = stats.get("collections", 0)

        total_docs = 0
        for col_name in db.list_collection_names():
            total_docs += db[col_name].count_documents({})

        text = (
            "<b>📊 MongoDB Status</b>\n\n"
            f"🗄 <b>Database</b> : <code>{db_name}</code>\n"
            f"📦 <b>Data Size</b> : <code>{data_size:.2f} MB</code>\n"
            f"🧾 <b>Index Size</b> : <code>{index_size:.2f} MB</code>\n"
            f"💾 <b>Storage Used</b> : <code>{storage_size:.2f} MB</code>\n"
            f"📁 <b>Collections</b> : <code>{collections}</code>\n"
            f"📄 <b>Total Documents</b> : <code>{total_docs}</code>\n"
            f"🕒 <b>Checked At</b> : <code>{datetime.now().strftime('%d-%m-%Y %H:%M:%S')}</code>"
        )

        await send_log(
            context.bot,
            update.effective_user,
            "Checked MongoDB status"
        )

        await update.message.reply_text(
            text,
            parse_mode=constants.ParseMode.HTML
        )

    except Exception as e:
        await update.message.reply_text(
            f"<blockquote>❌ Failed to fetch DB stats\n\n<code>{e}</code></blockquote>",
            parse_mode=constants.ParseMode.HTML
        )

# ---------- PRIVATE HANDLER ----------
async def private_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # BAN CHECK
    if is_banned(update.effective_user.id):
        return

    if not update.message:
        return

    uid = update.effective_user.id
    msg = update.message
    text = msg.text.strip() if msg.text else None

    # Ignore commands
    if text and text.startswith("/"):
        return
    
    # ---------- UPLOAD: PHOTO ----------
    if uid in UPLOAD_WAIT and UPLOAD_WAIT[uid]["step"] == "photo":
        if msg.photo:
            UPLOAD_WAIT[uid]["photo"] = msg.photo[-1].file_id
            UPLOAD_WAIT[uid]["step"] = "caption"

            await msg.reply_text(
                "<blockquote>📝 Now send the POST CAPTION (HTML supported)</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
        else:
            await msg.reply_text("❌ Please send a photo.")
        return
    # ---------- UPLOAD: CAPTION ----------
    if uid in UPLOAD_WAIT and UPLOAD_WAIT[uid]["step"] == "caption":
        if msg.text:
            try:
                UPLOAD_WAIT[uid]["caption"] = normalize_html_caption(msg.text)
            except Exception as e:
                await msg.reply_text(
                    f"<blockquote>❌ Invalid HTML in caption.\nFix your tags and send again.\n\n<code>{e}</code></blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                return

            UPLOAD_WAIT[uid]["step"] = "files"
            await msg.reply_text(
                "<blockquote>📂 Forward 2–4 FILES from BD CHANNEL</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
        else:
            await msg.reply_text("❌ Please send caption text.")
        return

    # ---------- UPLOAD: FILES ----------
    if uid in UPLOAD_WAIT and UPLOAD_WAIT[uid]["step"] == "files":
        if not (msg.document or msg.video):
            await msg.reply_text("❌ Send a file (document/video).")
            return

        # Must be forwarded from BD channel
        fwd_chat_id = None
        fwd_mid = None

        if msg.forward_origin and msg.forward_origin.chat:
            fwd_chat_id = msg.forward_origin.chat.id
            fwd_mid = msg.forward_origin.message_id
        elif msg.forward_from_chat:
            fwd_chat_id = msg.forward_from_chat.id
            fwd_mid = msg.forward_from_message_id

        if fwd_chat_id != BD_CHANNEL_ID or not fwd_mid:
            await msg.reply_text("❌ Files must be forwarded from BD CHANNEL only.")
            return

        # Detect quality from caption or filename
        cap = msg.caption or ""
        fname = ""
        if msg.document:
            fname = msg.document.file_name or ""
        elif msg.video:
            fname = msg.video.file_name or ""

        quality = detect_quality_caption_or_filename(cap, fname)

        if not quality:
            await msg.reply_text("❌ Quality not detected (360p / 480p / 720p / 1080p / 2160p or HDRip). Add it in caption or filename.")
            return

        # No duplicates
        existing = [f["quality"] for f in UPLOAD_WAIT[uid]["files"]]
        if quality in existing:
            await msg.reply_text(f"❌ {quality} already added.")
            return

        if len(UPLOAD_WAIT[uid]["files"]) >= MAX_UPLOAD_BUTTONS:
            await msg.reply_text("❌ Maximum 4 files allowed.")
            return

        UPLOAD_WAIT[uid]["files"].append({"msg_id": fwd_mid, "quality": quality})

        await msg.reply_text(f"✅ Added {quality}\n📦 Total files: {len(UPLOAD_WAIT[uid]['files'])}")
        return

    
    # ---------- FLINK PROCESS ----------
    if uid in FLINK_WAIT:
        data = FLINK_WAIT[uid]

        # ----- FIRST MESSAGE -----
        if data["step"] == "first":
            # must be forwarded from channel
            if msg.forward_origin and msg.forward_origin.chat:
                ch = msg.forward_origin.chat
                from_id = msg.forward_origin.message_id
            elif msg.forward_from_chat:
                ch = msg.forward_from_chat
                from_id = msg.forward_from_message_id
            else:
                await msg.reply_text(
                    "<blockquote>❌ Please forward a message from a channel.</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                return

            if ch.type != "channel":
                await msg.reply_text(
                    "<blockquote>❌ Please forward from a <b>CHANNEL</b> (not a group).</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                return

            FLINK_WAIT[uid] = {"step": "last", "chat_id": ch.id, "from_id": from_id}

            await msg.reply_text(
                "<blockquote><b>Now forward the LAST message from the same channel.</b></blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        # ----- LAST MESSAGE -----
        if data["step"] == "last":
            # get last id
            if msg.forward_origin and msg.forward_origin.chat:
                to_id = msg.forward_origin.message_id
            elif msg.forward_from_chat:
                to_id = msg.forward_from_message_id
            else:
                await msg.reply_text(
                    "<blockquote>❌ Please forward the last message from the channel.</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                return

            if to_id < data["from_id"]:
                await msg.reply_text(
                    "<blockquote>❌ Last message ID must be greater than first message ID.</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                return

            src_chat_id = data["chat_id"]
            from_id = data["from_id"]

            # scan range & bucket by quality
            qualities_order = ["360p", "480p", "720p", "1080p", HQ_LABEL]
            quality_map = {q: {"mids": [], "start_message_id": None} for q in qualities_order}

            # safety limit (optional)
            if (to_id - from_id) > 500:
                await msg.reply_text(
                    "<blockquote>❌ Too many messages in range (max 500). Split into smaller parts.</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                del FLINK_WAIT[uid]
                return

            for mid in range(from_id, to_id + 1):
                try:
                    cap_or_text, filename, is_captioned_photo = await get_msg_text_via_forward(context, src_chat_id, mid)
                    # ✅ caption priority, else filename
                    q = detect_quality_caption_or_filename(cap_or_text, filename)
                    if not q or q not in quality_map:
                        continue
                    quality_map[q]["mids"].append(mid)
                    # ✅ banner rule:
                    # only if THIS message is a captioned PHOTO
                    # and the caption itself contains the quality tag
                    if quality_map[q]["start_message_id"] is None and is_captioned_photo: 
                            q_from_caption = detect_quality_caption_or_filename(cap_or_text, None)
                            if q_from_caption == q:
                                quality_map[q]["start_message_id"] = mid

                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                except:
                    continue

            # create 1 link per quality (batch delivery)
            bot_username = "AG_Augusta_bot" # replace with your bot username
            inline_parts = []

            created_any = False

            for q in qualities_order:
                mids = quality_map[q]["mids"]
                if not mids:
                    continue

                created_any = True
                key = f"FLINK_{q}_{uuid.uuid4().hex[:12]}"

                flink_col.insert_one({
                    "_id": key,
                    "chat_id": src_chat_id,
                    "quality": q,
                    "message_ids": mids,
                    "sticker_id": FLINK_END_STICKER_ID,
                    "start_message_id": quality_map[q]["start_message_id"],
                    "start_sticker_id": FLINK_START_STICKER_ID
                })

                link = f"https://t.me/{bot_username}?start={key}"
                inline_parts.append(f"{q} - {link}")

            del FLINK_WAIT[uid]

            if not created_any:
                await msg.reply_text(
                    "<blockquote>❌ No quality tags found in that range.\n\n"
                    "Tip: make sure captions/text contain 480p / 720p / 1080p.</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                return

            # format exactly like you want (2 in first line if possible)
            out = ""
            if len(inline_parts) >= 2:
                out += f"{inline_parts[0]} | {inline_parts[1]}\n"
                for part in inline_parts[2:]:
                    out += f"{part}\n"
            else:
                out = "\n".join(inline_parts) + "\n"

            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔗 SHARE", url=f"https://t.me/share/url?url={out.strip()}")]]
            )

            await msg.reply_text(
                f"<blockquote><b>✅ Formatted Links Created:</b></blockquote>\n\n{out}",
                reply_markup=keyboard,
                disable_web_page_preview=True,
                parse_mode=constants.ParseMode.HTML
            )
            return

    # ---------- ADD FSUB PROCESS ----------
    if uid in ADD_FSUB_WAIT:
        # must be forwarded from channel
        ch = None
        if msg.forward_origin and msg.forward_origin.chat:
            ch = msg.forward_origin.chat
        elif msg.forward_from_chat:
            ch = msg.forward_from_chat

        if not ch:
            await msg.reply_text(
                "<blockquote>❌ Please forward a message from a channel.</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        # must be a channel
        if ch.type != "channel":
            await msg.reply_text(
                "<blockquote>❌ Please forward from a <b>CHANNEL</b> (not a group).</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        # try to check bot permissions quickly (optional but helpful)
        try:
            me = await context.bot.get_me()
            member = await context.bot.get_chat_member(ch.id, me.id)
            # status could be administrator or member; admin recommended
        except Exception:
            await msg.reply_text(
                "<blockquote>❌ I can't access that channel.\n\n"
                "Make sure I am added as <b>Admin</b> in the channel, then try again.</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        channel_id = ch.id
        channel_name = ch.title or "Channel"

        # already exists?
        if fsub_col.find_one({"id": channel_id}):
            ADD_FSUB_WAIT.discard(uid)
            await msg.reply_text(
                f"<blockquote>✅ <b>{channel_name}</b> is already in Force-Sub list.</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        # PUBLIC CHANNEL
        if getattr(ch, "username", None):
            url = f"https://t.me/{ch.username}"

            fsub_col.insert_one({
                "id": channel_id,
                "name": channel_name,
                "url": url,
                "mode": "public"
            })

            ADD_FSUB_WAIT.discard(uid)

            await send_log(context.bot, update.effective_user, f"Added FSUB channel: {channel_name} ({channel_id})")

            await msg.reply_text(
                f"<blockquote>✅ Added <b>{channel_name}</b> to Force-Sub.</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        # PRIVATE CHANNEL -> ask mode
        # store pending in user_data
        context.user_data["pending_fsub"] = {
            "id": channel_id,
            "name": channel_name
        }

        ADD_FSUB_WAIT.discard(uid)

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Normal", callback_data="fsub_mode_normal"),
            InlineKeyboardButton("Join Request Mode", callback_data="fsub_mode_jr")
        ], [
            InlineKeyboardButton("❌ Cancel", callback_data="fsub_mode_cancel")
        ]])

        await msg.reply_text(
            f"<blockquote>🔒 <b>{channel_name}</b> looks like a private channel.\n\n"
            "Choose how users should join:</blockquote>",
            reply_markup=kb,
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    # ---------- LINK PROCESS ----------
    if uid in LINK_WAIT:
        LINK_WAIT.remove(uid)

        # must be forwarded from channel
        if msg.forward_origin and msg.forward_origin.chat:
            chat = msg.forward_origin.chat
            chat_id = chat.id
            message_id = msg.forward_origin.message_id
            channel_name = chat.title or "Channel"

        elif msg.forward_from_chat:
            chat = msg.forward_from_chat
            chat_id = chat.id
            message_id = msg.forward_from_message_id
            channel_name = chat.title or "Channel"

        else:
            await msg.reply_text(
                "<blockquote>❌ Please forward a message from a channel.</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        key = f"LINK_{uuid.uuid4().hex[:12]}"

        links_col.insert_one({
            "_id": key,
            "chat_id": chat_id,
            "message_id": message_id,
            "channel_name": channel_name,
            "type": "channel_link"
        })

        link = f"https://t.me/AG_Augusta_bot?start={key}"

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 SHARE LINK", url=f"https://t.me/share/url?url={link}")]]
        )

        await msg.reply_text(
            f"Here is your <b>{channel_name}</b> shareable link:\n\n{link}",
            reply_markup=keyboard,
            disable_web_page_preview=True,
            parse_mode=constants.ParseMode.HTML
        )
        return

    # ---------- GENLINK PROCESS ----------
    if uid in GENLINK_WAIT:
        GENLINK_WAIT.remove(uid)

        key = uuid.uuid4().hex[:12]

        links_col.insert_one({
            "_id": key,
            "chat_id": msg.chat.id,
            "message_id": msg.message_id
        })

        link = f"https://t.me/AG_Augusta_bot?start={key}"

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔗 Share", url=f"https://t.me/share/url?url={link}")]]
        )

        await msg.reply_text(
            f"Here is your link:\n\n{link}",
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
        return

    # ---------- BATCH PROCESS ----------
    if uid in BATCH_WAIT:
        data = BATCH_WAIT[uid]

        # ----- FIRST MESSAGE -----
        if data["step"] == "first":

            # ✅ NEW Telegram forward system (PTB v20+)
            if msg.forward_origin and msg.forward_origin.chat:
                data["chat_id"] = msg.forward_origin.chat.id
                data["from_id"] = msg.forward_origin.message_id

            # ✅ Old-style forward support
            elif msg.forward_from_chat:
                data["chat_id"] = msg.forward_from_chat.id
                data["from_id"] = msg.forward_from_message_id

            # ✅ Message link support
            elif text and "t.me/c/" in text:
                try:
                    parts = text.split("/")
                    data["chat_id"] = int("-100" + parts[-2])
                    data["from_id"] = int(parts[-1])
                except:
                    await msg.reply_text(
                        "<blockquote>❌ Invalid link. Please send a valid message link.</blockquote>",
                        parse_mode=constants.ParseMode.HTML
                    )
                    return
            else:
                await msg.reply_text(
                    "<blockquote>❌ Please forward a message from a channel.</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                return

            data["step"] = "last"

            await msg.reply_text(
                "<blockquote>Forward The Batch Last Message From Your Batch Channel (With Forward Tag)..</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        # ----- LAST MESSAGE -----
        if data["step"] == "last":

            # ✅ NEW Telegram forward system
            if msg.forward_origin and msg.forward_origin.chat:
                to_id = msg.forward_origin.message_id

            # ✅ Old-style forward
            elif msg.forward_from_chat:
                to_id = msg.forward_from_message_id

            # ✅ Message link
            elif text and "t.me/c/" in text:
                try:
                    to_id = int(text.split("/")[-1])
                except:
                    await msg.reply_text(
                        "<blockquote>❌ Invalid link. Please send a valid message link.</blockquote>",
                        parse_mode=constants.ParseMode.HTML
                    )
                    return
            else:
                await msg.reply_text(
                    "<blockquote>❌ Please forward the last message from the channel.</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                return

            # ✅ Safety check
            if to_id < data["from_id"]:
                await msg.reply_text(
                    "<blockquote>❌ Last message ID must be greater than first message ID.</blockquote>",
                    parse_mode=constants.ParseMode.HTML
                )
                return

            batch_key = f"BATCH_{uuid.uuid4().hex[:12]}"

            batch_col.insert_one({
                "_id": batch_key,
                "chat_id": data["chat_id"],
                "from_id": data["from_id"],
                "to_id": to_id
            })

            del BATCH_WAIT[uid]

            link = f"https://t.me/AG_Augusta_bot?start={batch_key}"

            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔗 Share", url=f"https://t.me/share/url?url={link}")]]
            )

            await msg.reply_text(
                f"Here is your link:\n\n{link}",
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
            return

    # ---------- BAN ----------
    if uid in BAN_WAIT:
        BAN_WAIT.remove(uid)

        if not text.isdigit():
            return

        ban_col.update_one(
            {"_id": int(text)},
            {"$set": {"_id": int(text)}},
            upsert=True
        )

        await send_log(
            context.bot,
            update.effective_user,
            f"Banned user ID {text}"
        )

        await update.message.reply_text(
            "<blockquote>✨ Successfully Banned the user</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    # ---------- UNBAN ----------
    if uid in UNBAN_WAIT:
        UNBAN_WAIT.remove(uid)

        if not text.isdigit():
            return

        ban_col.delete_one({"_id": int(text)})

        await send_log(
            context.bot,
            update.effective_user,
            f"Unbanned user ID {text}"
        )

        await update.message.reply_text(
            "<blockquote>✨ Successfully Unbanned the user</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    # ---------- ADD MODERATOR ----------
    if uid in MOD_WAIT:
        MOD_WAIT.remove(uid)

        if not text.isdigit():
            return

        mods_col.update_one(
            {"_id": int(text)},
            {"$set": {"_id": int(text)}},
            upsert=True
        )

        await send_log(
            context.bot,
            update.effective_user,
            f"Added moderator ID {text}"
        )

        await update.message.reply_text(
            "<blockquote>✨ Successfully Added Moderator</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    # ---------- REMOVE MODERATOR ----------
    if uid in REVMOD_WAIT:
        REVMOD_WAIT.remove(uid)

        if not text.isdigit():
            return

        mods_col.delete_one({"_id": int(text)})

        await send_log(
            context.bot,
            update.effective_user,
            f"Removed moderator ID {text}"
        )

        await update.message.reply_text(
            "<blockquote>✨ Successfully Removed Moderator</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return
        
# ---------- CALLBACK HANDLER ----------
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id

    # BAN CHECK
    if is_banned(uid):
        await query.answer("You are banned from using this bot.", show_alert=True)
        return

    await query.answer()

    # ----------- LINK CHANNEL SELECT ----------
    if query.data.startswith("linkch_"):
        chat_id = int(query.data.split("_", 1)[1])

        await send_log(
            context.bot,
            update.effective_user,
            f"Accessed channel link!"
        )

        links = links_col.find(
            {"chat_id": chat_id, "type": "channel_link"}
        )

        buttons = []
        row = []
        text = "<b>Shareable links:</b>\n\n"

        for i, doc in enumerate(links, start=1):
            key = doc["_id"]
            link = f"https://t.me/AG_Augusta_bot?start={key}"
            text += f"{i}. {link}\n"

            row.append(
                InlineKeyboardButton(
                    f"🔗 {i}",
                    url=f"https://t.me/share/url?url={link}"
                )
            )

            if len(row) == 3:  # ✅ 3 per row
                buttons.append(row)
                row = []

        if row:
            buttons.append(row)

            buttons.append([
                InlineKeyboardButton("⬅️", callback_data="linkch_prev"),
                InlineKeyboardButton("➡️", callback_data="linkch_next")
            ])

            buttons.append([
                InlineKeyboardButton("« BACK", callback_data="linkch_back"),
                InlineKeyboardButton("❌ CLOSE", callback_data="close_msg")
            ])

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=constants.ParseMode.HTML,
            disable_web_page_preview=True
        )
        return
    # ---------- LINKCH NAVIGATION ----------
    if query.data == "linkch_back":
        await linkch_cmd(update, context)
        return
    if query.data == "linkch_prev":
        await query.answer("⬅️ Previous page coming soon")
        return
    if query.data == "linkch_next":
        await query.answer("➡️ Next page coming soon")
        return
     # =================================================
         
    # ---------- FORCE SUB CHECK ----------
    if query.data == "check_fsub":

        if is_force_sub_enabled() and not await is_user_joined(context.bot, uid):
            await query.answer(
                "Join all channels first!",
                show_alert=True
            )
            return

        await query.answer("✅ Verified! Access granted.", show_alert=True)

        try:
            await query.message.delete()
        except:
            pass

        # 🔁 RESUME PENDING FILE / BATCH
        pending = fsub_pending_col.find_one({"_id": uid})

        if pending:
            key = pending["key"]
            fsub_pending_col.delete_one({"_id": uid})

            # 🔥 MANUAL RESUME (SAFE)
            fake_update = Update(
                update.update_id,
                message=query.message
            )

            context.args = [key]
            await start(fake_update, context)
            return
            
        # fallback → normal start
        fake_update = Update(
            update.update_id,
            message=query.message
        )

        # fallback → normal start
        context.args = []
        await start(fake_update, context)
        return
    
    # ---------- FSUB MODE SELECT (PRIVATE CHANNEL) ----------
    if query.data in ("fsub_mode_normal", "fsub_mode_jr", "fsub_mode_cancel"):
        pending = context.user_data.get("pending_fsub")
        if not pending:
            await query.answer("No pending channel found.", show_alert=True)
            return

        if query.data == "fsub_mode_cancel":
            context.user_data.pop("pending_fsub", None)
            await query.edit_message_text("❌ Cancelled.")
            return

        channel_id = pending["id"]
        channel_name = pending["name"]

        try:
            chat = await context.bot.get_chat(channel_id)

            if query.data == "fsub_mode_normal":
                # normal private invite
                invite = await context.bot.create_chat_invite_link(chat_id=channel_id)
                url = invite.invite_link
                mode = "private_normal"

            else:
                # join request mode
                invite = await context.bot.create_chat_invite_link(
                    chat_id=channel_id,
                    creates_join_request=True
                )
                url = invite.invite_link
                mode = "private_join_request"

        except Exception:
            context.user_data.pop("pending_fsub", None)
            await query.edit_message_text(
                "❌ Failed to create invite link.\n\n"
                "Make sure the bot is <b>Admin</b> with invite permissions.",
                parse_mode=constants.ParseMode.HTML
            )
            return

        # save
        fsub_col.insert_one({
            "id": channel_id,
            "name": channel_name,
            "url": url,
            "mode": mode
        })

        context.user_data.pop("pending_fsub", None)

        await send_log(context.bot, update.effective_user, f"Added FSUB private channel: {channel_name} ({channel_id}) mode={mode}")

        await query.edit_message_text(
            f"<blockquote>✅ Added <b>{channel_name}</b> to Force-Sub.</blockquote>",
            parse_mode=constants.ParseMode.HTML
        )
        return
    
    # ---------- FSUB PICK ----------
    if query.data.startswith("fsub_pick_"):
        ch_id = int(query.data.split("_", 2)[2])
        ch = fsub_col.find_one({"id": ch_id})
        if not ch:
            await query.answer("Channel not found.", show_alert=True)
            return

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Remove", callback_data=f"fsub_remove_{ch_id}"),
            InlineKeyboardButton("❌ Close", callback_data="close_msg")
        ]])

        await query.edit_message_text(
            f"<blockquote><b>{ch.get('name','Channel')}</b>\n\n"
            "Do you want to remove it from Force-Sub?</blockquote>",
            reply_markup=kb,
            parse_mode=constants.ParseMode.HTML
        )
        return

    # ---------- FSUB REMOVE ----------
    if query.data.startswith("fsub_remove_"):
        ch_id = int(query.data.split("_", 2)[2])
        doc = fsub_col.find_one({"id": ch_id})
        fsub_col.delete_one({"id": ch_id})

        await send_log(context.bot, update.effective_user, f"Removed FSUB channel: {ch_id}")

        # refresh list UI
        channels = get_fsub_channels()
        if not channels:
            await query.edit_message_text(
                "<blockquote>✅ Removed.\n\nNo Force-Sub channels left.</blockquote>",
                parse_mode=constants.ParseMode.HTML
            )
            return

        buttons, row = [], []
        for ch in channels:
            row.append(InlineKeyboardButton(ch["name"], callback_data=f"fsub_pick_{ch['id']}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        buttons.append([InlineKeyboardButton("❌ Close", callback_data="close_msg")])

        removed_name = doc.get("name", "Channel") if doc else "Channel"

        await query.edit_message_text(
            f"<blockquote>✅ Removed <b>{removed_name}</b>.\n\n"
            "<b>Select another channel to remove:</b></blockquote>",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=constants.ParseMode.HTML
        )
        return
        
    # ---------- CLOSE ----------
    if query.data == "close_msg":
        try:
            await query.message.delete()
        except:
            pass
        return

    # ---------- ABOUT ----------
    if query.data == "about":
        await query.edit_message_media(
            media=InputMediaPhoto(
                media=PHOTO_ABOUT,
                caption=(
                    "<code>BOT INFORMATION AND STATISTICS</code>\n\n"
                    "<blockquote expandable><b>»» My Name :</b>"
                    "<a href='https://t.me/AG_Augusta_bot'>Aᴜɢᴜsᴛᴀ</a>\n"
                    "<b>»» Developer :</b> @Prince_Vegeta_36\n"
                    "<b>»» Library :</b> <a href='https://docs.python-telegram-bot.org/'>PTB v22</a>\n"
                    "<b>»» Language :</b> <a href='https://www.python.org/'>Python 3</a>\n"
                    "<b>»» Database :</b> <a href='https://www.mongodb.com/docs/'>MongoDB</a>\n"
                    "<b>»» Hosting :</b> <a href='https://render.com/'>Render</a>"
                    "</blockquote>"
                ),
                parse_mode=constants.ParseMode.HTML
            ),
            reply_markup=about_keyboard()
        )
        return

    # ---------- BACK TO START ----------
    if query.data == "back_to_start":
        await query.edit_message_media(
            media=InputMediaPhoto(
                media=PHOTO_MAIN,
                caption=(
                    f"<b>ʜᴇʏ {update.effective_user.mention_html()} ᴄᴜᴛɪᴇ</b>\n"
                    "➖➖➖➖➖➖➖➖➖\n"
                    "<blockquote><b>➩ ɪ ᴀᴍ ᴘʟᴇᴀsᴇᴅ ᴛᴏ ɪɴғᴏʀᴍ ʏᴏᴜ ᴛʜᴀᴛ ɪ ᴄᴀɴ ᴘʀᴏᴠɪᴅᴇ ʏᴏᴜ ᴡɪᴛʜ\n"
                    "ᴀɴɪᴍᴇ ғɪʟᴇs ғʀᴏᴍ ʏᴏᴜʀ ғᴀᴠᴏʀɪᴛᴇ sᴇʀɪᴇs.</b></blockquote>\n"
                    "➖➖➖➖➖➖➖➖➖\n"
                    "<blockquote><b>➩ ʏᴏᴜ ᴡɪʟʟ ʜᴀᴠᴇ ᴛʜᴇ ᴏᴘᴛɪᴏɴ ᴛᴏ sᴇʟᴇᴄᴛ ᴛʜᴇ ғᴏʀᴍᴀᴛ ᴏғ ʏᴏᴜʀ\n"
                    "ᴄʜᴏɪᴄᴇ, ᴡʜᴇᴛʜᴇʀ ɪᴛ ʙᴇ 480ᴘ, 720ᴘ, 1080ᴘ, ᴏʀ ᴀɴʏ ᴏᴛʜᴇʀ\n"
                    "ᴘʀᴇғᴇʀᴇɴᴄᴇ ʏᴏᴜ ᴍᴀʏ ʜᴀᴠᴇ.</b></blockquote>\n"
                    "➖➖➖➖➖➖➖➖➖\n"
                    "<blockquote><b>➩ ᴡᴇ ᴀʀᴇ ʜᴇʀᴇ ᴛᴏ ᴄᴀᴛᴇʀ ᴛᴏ ʏᴏᴜʀ ᴀɴɪᴍᴇ ɴᴇᴇᴅs ᴡɪᴛʜ ᴛʜᴇ\n"
                    "ᴜᴛᴍᴏsᴛ ᴘʀᴏғᴇssɪᴏɴᴀʟɪsᴍ ᴀɴᴅ ǫᴜᴀʟɪᴛʏ.</b></blockquote>"
                ),
                parse_mode=constants.ParseMode.HTML
            ),
            reply_markup=start_keyboard()
        )
        return

# ---------- RESTART BROADCAST (ALWAYS ON REDEPLOY) ----------
async def broadcast_restart(application: Application):
    RE_caption = (
        "<blockquote>"
        "🔄 <b>Bot Restarted Successfully!\n\n"
        "✅ New changes have been deployed.\n"
        "🚀 Bot is now online and running smoothly.\n\n"
        "Thank you for your patience.</b>"
        "</blockquote>"
    )

    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➥ Support", url="https://t.me/BotifyX_support"),
                InlineKeyboardButton("➥ Update Channel", url="https://t.me/BotifyX_Pro")
            ]
        ]
    )

    for user in users_col.find({}):
        try:
            await application.bot.send_photo(
                chat_id=user["_id"],
                photo=RESTART_PHOTO_ID,
                caption=RE_caption,
                reply_markup=buttons,
                parse_mode=constants.ParseMode.HTML
            )
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except:
            continue

# ---------- POST INIT ----------
async def post_init(application: Application):
    await application.bot.send_message(
        LOG_CHANNEL_ID,
        "<b>🤖 Bot has started successfully!</b>",
        parse_mode=constants.ParseMode.HTML
    )
    await broadcast_restart(application)

# ---------- MAIN ----------
def main():
    Thread(target=run_flask, daemon=True).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .job_queue(JobQueue())
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callbacks))
    application.add_handler(CommandHandler("genlink", genlink_cmd))
    application.add_handler(CommandHandler("flink", flink_cmd))
    application.add_handler(CommandHandler("batch", batch_cmd))
    application.add_handler(CommandHandler("ban", ban_cmd))
    application.add_handler(CommandHandler("unban", unban_cmd))
    application.add_handler(CommandHandler("moderator", moderator_cmd))
    application.add_handler(CommandHandler("revmoderator", revmoderator_cmd))
    application.add_handler(CommandHandler("broadcast", broadcast_cmd))
    application.add_handler(CommandHandler("setdel", setdel_cmd))
    application.add_handler(CommandHandler("addfsub", addfsub_cmd))
    application.add_handler(CommandHandler("delfsub", delfsub_cmd))
    application.add_handler(CommandHandler("fsub", fsub_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("link", link_cmd))
    application.add_handler(CommandHandler("linkch", linkch_cmd))
    application.add_handler(CommandHandler("check_db", check_db_cmd))
    application.add_handler(CommandHandler("setuploads", setuploads_cmd))
    application.add_handler(CommandHandler("upload", upload_cmd))
    application.add_handler(CommandHandler("cancelupload", cancelupload_cmd))
    application.add_handler(ChatJoinRequestHandler(on_fsub_join_request))
    application.add_handler(ChatJoinRequestHandler(auto_approve))
    application.add_handler(
    MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, private_handler)
)


    application.run_polling()


if __name__ == "__main__":
    main()







