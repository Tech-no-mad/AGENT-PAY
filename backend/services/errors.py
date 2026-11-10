"""Nonempty error descriptions for persisted execution diagnostics."""


def error_message(error: BaseException) -> str:
    message = str(error).strip()
    return message or f"{type(error).__name__}: no additional error message was supplied"
