import importlib, sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
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


class FakeQ:
    def __init__(self, uid, data):
        self.from_user = SimpleNamespace(id=uid, username=None)
        self.data = data
        self.message = SimpleNamespace(chat=SimpleNamespace(id=1))
        self.edits = []
        self.markups = []

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edits.append(text)
        self.markups.append(reply_markup)


class FakeMsg:
    def __init__(self, text=None):
        self.text = text
        self.replies = []
        self.reply_markups = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.replies.append(text)
        self.reply_markups.append(reply_markup)


class FakeUpdateCB:
    def __init__(self, uid, data):
        self.callback_query = FakeQ(uid, data)
        self.effective_user = SimpleNamespace(id=uid)


class FakeUpdateMsg:
    def __init__(self, uid, text):
        self.effective_user = SimpleNamespace(id=uid)
        self.effective_chat = SimpleNamespace(type="private", id=uid)
        self.message = FakeMsg(text)


@pytest.fixture
def ctx():
    c = SimpleNamespace()
    c.bot = SimpleNamespace(send_message=AsyncMock())
    c.args = []
    c.user_data = {}
    return c


def _all_buttons(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def _find_button(markup, text_substr):
    for row in markup.inline_keyboard:
        for b in row:
            if text_substr in b.text:
                return b
    return None


@pytest.fixture
def two_users(bot):
    bot.save_users({
        "111": {"first_name": "Ali", "username": "ali_u", "tarif": "free", "referral_count": 2},
        "222": {"first_name": "Vali", "username": None, "tarif": "vip", "referral_count": 0},
    })


def test_listusers_shows_every_user_with_username_and_tarif(bot, ctx, two_users):
    text, kb = bot._build_users_page(0)
    labels = _all_buttons(kb)
    assert any("Ali" in l and "@ali_u" in l for l in labels)
    assert any("Vali" in l and "222" in l for l in labels)  # no username -> falls back to uid


@pytest.mark.asyncio
async def test_cmd_listusers_sends_interactive_list(bot, ctx, two_users):
    update = FakeUpdateMsg(SUPERADMIN_ID, "/listusers")
    await bot.cmd_listusers(update, ctx)
    assert update.message.replies
    assert "Foydalanuvchilar" in update.message.replies[0]
    assert update.message.reply_markups[0] is not None


@pytest.mark.asyncio
async def test_user_detail_shows_chat_id_username_and_topics(bot, ctx, two_users):
    bot.save_topic({"name": "english", "emoji": "📘", "prize": None,
                     "created_by": 111, "access": {"type": "all", "allowed": []},
                     "questions": []})
    update = FakeUpdateCB(SUPERADMIN_ID, "user_detail:111:0")
    await bot.callback_handler(update, ctx)
    q = update.callback_query
    assert q.edits
    text = q.edits[0]
    assert "Chat ID: `111`" in text
    assert "ali" in text and "u" in text and "@" in text
    assert "english" in text
    labels = _all_buttons(q.markups[0])
    assert any("Tarif berish" in l for l in labels)
    assert any("Referral berish" in l for l in labels)


@pytest.mark.asyncio
async def test_grant_tarif_full_flow_sets_user_tarif(bot, ctx, two_users):
    update1 = FakeUpdateCB(SUPERADMIN_ID, "grant_tarif:111:0")
    await bot.callback_handler(update1, ctx)
    q1 = update1.callback_query
    vip_btn = _find_button(q1.markups[0], "VIP")
    assert vip_btn is not None

    update2 = FakeUpdateCB(SUPERADMIN_ID, vip_btn.callback_data)
    await bot.callback_handler(update2, ctx)
    q2 = update2.callback_query
    permanent_btn = _find_button(q2.markups[0], "Doimiy")
    assert permanent_btn is not None

    update3 = FakeUpdateCB(SUPERADMIN_ID, permanent_btn.callback_data)
    await bot.callback_handler(update3, ctx)
    users = bot.load_users()
    assert users["111"]["tarif"] == bot.TARIF_VIP
    assert users["111"]["tarif_expires"] is None
    assert bot.get_user_tarif(111) == bot.TARIF_VIP


@pytest.mark.asyncio
async def test_grant_tarif_with_duration_sets_expiry(bot, ctx, two_users):
    update = FakeUpdateCB(SUPERADMIN_ID, "gtd:111:premium:30:0")
    await bot.callback_handler(update, ctx)
    users = bot.load_users()
    assert users["111"]["tarif"] == "premium"
    assert users["111"]["tarif_expires"] is not None


@pytest.mark.asyncio
async def test_grant_referral_flow_adds_count(bot, ctx, two_users):
    update1 = FakeUpdateCB(SUPERADMIN_ID, "grant_ref:222:0")
    await bot.callback_handler(update1, ctx)
    assert ctx.user_data.get("step") == "grant_ref_waiting"
    assert ctx.user_data.get("gr_uid") == 222

    msg_update = FakeUpdateMsg(SUPERADMIN_ID, "5")
    await bot.handle_text(msg_update, ctx)
    users = bot.load_users()
    assert users["222"]["referral_count"] == 5
    assert "step" not in ctx.user_data


@pytest.mark.asyncio
async def test_grant_referral_can_subtract(bot, ctx, two_users):
    ctx.user_data.update({"step": "grant_ref_waiting", "gr_uid": 111, "gr_back": 0})
    msg_update = FakeUpdateMsg(SUPERADMIN_ID, "-1")
    await bot.handle_text(msg_update, ctx)
    users = bot.load_users()
    assert users["111"]["referral_count"] == 1  # 2 - 1


@pytest.mark.asyncio
async def test_grant_referral_never_goes_negative(bot, ctx, two_users):
    ctx.user_data.update({"step": "grant_ref_waiting", "gr_uid": 222, "gr_back": 0})
    msg_update = FakeUpdateMsg(SUPERADMIN_ID, "-100")
    await bot.handle_text(msg_update, ctx)
    users = bot.load_users()
    assert users["222"]["referral_count"] == 0


@pytest.mark.asyncio
async def test_non_superadmin_cannot_open_users_panel(bot, ctx, two_users):
    update = FakeUpdateCB(111, "userslist:0")
    await bot.callback_handler(update, ctx)
    assert update.callback_query.edits == []


@pytest.mark.asyncio
async def test_users_pagination(bot, ctx):
    bot.save_users({str(i): {"first_name": f"U{i}", "tarif": "free"} for i in range(20)})
    text, kb = bot._build_users_page(0)
    assert "20 ta" in text
    nav_labels = kb.inline_keyboard[-2]  # oxirgi qatordan oldingi -> nav tugmalari
    assert any(b.text == "➡️" for b in nav_labels)
