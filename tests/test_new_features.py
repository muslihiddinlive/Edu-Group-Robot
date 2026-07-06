"""
Tests for the new features added on top of the existing bot:
  - ⚡ reaction on/off toggle for superadmin
  - Game engine: standard (wait-for-correct), speed (skip+timer), lang (swap),
    admin (✅-judged) modes
  - Profanity: sacred-name escalation, admin 🗿 reaction, incident log + DM
  - Dot-commands (.ban .mute .warn .pin .del ...)

Run with: pytest tests/test_new_features.py -v
"""
import asyncio
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

SUPERADMIN_ID = 999999


# ══════════════════════════════════════════════════════
# Fixtures & fakes
# ══════════════════════════════════════════════════════

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
    _next_id = 5000
    def __init__(self):
        FakeSent._next_id += 1
        self.message_id = FakeSent._next_id


class FakeBot:
    def __init__(self):
        self.id = 42
        self.sent = []
        self.reactions = []
        self.deleted = []
        self.banned = []
        self.unbanned = []
        self.restricted = []
        self.promoted = []
        self.pinned = []
        self.unpinned = []
        self.chat_members = {}       # (chat_id, uid) -> status
        self.chat_admins = {}        # chat_id -> [(uid, status)]
        self.linked_chat_id = {}     # chat_id -> linked id
        self.dms = []                # (uid, text)
        self.fail_chats = set()      # chat_ids where send_message should fail

    async def send_message(self, chat_id, text=None, **kw):
        if chat_id in self.fail_chats:
            raise RuntimeError("blocked")
        s = FakeSent()
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        if isinstance(chat_id, int) and chat_id > 0 and chat_id not in self.chat_admins:
            self.dms.append((chat_id, text))
        return s

    async def send_photo(self, chat_id, photo, **kw):
        return await self.send_message(chat_id, kw.get("caption"))

    async def send_video(self, chat_id, video, **kw):
        return await self.send_message(chat_id, kw.get("caption"))

    async def send_animation(self, chat_id, animation, **kw):
        return await self.send_message(chat_id, kw.get("caption"))

    async def send_sticker(self, chat_id, sticker, **kw):
        return await self.send_message(chat_id, None)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def set_message_reaction(self, chat_id, message_id, reaction, is_big=False):
        self.reactions.append((chat_id, message_id, reaction[0].emoji))

    async def get_chat_member(self, chat_id, uid):
        status = self.chat_members.get((chat_id, uid), "member")
        return SimpleNamespace(status=status)

    async def get_chat_administrators(self, chat_id):
        return [SimpleNamespace(status=s, user=SimpleNamespace(id=u))
                for u, s in self.chat_admins.get(chat_id, [])]

    async def get_chat(self, chat_id):
        return SimpleNamespace(linked_chat_id=self.linked_chat_id.get(chat_id),
                                title=f"Chat{chat_id}")

    async def ban_chat_member(self, chat_id, uid):
        self.banned.append((chat_id, uid))

    async def unban_chat_member(self, chat_id, uid, **kw):
        self.unbanned.append((chat_id, uid))

    async def restrict_chat_member(self, chat_id, uid, permissions, **kw):
        self.restricted.append((chat_id, uid, permissions, kw.get("until_date")))

    async def promote_chat_member(self, chat_id, uid, **kw):
        self.promoted.append((chat_id, uid, kw))

    async def pin_chat_message(self, chat_id, message_id, **kw):
        self.pinned.append((chat_id, message_id))

    async def unpin_chat_message(self, chat_id, message_id=None, **kw):
        self.unpinned.append((chat_id, message_id))

    async def unpin_all_chat_messages(self, chat_id):
        self.unpinned.append((chat_id, "ALL"))


class FUser:
    def __init__(self, uid, username=None, first_name="Test", is_bot=False):
        self.id = uid
        self.username = username
        self.first_name = first_name
        self.is_bot = is_bot


class FChat:
    def __init__(self, chat_id=-100123, chat_type="supergroup", title="TestGroup"):
        self.id = chat_id
        self.type = chat_type
        self.title = title


