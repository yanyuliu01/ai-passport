"""Shared WeChat transport failures without adapter dependency cycles."""


class WeixinError(Exception):
    pass


class WeixinTransportError(WeixinError):
    """Transport failure; a send may already have reached the server."""


class WeixinPausedError(WeixinError):
    """The upstream requested an account pause; do not infer logout."""
