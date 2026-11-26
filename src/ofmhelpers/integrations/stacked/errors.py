# Copyright (C) 2026 jpkrajewski

from ofmhelpers.integrations.chat_source import ChatSourceError


class StackedError(ChatSourceError): ...


class StackedAuthError(StackedError): ...


class StackedForbiddenError(StackedError): ...