class FMessage:
    _next_id = 100
    def __init__(self, text=None, chat=None, user=None, reply_to=None):
        FMessage._next_id += 1
        self.message_id = FMessage._next_id
        self.text = text
        self.chat = chat or FChat()
        self.from_user = user
        self.reply_to_message = reply_to
        self.deleted = False
        self.replies = []
        self.date = SimpleNamespace(timestamp=lambda: 0)
        self.sender_chat = None

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return FMessage(text=text, chat=self.chat)

    async def delete(self):
        self.deleted = True


class FUpdate:
    def __init__(self, uid, text, chat=None, username=None, reply_to=None, first_name="Test"):
        self.effective_user = FUser(uid, username=username, first_name=first_name)
        self.effective_chat = chat or FChat()
        self.message = FMessage(text=text, chat=self.effective_chat,
                                 user=self.effective_user, reply_to=reply_to)


@pytest.fixture
def fakebot():
    return FakeBot()


@pytest.fixture
def ctx(fakebot):
    c = SimpleNamespace()
    c.bot = fakebot
    c.args = []
    c.user_data = {}
    return c


# ══════════════════════════════════════════════════════
# 1) Reaction toggle
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_toggle_reaction_flips_config(bot, ctx):
    update = FUpdate(SUPERADMIN_ID, "/togglereaction")
    assert bot.load_config().get("lightning_reaction_enabled", True) is True
    await bot.cmd_togglereaction(update, ctx)
    assert bot.load_config()["lightning_reaction_enabled"] is False
    await bot.cmd_togglereaction(update, ctx)
    assert bot.load_config()["lightning_reaction_enabled"] is True


@pytest.mark.asyncio
async def test_non_superadmin_cannot_toggle(bot, ctx):
    update = FUpdate(111, "/togglereaction")
    await bot.cmd_togglereaction(update, ctx)
    assert "lightning_reaction_enabled" not in bot.load_config()


# ══════════════════════════════════════════════════════
# 2) Game modes
# ══════════════════════════════════════════════════════

def _make_topic(bot, name="english"):
    bot.save_topic({
        "name": name, "emoji": "📘", "prize": None,
        "created_by": SUPERADMIN_ID, "access": {"type": "all", "allowed": []},
        "questions": [
            {"question": "apple", "answer": "olma", "alternatives": []},
            {"question": "orange", "answer": "apelsin", "alternatives": ["olovrang"]},
        ],
    })


@pytest.mark.asyncio
async def test_standard_mode_wrong_answer_waits_no_advance(bot, ctx, fakebot):
    _make_topic(bot)
    g = bot.get_game(-100123)
    g.update({"active": True, "mode": "standard", "topic": "english", "emoji": "📘",
              "questions": [{"question": "apple", "answer": "olma", "alternatives": []}],
              "asked": 1, "current": {"question": "apple", "answer": "olma", "alternatives": []},
              "current_msg_id": 50, "scores": {}, "waiting": False, "current_reversed": False})
    q_msg = FMessage(text="q", chat=FChat())
    q_msg.message_id = 50
    update = FUpdate(111, "yong'oq", chat=FChat(), reply_to=q_msg)
    ctx.bot = fakebot
    await bot._check_answer(update, ctx)
    # Hali ham xuddi shu savol faol, keyingisiga o'tmagan
    assert g["current"]["answer"] == "olma"
    assert g["asked"] == 1
    assert "Noto'g'ri" in update.message.replies[-1]
    # To'g'ri javob berilsa endi ilgari surilishi kerak
    update2 = FUpdate(111, "olma", chat=FChat(), reply_to=q_msg)
    await bot._check_answer(update2, ctx)
    assert g["active"] is False  # bitta savol edi -> tugadi
    assert any("TO'G'RI" in r for r in update2.message.replies)


