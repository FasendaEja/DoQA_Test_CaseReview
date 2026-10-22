"""Length limits and the truncation marker."""

from typing import Optional

from doqa.client.config import DoqaConfig

#: Server column limits: a longer value rejects the whole package.
MAX_NAME_LENGTH = 255
MAX_EXTERNAL_ID_LENGTH = 255
MAX_STEP_TITLE_LENGTH = 500


def truncate(s: Optional[str], max_length: int) -> Optional[str]:
    if s is None or len(s) <= max_length:
        return s
    return s[:max_length] + "\n… truncated (" + str(len(s) - max_length) + " chars)"


def clip(s: Optional[str], max_length: int) -> Optional[str]:
    """Hard cut: the result never exceeds ``max_length``."""
    if s is None or len(s) <= max_length:
        return s
    return s[:max_length - 1] + "\u2026"


def max_message_length() -> int:
    config = _current_config()
    return config.max_message_length if config else DoqaConfig.DEFAULT_MAX_MESSAGE_LENGTH


def max_trace_length() -> int:
    config = _current_config()
    return config.max_trace_length if config else DoqaConfig.DEFAULT_MAX_TRACE_LENGTH


def max_parameter_length() -> int:
    config = _current_config()
    return config.max_parameter_length if config else DoqaConfig.DEFAULT_MAX_PARAMETER_LENGTH


def _current_config():
    from .session import DoqaSession
    return DoqaSession.current_config()
