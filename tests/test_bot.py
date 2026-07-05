"""
Tests for bot.py (single-file test suite: fixtures + tests together).

Section 0 — fixtures & fake Telegram object factories.
Section 1 — pure logic (tarif/limit/permission helpers, persistence).
Section 2 — regressions for the 4 bugs found & fixed on 2026-07-04:
    1. Regular (non-admin) users could not finish the /newtopic flow because
       the "newtopic_emoji" step handler lived after an admin-only gate.
    2. Regular users could not set access on a topic they own ("acc:" and
       "access_custom_input" were gated to admins only).
    3. Three superadmin-panel buttons (menu:newtopic_prompt,
       menu:addadmin_prompt, menu:badwords) had no matching handler at all
       and silently did nothing.
    4. Clicking "Tarif sotib olish" deleted the message *before* trying to
       send the Stars invoice, so a failed invoice left the user with no
       message and no feedback at all.

Run with:  pytest test_bot.py -v
Requires:  pip install -r requirements-dev.txt --break-system-packages
"""
import importlib
import sys
from unittest.mock import AsyncMock

import pytest

SUPERADMIN_ID = 999999


# ══════════════════════════════════════════════════════
# Section 0 — fixtures & fake Telegram object factories
# ══════════════════════════════════════════════════════

@pytest.fixture
def bot(tmp_path, monkeypatch):
    """Import a fresh copy of bot.py isolated in tmp_path."""
    monkeypatch.setenv("BOT_TOKEN", "TEST:TOKEN")
    monkeypatch.setenv("SUPERADMIN_ID", str(SUPERADMIN_ID))
    monkeypatch.chdir(tmp_path)

    sys.modules.pop("bot", None)
    sys.modules.pop("core", None)
    module = importlib.import_module("bot")

    # Never schedule real background export/backup work during tests.
    monkeypatch.setattr(module, "mark_changed", lambda: None)

    yield module

    sys.modules.pop("bot", None)
    sys.modules.pop("core", None)


class FakeUser:
    def __init__(self, uid, username=None, first_name="Test"):
        self.id = uid
        self.username = username
        self.first_name = first_name
        self.last_name = ""
        self.language_code = "uz"


class FakeChat:
    def __init__(self, chat_id=1, chat_type="private"):
        self.id = chat_id
        self.type = chat_type


class FakeMessage:
    def __init__(self, text=None, chat=None):
        self.text = text
        self.chat = chat or FakeChat()
        self.message_id = 1
        self.deleted = False
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return FakeMessage(text=text, chat=self.chat)

    async def delete(self):
        self.deleted = True


class FakeUpdate:
    """Mimics telegram.Update for handle_text-style handlers."""
    def __init__(self, uid, text, chat_type="private", username=None):
        self.effective_user = FakeUser(uid, username=username)
        self.effective_chat = FakeChat(chat_type=chat_type)
        self.message = FakeMessage(text=text, chat=self.effective_chat)


class FakeCallbackQuery:
    def __init__(self, uid, data, username=None, chat_id=1):
        self.from_user = FakeUser(uid, username=username)
        self.data = data
        self.message = FakeMessage(chat=FakeChat(chat_id=chat_id))
        self.answered = False
        self.edits = []
        self.last_markup = None

    async def answer(self, *a, **kw):
        self.answered = True

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append(text)
        self.last_markup = reply_markup


class FakeCallbackUpdate:
    def __init__(self, uid, data, username=None):
        self.callback_query = FakeCallbackQuery(uid, data, username=username)


class FakeContext:
    def __init__(self, bot=None):
        self.user_data = {}
        self.bot = bot


@pytest.fixture
def make_text_update():
    return FakeUpdate


@pytest.fixture
def make_callback_update():
    return FakeCallbackUpdate


@pytest.fixture
def make_context():
    return FakeContext


# ══════════════════════════════════════════════════════
# Section 1 — pure logic
# ══════════════════════════════════════════════════════

def test_free_user_default_topic_limit(bot):
    bot.save_user(111, {"tarif": bot.TARIF_FREE, "referral_count": 0})
    assert bot.get_user_topic_limit(111) == 1


def test_free_user_referral_bonus_caps_at_max(bot):
    bot.save_user(111, {"tarif": bot.TARIF_FREE, "referral_count": 999})
    assert bot.get_user_topic_limit(111) == bot.FREE_MAX_TOPIC_REFERRAL


