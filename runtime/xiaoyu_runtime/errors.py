"""Shared failure contracts, independent of storage and execution adapters.

Original modules re-export these names so existing exception handlers retain
the same class identities and inheritance.
"""


class AgentError(Exception):
    """An agent did not return a result; the message can be shown to the owner."""


class EffectUncertain(AgentError):
    """A saved mutation needs reconciliation before it can be repeated."""


class CapabilityError(Exception):
    """A capability configuration or invocation is unavailable."""


class GatewayError(Exception):
    """A model gateway request could not complete."""
