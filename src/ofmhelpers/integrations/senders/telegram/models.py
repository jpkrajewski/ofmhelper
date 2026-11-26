# Copyright (C) 2026 jpkrajewski

from typing import Any

from pydantic import BaseModel

MAX_TEXT = 4096


class ResponseParameters(BaseModel):
    retry_after: int | None = None
    migrate_to_chat_id: int | None = None


class Reply(BaseModel):
    ok: bool
    result: Any = None
    description: str = ""
    error_code: int | None = None
    parameters: ResponseParameters | None = None


class Bot(BaseModel):
    id: int
    is_bot: bool
    username: str = ""
    first_name: str = ""


class Chat(BaseModel):
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
    date: int = 0


class ChatMemberUpdated(BaseModel):
    chat: Chat


class Update(BaseModel):
    update_id: int
    message: Message | None = None
    channel_post: Message | None = None
    my_chat_member: ChatMemberUpdated | None = None

    @property
    def chat(self) -> Chat | None:
        for found in (self.message, self.channel_post, self.my_chat_member):
            if found is not None:
                return found.chat
        return None