def test_plus_user_topic_limit_from_tarif_table(bot):
    bot.save_user(111, {"tarif": bot.TARIF_PLUS, "referral_count": 0})
    assert bot.get_user_topic_limit(111) == bot.TARIF_TOPIC_LIMIT[bot.TARIF_PLUS]


def test_superadmin_has_unlimited_topics(bot):
    assert bot.get_user_topic_limit(bot.SUPERADMIN) == 9999


def test_expired_tarif_reverts_to_free(bot):
    bot.save_user(111, {
        "tarif": bot.TARIF_VIP,
        "tarif_expires": "2000-01-01T00:00:00+05:00",
        "referral_count": 0,
    })
    assert bot.get_user_tarif(111) == bot.TARIF_FREE
    # and it should have been persisted back to disk
    assert bot.get_user(111)["tarif"] == bot.TARIF_FREE


def test_admin_topic_limit_overrides_tarif(bot):
    bot.save_admins({"222": {"topic_limit": 7, "max_questions": 500}})
    assert bot.get_user_topic_limit(222) == 7


def test_parse_allowed_mixed_ids_and_usernames(bot):
    result = bot.parse_allowed("123456, @AkaJon  789")
    assert result == [123456, "@akajon", 789]


def test_parse_allowed_ignores_garbage(bot):
    assert bot.parse_allowed("not_a_number ,,, ") == []


def test_topic_save_and_load_roundtrip(bot):
    bot.save_topic({"name": "english", "emoji": "🇬🇧", "created_by": 5,
                     "access": {"type": "all", "allowed": []}, "questions": []})
    assert bot.topic_exists("english")
    loaded = bot.load_topic("english")
    assert loaded["emoji"] == "🇬🇧"
    assert loaded["created_by"] == 5


def test_can_manage_topic_owner_only_access(bot):
    topic = {"created_by": 5, "access": {"type": "owner"}}
    assert bot.can_manage_topic(topic, 5) is True
    assert bot.can_manage_topic(topic, 6) is False


def test_can_edit_topic_access_owner_and_superadmin(bot):
    topic = {"created_by": 5}
    assert bot.can_edit_topic_access(topic, 5) is True
    assert bot.can_edit_topic_access(topic, bot.SUPERADMIN) is True
    assert bot.can_edit_topic_access(topic, 42) is False


def test_is_admin_or_superadmin(bot):
    bot.save_admins({"321": {"topic_limit": 1, "max_questions": 10}})
    assert bot.is_admin_or_superadmin(321) is True
    assert bot.is_admin_or_superadmin(bot.SUPERADMIN) is True
    assert bot.is_admin_or_superadmin(4444) is False


# ══════════════════════════════════════════════════════
# Section 2 — regression tests for the fixed bugs
# ══════════════════════════════════════════════════════

REGULAR_USER = 555555   # not an admin, not the superadmin


@pytest.mark.asyncio
async def test_regular_user_can_complete_newtopic_flow(bot, make_text_update, make_context):
    """Bug #1: a plain Free-tarif user must be able to create a topic
    end-to-end via /newtopic -> name -> emoji, without being an admin."""
    ctx = make_context()
    ctx.user_data.update({"step": "newtopic_emoji", "topic_name": "math"})
    update = make_text_update(REGULAR_USER, "🔢")

    await bot.handle_text(update, ctx)

    assert bot.topic_exists("math")
    topic = bot.load_topic("math")
    assert topic["created_by"] == REGULAR_USER
    assert ctx.user_data.get("step") == "newtopic_access"


@pytest.mark.asyncio
async def test_regular_user_can_set_access_on_own_topic(bot, make_callback_update, make_context):
    """Bug #2: the topic owner (regular user) must be able to pick who can
    use their own topic via the 'acc:' callback."""
    bot.save_topic({"name": "math", "emoji": "🔢", "created_by": REGULAR_USER,
                     "access": {"type": "all", "allowed": []}, "questions": []})
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(REGULAR_USER, "acc:owner:math")

    await bot.callback_handler(update, ctx)

    topic = bot.load_topic("math")
    assert topic["access"]["type"] == "owner"