@pytest.mark.asyncio
async def test_speed_mode_wrong_answer_advances(bot, ctx, fakebot):
    _make_topic(bot)
    g = bot.get_game(-100124)
    q1 = {"question": "apple", "answer": "olma", "alternatives": []}
    q2 = {"question": "orange", "answer": "apelsin", "alternatives": []}
    g.update({"active": True, "mode": "speed", "topic": "english", "emoji": "📘",
              "questions": [q1, q2], "asked": 1, "current": q1,
              "current_msg_id": 60, "scores": {}, "waiting": False,
              "current_reversed": False, "time_limit": None})
    q_msg = FMessage(text="q", chat=FChat(chat_id=-100124))
    q_msg.message_id = 60
    update = FUpdate(111, "notogri javob", chat=FChat(chat_id=-100124), reply_to=q_msg)
    ctx.bot = fakebot
    await bot._check_answer(update, ctx)
    # Speed mode: xato bo'lsa ham keyingi savolga o'tishi kerak
    assert g["asked"] == 2
    assert g["current"] is q2
    assert any("XATO" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_lang_mode_reversed_accepts_original_word(bot, ctx, fakebot):
    g = bot.get_game(-100125)
    q1 = {"question": "orange", "answer": "apelsin", "alternatives": ["olovrang"]}
    g.update({"active": True, "mode": "lang", "topic": "english", "emoji": "📘",
              "questions": [q1], "asked": 1, "current": q1, "current_reversed": True,
              "current_msg_id": 70, "scores": {}, "waiting": False, "time_limit": None})
    q_msg = FMessage(text="q", chat=FChat(chat_id=-100125))
    q_msg.message_id = 70
    # Reversed holatda savol "apelsin?" ko'rsatilgan, to'g'ri javob "orange"
    update = FUpdate(111, "orange", chat=FChat(chat_id=-100125), reply_to=q_msg)
    ctx.bot = fakebot
    await bot._check_answer(update, ctx)
    assert any("TO'G'RI" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_send_question_lang_mode_can_reverse_direction(bot, ctx, fakebot, monkeypatch):
    g = bot.get_game(-100126)
    q1 = {"question": "apple", "answer": "olma", "alternatives": []}
    g.update({"active": True, "mode": "lang", "topic": "english", "emoji": "📘",
              "questions": [q1], "asked": 0, "current": None, "scores": {},
              "waiting": False, "time_limit": None})
    ctx.bot = fakebot
    monkeypatch.setattr(bot.random, "random", lambda: 0.1)  # < 0.5 -> reversed=True
    await bot.send_question(-100126, ctx)
    assert g["current_reversed"] is True
    assert "olma" in fakebot.sent[-1]["text"]  # reversed -> savol o'rniga javob ko'rsatiladi


@pytest.mark.asyncio
async def test_speed_mode_timeout_advances(bot, ctx, fakebot):
    g = bot.get_game(-100127)
    q1 = {"question": "apple", "answer": "olma", "alternatives": []}
    q2 = {"question": "orange", "answer": "apelsin", "alternatives": []}
    g.update({"active": True, "mode": "speed", "topic": "english", "emoji": "📘",
              "questions": [q1, q2], "asked": 1, "current": q1, "current_msg_id": 80,
              "scores": {}, "waiting": False, "current_reversed": False, "time_limit": 1})
    ctx.bot = fakebot
    await bot._speed_timeout(-100127, ctx, q1)
    assert g["asked"] == 2
    assert g["current"] is q2
    assert any("VAQT TUGADI" in s["text"] for s in fakebot.sent)


@pytest.mark.asyncio
async def test_speed_mode_timeout_noop_if_already_answered(bot, ctx, fakebot):
    g = bot.get_game(-100128)
    q1 = {"question": "apple", "answer": "olma", "alternatives": []}
    q2 = {"question": "orange", "answer": "apelsin", "alternatives": []}
    g.update({"active": True, "mode": "speed", "topic": "english", "emoji": "📘",
              "questions": [q1, q2], "asked": 2, "current": q2, "current_msg_id": 81,
              "scores": {}, "waiting": False, "current_reversed": False, "time_limit": 1})
    ctx.bot = fakebot
    before = len(fakebot.sent)
    # question_ref stale (q1), current allaqachon q2 -> hech narsa qilmasligi kerak
    await bot._speed_timeout(-100128, ctx, q1)
    assert len(fakebot.sent) == before
    assert g["current"] is q2


def test_parse_duration_units(bot):
    assert bot.parse_duration("30s") == 30
    assert bot.parse_duration("5d") == 300
    assert bot.parse_duration("2soat") == 7200
    assert bot.parse_duration("1kun") == 86400
    assert bot.parse_duration("3k") == 3 * 86400
    assert bot.parse_duration("abc") is None


# ══════════════════════════════════════════════════════
# 3) Admin mode (Module 1)
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_admin_mode_reply_checkmark_ranks_winner(bot, ctx, fakebot):
    chat = FChat(chat_id=-100200)
    g = bot.get_game(chat.id)
    g.update({"active": True, "mode": "admin", "admin_ranks": []})
    fakebot.chat_members[(chat.id, SUPERADMIN_ID)] = "creator"
    winner = FUser(555, username="Muslihiddin", first_name="Muslihiddin")
    win_msg = FMessage(text="mening javobim", chat=chat, user=winner)
    update = FUpdate(SUPERADMIN_ID, "✅", chat=chat, reply_to=win_msg)
    ctx.bot = fakebot
    handled = await bot.handle_admin_mode_message(update, ctx)
    assert handled is True
    assert g["admin_ranks"][0]["uid"] == 555
    assert any("Tabriklaymiz" in s["text"] for s in fakebot.sent)
    assert (chat.id, win_msg.message_id, "🎉") in fakebot.reactions


@pytest.mark.asyncio
async def test_admin_mode_checkmark_with_username(bot, ctx, fakebot):
    chat = FChat(chat_id=-100201)
    g = bot.get_game(chat.id)
    g.update({"active": True, "mode": "admin", "admin_ranks": []})
    bot.save_users({"777": {"username": "Muslihiddin", "first_name": "Muslihiddin", "tarif": "free"}})
    update = FUpdate(SUPERADMIN_ID, "✅ @Muslihiddin", chat=chat)
    ctx.bot = fakebot
    handled = await bot.handle_admin_mode_message(update, ctx)
    assert handled is True
    assert g["admin_ranks"][0]["uid"] == 777


@pytest.mark.asyncio
async def test_admin_mode_plain_checkmark_asks_for_reply(bot, ctx, fakebot):
    chat = FChat(chat_id=-100202)
    g = bot.get_game(chat.id)
    g.update({"active": True, "mode": "admin", "admin_ranks": []})
    update = FUpdate(SUPERADMIN_ID, "✅", chat=chat)
    ctx.bot = fakebot
    handled = await bot.handle_admin_mode_message(update, ctx)
    assert handled is True
    assert g["admin_ranks"] == []
    assert any("javob" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_admin_mode_duplicate_winner_rejected(bot, ctx, fakebot):
    chat = FChat(chat_id=-100203)
    g = bot.get_game(chat.id)
    g.update({"active": True, "mode": "admin",
              "admin_ranks": [{"uid": 555, "name": "X", "username": None}]})
    winner = FUser(555, first_name="X")
    win_msg = FMessage(text="a", chat=chat, user=winner)
    update = FUpdate(SUPERADMIN_ID, "✅", chat=chat, reply_to=win_msg)
    ctx.bot = fakebot
    await bot.handle_admin_mode_message(update, ctx)
    assert len(g["admin_ranks"]) == 1
    assert any("allaqachon" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_admin_mode_non_admin_checkmark_ignored(bot, ctx, fakebot):
    chat = FChat(chat_id=-100204)
    g = bot.get_game(chat.id)
    g.update({"active": True, "mode": "admin", "admin_ranks": []})
    fakebot.chat_members[(chat.id, 222)] = "member"
    winner = FUser(555, first_name="X")
    win_msg = FMessage(text="a", chat=chat, user=winner)
    update = FUpdate(222, "✅", chat=chat, reply_to=win_msg)
    ctx.bot = fakebot
    handled = await bot.handle_admin_mode_message(update, ctx)
    assert handled is False
    assert g["admin_ranks"] == []


@pytest.mark.asyncio
async def test_finish_admin_game_posts_to_group_when_no_linked_channel(bot, ctx, fakebot):
    chat_id = -100205
    g = bot.get_game(chat_id)
    g.update({"active": True, "mode": "admin",
              "admin_ranks": [{"uid": 1, "name": "Ali", "username": "ali_u"},
                               {"uid": 2, "name": "Vali", "username": None}]})
    ctx.bot = fakebot
    await bot._finish_admin_game(chat_id, ctx)
    assert g["active"] is False
    posted = [s for s in fakebot.sent if s["chat_id"] == chat_id]
    assert posted and "NATIJALAR" in posted[-1]["text"]
    assert "Ali" in posted[-1]["text"] and "Vali" in posted[-1]["text"]


@pytest.mark.asyncio
async def test_finish_admin_game_posts_to_linked_channel(bot, ctx, fakebot):
    chat_id = -100206
    fakebot.linked_chat_id[chat_id] = -100999
    g = bot.get_game(chat_id)
    g.update({"active": True, "mode": "admin",
              "admin_ranks": [{"uid": 1, "name": "Ali", "username": None}]})
    ctx.bot = fakebot
    await bot._finish_admin_game(chat_id, ctx)
    posted_channel = [s for s in fakebot.sent if s["chat_id"] == -100999]
    assert posted_channel


@pytest.mark.asyncio
async def test_newgame_admin_starts_admin_mode(bot, ctx, fakebot):
    chat = FChat(chat_id=-100207)
    fakebot.chat_members[(chat.id, SUPERADMIN_ID)] = "creator"
    ctx.bot = fakebot
    ctx.args = ["admin"]
    update = FUpdate(SUPERADMIN_ID, "/newgame admin", chat=chat)
    await bot.cmd_newgame(update, ctx)
    g = bot.get_game(chat.id)
    assert g["active"] is True
    assert g["mode"] == "admin"


# ══════════════════════════════════════════════════════
# 4) Profanity: sacred-name escalation & admin 🗿
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_normal_badword_regular_user_deleted_and_warned(bot, ctx, fakebot):
    bot.save_badwords({"words": ["yomonsoz"], "severe_words": [], "warnings": ["Iltimos!"],
                        "sacred_names": ["muhammad"]})
    chat = FChat(chat_id=-100300)
    fakebot.chat_members[(chat.id, fakebot.id)] = "administrator"
    fakebot.chat_members[(chat.id, 111)] = "member"
    fakebot.chat_admins[chat.id] = [(SUPERADMIN_ID, "creator")]
    update = FUpdate(111, "sen yomonsoz odamsan", chat=chat)
    ctx.bot = fakebot
    await bot.check_profanity(update, ctx)
    assert update.message.deleted is True
    assert not any(r[2] == "🗿" for r in fakebot.reactions)
    incidents = bot.load_incidents()
    assert len(incidents) == 1 and incidents[0]["sacred_name"] is False


@pytest.mark.asyncio
async def test_normal_badword_admin_user_gets_stone_reaction(bot, ctx, fakebot):
    bot.save_badwords({"words": ["yomonsoz"], "severe_words": [], "warnings": ["Iltimos!"],
                        "sacred_names": ["muhammad"]})
    chat = FChat(chat_id=-100301)
    fakebot.chat_members[(chat.id, fakebot.id)] = "administrator"
    fakebot.chat_members[(chat.id, 222)] = "administrator"
    update = FUpdate(222, "sen yomonsoz odamsan", chat=chat)
    ctx.bot = fakebot
    await bot.check_profanity(update, ctx)
    assert any(r[2] == "🗿" for r in fakebot.reactions)


@pytest.mark.asyncio
async def test_sacred_name_curse_escalates_even_for_admin(bot, ctx, fakebot):
    bot.save_badwords({"words": ["yomonsoz"], "severe_words": [], "warnings": ["Iltimos!"],
                        "sacred_names": ["muhammad"]})
    chat = FChat(chat_id=-100302)
    fakebot.chat_members[(chat.id, fakebot.id)] = "administrator"
    fakebot.chat_members[(chat.id, 333)] = "administrator"  # admin bo'lsa ham
    update = FUpdate(333, "Muhammad yomonsoz ekan", chat=chat)
    ctx.bot = fakebot
    await bot.check_profanity(update, ctx)
    assert update.message.deleted is True
    assert any(r[2] == "🤬" for r in fakebot.reactions)
    incidents = bot.load_incidents()
    assert incidents[-1]["sacred_name"] is True


@pytest.mark.asyncio
async def test_severe_badword_kicks_regular_user(bot, ctx, fakebot):
    bot.save_badwords({"words": [], "severe_words": ["ogirsoz"], "warnings": ["Ogohlantirish!"],
                        "sacred_names": []})
    chat = FChat(chat_id=-100303)
    fakebot.chat_members[(chat.id, fakebot.id)] = "administrator"
    fakebot.chat_members[(chat.id, 444)] = "member"
    update = FUpdate(444, "ogirsoz gap", chat=chat)
    ctx.bot = fakebot
    await bot.check_profanity(update, ctx)
    assert (chat.id, 444) in fakebot.banned
    assert (chat.id, 444) in fakebot.unbanned


@pytest.mark.asyncio
async def test_incident_dm_sent_to_creator_and_superadmin(bot, ctx, fakebot):
    bot.save_badwords({"words": ["yomonsoz"], "severe_words": [], "warnings": ["!"],
                        "sacred_names": []})
    chat = FChat(chat_id=-100304)
    fakebot.chat_members[(chat.id, fakebot.id)] = "administrator"
    fakebot.chat_admins[chat.id] = [(777, "creator")]
    update = FUpdate(111, "sen yomonsoz odamsan", chat=chat)
    ctx.bot = fakebot
    await bot.check_profanity(update, ctx)
    dm_targets = {c["chat_id"] for c in fakebot.sent if c["chat_id"] in (777, SUPERADMIN_ID)}
    assert 777 in dm_targets
    assert SUPERADMIN_ID in dm_targets
    # Har birida "Ko'rish" tugmasi bo'lishi kerak
    for s in fakebot.sent:
        if s["chat_id"] in (777, SUPERADMIN_ID) and "So'kinish aniqlandi" in (s["text"] or ""):
            assert s["reply_markup"].inline_keyboard[0][0].text == "🔍 Ko'rish"


@pytest.mark.asyncio
async def test_profview_callback_shows_incident_detail(bot, ctx, fakebot):
    chat = SimpleNamespace(id=-100305, title="Grp")
    offender = FUser(111, username="off", first_name="Off")
    iid = bot.record_incident(chat, offender, "Umumiy", "yomon gap", "O'chirildi", False, False)

    class FakeQ:
        def __init__(self):
            self.data = f"profview:{iid}"
            self.from_user = FUser(SUPERADMIN_ID)
            self.edits = []
        async def answer(self, *a, **kw): pass
        async def edit_message_text(self, text, **kw): self.edits.append(text)

    q = FakeQ()
    update = SimpleNamespace(callback_query=q)
    ctx.bot = fakebot
    await bot.callback_handler(update, ctx)
    assert q.edits and f"Hodisa #{iid}" in q.edits[0]
    assert "yomon gap" in q.edits[0]


# ══════════════════════════════════════════════════════
# 5) Dot-commands
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dot_ban_requires_reply(bot, ctx, fakebot):
    chat = FChat(chat_id=-100400)
    fakebot.chat_members[(chat.id, SUPERADMIN_ID)] = "creator"
    update = FUpdate(SUPERADMIN_ID, ".ban", chat=chat)
    ctx.bot = fakebot
    handled = await bot.handle_dot_command(update, ctx)
    assert handled is True
    assert not fakebot.banned
    assert any("javob" in r for r in update.message.replies)


@pytest.mark.asyncio
async def test_dot_ban_with_reply_bans_target(bot, ctx, fakebot):
    chat = FChat(chat_id=-100401)
    fakebot.chat_members[(chat.id, SUPERADMIN_ID)] = "creator"
    target = FUser(888, first_name="Bad")
    reply = FMessage(text="x", chat=chat, user=target)
    update = FUpdate(SUPERADMIN_ID, ".ban", chat=chat, reply_to=reply)
    ctx.bot = fakebot
    handled = await bot.handle_dot_command(update, ctx)
    assert handled is True
    assert (chat.id, 888) in fakebot.banned


@pytest.mark.asyncio
async def test_dot_commands_denied_for_regular_member(bot, ctx, fakebot):
    chat = FChat(chat_id=-100402)
    fakebot.chat_members[(chat.id, 555)] = "member"
    target = FUser(888, first_name="Bad")
    reply = FMessage(text="x", chat=chat, user=target)
    update = FUpdate(555, ".ban", chat=chat, reply_to=reply)
    ctx.bot = fakebot
    handled = await bot.handle_dot_command(update, ctx)
    assert handled is False
    assert not fakebot.banned


@pytest.mark.asyncio
async def test_dot_warn_three_times_autokicks(bot, ctx, fakebot):
    chat = FChat(chat_id=-100403)
    fakebot.chat_members[(chat.id, SUPERADMIN_ID)] = "creator"
    target = FUser(999, first_name="Naughty")
    for i in range(3):
        reply = FMessage(text="x", chat=chat, user=target)
        update = FUpdate(SUPERADMIN_ID, ".warn", chat=chat, reply_to=reply)
        ctx.bot = fakebot
        await bot.handle_dot_command(update, ctx)
    assert (chat.id, 999) in fakebot.banned
    assert (chat.id, 999) in fakebot.unbanned


@pytest.mark.asyncio
async def test_dot_mute_with_duration(bot, ctx, fakebot):
    chat = FChat(chat_id=-100404)
    fakebot.chat_members[(chat.id, SUPERADMIN_ID)] = "creator"
    target = FUser(111, first_name="M")
    reply = FMessage(text="x", chat=chat, user=target)
    update = FUpdate(SUPERADMIN_ID, ".mute 30s", chat=chat, reply_to=reply)
    ctx.bot = fakebot
    await bot.handle_dot_command(update, ctx)
    assert len(fakebot.restricted) == 1
    assert fakebot.restricted[0][3] is not None  # until_date berilgan


@pytest.mark.asyncio
async def test_dot_del_reply_deletes_single_message(bot, ctx, fakebot):
    chat = FChat(chat_id=-100405)
    fakebot.chat_members[(chat.id, SUPERADMIN_ID)] = "creator"
    reply = FMessage(text="oʻchirilsin", chat=chat)
    update = FUpdate(SUPERADMIN_ID, ".del", chat=chat, reply_to=reply)
    ctx.bot = fakebot
    await bot.handle_dot_command(update, ctx)
    assert reply.deleted is True
    assert update.message.deleted is True


@pytest.mark.asyncio
async def test_dot_del_username_deletes_all_their_tracked_messages(bot, ctx, fakebot):
    chat_id = -100406
    fakebot.chat_members[(chat_id, SUPERADMIN_ID)] = "creator"
    bot.track_msg(chat_id, 10, 321, "spammer", 0)
    bot.track_msg(chat_id, 11, 321, "spammer", 1)
    bot.track_msg(chat_id, 12, 654, "other", 2)
    update = FUpdate(SUPERADMIN_ID, ".del @spammer", chat=FChat(chat_id=chat_id))
    ctx.bot = fakebot
    await bot.handle_dot_command(update, ctx)
    assert (chat_id, 10) in fakebot.deleted
    assert (chat_id, 11) in fakebot.deleted
    assert (chat_id, 12) not in fakebot.deleted
    remaining_ids = {m["id"] for m in bot.msg_history.get(chat_id, [])}
    assert 10 not in remaining_ids and 11 not in remaining_ids and 12 in remaining_ids


@pytest.mark.asyncio
async def test_dot_del_at_a_clears_whole_history(bot, ctx, fakebot):
    chat_id = -100407
    fakebot.chat_members[(chat_id, SUPERADMIN_ID)] = "creator"
    bot.track_msg(chat_id, 20, 1, "a", 0)
    bot.track_msg(chat_id, 21, 2, "b", 1)
    update = FUpdate(SUPERADMIN_ID, ".del @ a", chat=FChat(chat_id=chat_id))
    ctx.bot = fakebot
    await bot.handle_dot_command(update, ctx)
    assert (chat_id, 20) in fakebot.deleted
    assert (chat_id, 21) in fakebot.deleted
    assert bot.msg_history.get(chat_id, []) == []


@pytest.mark.asyncio
async def test_dot_pin_and_unpin(bot, ctx, fakebot):
    chat = FChat(chat_id=-100408)
    fakebot.chat_members[(chat.id, SUPERADMIN_ID)] = "creator"
    reply = FMessage(text="x", chat=chat)
    update = FUpdate(SUPERADMIN_ID, ".pin", chat=chat, reply_to=reply)
    ctx.bot = fakebot
    await bot.handle_dot_command(update, ctx)
    assert (chat.id, reply.message_id) in fakebot.pinned

    update2 = FUpdate(SUPERADMIN_ID, ".unpin", chat=chat)
    await bot.handle_dot_command(update2, ctx)
    assert (chat.id, "ALL") in fakebot.unpinned


@pytest.mark.asyncio
async def test_dot_unknown_command_not_handled(bot, ctx, fakebot):
    chat = FChat(chat_id=-100409)
    update = FUpdate(SUPERADMIN_ID, ".notacommand", chat=chat)
    ctx.bot = fakebot
    handled = await bot.handle_dot_command(update, ctx)
    assert handled is False
