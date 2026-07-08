import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

SUPERADMIN_ID = 999999


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "TEST:TOKEN")
    monkeypatch.setenv("SUPERADMIN_ID", str(SUPERADMIN_ID))
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("bot", None)
    sys.modules.pop("core", None)
    module = importlib.import_module("bot")
    monkeypatch.setattr(module, "mark_changed", lambda: None)
    yield module
    sys.modules.pop("bot", None)
    sys.modules.pop("core", None)


class FakeSent:
    _next = 6000
    def __init__(self):
        FakeSent._next += 1
        self.message_id = FakeSent._next


class FakeMsg:
    _next_id = 7000
    def __init__(self, text=None, reply_to=None, forward_from=None, entities=None):
        FakeMsg._next_id += 1
        self.message_id = FakeMsg._next_id
        self.text = text
        self.reply_to_message = reply_to
        self.forward_from = forward_from
        self.entities = entities or []
        self.replies = []
        self.reply_markups = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.replies.append(text)
        self.reply_markups.append(reply_markup)
        return FakeSent()


class FUser:
    def __init__(self, uid, first_name="Test", username=None):
        self.id = uid
        self.first_name = first_name
        self.username = username


class FChat:
    def __init__(self, chat_id=-100500, chat_type="supergroup"):
        self.id = chat_id
        self.type = chat_type


class FUpdate:
    def __init__(self, uid, text, chat=None, reply_to=None, forward_from=None,
                 entities=None, chat_type="private"):
        self.effective_user = FUser(uid)
        self.effective_chat = chat or FChat(chat_type=chat_type)
        self.message = FMsg2(text, chat or FChat(chat_type=chat_type), reply_to,
                              forward_from, entities)


class FMsg2(FakeMsg):
    def __init__(self, text, chat, reply_to, forward_from, entities):
        super().__init__(text=text, reply_to=reply_to, forward_from=forward_from,
                          entities=entities)
        self.chat = chat


@pytest.fixture
def ctx():
    c = SimpleNamespace()
    c.bot = MagicMock()
    c.bot.promote_chat_member = AsyncMock()
    c.bot.send_message = AsyncMock(side_effect=lambda *a, **kw: FakeSent())
    c.bot.send_photo = AsyncMock(side_effect=lambda *a, **kw: FakeSent())
    c.bot.send_animation = AsyncMock(side_effect=lambda *a, **kw: FakeSent())
    c.bot.send_sticker = AsyncMock(side_effect=lambda *a, **kw: FakeSent())
    c.args = []
    c.user_data = {}
    return c


class FakeQ:
    def __init__(self, uid, data):
        self.from_user = FUser(uid)
        self.data = data
        self.message = SimpleNamespace(chat=SimpleNamespace(id=1))
        self.edits = []

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)


class FUpdateCB:
    def __init__(self, uid, data):
        self.callback_query = FakeQ(uid, data)
        self.effective_user = FUser(uid)