@pytest.mark.asyncio
async def test_non_owner_non_admin_cannot_set_access(bot, make_callback_update, make_context):
    """The permission check itself (can_edit_topic_access) must still be
    enforced — only the owner or superadmin can change access."""
    bot.save_topic({"name": "math", "emoji": "🔢", "created_by": REGULAR_USER,
                     "access": {"type": "all", "allowed": []}, "questions": []})
    ctx = make_context(bot=AsyncMock())
    intruder = 777777
    update = make_callback_update(intruder, "acc:owner:math")

    await bot.callback_handler(update, ctx)

    topic = bot.load_topic("math")
    assert topic["access"]["type"] == "all"  # unchanged


@pytest.mark.asyncio
async def test_regular_user_can_submit_custom_access_list(bot, make_text_update, make_context):
    """Bug #2 (continued): the free-text 'custom access' step must also work
    for the topic owner, not just admins."""
    bot.save_topic({"name": "math", "emoji": "🔢", "created_by": REGULAR_USER,
                     "access": {"type": "all", "allowed": []}, "questions": []})
    ctx = make_context()
    ctx.user_data.update({"step": "access_custom_input", "topic_name": "math"})
    update = make_text_update(REGULAR_USER, "@akajon 12345")

    await bot.handle_text(update, ctx)

    topic = bot.load_topic("math")
    assert topic["access"]["type"] == "custom"
    assert topic["access"]["allowed"] == ["@akajon", 12345]


@pytest.mark.asyncio
async def test_menu_newtopic_prompt_button_now_works(bot, make_callback_update, make_context):
    """Bug #3: the superadmin '➕ Yangi topic' button used to do nothing."""
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(bot.SUPERADMIN, "menu:newtopic_prompt")

    await bot.callback_handler(update, ctx)

    assert ctx.user_data.get("step") == "newtopic_name_prompt"
    assert update.callback_query.edits, "kutilgan javob yuborilmadi"


@pytest.mark.asyncio
async def test_menu_addadmin_prompt_button_now_works(bot, make_callback_update, make_context):
    """Bug #3: the superadmin '➕ Admin qo'shish' button used to do nothing."""
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(bot.SUPERADMIN, "menu:addadmin_prompt")

    await bot.callback_handler(update, ctx)

    assert ctx.user_data.get("step") == "addadmin_uid"
    assert update.callback_query.edits


@pytest.mark.asyncio
async def test_menu_badwords_button_now_works(bot, make_callback_update, make_context):
    """Bug #3: the superadmin '🔤 So'z filtri' button used to do nothing."""
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(bot.SUPERADMIN, "menu:badwords")

    await bot.callback_handler(update, ctx)

    assert update.callback_query.edits
    assert "So'z filtri" in update.callback_query.edits[-1]


@pytest.mark.asyncio
async def test_newtopic_name_prompt_rejects_duplicate(bot, make_text_update, make_context):
    bot.save_topic({"name": "math", "emoji": "🔢", "created_by": 1,
                     "access": {"type": "all", "allowed": []}, "questions": []})
    ctx = make_context()
    ctx.user_data["step"] = "newtopic_name_prompt"
    update = make_text_update(bot.SUPERADMIN, "math")

    await bot.handle_text(update, ctx)

    assert "allaqachon bor" in update.message.replies[-1]
    assert ctx.user_data.get("step") == "newtopic_name_prompt"  # unchanged, stays in this step


@pytest.mark.asyncio
async def test_buy_tarif_invoice_success_deletes_prompt(bot, make_callback_update, make_context, monkeypatch):
    """Bug #4 (happy path): once the invoice is actually sent, the old
    'choose a tarif' message should be cleaned up."""
    monkeypatch.setattr(bot, "_send_invoice", AsyncMock())
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(REGULAR_USER, f"buy:{bot.TARIF_PLUS}")

    await bot.callback_handler(update, ctx)

    assert update.callback_query.message.deleted is True


@pytest.mark.asyncio
async def test_buy_tarif_invoice_failure_keeps_message_and_reports_error(
        bot, make_callback_update, make_context, monkeypatch):
    """Bug #4: if sending the invoice fails, the user must NOT be left with
    the message silently deleted and no feedback."""
    monkeypatch.setattr(bot, "_send_invoice", AsyncMock(side_effect=RuntimeError("stars xato")))
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(REGULAR_USER, f"buy:{bot.TARIF_PLUS}")

    await bot.callback_handler(update, ctx)

    assert update.callback_query.message.deleted is False
    assert update.callback_query.message.replies, "xato haqida xabar berilmadi"


