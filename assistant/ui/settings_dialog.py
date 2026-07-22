"""Settings dialog: edits and persists AppConfig."""
from __future__ import annotations

from tkinter import filedialog

import customtkinter as ctk


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, config, on_saved):
        super().__init__(master)
        self.config = config
        self.on_saved = on_saved

        self.title("Settings")
        self.geometry("520x560")
        self.resizable(False, False)
        self.grab_set()  # modal

        pad = {"padx": 16, "pady": (10, 0)}

        ctk.CTkLabel(self, text="Assistant name").pack(anchor="w", **pad)
        self.name_entry = ctk.CTkEntry(self)
        self.name_entry.insert(0, config.assistant_name)
        self.name_entry.pack(fill="x", padx=16)

        ctk.CTkLabel(self, text="Music folder").pack(anchor="w", **pad)
        music_row = ctk.CTkFrame(self, fg_color="transparent")
        music_row.pack(fill="x", padx=16)
        self.music_entry = ctk.CTkEntry(music_row)
        self.music_entry.insert(0, config.music_dir)
        self.music_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(music_row, text="Browse", width=80, command=self._browse_music).pack(
            side="left", padx=(8, 0)
        )

        ctk.CTkLabel(self, text="Default weather city").pack(anchor="w", **pad)
        self.city_entry = ctk.CTkEntry(self)
        self.city_entry.insert(0, config.weather_city)
        self.city_entry.pack(fill="x", padx=16)

        ctk.CTkLabel(self, text="Theme").pack(anchor="w", **pad)
        self.theme_menu = ctk.CTkOptionMenu(self, values=["dark", "light", "system"])
        self.theme_menu.set(config.theme)
        self.theme_menu.pack(fill="x", padx=16)

        switches_row = ctk.CTkFrame(self, fg_color="transparent")
        switches_row.pack(fill="x", padx=16, pady=(14, 0))
        self.voice_replies_var = ctk.BooleanVar(value=config.voice_replies_enabled)
        ctk.CTkSwitch(
            switches_row, text="Speak replies aloud", variable=self.voice_replies_var
        ).pack(anchor="w")
        self.voice_input_var = ctk.BooleanVar(value=config.voice_input_enabled)
        ctk.CTkSwitch(
            switches_row, text="Enable microphone input", variable=self.voice_input_var
        ).pack(anchor="w", pady=(6, 0))
        self.confirm_var = ctk.BooleanVar(value=config.require_confirmation_for_destructive)
        ctk.CTkSwitch(
            switches_row,
            text="Confirm before shutdown/restart/delete",
            variable=self.confirm_var,
        ).pack(anchor="w", pady=(6, 0))

        ctk.CTkLabel(self, text="Custom apps (one per line: name=path)").pack(anchor="w", **pad)
        self.apps_box = ctk.CTkTextbox(self, height=110)
        self.apps_box.insert("1.0", "\n".join(f"{k}={v}" for k, v in config.apps.items()))
        self.apps_box.pack(fill="x", padx=16)

        ctk.CTkLabel(self, text="Allowed shell commands (comma separated)").pack(anchor="w", **pad)
        self.allowlist_entry = ctk.CTkEntry(self)
        self.allowlist_entry.insert(0, ", ".join(config.shell_allowlist))
        self.allowlist_entry.pack(fill="x", padx=16)

        button_row = ctk.CTkFrame(self, fg_color="transparent")
        button_row.pack(fill="x", padx=16, pady=16)
        ctk.CTkButton(button_row, text="Save", command=self._save).pack(side="right")
        ctk.CTkButton(
            button_row, text="Cancel", fg_color="transparent", border_width=1, command=self.destroy
        ).pack(side="right", padx=(0, 8))

    def _browse_music(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.music_entry.get() or None)
        if folder:
            self.music_entry.delete(0, "end")
            self.music_entry.insert(0, folder)

    def _save(self) -> None:
        self.config.assistant_name = self.name_entry.get().strip() or self.config.assistant_name
        self.config.music_dir = self.music_entry.get().strip() or self.config.music_dir
        self.config.weather_city = self.city_entry.get().strip() or self.config.weather_city
        self.config.theme = self.theme_menu.get()
        self.config.voice_replies_enabled = self.voice_replies_var.get()
        self.config.voice_input_enabled = self.voice_input_var.get()
        self.config.require_confirmation_for_destructive = self.confirm_var.get()

        apps = {}
        for line in self.apps_box.get("1.0", "end").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            name, path = line.split("=", 1)
            if name.strip():
                apps[name.strip().lower()] = path.strip()
        if apps:
            self.config.apps = apps

        allowlist = [c.strip() for c in self.allowlist_entry.get().split(",") if c.strip()]
        if allowlist:
            self.config.shell_allowlist = allowlist

        self.config.save()
        if self.on_saved:
            self.on_saved()
        self.destroy()
