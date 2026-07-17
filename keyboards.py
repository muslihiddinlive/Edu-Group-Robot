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

REACTION_EMOJIS = [e.value for e in ReactionEmoji]

_REACTION_PAGE_SIZE = 40  # 8 ustun x 5 qator

def _reaction_pick_kb(target_uid: int, page: int = 0) -> InlineKeyboardMarkup:
    """Superadmin yangi admin qo'shganda, o'sha admin xabarlariga qanday
    reaksiya bosilishini tanlash uchun klaviatura."""
    start = page * _REACTION_PAGE_SIZE
    chunk = REACTION_EMOJIS[start:start + _REACTION_PAGE_SIZE]
    rows, row = [], []
    for e in chunk:
        row.append(IKB(e, callback_data=f"admreact:{target_uid}:{e}"))
        if len(row) == 8:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(IKB("⬅️", callback_data=f"admreact_page:{target_uid}:{page-1}"))
    if start + _REACTION_PAGE_SIZE < len(REACTION_EMOJIS):
        nav.append(IKB("➡️", callback_data=f"admreact_page:{target_uid}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([IKB("🆔 ID bilan (premium/animatsion)", callback_data=f"admreact_custom:{target_uid}")])
    rows.append([IKB("⏭ O'tkazib yuborish (standart: 🔥)", callback_data=f"admreact_skip:{target_uid}")])
    return InlineKeyboardMarkup(rows)

def _after_action_kb(topic_name: str = None) -> InlineKeyboardMarkup:
    """Topic yaratilgach yoki savol qo'shilgach ko'rsatiladigan
    'Bosh menyu' + savol qo'shish tugmalari (bitta-bitta / ommaviy)."""
    rows = []
    if topic_name:
        rows.append([IKB("📝 Bitta-bitta", callback_data=f"addq_topic:{topic_name}"),
                      IKB("📥 Ommaviy",      callback_data=f"bulkq_topic:{topic_name}")])
    rows.append([IKB("🏠 Bosh menyu", callback_data="menu:back")])
    return InlineKeyboardMarkup(rows)

def _after_action_kb_user(topic_name: str = None) -> InlineKeyboardMarkup:
    """Oddiy foydalanuvchi uchun xuddi shu tugmalar, faqat 'bosh menyu'
    userning o'z menyusiga qaytaradi."""
    rows = []
    if topic_name:
        rows.append([IKB("📝 Bitta-bitta", callback_data=f"addq_topic:{topic_name}"),
                      IKB("📥 Ommaviy",      callback_data=f"bulkq_topic:{topic_name}")])
    rows.append([IKB("🏠 Bosh menyu", callback_data="u:back")])
    return InlineKeyboardMarkup(rows)

def _access_kb(topic_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB("👥 Hamma adminlar",      callback_data=f"acc:all:{topic_name}"),
         IKB("👤 Faqat men",           callback_data=f"acc:owner:{topic_name}")],
        [IKB("🔑 Faqat bot adminlari", callback_data=f"acc:admins:{topic_name}")],
        [IKB("✏️ Qo'lda kiritish",     callback_data=f"acc:custom:{topic_name}")],
    ])

def _aa_tlimit_kb(max_tl: int = 10) -> InlineKeyboardMarkup:
    vals = [v for v in [1, 2, 3, 5, 10] if v <= max_tl]
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_t:{v}") for v in vals],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_qlimit_kb(max_mq: int = 1000) -> InlineKeyboardMarkup:
    vals = [v for v in [100, 250, 500, 750, 1000] if v <= max_mq]
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_q:{v}") for v in vals],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_can_add_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB("✅ Ha, admin qo'sha olsin", callback_data="aa_ca:1"),
         IKB("❌ Yo'q",                   callback_data="aa_ca:0")],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_cnt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_sm:{v}") for v in [1, 2, 3, 5, 10]],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_sub_tl_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_st:{v}") for v in [1, 2, 3, 5, 10]],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_sub_ql_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_sq:{v}") for v in [100, 250, 500, 750, 1000]],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _editadmin_kb(uid_e: int, info: dict) -> InlineKeyboardMarkup:
    from tariffs import MAX_QUESTIONS  # aylanma importdan qochish uchun
    tl = info["topic_limit"]
    mq = info.get("max_questions", MAX_QUESTIONS)
    ca = info.get("can_add_admins", False)
    row_t = [IKB(f"✅{v}" if tl == v else str(v), callback_data=f"eal_t:{uid_e}:{v}")
             for v in [1, 2, 3, 5, 10]]
    row_q = [IKB(f"✅{v}" if mq == v else str(v), callback_data=f"eal_q:{uid_e}:{v}")
             for v in [100, 250, 500, 750, 1000]]
    return InlineKeyboardMarkup([
        row_t, row_q,
        [IKB("🏷 Nom o'zgartirish", callback_data=f"eal_dn:{uid_e}"),
         IKB("❌ O'chirish",        callback_data=f"del_adm:{uid_e}")],
        [IKB(f"👥 Admin qo'sha olish: {'✅' if ca else '❌'}",
             callback_data=f"eal_ca:{uid_e}")],
        [IKB("🎭 Reaksiya belgilash", callback_data=f"admreact_page:{uid_e}:0")],
        [IKB("⬅️ Orqaga",           callback_data="list_adm_cb")],
    ])

