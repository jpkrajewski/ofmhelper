"""The Bot API as `TelegramClient` uses it: on the fake, and on the real bot
(`OFM_TELEGRAM_BOT_TOKEN` + `OFM_TELEGRAM_CHAT_IDS` in .env; free calls, the
message one posts a line in the first chat)."""

import pytest

from ofmhelpers.config import settings
from ofmhelpers.integrations.senders.telegram import TelegramClient, TelegramError
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram

_FAKE_CHAT = "4242"
_WRONG_TOKEN = "123456789:AAWrongTokenWrongTokenWrongTokenWro"


@pytest.fixture
def token(backend) -> str:
    if backend == "fake":
        return TOKEN
    found = settings.telegram.bot_token
    if found is None:
        pytest.skip("no OFM_TELEGRAM_BOT_TOKEN in .env")
    return found.get_secret_value()


@pytest.fixture
def chat(backend) -> str:
    if backend == "fake":
        fake_telegram().wrote(int(_FAKE_CHAT))
        return _FAKE_CHAT
    chats = settings.telegram.chat_id_list()
    if not chats:
        pytest.skip("no OFM_TELEGRAM_CHAT_IDS in .env")
    return chats[0]


def test_the_token_names_its_bot(token):
    bot = TelegramClient(token).me()

    assert bot.is_bot
    assert bot.username


def test_a_wrong_token_is_refused_without_quoting_it(backend):
    with pytest.raises(TelegramError, match="getMe refused \\(401\\)") as raised:
        TelegramClient(_WRONG_TOKEN).me()

    assert _WRONG_TOKEN not in str(raised.value)


def test_a_message_reaches_a_chat(token, chat):
    sent = TelegramClient(token).send_message(chat, "Ascend: Telegram check")

    assert str(sent.chat.id) == chat
    assert sent.text == "Ascend: Telegram check"


def test_the_chats_that_wrote_can_be_listed(token):
    """getUpdates answers while no webhook is set: what finds a chat's id."""
    assert isinstance(TelegramClient(token).chats(), list)