# ══════════════════════════════════════════════════════
# Section 3 — regressions for the empty-topic-list bug found on 2026-07-04
#   (the "➕ Yangi topic" button vanished entirely when the topic list was
#    empty — the exact situation on a fresh DB — for both admins and users;
#    and regular users never had a create-topic button anywhere at all)
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_superadmin_sees_create_button_when_topic_list_empty(
        bot, make_callback_update, make_context):
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(bot.SUPERADMIN, "menu:topics")

    await bot.callback_handler(update, ctx)

    kb = update.callback_query.edits  # we only track text, need markup too
    # inspect the markup that was actually passed
    assert update.callback_query.last_markup is not None
    flat = [btn.callback_data for row in update.callback_query.last_markup.inline_keyboard for btn in row]
    assert "menu:newtopic_prompt" in flat


@pytest.mark.asyncio
async def test_regular_user_sees_create_button_when_no_topics(
        bot, make_callback_update, make_context):
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(REGULAR_USER, "u:topics")

    await bot.callback_handler(update, ctx)

    flat = [btn.callback_data for row in update.callback_query.last_markup.inline_keyboard for btn in row]
    assert "u:newtopic" in flat


@pytest.mark.asyncio
async def test_regular_user_newtopic_button_starts_flow(
        bot, make_callback_update, make_context):
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(REGULAR_USER, "u:newtopic")

    await bot.callback_handler(update, ctx)

    assert ctx.user_data.get("step") == "newtopic_name_prompt"


@pytest.mark.asyncio
async def test_regular_user_newtopic_blocked_when_limit_reached(
        bot, make_callback_update, make_context):
    bot.save_user(REGULAR_USER, {"tarif": bot.TARIF_FREE, "referral_count": 0})
    bot.save_topic({"name": "already1", "emoji": "📘", "created_by": REGULAR_USER,
                     "access": {"type": "all", "allowed": []}, "questions": []})
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(REGULAR_USER, "u:newtopic")

    await bot.callback_handler(update, ctx)

    assert ctx.user_data.get("step") != "newtopic_name_prompt"
    assert "limit to'ldi" in update.callback_query.edits[-1]


@pytest.mark.asyncio
async def test_regular_user_can_type_name_via_newtopic_name_prompt(
        bot, make_text_update, make_context):
    ctx = make_context()
    ctx.user_data["step"] = "newtopic_name_prompt"
    update = make_text_update(REGULAR_USER, "geography")

    await bot.handle_text(update, ctx)

    assert ctx.user_data.get("step") == "newtopic_emoji"
    assert ctx.user_data.get("topic_name") == "geography"


# ══════════════════════════════════════════════════════
# Section 3 — yangi funksiyalar (bulk-qo'shish tugmasi,
# topic/savol yaratilgach ko'rinadigan tugmalar, admin reaksiyasi)
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bulkq_topic_button_enters_bulk_mode(bot, make_callback_update, make_context):
    """'📥 Ommaviy' tugmasi bosilganda bulkq_waiting bosqichiga o'tishi kerak."""
    bot.save_topic({"name": "english", "emoji": "🔤", "created_by": REGULAR_USER,
                     "access": {"type": "all", "allowed": []}, "questions": []})
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(REGULAR_USER, "bulkq_topic:english")

    await bot.callback_handler(update, ctx)

    assert ctx.user_data.get("step") == "bulkq_waiting"
    assert ctx.user_data.get("topic_name") == "english"


@pytest.mark.asyncio
async def test_bulk_add_parses_question_answer_synonyms_format(
        bot, make_text_update, make_context):
    """'orange - apelsin - olovrang - sabzirang' formatidagi qatorlar
    savol/javob/sinonimlar sifatida to'g'ri saqlanishi kerak."""
    bot.save_topic({"name": "english", "emoji": "🔤", "created_by": REGULAR_USER,
                     "access": {"type": "all", "allowed": []}, "questions": []})
    ctx = make_context()
    ctx.user_data.update({"step": "bulkq_waiting", "topic_name": "english"})
    update = make_text_update(
        REGULAR_USER, "orange - apelsin - olovrang - sabzirang\napple - olma")

    await bot._process_bulkq(update, ctx)

    topic = bot.load_topic("english")
    assert len(topic["questions"]) == 2
    q1 = topic["questions"][0]
    assert q1["question"] == "orange"
    assert q1["answer"] == "apelsin"
    assert q1["alternatives"] == ["olovrang", "sabzirang"]


