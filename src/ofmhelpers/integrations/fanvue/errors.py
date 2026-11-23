# Copyright (C) 2026 jpkrajewski

from ofmhelpers.integrations.chat_source import ChatSourceError


class FanvueError(ChatSourceError): ...


class FanvueRefusedError(FanvueError): ...


class FanvueAuthError(FanvueError): ...
