"""Clipboard read/write via pyperclip (already installed transitively through
pyautogui's dependency chain)."""
from __future__ import annotations

import logging

logger = logging.getLogger("assistant.clipboard")


def read_clipboard() -> str:
    try:
        import pyperclip

        text = pyperclip.paste()
        return f"Clipboard: {text}" if text else "The clipboard is empty."
    except Exception as exc:
        logger.warning("Clipboard read failed", exc_info=True)
        return f"Couldn't read the clipboard: {exc}"


def write_clipboard(text: str) -> str:
    try:
        import pyperclip

        pyperclip.copy(text)
        return "Copied to clipboard." if text else "Cleared the clipboard."
    except Exception as exc:
        logger.warning("Clipboard write failed", exc_info=True)
        return f"Couldn't write to the clipboard: {exc}"


def clear_clipboard() -> str:
    return write_clipboard("")