@pytest.mark.asyncio
async def test_topic_creation_shows_after_action_buttons(
        bot, make_callback_update, make_context):
    """Topic yaratilib access tanlangach, 'Bosh menyu' va
    'Topicga savol qo'shish' tugmalari ko'rinishi kerak."""
    bot.save_topic({"name": "math", "emoji": "🔢", "created_by": REGULAR_USER,
                     "access": {"type": "all", "allowed": []}, "questions": []})
    ctx = make_context(bot=AsyncMock())
    ctx.user_data["step"] = "newtopic_access"
    update = make_callback_update(REGULAR_USER, "acc:owner:math")

    await bot.callback_handler(update, ctx)

    markup = update.callback_query.last_markup
    assert markup is not None
    flat_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert f"addq_topic:math" in flat_cbs
    assert "u:back" in flat_cbs
    assert ctx.user_data == {}


@pytest.mark.asyncio
async def test_admreact_saves_named_emoji_for_admin(bot, make_callback_update, make_context):
    """Superadmin ro'yxatdagi emojini tansa, admin yozuviga saqlanishi kerak."""
    bot.save_admins({str(REGULAR_USER): {"topic_limit": 1, "max_questions": 10}})
    ctx = make_context(bot=AsyncMock())
    update = make_callback_update(SUPERADMIN_ID, f"admreact:{REGULAR_USER}:🔥")

    await bot.callback_handler(update, ctx)

    adm = bot.load_admins()
    assert adm[str(REGULAR_USER)]["reaction_emoji"] == "🔥"


@pytest.mark.asyncio
async def test_admreact_custom_id_saved_via_text_step(bot, make_text_update, make_context):
    """'ID bilan' tanlab, raqamli custom_emoji_id yuborilsa saqlanishi kerak."""
    bot.save_admins({str(REGULAR_USER): {"topic_limit": 1, "max_questions": 10}})
    ctx = make_context()
    ctx.user_data.update({"step": "admreact_custom_wait", "ar_target": REGULAR_USER})
    update = make_text_update(SUPERADMIN_ID, "5368324170671202286")

    await bot.handle_text(update, ctx)

    adm = bot.load_admins()
    assert adm[str(REGULAR_USER)]["reaction_custom_emoji_id"] == "5368324170671202286"


@pytest.mark.asyncio
async def test_admreact_non_digit_id_rejected(bot, make_text_update, make_context):
    bot.save_admins({str(REGULAR_USER): {"topic_limit": 1, "max_questions": 10}})
    ctx = make_context()
    ctx.user_data.update({"step": "admreact_custom_wait", "ar_target": REGULAR_USER})
    update = make_text_update(SUPERADMIN_ID, "not-a-number")

    await bot.handle_text(update, ctx)

    adm = bot.load_admins()
    assert "reaction_custom_emoji_id" not in adm[str(REGULAR_USER)]


def test_reaction_pick_kb_builds_without_error(bot):
    kb = bot._reaction_pick_kb(REGULAR_USER, page=0)
    flat_cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert any(cb.startswith(f"admreact:{REGULAR_USER}:") for cb in flat_cbs)
    assert f"admreact_custom:{REGULAR_USER}" in flat_cbs
    assert f"admreact_skip:{REGULAR_USER}" in flat_cbs


@pytest.mark.asyncio
async def test_set_and_get_supergroup_id(bot):
    """Superadmin /setgroup orqali DB guruhni dinamik almashtira olishi kerak.
    CONTROL_GROUP_ID sozlanmagan holatda ham xotiradagi kesh to'g'ri
    ishlashi kerak (control guruhga yozish shunchaki sukut bilan
    o'tkazib yuboriladi)."""
    assert bot.get_supergroup_id() == 0
    await bot.set_supergroup_id(None, -1009999999999)
    assert bot.get_supergroup_id() == -1009999999999
    await bot.set_supergroup_id(None, None)
    assert bot.get_supergroup_id() == 0


