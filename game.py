"""
Avtomatik generatsiya qilingan modul — asl bot.py/core.py'dan ajratildi.
"""
import os
import io
import re
import json
import random
import logging
import asyncio
from datetime import datetime, timedelta, timezone, time as dt_time
from zoneinfo import ZoneInfo

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardButton as IKB, InlineKeyboardMarkup,
    LabeledPrice, BotCommand, BotCommandScopeChat, BotCommandScopeDefault,
    ReactionTypeEmoji, ReactionTypeCustomEmoji, ChatPermissions, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.constants import ParseMode, ChatAction, ReactionEmoji
from telegram.error import TelegramError, BadRequest, Forbidden
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    PreCheckoutQueryHandler, CallbackQueryHandler, ContextTypes, filters,
)
import common as _common
globals().update({k: v for k, v in vars(_common).items() if not k.startswith('__')})

ACCESS_LABELS = {
    "all":     "👥 Hamma adminlar",
    "owner":   "👤 Faqat men",
    "admins":  "🔑 Faqat bot adminlari",
    "custom":  "✏️ Qo'lda belgilangan",
}

def _ensure_topics_dir():
    os.makedirs(TOPICS_DIR, exist_ok=True)

os.makedirs(TOPICS_DIR, exist_ok=True)  # import vaqtida ham papka tayyor bo'lsin

def topic_path(name: str) -> str:
    return os.path.join(TOPICS_DIR, f"{name.lower()}.json")

def topic_exists(name: str) -> bool:
    _ensure_topics_dir()
    return os.path.exists(topic_path(name))

def load_topic(name: str) -> dict | None:
    _ensure_topics_dir()
    p = topic_path(name)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_topic(data: dict):
    _ensure_topics_dir()
    p   = topic_path(data["name"])
    tmp = f"{p}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    from backup import mark_changed
    mark_changed()