# ══════════════════════════════════════════════════════
# 1) Reply-to-retry-message acceptance
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_reply_to_original_question_still_works(bot, ctx):
    chat = FChat(chat_id=-100501)
    g = bot.get_game(chat.id)
    q1 = {"question": "apple", "answer": "olma", "alternatives": []}
    g.update({"active": True, "mode": "standard", "topic": "e", "emoji": "📘",
              "questions": [q1], "asked": 1, "current": q1, "current_msg_id": 500,
              "last_wrong_msg_id": None, "scores": {}, "waiting": False,
              "current_reversed": False})
    q_msg = FakeMsg(text="q")
    q_msg.message_id = 500
    update = FUpdate(111, "olma", chat=chat, reply_to=q_msg)
    await bot._check_answer(update, ctx)
    assert any("TO'G'RI" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_wrong_answer_reply_message_id_is_tracked(bot, ctx):
    chat = FChat(chat_id=-100502)
    g = bot.get_game(chat.id)
    q1 = {"question": "apple", "answer": "olma", "alternatives": []}
    g.update({"active": True, "mode": "standard", "topic": "e", "emoji": "📘",
              "questions": [q1], "asked": 1, "current": q1, "current_msg_id": 501,
              "last_wrong_msg_id": None, "scores": {}, "waiting": False,
              "current_reversed": False})
    q_msg = FakeMsg(text="q")
    q_msg.message_id = 501
    update = FUpdate(111, "notogri", chat=chat, reply_to=q_msg)
    await bot._check_answer(update, ctx)
    assert g["last_wrong_msg_id"] is not None


@pytest.mark.asyncio
async def test_reply_to_wrong_answer_prompt_is_now_accepted(bot, ctx):
    """Asosiy bug: foydalanuvchi botning 'Notoʻgʻri, qayta urinib
    koʻring' xabariga javoban yozsa ham javob sifatida qabul
    qilinishi kerak."""
    chat = FChat(chat_id=-100503)
    g = bot.get_game(chat.id)
    q1 = {"question": "apple", "answer": "olma", "alternatives": []}
    g.update({"active": True, "mode": "standard", "topic": "e", "emoji": "📘",
              "questions": [q1], "asked": 1, "current": q1, "current_msg_id": 502,
              "last_wrong_msg_id": None, "scores": {}, "waiting": False,
              "current_reversed": False})

    # 1) Birinchi (notoʻgʻri) urinish — asl savolga reply
    q_msg = FakeMsg(text="q")
    q_msg.message_id = 502
    wrong_update = FUpdate(111, "yong'oq", chat=chat, reply_to=q_msg)
    await bot._check_answer(wrong_update, ctx)
    wrong_bot_msg_id = g["last_wrong_msg_id"]
    assert wrong_bot_msg_id is not None

    # 2) Ikkinchi urinish — ENDI asl savolga emas, botning "Notoʻgʻri"
    #    xabariga javoban to'g'ri javob yozadi
    bot_wrong_msg = FakeMsg(text="notogri")
    bot_wrong_msg.message_id = wrong_bot_msg_id
    correct_update = FUpdate(111, "olma", chat=chat, reply_to=bot_wrong_msg)
    await bot._check_answer(correct_update, ctx)
    assert any("TO'G'RI" in r for r in correct_update.message.replies)
    assert g["active"] is False  # bitta savol edi -> tugadi


@pytest.mark.asyncio
async def test_reply_to_unrelated_message_still_ignored(bot, ctx):
    chat = FChat(chat_id=-100504)
    g = bot.get_game(chat.id)
    q1 = {"question": "apple", "answer": "olma", "alternatives": []}
    g.update({"active": True, "mode": "standard", "topic": "e", "emoji": "📘",
              "questions": [q1], "asked": 1, "current": q1, "current_msg_id": 503,
              "last_wrong_msg_id": None, "scores": {}, "waiting": False,
              "current_reversed": False})
    unrelated = FakeMsg(text="salom")
    unrelated.message_id = 12345
    update = FUpdate(111, "olma", chat=chat, reply_to=unrelated)
    await bot._check_answer(update, ctx)
    assert update.message.replies == []


@pytest.mark.asyncio
async def test_new_question_resets_last_wrong_msg_id(bot, ctx):
    chat_id = -100505
    g = bot.get_game(chat_id)
    q1 = {"question": "apple", "answer": "olma", "alternatives": []}
    q2 = {"question": "orange", "answer": "apelsin", "alternatives": []}
    g.update({"active": True, "mode": "standard", "topic": "e", "emoji": "📘",
              "questions": [q1, q2], "asked": 0, "current": None,
              "last_wrong_msg_id": 999, "scores": {}, "waiting": False,
              "current_reversed": False, "time_limit": None})
    ctx.bot.send_message = AsyncMock(side_effect=lambda *a, **kw: FakeSent())
    await bot.send_question(chat_id, ctx)
    assert g["last_wrong_msg_id"] is None


# ══════════════════════════════════════════════════════
# 2) Add chat admin (group or channel)
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_addchatadmin_lists_groups_and_channels(bot, ctx):
    bot.save_chats({
        "-100601": {"chat_id": -100601, "type": "supergroup", "name": "MyGroup"},
        "-100602": {"chat_id": -100602, "type": "channel", "name": "MyChannel"},
    })
    update = FUpdate(SUPERADMIN_ID, "/addchatadmin")
    await bot.cmd_addchatadmin(update, ctx)
    assert update.message.replies
    reply_markup_calls = update.message.replies
    assert "guruh" in update.message.replies[0].lower() or "kanal" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_addchatadmin_forward_promotes_user(bot, ctx):
    bot.save_chats({"-100603": {"chat_id": -100603, "type": "supergroup", "name": "G"}})
    ctx.user_data.update({"step": "acadm_waiting_user", "acadm_chat": -100603})
    target = FUser(777, first_name="Target")
    update = FUpdate(SUPERADMIN_ID, "", forward_from=target)
    await bot.handle_text(update, ctx)
    ctx.bot.promote_chat_member.assert_awaited_once()
    args, kwargs = ctx.bot.promote_chat_member.call_args
    assert args[0] == -100603
    assert args[1] == 777
    assert any("admin qilindi" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_addchatadmin_username_lookup_promotes_user(bot, ctx):
    bot.save_chats({"-100604": {"chat_id": -100604, "type": "channel", "name": "C"}})
    bot.save_users({"888": {"username": "nastya", "first_name": "Nastya"}})
    ctx.user_data.update({"step": "acadm_waiting_user", "acadm_chat": -100604})
    update = FUpdate(SUPERADMIN_ID, "@nastya")
    await bot.handle_text(update, ctx)
    ctx.bot.promote_chat_member.assert_awaited_once()
    args, kwargs = ctx.bot.promote_chat_member.call_args
    assert args[1] == 888


@pytest.mark.asyncio
async def test_addchatadmin_user_id_input_promotes_user(bot, ctx):
    bot.save_chats({"-100605": {"chat_id": -100605, "type": "supergroup", "name": "G2"}})
    ctx.user_data.update({"step": "acadm_waiting_user", "acadm_chat": -100605})
    update = FUpdate(SUPERADMIN_ID, "555444333")
    await bot.handle_text(update, ctx)
    ctx.bot.promote_chat_member.assert_awaited_once()
    args, kwargs = ctx.bot.promote_chat_member.call_args
    assert args[1] == 555444333


@pytest.mark.asyncio
async def test_addchatadmin_unresolvable_target_gives_clear_error(bot, ctx):
    ctx.user_data.update({"step": "acadm_waiting_user", "acadm_chat": -100606})
    update = FUpdate(SUPERADMIN_ID, "bilmadim kim")
    await bot.handle_text(update, ctx)
    ctx.bot.promote_chat_member.assert_not_awaited()
    assert any("topilmadi" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_addchatadmin_promote_failure_reported(bot, ctx):
    bot.save_chats({"-100607": {"chat_id": -100607, "type": "supergroup", "name": "G3"}})
    ctx.user_data.update({"step": "acadm_waiting_user", "acadm_chat": -100607})
    ctx.bot.promote_chat_member = AsyncMock(side_effect=Exception("Not enough rights"))
    target = FUser(111, first_name="X")
    update = FUpdate(SUPERADMIN_ID, "", forward_from=target)
    await bot.handle_text(update, ctx)
    assert any("Xatolik" in r for r in update.message.replies)


# ══════════════════════════════════════════════════════
# 3) Emoji pack picker (sticker-set name -> inline picker)
# ══════════════════════════════════════════════════════

class FakeSticker:
    def __init__(self, emoji, custom_emoji_id):
        self.emoji = emoji
        self.custom_emoji_id = custom_emoji_id


class FakeStickerSet:
    def __init__(self, stickers):
        self.stickers = stickers


def _find_btn(kb, cb_prefix):
    for row in kb.inline_keyboard:
        for b in row:
            if b.callback_data.startswith(cb_prefix):
                return b
    return None


@pytest.mark.asyncio
async def test_getemojiid_starts_waiting_step(bot, ctx):
    update = FUpdate(SUPERADMIN_ID, "/getemojiid")
    await bot.cmd_getemojiid(update, ctx)
    assert ctx.user_data.get("step") == "emojipack_waiting"


@pytest.mark.asyncio
async def test_emojipack_shows_inline_picker(bot, ctx):
    ctx.user_data["step"] = "emojipack_waiting"
    ctx.bot.get_sticker_set = AsyncMock(return_value=FakeStickerSet([
        FakeSticker("🔥", "111"), FakeSticker("🎉", "222"),
    ]))
    update = FUpdate(SUPERADMIN_ID, "MyPack")
    await bot.handle_text(update, ctx)
    ctx.bot.get_sticker_set.assert_awaited_once_with("MyPack")
    assert update.message.reply_markups
    kb = update.message.reply_markups[-1]
    assert _find_btn(kb, "epick:0") is not None
    assert _find_btn(kb, "epick:1") is not None
    assert ctx.user_data["epack_items"][0]["id"] == "111"


@pytest.mark.asyncio
async def test_emojipack_not_found_reports_error(bot, ctx):
    ctx.user_data["step"] = "emojipack_waiting"
    ctx.bot.get_sticker_set = AsyncMock(side_effect=Exception("STICKERSET_INVALID"))
    update = FUpdate(SUPERADMIN_ID, "GhostPack")
    await bot.handle_text(update, ctx)
    assert any("topilmadi" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_emojipack_with_no_custom_emojis(bot, ctx):
    ctx.user_data["step"] = "emojipack_waiting"
    ctx.bot.get_sticker_set = AsyncMock(return_value=FakeStickerSet([
        SimpleNamespace(emoji="🔥", custom_emoji_id=None),
    ]))
    update = FUpdate(SUPERADMIN_ID, "RegularStickers")
    await bot.handle_text(update, ctx)
    assert any("custom emoji topilmadi" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_epick_callback_reveals_id(bot, ctx):
    ctx.user_data["epack_items"] = [{"id": "555", "glyph": "🔥"},
                                     {"id": "666", "glyph": "🎉"}]
    update = FUpdateCB(SUPERADMIN_ID, "epick:1")
    await bot.callback_handler(update, ctx)
    assert any("666" in e for e in update.callback_query.edits)


@pytest.mark.asyncio
async def test_epage_pagination(bot, ctx):
    items = [{"id": str(i), "glyph": "🔥"} for i in range(50)]
    ctx.user_data["epack_items"] = items
    ctx.user_data["epack_name"] = "Big"
    update = FUpdateCB(SUPERADMIN_ID, "epage:1")
    await bot.callback_handler(update, ctx)
    assert "2-sahifa" in update.callback_query.edits[0]


@pytest.mark.asyncio
async def test_epick_stale_session_handled(bot, ctx):
    ctx.user_data.clear()
    update = FUpdateCB(SUPERADMIN_ID, "epick:0")
    await bot.callback_handler(update, ctx)
    assert any("eskirgan" in e for e in update.callback_query.edits)


# ══════════════════════════════════════════════════════
# 4) Editadmin: toggle can_add_admins
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_editadmin_toggle_can_add_admins_on(bot, ctx):
    bot.save_admins({"333": {"topic_limit": 2, "max_questions": 100, "added_by": SUPERADMIN_ID}})
    update = FUpdateCB(SUPERADMIN_ID, "eal_ca:333")
    await bot.callback_handler(update, ctx)
    adm = bot.load_admins()
    assert adm["333"]["can_add_admins"] is True
    assert adm["333"]["sub_admin_settings"]["max_topic_limit"] == 2
    assert any("✅" in e for e in update.callback_query.edits)


@pytest.mark.asyncio
async def test_editadmin_toggle_can_add_admins_off(bot, ctx):
    bot.save_admins({"333": {"topic_limit": 2, "max_questions": 100, "added_by": SUPERADMIN_ID,
                              "can_add_admins": True,
                              "sub_admin_settings": {"max_admins": 5, "max_topic_limit": 2,
                                                       "max_questions_per_topic": 100}}})
    update = FUpdateCB(SUPERADMIN_ID, "eal_ca:333")
    await bot.callback_handler(update, ctx)
    adm = bot.load_admins()
    assert adm["333"]["can_add_admins"] is False

