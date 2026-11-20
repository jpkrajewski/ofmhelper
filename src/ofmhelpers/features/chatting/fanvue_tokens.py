import time

from ofmhelpers.config import settings
from ofmhelpers.db.chats import store as chats
from ofmhelpers.db.chats.schema import ChatAccount, ChatPlatform
from ofmhelpers.integrations.fanvue import oauth
from ofmhelpers.integrations.fanvue.errors import FanvueAuthError


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