def all_topics() -> list:
    _ensure_topics_dir()
    out = []
    for fn in sorted(os.listdir(TOPICS_DIR)):
        if fn.endswith(".json"):
            fp = os.path.join(TOPICS_DIR, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    out.append(json.load(f))
            except (json.JSONDecodeError, OSError) as e:
                # Bitta buzilgan topic fayli butun ro'yxatni yiqitmasin
                logger.error(f"Topic o'qishda xato ({fp}): {e} — o'tkazib yuborildi")
    return out

def count_topics() -> int:
    _ensure_topics_dir()
    return sum(1 for f in os.listdir(TOPICS_DIR) if f.endswith(".json"))

async def cmd_listgames(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    active = {cid: g for cid, g in games.items() if g.get("active")}
    if not active:
        await update.message.reply_text("🎮 Faol o'yin yo'q.")
        return
    lines = []
    btns  = []
    for cid, g in active.items():
        lines.append(f"🟢 `{cid}` — {g['emoji']}{g['topic']} | "
                     f"{g['asked']}/{len(g['questions'])} | {len(g['scores'])} o'yinchi")
        btns.append([IKB(f"⏹ {g['emoji']}{g['topic']} ({cid})",
                         callback_data=f"stopgame_ask:{cid}")])
    await update.message.reply_text(
        "🎮 *Faol o'yinlar:*\n\n" + "\n".join(lines),
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("❌ Faqat guruhlarda!")
        return
    if not await _require_bot_admin(update, context):
        return
    if not await is_group_admin(update, context):
        await update.message.reply_text("❌ Faqat guruh admini o'yin boshlay oladi!")
        return
    from admin import register_chat  # aylanma importdan qochish uchun
    register_chat(chat)
    args = context.args
    if not args:
        topics = all_topics()
        names = ", ".join(f"`{t['name']}`" for t in topics) if topics else "(yo'q)"
        await update.message.reply_text(
            "❌ Foydalanish:\n"
            "`/newgame <topic>` — standart\n"
            "`/newgame <topic> langmode` — tarjima o'yini\n"
            "`/newgame <topic> speed [vaqt]` — tezkor (masalan `30s`)\n"
            "`/newgame admin` — admin belgilaydigan o'yin\n\n"
            f"📚 Mavjud topiclar: {names}", parse_mode="Markdown")
        return

    cid = chat.id
    g   = get_game(cid)
    if g["active"]:
        label = "admin o'yini" if g["mode"] == "admin" else f"{g['emoji']}{g['topic']}"
        await update.message.reply_text(
            f"⚠️ Allaqachon *{label}* ketmoqda!", parse_mode="Markdown")
        return

    # ── Module 1: Admin mode ──
    if args[0].lower() == "admin":
        g.update({"active": True, "mode": "admin", "topic": None, "emoji": "",
                  "questions": [], "asked": 0, "current": None,
                  "current_msg_id": None, "scores": {}, "waiting": False,
                  "time_limit": None, "admin_ranks": [], "admin_scored_msgs": [],
                  "started_by": update.effective_user.id})
        await update.message.reply_text(
            "🎮 *ADMIN O'YINI BOSHLANDI!*\n\n"
            "Savolni o'zingiz istalgan formatda yozing. To'g'ri javob "
            "bergan kishining xabariga ✅ ni *javob* tariqasida yuboring "
            "(yoki `✅ @username`) — unga 1 ball qo'shiladi.\n\n"
            "⏹ Tugatish: `/endgame`", parse_mode="Markdown")
        return

    tn = args[0].lower()
    t  = load_topic(tn)
    if not t:
        await update.message.reply_text(f"❌ `{tn}` mavjud emas!", parse_mode="Markdown")
        return
    if not t["questions"]:
        await update.message.reply_text("❌ Bu topicda savollar yo'q!")
        return

    mode       = "standard"
    time_limit = None
    if len(args) >= 2:
        m2 = args[1].lower()
        if m2 == "langmode":
            mode = "lang"
        elif m2 == "speed":
            mode = "speed"
            if len(args) >= 3:
                time_limit = parse_duration(args[2])
                if time_limit is None:
                    await update.message.reply_text(
                        "❌ Vaqt formati noto'g'ri! Masalan: `30s`, `5d`, `2soat`, `1kun`",
                        parse_mode="Markdown")
                    return
        else:
            await update.message.reply_text(
                "❌ Noma'lum rejim! `langmode` yoki `speed` bo'lishi kerak.")
            return

    qs = t["questions"].copy()
    random.shuffle(qs)
    g.update({"active": True, "mode": mode, "topic": tn, "emoji": t["emoji"],
              "questions": qs, "asked": 0, "current": None, "current_reversed": False,
              "current_msg_id": None, "scores": {}, "waiting": False,
              "time_limit": time_limit, "admin_ranks": [], "admin_scored_msgs": [],
              "started_by": update.effective_user.id})
    mode_label = {"standard": "", "lang": " 🔤 Lang mode",
                  "speed": f" ⚡ Speed mode" + (f" ({args[2]})" if time_limit else "")}[mode]
    await update.message.reply_text(
        f"🎮 *O'YIN BOSHLANDI!*{mode_label}\n\n{t['emoji']} *{tn.capitalize()}*\n"
        f"📊 {len(qs)} ta savol\n\n🎯 Reply qilib javob bering!",
        parse_mode="Markdown")
    await send_question(cid, context)

def get_user_topic_names(uid: int) -> list:
    return [t["name"] for t in all_topics() if t.get("created_by") == uid]

def parse_allowed(text: str) -> list:
    result = []
    for item in re.split(r'[\s,]+', text.strip()):
        item = item.strip()
        if not item:
            continue
        if item.startswith('@'):
            result.append(item.lower())
        else:
            try:
                result.append(int(item))
            except ValueError:
                pass
    return result

async def cmd_endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid  = update.effective_chat.id
    uid  = update.effective_user.id
    chat = update.effective_chat
    if not is_superadmin(uid):
        if chat.type not in ("group", "supergroup"):
            return
        if not await is_group_admin(update, context):
            await update.message.reply_text("❌ Faqat admin!")
            return
    g = get_game(cid)
    if not g["active"]:
        await update.message.reply_text("⚠️ Faol o'yin yo'q.")
        return

    if g.get("mode") == "admin":
        await _finish_admin_game(cid, context)
        return

    g["active"] = False
    g["current"] = None
    g["waiting"] = False
    await update.message.reply_text(
        f"⏹ *{g['emoji']}{g['topic']} tugatildi!*", parse_mode="Markdown")

def check_allowed(uid: int, username: str | None, allowed: list) -> bool:
    if uid in allowed:
        return True
    if username and f"@{username.lower()}" in allowed:
        return True
    return False

def can_manage_topic(topic: dict, uid: int, username: str = None) -> bool:
    if uid == SUPERADMIN:
        return True
    cb  = topic.get("created_by")
    acc = topic.get("access", {"type": "all"})
    at  = acc.get("type", "all")
    if at == "all":
        return is_admin_or_superadmin(uid)
    if at == "owner":
        return uid == cb
    if at == "admins":
        return is_bot_admin(uid)
    if at == "custom":
        allowed = acc.get("allowed", [])
        return uid == cb or check_allowed(uid, username, allowed)
    return False

async def cmd_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    g   = get_game(cid)
    if not g["scores"]:
        await update.message.reply_text("📊 Hozircha ballar yo'q.")
        return
    ss     = sorted(g["scores"].items(), key=lambda x: x[1]["count"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    res    = f"📊 *Joriy — {g['emoji']}{g['topic']}:*\n\n"
    for i, (uid_s, d) in enumerate(ss[:10]):
        m  = medals[i] if i < 3 else f"{i+1}."
        dn = get_display_name(int(uid_s), d["name"])
        res += f"{m} {dn}: {d['count']} ball\n"
    await update.message.reply_text(res, parse_mode="Markdown")

def can_edit_topic_access(topic: dict, uid: int) -> bool:
    if uid == SUPERADMIN:
        return True
    return topic.get("created_by") == uid

games: dict = {}

def get_game(chat_id: int) -> dict:
    if chat_id not in games:
        games[chat_id] = {
            "active": False, "mode": "standard", "topic": None, "emoji": "",
            "questions": [], "asked": 0, "current": None, "current_reversed": False,
            "current_msg_id": None, "last_wrong_msg_id": None,
            "scores": {}, "waiting": False,
            "time_limit": None,
            # Admin (module 1) uchun:
            "admin_ranks": [], "admin_scored_msgs": [], "started_by": None,
        }
    return games[chat_id]

def parse_duration(s: str):
    """'30s', '5d', '2soat', '1kun', '3k' -> soniyalarga o'giradi.
    s=soniya, d=daqiqa, soat=soat, kun/k=kun."""
    s = s.strip().lower()
    m = re.match(r"^(\d+)\s*(s|soniya|d|daqiqa|soat|kun|k)$", s)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    mult = {"s": 1, "soniya": 1, "d": 60, "daqiqa": 60,
             "soat": 3600, "kun": 86400, "k": 86400}[unit]
    return n * mult

async def _save_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tn  = context.user_data.get("topic_name")
    t   = load_topic(tn)
    if not t:
        await update.message.reply_text("❌ Topic topilmadi.")
        context.user_data.clear()
        return
    from admin import get_admin_max_questions  # aylanma importdan qochish uchun
    mq = get_admin_max_questions(uid)
    if len(t["questions"]) >= mq:
        await update.message.reply_text(f"❌ Limit: {mq} ta savol!")
        context.user_data.clear()
        return
    mt = context.user_data.get("q_media_type", "none")
    fi = context.user_data.get("q_file_id", None)
    if mt != "none" and fi:
        stable = await _pin_media(context.bot, mt, fi,
                                  f"{tn} | {context.user_data.get('q_question','')}")
        if stable:
            fi = stable
    q = {
        "question":     context.user_data.get("q_question", ""),
        "answer":       context.user_data.get("q_answer", ""),
        "alternatives": context.user_data.get("q_alts", []),
        "media_type":   mt,
        "file_id":      fi,
    }
    t["questions"].append(q)
    save_topic(t)
    cnt = len(t["questions"])
    for k in ("q_question", "q_answer", "q_alts", "q_media_type", "q_file_id"):
        context.user_data.pop(k, None)
    context.user_data["step"] = "addq_question"
    icon = {"photo": "🖼", "video": "🎬", "gif": "🎞", "sticker": "🎭"}.get(mt, "📝")
    kb = InlineKeyboardMarkup([
        [IKB("➕ Yana savol", callback_data="addq_continue"),
         IKB("⏹ Tugatish",   callback_data="addq_finish")],
        [IKB("🏠 Bosh menyu", callback_data="menu:back" if is_admin_or_superadmin(uid) else "u:back")],
    ]) if cnt < mq else InlineKeyboardMarkup([
        [IKB("🏠 Bosh menyu", callback_data="menu:back" if is_admin_or_superadmin(uid) else "u:back")],
    ])
    await update.message.reply_text(
        f"✅ *Savol saqlandi!* {icon}\n📊 {t['emoji']} {mdesc(tn)}: {cnt}/{mq}",
        parse_mode="Markdown", reply_markup=kb)

async def send_question(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    g = get_game(chat_id)
    if not g["active"]:
        return
    if g["asked"] >= len(g["questions"]):
        await finish_game(chat_id, context)
        return
    q = g["questions"][g["asked"]]
    g["asked"] += 1
    g["current"] = q
    g["last_wrong_msg_id"] = None

    # Lang mode: tasodifiy savol/javob tomonini almashtiradi
    reversed_ = False
    if g.get("mode") == "lang":
        reversed_ = random.random() < 0.5
    g["current_reversed"] = reversed_

    prompt = q["answer"] if reversed_ else q["question"]
    cap = (f"{g['emoji']} *Savol {g['asked']}/{len(g['questions'])}*\n\n"
           f"❓ {mdesc(prompt)}\n\n↩️ Reply qilib javob bering:")
    mt = q.get("media_type", "none")
    fi = q.get("file_id")
    try:
        # Reversed holatda rasm/video asl savolga tegishli bo'lgani uchun,
        # faqat oddiy (reversed bo'lmagan) holatda media biriktiramiz
        if mt == "photo" and fi and not reversed_:
            sent = await context.bot.send_photo(chat_id, fi, caption=cap, parse_mode="Markdown")
        elif mt == "video" and fi and not reversed_:
            sent = await context.bot.send_video(chat_id, fi, caption=cap, parse_mode="Markdown")
        elif mt == "gif" and fi and not reversed_:
            sent = await context.bot.send_animation(chat_id, fi, caption=cap, parse_mode="Markdown")
        elif mt == "sticker" and fi and not reversed_:
            await context.bot.send_sticker(chat_id, fi)
            sent = await context.bot.send_message(chat_id, cap, parse_mode="Markdown")
        else:
            sent = await context.bot.send_message(chat_id, cap, parse_mode="Markdown")
        g["current_msg_id"] = sent.message_id
    except Exception as e:
        logger.error(f"send_question: {e}")
        return

    # Speed mode: vaqt limiti bo'lsa, taymer ishga tushiramiz
    if g.get("mode") == "speed" and g.get("time_limit"):
        asyncio.create_task(_speed_timeout(chat_id, context, q))

async def _speed_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, question_ref):
    """Speed mode uchun vaqt limiti tugaganda ishga tushadi. Agar shu
    orada javob berilgan yoki o'yin tugagan bo'lsa, hech narsa qilmaydi."""
    g = get_game(chat_id)
    try:
        await asyncio.sleep(g["time_limit"])
    except Exception:
        return
    if not g["active"] or g["current"] is not question_ref or g["waiting"]:
        return
    g["waiting"] = True
    correct = g["current"]["answer"]
    alts    = g["current"].get("alternatives", [])
    alt_t   = f"\n➕ Shuningdek: _{mdesc(', '.join(alts))}_" if alts else ""
    try:
        await context.bot.send_message(
            chat_id,
            f"⏰ *VAQT TUGADI!*\n✅ To'g'ri: *{mdesc(correct)}*{alt_t}\n\n⏩ Keyingi...",
            parse_mode="Markdown")
    except Exception:
        pass
    g["waiting"] = False
    if g["asked"] >= len(g["questions"]):
        await finish_game(chat_id, context)
    else:
        await send_question(chat_id, context)

async def _check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    g   = get_game(cid)
    if not g["active"] or g["current"] is None or g["waiting"]:
        return
    if g.get("mode") == "admin":
        return  # admin mode savol-javob bilan ishlamaydi
    reply = update.message.reply_to_message
    if reply is None or reply.message_id not in (g["current_msg_id"], g.get("last_wrong_msg_id")):
        return
    user    = update.effective_user
    uid_s   = str(user.id)
    raw_nm  = user.first_name or "Anonim"
    dname   = mdesc(get_display_name(user.id, raw_nm))
    ans     = update.message.text.strip().lower()

    if g.get("mode") == "lang" and g.get("current_reversed"):
        # Reversed holatda javob — asl so'zning o'zi
        correct = g["current"]["question"].lower()
        alts    = []
    else:
        correct = g["current"]["answer"].lower()
        alts    = [a.lower() for a in g["current"].get("alternatives", [])]
    ok = (ans == correct or ans in alts)

    mode = g.get("mode", "standard")

    if ok:
        g["waiting"] = True
        if uid_s not in g["scores"]:
            g["scores"][uid_s] = {"name": raw_nm, "count": 0}
        g["scores"][uid_s]["count"] += 1
        ball = g["scores"][uid_s]["count"]
        await update.message.reply_text(
            f"✅ *TO'G'RI!* 🎉\n👤 {dname}: {ball} ball\n\n⏩ Keyingi...",
            parse_mode="Markdown")
        g["waiting"] = False
        if g["asked"] >= len(g["questions"]):
            await finish_game(cid, context)
        else:
            await send_question(cid, context)
        return

    # Noto'g'ri javob — hech qaysi rejimda javob ochilmaydi va keyingisiga
    # o'tilmaydi. Speed mode faqat vaqt tugagach (_speed_timeout) o'tadi.
    # Standard/Lang uchun "Keyingisi" tugmasi qo'yiladi — hech kim
    # topolmasa, admin bosib o'tkazib yubora oladi.
    sent = await update.message.reply_text(
        "❌ *Noto'g'ri!* Yana urinib ko'ring 🔁", parse_mode="Markdown",
        reply_markup=(None if mode == "speed" else
                      InlineKeyboardMarkup([[IKB("⏩ Keyingisi", callback_data=f"skipq:{cid}")]])))
    g["last_wrong_msg_id"] = sent.message_id

async def finish_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    g = get_game(chat_id)
    g["active"] = False
    t = load_topic(g["topic"]) if g["topic"] else None
    if not g["scores"]:
        await context.bot.send_message(
            chat_id, "📊 *O'yin tugadi!* Hech kim to'g'ri javob bermadi.",
            parse_mode="Markdown")
        return
    ss      = sorted(g["scores"].items(), key=lambda x: x[1]["count"], reverse=True)
    max_sc  = ss[0][1]["count"]
    medals  = ["🥇", "🥈", "🥉"]
    winners = [mdesc(get_display_name(int(uid_s), d["name"])) for uid_s, d in ss
               if d["count"] == max_sc]
    hdr = (f"🏆 *G'OLIB: {winners[0]}* 🏆" if len(winners) == 1
           else f"🏆 *G'OLIBLAR: {', '.join(winners)}* 🏆")
    res = f"{hdr}\n📊 {max_sc}/{len(g['questions'])}\n\n📋 *Natijalar:*\n"
    for i, (uid_s, d) in enumerate(ss[:10]):
        m  = medals[i] if i < 3 else f"{i+1}."
        dn = mdesc(get_display_name(int(uid_s), d["name"]))
        res += f"{m} {dn}: {d['count']} ball\n"
    prize = t.get("prize") if t else None
    try:
        if prize:
            pt, fi = prize["type"], prize["file_id"]
            if pt == "photo":
                await context.bot.send_photo(chat_id, photo=fi, caption=res, parse_mode="Markdown")
            elif pt == "gif":
                await context.bot.send_animation(chat_id, animation=fi, caption=res, parse_mode="Markdown")
            elif pt == "sticker":
                await context.bot.send_sticker(chat_id, sticker=fi)
                await context.bot.send_message(chat_id, res, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id, res, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"finish_game: {e}")
        await context.bot.send_message(chat_id, res, parse_mode="Markdown")
    g["scores"] = {}

def _find_user_by_username(uname: str):
    """users.json ichidan @username bo'yicha qidiradi.
    Topilsa (uid, name, username) qaytaradi, aks holda None."""
    uname = uname.lstrip("@").lower()
    for uid_s, data in load_users().items():
        if (data.get("username") or "").lower() == uname:
            return int(uid_s), data.get("first_name") or "Anonim", data.get("username")
    return None

async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_or_superadmin(update.effective_user.id):
        return
    if context.user_data.get("step") == "addq_media_waiting":
        context.user_data.update({"q_media_type": "none", "q_file_id": None})
        await _save_q(update, context)
    else:
        await update.message.reply_text("⚠️ Hech narsa o'tkazilmadi.")

async def handle_admin_mode_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Admin-mode o'yinida ✅ orqali g'olib belgilashni qayta ishlaydi.
    True qaytarsa — xabar shu yerda to'liq qayta ishlangan (davom
    ettirish shart emas)."""
    msg  = update.message
    chat = update.effective_chat
    if not msg or not msg.text or not chat or chat.type not in ("group", "supergroup"):
        return False
    g = get_game(chat.id)
    if not g["active"] or g.get("mode") != "admin":
        return False
    text = msg.text.strip()
    if not text.startswith("✅"):
        return False
    user = update.effective_user
    if not user:
        return False
    is_allowed = (user.id == SUPERADMIN or is_bot_admin(user.id)
                  or await is_group_admin(update, context))
    if not is_allowed:
        return False  # oddiy ishtirokchi ✅ yozsa — bu oddiy matn, e'tiborsiz qoldiramiz

    winner_uid = winner_name = winner_username = None
    target_msg_id = None

    reply = msg.reply_to_message
    if reply and reply.from_user:
        winner_uid      = reply.from_user.id
        winner_name     = reply.from_user.first_name or "Anonim"
        winner_username = reply.from_user.username
        target_msg_id   = reply.message_id
    else:
        m = re.search(r"@(\w+)", text)
        if m:
            found = _find_user_by_username(m.group(1))
            if found:
                winner_uid, winner_name, winner_username = found

    if winner_uid is None:
        await msg.reply_text(
            "❗️ Iltimos, biror xabarga *javob* tariqasida yuboring yoki "
            "`✅ @username` shaklida yozing.", parse_mode="Markdown")
        return True

    if target_msg_id is not None and target_msg_id in g["admin_scored_msgs"]:
        await msg.reply_text("⚠️ Bu xabarga allaqachon bal berilgan!")
        return True

    uid_s = str(winner_uid)
    if uid_s not in g["scores"]:
        g["scores"][uid_s] = {"name": winner_name, "count": 0}
    g["scores"][uid_s]["count"] += 1
    ball = g["scores"][uid_s]["count"]
    if target_msg_id is not None:
        g["admin_scored_msgs"].append(target_msg_id)
    dname = mdesc(get_display_name(winner_uid, winner_name))
    await context.bot.send_message(
        chat.id, f"🎉 Tabriklaymiz, {dname}! +1 ball (jami: *{ball}* ball)",
        parse_mode="Markdown")
    if target_msg_id:
        try:
            await context.bot.set_message_reaction(
                chat_id=chat.id, message_id=target_msg_id,
                reaction=[ReactionTypeEmoji(emoji="🎉")], is_big=False)
        except Exception:
            pass  # reaksiya bosilmasa ham o'yin to'xtab qolmasin
    return True

async def _finish_admin_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Admin o'yinini yakunlaydi — natijalarni guruh biriktirilgan
    (linked) kanalga, aks holda guruhning o'ziga yuboradi."""
    g = get_game(chat_id)
    g["active"] = False
    scores = g.get("scores", {})
    if not scores:
        await context.bot.send_message(chat_id, "📊 O'yin tugadi. Hech kim belgilanmadi.")
        return
    ss     = sorted(scores.items(), key=lambda x: x[1]["count"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines  = ["🏆 *NATIJALAR* 🏆\n"]
    for i, (uid_s, d) in enumerate(ss):
        m     = medals[i] if i < 3 else f"{i+1}."
        dname = mdesc(get_display_name(int(uid_s), d["name"]))
        lines.append(f"{m} {dname} — {d['count']} ball")
    text   = "\n".join(lines)
    target = chat_id
    try:
        chat_obj = await context.bot.get_chat(chat_id)
        if getattr(chat_obj, "linked_chat_id", None):
            target = chat_obj.linked_chat_id
    except Exception as e:
        logger.warning(f"_finish_admin_game: get_chat xato: {e}")
    try:
        await context.bot.send_message(target, text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"_finish_admin_game: natija yuborilmadi ({target}): {e}")
        if target != chat_id:
            try:
                await context.bot.send_message(chat_id, text, parse_mode="Markdown")
            except Exception:
                pass
    g["admin_ranks"] = []
    g["admin_scored_msgs"] = []
    g["scores"] = {}