def _editadmin_txt(uid_e: int, info: dict) -> str:
    from tariffs import MAX_QUESTIONS  # aylanma importdan qochish uchun
    from admin import count_admin_topics
    tl    = info["topic_limit"]
    mq    = info.get("max_questions", MAX_QUESTIONS)
    dn    = info.get("display_name", "—")
    ca    = info.get("can_add_admins", False)
    owned = count_admin_topics(uid_e)
    sub_s = info.get("sub_admin_settings", {})
    extra = ""
    if ca:
        extra = (f"\n\n👥 *Admin qo'sha oladi:* ✅\n"
                 f"   Max sub-admin: {sub_s.get('max_admins','?')}\n"
                 f"   Sub-admin topic limiti: {sub_s.get('max_topic_limit','?')}\n"
                 f"   Sub-admin savol limiti: {sub_s.get('max_questions_per_topic','?')}")
    return (f"⚙️ *Admin tahrirlash: `{uid_e}`*\n\n"
            f"🏷 Nom: {dn}\n"
            f"📁 Topic limiti: *{tl}* ta | yaratilgan: {owned}\n"
            f"❓ Savol limiti: *{mq}* ta/topic{extra}\n\n"
            f"📁 *Topic limiti o'zgartirish:*\n"
            f"❓ *Savol limiti o'zgartirish:*")

def _superadmin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB("📁 Topiclar",     callback_data="menu:topics"),
         IKB("👥 Adminlar",     callback_data="menu:admins")],
        [IKB("👤 Userlar",      callback_data="menu:users"),
         IKB("💎 Tariflar",     callback_data="menu:tarifs")],
        [IKB("🎮 O'yinlar",     callback_data="menu:games"),
         IKB("🔤 So'z filtri",  callback_data="menu:badwords")],
        [IKB("📢 Reklama",      callback_data="menu:broadcast"),
         IKB("⚙️ Sozlamalar",   callback_data="menu:settings")],
        [IKB("📦 Export",       callback_data="menu:export"),
         IKB("🏫 Forum Topic",  callback_data="menu:forum")],
    ])

def _admin_main_kb(uid: int) -> InlineKeyboardMarkup:
    adm = load_admins()
    info = adm.get(str(uid), {})
    rows = [
        [IKB("📁 Topiclarim",   callback_data="menu:topics"),
         IKB("📝 Savol qo'sh",  callback_data="menu:addq")],
        [IKB("🎮 O'yin boshlash", callback_data="menu:newgame")],
    ]
    if info.get("can_add_admins"):
        rows.append([IKB("👥 Sub-adminlar", callback_data="menu:admins")])
    return InlineKeyboardMarkup(rows)

def _user_main_kb(uid: int) -> InlineKeyboardMarkup:
    tarif = get_user_tarif(uid)
    rows = [
        [IKB("💎 Tarifim",        callback_data="u:tarif"),
         IKB("👥 Referallarim",   callback_data="u:referral")],
        [IKB("📋 Topiclarim",     callback_data="u:topics"),
         IKB("➕ Yangi topic",    callback_data="u:newtopic")],
        [IKB("📨 Adminga murojaat", callback_data="u:contact")],
    ]
    if tarif == TARIF_FREE:
        rows.append([IKB("🛒 Tarif sotib olish", callback_data="u:buy")])
    return InlineKeyboardMarkup(rows)

def _buy_tarif_kb() -> InlineKeyboardMarkup:
    from tariffs import get_tarif_prices, TARIF_PLUS, TARIF_PREMIUM, TARIF_VIP
    prices = get_tarif_prices()
    return InlineKeyboardMarkup([
        [IKB(f"✨ PLUS — {prices[TARIF_PLUS]} ⭐",       callback_data=f"buy:{TARIF_PLUS}")],
        [IKB(f"💎 Premium — {prices[TARIF_PREMIUM]} ⭐", callback_data=f"buy:{TARIF_PREMIUM}")],
        [IKB(f"👑 VIP — {prices[TARIF_VIP]} ⭐",        callback_data=f"buy:{TARIF_VIP}")],
        [IKB("❌ Bekor", callback_data="u:back")],
    ])

def _tarif_admin_kb() -> InlineKeyboardMarkup:
    from tariffs import get_tarif_prices, TARIF_PLUS, TARIF_PREMIUM, TARIF_VIP
    prices = get_tarif_prices()
    return InlineKeyboardMarkup([
        [IKB(f"✨ PLUS narxi: {prices[TARIF_PLUS]} ⭐",       callback_data=f"setprice:{TARIF_PLUS}")],
        [IKB(f"💎 Premium narxi: {prices[TARIF_PREMIUM]} ⭐", callback_data=f"setprice:{TARIF_PREMIUM}")],
        [IKB(f"👑 VIP narxi: {prices[TARIF_VIP]} ⭐",        callback_data=f"setprice:{TARIF_VIP}")],
        [IKB("📢 Obuna kanali o'zgartirish",                  callback_data="setchannel")],
        [IKB("⬅️ Orqaga", callback_data="menu:back")],
    ])

