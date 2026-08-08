"""Main CustomTkinter application window.

This is the fallback window, kept reachable via ``--legacy-ui``. The Qt HUD
in ``assistant/ui/qt/`` is the real front-end.

Threading model: background work (command dispatch, mic capture) never
touches Tkinter widgets directly - Tkinter is not thread-safe. Background
threads only push events onto ``self._events`` (a plain ``queue.Queue``); a
single ``after()``-scheduled poller draining that queue on the main thread
is the only code that touches widgets.

Visuals: the sidebar's avatar and the startup splash reuse the animated
GIFs already sitting in the repo's ``GUI/`` folder from the original
prototype (``sophie body.gif``, ``voice.gif``, ``yy3.gif``, ``bg.jpg``) via
``assistant.ui.gif_player.AnimatedGifLabel``. If that folder isn't present
(e.g. a stripped-down deployment), everything degrades to a plain
text/color UI instead of failing.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from assistant.commands import register_all
from assistant.core.commands import register_builtins
from assistant.core.context import Context
from assistant.core.jobs import JobRunner
from assistant.core.router import CommandRouter
from assistant.core.speech import SpeechListener
from assistant.store.store import ConfigStore
from assistant.ui.gif_player import AnimatedGifLabel
from assistant.ui.settings_dialog import SettingsDialog

logger = logging.getLogger("assistant.ui")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

GUI_DIR = Path(__file__).resolve().parents[2] / "GUI"
AVATAR_SIZE = (150, 226)  # matches "sophie body.gif"'s ~2:3 aspect ratio
LISTENING_SIZE = (150, 150)


def _cover_resize(image, target_w: int, target_h: int):
    """Resize+crop an image to fill target_w x target_h without distortion."""
    from PIL import Image

    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = image.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


class App(ctk.CTk):
    def __init__(self, config):
        super().__init__()
        # NOTE: deliberately never withdraw()/deiconify() the root window.
        # An earlier version hid the root during startup and only
        # deiconified it *after* destroying the splash Toplevel - for a
        # moment zero top-level windows were mapped, and on some Tk/Windows
        # combinations that's enough for Tk to decide the application is
        # done and tear the whole GUI down (the process stays alive because
        # background threads keep running, but the window never comes
        # back). Keeping the root mapped continuously and layering the
        # splash on top of it avoids that entirely.
        self.config_ = config
        ctk.set_appearance_mode(config.theme)

        self.title(f"{config.assistant_name} - Personal Assistant")
        self.geometry("1060x700")
        self.minsize(900, 620)

        self._events: "queue.Queue[tuple]" = queue.Queue()
        self._mic_busy = False

        # Show the splash immediately, floating on top of the (still empty)
        # main window - building the router/context/layout below takes a
        # few seconds (TTS/mic init, decoding GIF frames), and without this
        # the user would stare at a blank window the whole time.
        splash, splash_gif = self._create_splash()

        # The config-driven packs (reports, links, projects, media, reminders)
        # are registered here too, so this fallback window can do everything
        # the Qt HUD can - it is a safety net, not a reduced build.
        self.store = ConfigStore()
        self.router = CommandRouter()
        register_builtins(self.router)
        register_all(self.router, self.store)
        self.jobs = JobRunner(workers=2)
        self._splash_tick(splash, splash_gif, 3)

        self.ctx = Context.build(config, self.store, self.router, self.jobs)
        self.ctx.listener = SpeechListener()
        self.ctx.confirm = self._confirm_blocking
        self._splash_tick(splash, splash_gif, 8)

        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._say("Hello! I'm online. Type a command or say 'help' to see everything I can do.")
        if not self.ctx.tts.available:
            self._append_log("(Text-to-speech is unavailable on this machine - replies are text-only.)")
        if not self.ctx.listener.available:
            self._append_log("(Microphone is unavailable - voice input is disabled, typed commands still work.)")

        self._finish_splash(splash, splash_gif)
        if self.avatar_idle.available:
            self.avatar_idle.start()

        self.lift()
        self.after(200, self._poll_events)
        self._tick_status_bar()

    # ---------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_banner()
        self._build_sidebar()
        self._build_main_area()

    def _build_banner(self) -> None:
        banner = ctk.CTkFrame(self, height=76, corner_radius=0)
        banner.grid(row=0, column=0, columnspan=2, sticky="ew")
        banner.grid_propagate(False)

        banner_image = self._load_banner_image(GUI_DIR / "bg.jpg", (1060, 76))
        if banner_image is not None:
            bg_label = ctk.CTkLabel(banner, image=banner_image, text="")
            bg_label.place(relwidth=1, relheight=1)

        ctk.CTkLabel(
            banner,
            text=f"{self.config_.assistant_name} — Personal Automation Assistant",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="white",
            fg_color="transparent",
        ).place(relx=0.03, rely=0.5, anchor="w")

    def _load_banner_image(self, path: Path, size: tuple[int, int]):
        if not path.exists():
            return None
        try:
            from PIL import Image, ImageEnhance

            with Image.open(path) as im:
                covered = _cover_resize(im.convert("RGB"), *size)
                darkened = ImageEnhance.Brightness(covered).enhance(0.55)
                return ctk.CTkImage(light_image=darkened, dark_image=darkened, size=size)
        except Exception:
            logger.warning("Could not load banner image %s", path, exc_info=True)
            return None

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0)
        sidebar.grid(row=1, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        avatar_container = ctk.CTkFrame(sidebar, height=236, fg_color="transparent")
        avatar_container.pack(padx=16, pady=(16, 4), fill="x")
        avatar_container.pack_propagate(False)

        self.avatar_idle = AnimatedGifLabel(avatar_container, GUI_DIR / "sophie body.gif", size=AVATAR_SIZE)
        self.avatar_listening = AnimatedGifLabel(avatar_container, GUI_DIR / "voice.gif", size=LISTENING_SIZE)
        if self.avatar_idle.available:
            self.avatar_idle.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.avatar_idle.configure(
                text=self.config_.assistant_name, font=ctk.CTkFont(size=18, weight="bold")
            )
            self.avatar_idle.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            sidebar, text="● online", text_color="#3ddc84", font=ctk.CTkFont(size=12)
        ).pack(pady=(0, 12))

        actions = [
            ("Open Explorer", lambda: self._quick(lambda: self.ctx.apps.open("explorer"))),
            ("Open Chrome", lambda: self._quick(lambda: self.ctx.apps.open("chrome"))),
            ("Screenshot", lambda: self._quick(lambda: self.ctx.windows.screenshot())),
            ("Lock PC", lambda: self._quick(lambda: self.ctx.windows.lock())),
            ("Mute / Unmute", lambda: self._quick(lambda: self.ctx.windows.mute_toggle())),
            ("Toggle Theme", lambda: self._quick(lambda: self.ctx.windows.toggle_theme())),
            ("System Specs", lambda: self._quick(lambda: self.ctx.system_info.specs())),
        ]
        for label, command in actions:
            ctk.CTkButton(sidebar, text=label, anchor="w", command=command).pack(
                padx=16, pady=4, fill="x"
            )

        ctk.CTkFrame(sidebar, height=2, fg_color="gray30").pack(fill="x", padx=16, pady=12)

        ctk.CTkButton(
            sidebar, text="Help", fg_color="transparent", border_width=1,
            command=lambda: self._append_log(self.router.help_text()),
        ).pack(padx=16, pady=4, fill="x", side="bottom")
        ctk.CTkButton(
            sidebar, text="Settings", fg_color="transparent", border_width=1,
            command=self._open_settings,
        ).pack(padx=16, pady=4, fill="x", side="bottom")

    def _build_main_area(self) -> None:
        self.log_box = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(size=13))
        self.log_box.grid(row=1, column=1, sticky="nsew", padx=(0, 16), pady=(16, 8))
        self.log_box.configure(state="disabled")

        input_row = ctk.CTkFrame(self, fg_color="transparent")
        input_row.grid(row=2, column=1, sticky="ew", padx=(0, 16), pady=(0, 8))
        input_row.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(input_row, placeholder_text="Type a command, e.g. 'open notepad'")
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self._on_send())

        self.mic_btn = ctk.CTkButton(input_row, text="Mic", width=60, command=self._on_mic_toggle)
        self.mic_btn.grid(row=0, column=1, padx=(0, 8))
        if not self.ctx.listener.available:
            self.mic_btn.configure(state="disabled")

        ctk.CTkButton(input_row, text="Send", width=80, command=self._on_send).grid(row=0, column=2)

        self.status_bar = ctk.CTkLabel(
            self, text="", anchor="w", text_color="gray60", font=ctk.CTkFont(size=11)
        )
        self.status_bar.grid(row=3, column=1, sticky="ew", padx=(0, 16), pady=(0, 10))

    # ---------------------------------------------------------------- splash
    def _create_splash(self):
        """Shows the startup splash immediately, before the slower
        router/context/layout construction below. Returns (splash, gif) -
        both None if the GIF asset isn't available."""
        loader_path = GUI_DIR / "yy3.gif"
        if not loader_path.exists():
            return None, None
        try:
            splash = ctk.CTkToplevel(self)
            splash.overrideredirect(True)
            splash.attributes("-topmost", True)
            width, height = 300, 300
            x = self.winfo_screenwidth() // 2 - width // 2
            y = self.winfo_screenheight() // 2 - height // 2
            splash.geometry(f"{width}x{height}+{x}+{y}")

            gif = AnimatedGifLabel(splash, loader_path, size=(width, height))
            gif.pack(expand=True, fill="both")
            if gif.available:
                gif.show_frame(0)
            splash.update()
            return splash, gif
        except Exception:
            logger.warning("Splash screen failed to start", exc_info=True)
            return None, None

    def _splash_tick(self, splash, gif, frame_index: int) -> None:
        """Advances the splash to a given frame as a lightweight progress
        cue between initialization steps (cheap - just one redraw)."""
        if splash is None or gif is None or not gif.available:
            return
        try:
            gif.show_frame(frame_index)
            splash.update()
        except Exception:
            logger.warning("Splash tick failed", exc_info=True)

    def _finish_splash(self, splash, gif) -> None:
        """Plays a short closing flourish, then tears the splash down."""
        if splash is not None and gif is not None and gif.available:
            try:
                frame_count = min(24, gif.frame_count)
                for i in range(frame_count):
                    duration = gif.show_frame(i)
                    splash.update()
                    time.sleep(duration / 1000)
            except Exception:
                logger.warning("Splash flourish failed", exc_info=True)
        if splash is not None:
            try:
                splash.destroy()
            except Exception:
                pass

    # ------------------------------------------------------------- commands
    def _say(self, text: str) -> None:
        """Main-thread only: appends to the log and speaks it (if enabled).

        Every assistant reply - the startup greeting, settings confirmations,
        and every command response - goes through either this or
        ``_emit_response`` (its background-thread-safe twin) so voice output
        never silently gets skipped for some messages but not others.
        """
        self._append_log(f"{self.config_.assistant_name}: {text}")
        if self.config_.voice_replies_enabled:
            self.ctx.tts.say(text)

    def _emit_response(self, response: str) -> None:
        """Background-thread safe: queues the log line and speaks it."""
        self._events.put(("log", f"{self.config_.assistant_name}: {response}"))
        if self.config_.voice_replies_enabled:
            self.ctx.tts.say(response)

    def _quick(self, action) -> None:
        threading.Thread(target=self._run_and_log, args=(action,), daemon=True).start()

    def _run_and_log(self, action) -> None:
        try:
            response = action()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Quick action failed")
            response = f"That action hit an error: {exc}"
        self._emit_response(response)

    def _on_send(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._append_log(f"You: {text}")
        threading.Thread(target=self._dispatch, args=(text,), daemon=True).start()

    def _dispatch(self, text: str) -> None:
        if self.ctx.conversation.active:
            self._emit_response(self.ctx.conversation.feed(text))
            return
        self._emit_response(self.router.dispatch(text, self.ctx).text)

    # ---------------------------------------------------------------- voice
    def _on_mic_toggle(self) -> None:
        if self._mic_busy or not self.ctx.listener.available:
            return
        self._mic_busy = True
        self.mic_btn.configure(text="...", state="disabled")
        self._show_listening_avatar()
        threading.Thread(target=self._listen_and_dispatch, daemon=True).start()

    def _listen_and_dispatch(self) -> None:
        text = self.ctx.listener.listen_once()
        self._events.put(("mic_done", None))
        if not text:
            self._emit_response("I didn't catch that.")
            return
        self._events.put(("log", f"You (voice): {text}"))
        self._dispatch(text)

    def _show_listening_avatar(self) -> None:
        self.avatar_idle.stop()
        self.avatar_idle.place_forget()
        if self.avatar_listening.available:
            self.avatar_listening.place(relx=0.5, rely=0.5, anchor="center")
            self.avatar_listening.start()

    def _show_idle_avatar(self) -> None:
        self.avatar_listening.stop()
        self.avatar_listening.place_forget()
        if self.avatar_idle.available:
            self.avatar_idle.place(relx=0.5, rely=0.5, anchor="center")
            self.avatar_idle.start()

    # ------------------------------------------------------------ settings
    def _open_settings(self) -> None:
        SettingsDialog(self, self.config_, on_saved=self._on_settings_saved)

    def _on_settings_saved(self) -> None:
        ctk.set_appearance_mode(self.config_.theme)
        self._say("Settings saved. Some voice/camera changes need a restart to fully apply.")

    # ------------------------------------------------------------- confirm
    def _confirm_blocking(self, prompt: str) -> bool:
        """Called from a background command thread; blocks that thread while
        the main thread shows a real Yes/No dialog, then returns the answer."""
        result: dict = {}
        event = threading.Event()
        self._events.put(("confirm", prompt, result, event))
        event.wait(30)
        return bool(result.get("value", False))

    # --------------------------------------------------------------- events
    def _poll_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._append_log(event[1])
                elif kind == "status":
                    self._append_log(f"[{event[1]}]")
                elif kind == "mic_done":
                    self._mic_busy = False
                    self.mic_btn.configure(text="Mic", state="normal")
                    self._show_idle_avatar()
                elif kind == "confirm":
                    _, prompt, result, ev = event
                    result["value"] = messagebox.askyesno("Please confirm", prompt, parent=self)
                    ev.set()
        except queue.Empty:
            pass

        self.after(40, self._poll_events)

    def _tick_status_bar(self) -> None:
        try:
            import datetime

            import psutil

            now = datetime.datetime.now().strftime("%H:%M:%S")
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            battery = psutil.sensors_battery()
            batt_text = f" | Battery {battery.percent:.0f}%" if battery else ""
            self.status_bar.configure(text=f"{now}  |  CPU {cpu:.0f}%  |  RAM {ram:.0f}%{batt_text}")
        except Exception:
            logger.exception("Status bar update failed")
        self.after(1000, self._tick_status_bar)

    # ---------------------------------------------------------------- misc
    def _append_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text.rstrip() + "\n")
        self.log_box.configure(state="disabled")
        self.log_box.see("end")

    def _on_close(self) -> None:
        try:
            self.ctx.tts.stop()
            self.config_.save()
        finally:
            self.destroy()


def run(config) -> None:
    app = App(config)
    app.mainloop()
