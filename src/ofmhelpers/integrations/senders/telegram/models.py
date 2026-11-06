"""The Bot API's answers, the parts this app reads (core.telegram.org/bots/api,
Bot API 10.3 checked 2026-11-06)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# sendMessage `text`: "1-4096 characters after entities parsing".
MAX_TEXT = 4096


class ResponseParameters(BaseModel):
    # Flood control: seconds left before the request may be repeated.
    retry_after: int | None = None
    # The group became a supergroup with this id.
    migrate_to_chat_id: int | None = None


class Reply(BaseModel):
    """Every answer: `ok`, then `result` or `description` (+ `error_code`)."""

    ok: bool
    result: Any = None
    description: str = ""
    error_code: int | None = None
    parameters: ResponseParameters | None = None


class Bot(BaseModel):
    """getMe: the bot the token belongs to."""

    id: int
    is_bot: bool
    username: str = ""
    first_name: str = ""


class Chat(BaseModel):
    """A private chat (someone pressed Start), a group or a channel."""

    id: int
    type: str
    title: str = ""
    username: str = ""
    first_name: str = ""

    @property
    def label(self) -> str:
        return self.title or self.username or self.first_name or str(self.id)


class Message(BaseModel):
    message_id: int
    chat: Chat
    text: str = ""


class ChatMemberUpdated(BaseModel):
    """The bot added to (or removed from) a chat."""

    chat: Chat


class Update(BaseModel):
    update_id: int
    message: Message | None = None
    channel_post: Message | None = None
    my_chat_member: ChatMemberUpdated | None = None

    @property
    def chat(self) -> Chat | None:
        """The chat this update came from: a message, a channel post, or the bot
        being added."""
        for found in (self.message, self.channel_post, self.my_chat_member):
            if found is not None:
                return found.chat
        return None
