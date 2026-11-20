from ofmhelpers.integrations.chat_source import ChatSourceError


class FanvueError(ChatSourceError): ...


class FanvueRefusedError(FanvueError): ...


class FanvueAuthError(FanvueError): ...
