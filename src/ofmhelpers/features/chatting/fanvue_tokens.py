"""Who connected Fanvue and their live tokens, for `FanvueSource`: the part of that
platform that needs the database."""

import time

from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.integrations.fanvue import oauth
from ofmhelpers.integrations.fanvue.errors import FanvueAuthError
from ofmhelpers.schemas.chatting import ChatAccount, ChatPlatform


class FanvueTokens:
    def accounts(self) -> list[ChatAccount]:
        return chats.accounts(ChatPlatform.FANVUE)

    def access_token(self, account_id: str) -> str:
        token = chats.fresh_token(
            ChatPlatform.FANVUE,
            account_id,
            oauth.refresh,
            valid_until=time.time() + settings.fanvue.token_refresh_margin_s,
        )
        if token is None:
            msg = "Connect Fanvue again"
            raise FanvueAuthError(msg)
        return token