@pytest.mark.asyncio
async def test_control_group_roundtrip(bot, monkeypatch):
    """CONTROL_GROUP_ID sozlangan bo'lsa, ma'lumot pin xabar orqali
    to'g'ri yozilishi/o'qilishi va Render disk tozalanishidan keyin ham
    (config.json'siz) tiklanishi kerak."""
    monkeypatch.setattr(bot.core, "CONTROL_GROUP_ID", -100555)

    class FakeControlBot:
        def __init__(self):
            self.pinned_text = None
            self.pinned_id = None

        async def get_chat(self, cid):
            from unittest.mock import MagicMock
            pm = None
            if self.pinned_text is not None:
                pm = MagicMock(text=self.pinned_text, message_id=self.pinned_id)
            return MagicMock(pinned_message=pm)

        async def send_message(self, chat_id, text):
            from unittest.mock import MagicMock
            self.pinned_text = text
            self.pinned_id = 42
            return MagicMock(message_id=42)

        async def pin_chat_message(self, chat_id, message_id, disable_notification=True):
            return True

        async def edit_message_text(self, chat_id, message_id, text):
            self.pinned_text = text
            return True

    fake = FakeControlBot()
    await bot.set_supergroup_id(fake, -100777)
    assert bot.get_supergroup_id() == -100777

    ctrl = await bot.load_control_data(fake)
    assert ctrl["supergroup_id"] == -100777

    # Xotiradagi keshni tozalab (Render disk/tuzilma tozalanishini
    # simulyatsiya qilamiz), faqat control guruhdan bootstrap qilamiz
    bot._SUPERGROUP_ID_CACHE = None
    ctrl2 = await bot.load_control_data(fake)
    assert ctrl2.get("supergroup_id") == -100777


def test_set_and_get_supergroup_id_old_name_removed(bot):
    """Eski sinxron set_supergroup_id endi mavjud emas (async bo'lgan)."""
    import inspect
    assert inspect.iscoroutinefunction(bot.set_supergroup_id)


# ══════════════════════════════════════════════════════
# Section 3 — savol qo'shish tanlovi va /addbadword interaktiv oqimi
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_after_action_kb_offers_single_and_bulk_choice(bot):
    """Topic yaratilgach chiqadigan tugmalarda ENDI ikkalasi ham
    bo'lishi kerak: bitta-bitta VA ommaviy (avval faqat bitta-bitta
    tugma bo'lib, ommaviy variant ko'rinmas edi)."""
    kb = bot._after_action_kb("english")
    flat = [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
    assert ("📝 Bitta-bitta", "addq_topic:english") in flat
    assert ("📥 Ommaviy", "bulkq_topic:english") in flat

    kb_user = bot._after_action_kb_user("english")
    flat_user = [(b.text, b.callback_data) for row in kb_user.inline_keyboard for b in row]
    assert ("📝 Bitta-bitta", "addq_topic:english") in flat_user
    assert ("📥 Ommaviy", "bulkq_topic:english") in flat_user


@pytest.mark.asyncio
async def test_addbadword_without_args_asks_instead_of_erroring(bot, make_context):
    """Argumentsiz /addbadword endi xato qaytarmasligi, so'z so'rashi kerak."""
    update = FakeUpdate(SUPERADMIN_ID, "/addbadword", chat_type="private")
    ctx = make_context()
    ctx.args = []

    await bot.cmd_addbadword(update, ctx)

    assert ctx.user_data.get("step") == "addbadword_waiting"
    assert update.message.replies
    assert "❌" not in update.message.replies[-1]


@pytest.mark.asyncio
async def test_addbadword_waiting_step_adds_word(bot, make_context):
    """addbadword_waiting bosqichida yuborilgan keyingi xabar so'z
    sifatida qo'shilishi kerak."""
    update = FakeUpdate(SUPERADMIN_ID, "yomonsoz", chat_type="private")
    ctx = make_context()
    ctx.user_data["step"] = "addbadword_waiting"

    await bot.handle_text(update, ctx)

    bw = bot.load_badwords()
    assert "yomonsoz" in bw["words"]
    assert ctx.user_data.get("step") is None

