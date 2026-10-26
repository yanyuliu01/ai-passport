"""Provider binding facts shared by account projections and helper adapters."""

DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"


def uses_deepseek(spec):
    # Only explicitly configured agents: never change the owner's other agents.
    return (spec.name == "deepseek" or
            spec.env.get("ANTHROPIC_BASE_URL", "").rstrip("/") == DEEPSEEK_BASE_URL)
