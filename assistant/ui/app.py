"""Main CustomTkinter application window.

Threading model: background work (command dispatch, mic capture, the
virtual-mouse camera loop) never touches Tkinter widgets directly - Tkinter
is not thread-safe. Background threads only push events onto ``self._events``
(a plain ``queue.Queue``); a single ``after()``-scheduled poller draining
that queue on the main thread is the only code that touches widgets. This
is what keeps the UI responsive and avoids the frame-rate/reliability
problems in the legacy ``virtual mouse.py`` (which ran everything on one
blocking loop with no separation between capture and any UI).
"""
from __future__ import annotations

import logging
import queue
import threading
from tkinter import messagebox

import customtkinter as ctk

from assistant.core.commands import build_router
from assistant.core.context import Context
from assistant.ui.settings_dialog import SettingsDialog

logger = logging.getLogger("assistant.ui")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self, config):
        super().__init__()
        self.config_ = config
        ctk.set_appearance_mode(config.theme)

        self.title(f"{config.assistant_name} - Personal Assistant")
        self.geometry("980x640")
        self.minsize(820, 560)

        self._events: "queue.Queue[tuple]" = queue.Queue()
        self._vm_frame_lock = threading.Lock()
        self._vm_latest_frame = None
        self._mic_busy = False

        self.router = build_router()
        self.ctx = Context.build(
            config, on_vm_frame=self._push_vm_frame, on_vm_status=self._push_vm_status
        )
        self.ctx.confirm = self._confirm_blocking

        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._append_log(
            f"{config.assistant_name}: Hello! I'm online. Type a command or say 'help' to see "
            "everything I can do."
        )
        if not self.ctx.tts.available:
            self._append_log("(Text-to-speech is unavailable on this machine - replies are text-only.)")
        if not self.ctx.listener.available:
            self._append_log("(Microphone is unavailable - voice input is disabled, typed commands still work.)")

        self.after(200, self._poll_events)
        self._tick_status_bar()

    # ---------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_area()

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=190, corner_radius=0)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text=self.config_.assistant_name, font=ctk.CTkFont(size=20, weight="bold")
        ).pack(padx=16, pady=(20, 4), anchor="w")
        ctk.CTkLabel(
            sidebar, text="Automation Assistant", text_color="gray60", font=ctk.CTkFont(size=12)
        ).pack(padx=16, pady=(0, 20), anchor="w")

        actions = [
            ("Open Explorer", lambda: self._quick(lambda: self.ctx.apps.open("explorer"))),
            ("Open Chrome", lambda: self._quick(lambda: self.ctx.apps.open("chrome"))),
            ("Screenshot", lambda: self._quick(lambda: self.ctx.windows.screenshot())),
            ("Lock PC", lambda: self._quick(lambda: self.ctx.windows.lock())),
            ("System Specs", lambda: self._quick(lambda: self.ctx.system_info.specs())),
        ]
        for label, command in actions:
            ctk.CTkButton(sidebar, text=label, anchor="w", command=command).pack(
                padx=16, pady=6, fill="x"
            )

        ctk.CTkFrame(sidebar, height=2, fg_color="gray30").pack(fill="x", padx=16, pady=14)

        self.vm_toggle_btn = ctk.CTkButton(
            sidebar, text="Start Virtual Mouse", command=self._on_vm_toggle
        )
        self.vm_toggle_btn.pack(padx=16, pady=6, fill="x")

        self.vm_preview = ctk.CTkLabel(sidebar, text="Virtual mouse is off", height=140)
        self.vm_preview.pack(padx=16, pady=(0, 10), fill="x")

        ctk.CTkButton(
            sidebar, text="Help", fg_color="transparent", border_width=1,
            command=lambda: self._append_log(self.router.help_text()),
        ).pack(padx=16, pady=6, fill="x", side="bottom")
        ctk.CTkButton(
            sidebar, text="Settings", fg_color="transparent", border_width=1,
            command=self._open_settings,
        ).pack(padx=16, pady=6, fill="x", side="bottom")

    def _build_main_area(self) -> None:
        self.log_box = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(size=13))
        self.log_box.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=(16, 8))
        self.log_box.configure(state="disabled")

        input_row = ctk.CTkFrame(self, fg_color="transparent")
        input_row.grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=(0, 8))
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
        self.status_bar.grid(row=2, column=1, sticky="ew", padx=(0, 16), pady=(0, 10))

    # ------------------------------------------------------------- commands
    def _quick(self, action) -> None:
        threading.Thread(target=self._run_and_log, args=(action,), daemon=True).start()

    def _run_and_log(self, action) -> None:
        try:
            response = action()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Quick action failed")
            response = f"That action hit an error: {exc}"
        self._events.put(("log", f"{self.config_.assistant_name}: {response}"))
        if self.config_.voice_replies_enabled:
            self.ctx.tts.say(response)

    def _on_send(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._append_log(f"You: {text}")
        threading.Thread(target=self._dispatch, args=(text,), daemon=True).start()

    def _dispatch(self, text: str) -> None:
        response = self.router.dispatch(text, self.ctx)
        self._events.put(("log", f"{self.config_.assistant_name}: {response}"))
        if self.config_.voice_replies_enabled:
            self.ctx.tts.say(response)

    # ---------------------------------------------------------------- voice
    def _on_mic_toggle(self) -> None:
        if self._mic_busy or not self.ctx.listener.available:
            return
        self._mic_busy = True
        self.mic_btn.configure(text="...", state="disabled")
        threading.Thread(target=self._listen_and_dispatch, daemon=True).start()

    def _listen_and_dispatch(self) -> None:
        text = self.ctx.listener.listen_once()
        self._events.put(("mic_done", None))
        if not text:
            self._events.put(("log", f"{self.config_.assistant_name}: I didn't catch that."))
            return
        self._events.put(("log", f"You (voice): {text}"))
        self._dispatch(text)

    # --------------------------------------------------------- virtual mouse
    def _on_vm_toggle(self) -> None:
        threading.Thread(target=self._toggle_vm_worker, daemon=True).start()

    def _toggle_vm_worker(self) -> None:
        if self.ctx.virtual_mouse.running:
            response = self.ctx.virtual_mouse.stop()
        else:
            response = self.ctx.virtual_mouse.start()
        self._events.put(("log", f"{self.config_.assistant_name}: {response}"))
        self._events.put(("vm_state", self.ctx.virtual_mouse.running))

    def _push_vm_frame(self, frame) -> None:
        with self._vm_frame_lock:
            self._vm_latest_frame = frame

    def _push_vm_status(self, status: str) -> None:
        self._events.put(("status", status))

    # ------------------------------------------------------------ settings
    def _open_settings(self) -> None:
        SettingsDialog(self, self.config_, on_saved=self._on_settings_saved)

    def _on_settings_saved(self) -> None:
        ctk.set_appearance_mode(self.config_.theme)
        self._append_log(
            f"{self.config_.assistant_name}: Settings saved. Some voice/camera changes need a restart to fully apply."
        )

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
                elif kind == "vm_state":
                    running = event[1]
                    self.vm_toggle_btn.configure(
                        text="Stop Virtual Mouse" if running else "Start Virtual Mouse"
                    )
                    if not running:
                        self.vm_preview.configure(image=None, text="Virtual mouse is off")
                elif kind == "confirm":
                    _, prompt, result, ev = event
                    result["value"] = messagebox.askyesno("Please confirm", prompt, parent=self)
                    ev.set()
        except queue.Empty:
            pass

        if self.ctx.virtual_mouse.running:
            self._refresh_vm_preview()

        self.after(40, self._poll_events)

    def _refresh_vm_preview(self) -> None:
        with self._vm_frame_lock:
            frame = self._vm_latest_frame
            self._vm_latest_frame = None
        if frame is None:
            return
        try:
            from PIL import Image

            rgb = frame[:, :, ::-1]
            image = Image.fromarray(rgb)
            ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=(160, 120))
            self.vm_preview.configure(image=ctk_image, text="")
        except Exception:
            logger.exception("Failed to render virtual mouse preview frame")

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
            if self.ctx.virtual_mouse.running:
                self.ctx.virtual_mouse.stop()
            self.ctx.tts.stop()
            self.config_.save()
        finally:
            self.destroy()


def run(config) -> None:
    app = App(config)
    app.mainloop()
