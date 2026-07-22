"""Plays an animated GIF's frames inside a CTkLabel.

Frames are decoded to ``CTkImage`` objects once at construction time, so
the animation loop itself is just swapping pre-built images between
``after()`` callbacks - cheap enough to hold a steady frame rate without
competing with the rest of the UI thread. Each frame uses its own duration
from the GIF (rather than a fixed tick), so playback speed matches how the
GIF was authored.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import customtkinter as ctk

logger = logging.getLogger("assistant.ui.gif_player")


class AnimatedGifLabel(ctk.CTkLabel):
    def __init__(self, master, gif_path: Path | str, size: Optional[tuple[int, int]] = None, **kwargs):
        super().__init__(master, text="", **kwargs)
        self._frames: list[ctk.CTkImage] = []
        self._durations: list[int] = []
        self._index = 0
        self._job: Optional[str] = None
        self._load(Path(gif_path), size)

    def _load(self, gif_path: Path, size: Optional[tuple[int, int]]) -> None:
        try:
            from PIL import Image, ImageSequence

            with Image.open(gif_path) as im:
                for frame in ImageSequence.Iterator(im):
                    rgba = frame.convert("RGBA")
                    if size:
                        rgba = rgba.resize(size, Image.LANCZOS)
                    self._frames.append(
                        ctk.CTkImage(light_image=rgba, dark_image=rgba, size=size or rgba.size)
                    )
                    self._durations.append(max(20, frame.info.get("duration", 80)))
        except Exception:
            logger.warning("Could not load GIF %s", gif_path, exc_info=True)

    @property
    def available(self) -> bool:
        return bool(self._frames)

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def show_frame(self, index: int) -> int:
        """Displays frame ``index`` and returns its duration in ms (0 if unavailable)."""
        if not self._frames:
            return 0
        index = index % len(self._frames)
        self.configure(image=self._frames[index])
        return self._durations[index]

    def start(self) -> None:
        self.stop()
        if not self._frames:
            return
        self._index = 0
        self._tick()

    def _tick(self) -> None:
        duration = self.show_frame(self._index)
        self._index = (self._index + 1) % len(self._frames)
        self._job = self.after(duration, self._tick)

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
