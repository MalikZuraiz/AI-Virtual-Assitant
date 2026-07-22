"""Local audio playback from a user-configured music folder."""
from __future__ import annotations

import os
from pathlib import Path

_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a")


def play_music(query: str, music_dir: str) -> str:
    base = Path(music_dir).expanduser()
    if not base.exists():
        return f"Your music folder '{base}' doesn't exist. Set it in Settings."

    songs = [f for f in os.listdir(base) if f.lower().endswith(_AUDIO_EXTS)]
    if not songs:
        return f"No audio files found in {base}."

    query = query.strip().lower()
    matches = [s for s in songs if query in s.lower()] if query else songs
    if not matches:
        return f"I couldn't find '{query}' in {base}."

    song = matches[0]
    os.startfile(base / song)  # noqa: S606
    return f"Playing {song}"
