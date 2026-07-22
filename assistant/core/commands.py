"""Registers every built-in command against a CommandRouter.

This is the direct replacement for the giant ``if/elif`` chain in the old
``main.py`` (see ``legacy_old/main.py``). Each command is a small function
decorated with ``@router.register(...)`` - to add a new capability, add a
function here (or in one of the ``assistant.automation``/``assistant.
integrations`` modules it calls into) and register it; nothing else in the
app needs to change.
"""
from __future__ import annotations

import re
from datetime import datetime

from assistant.core.router import CommandRouter
from assistant.integrations import music, web


def _strip_any(text: str, prefixes: tuple[str, ...]) -> str:
    """Remove the first matching prefix phrase (whichever is longest/found),
    returning whatever argument text remains."""
    low = text.lower()
    for prefix in sorted(prefixes, key=len, reverse=True):
        idx = low.find(prefix)
        if idx != -1:
            return text[idx + len(prefix):].strip(" :\"'")
    return text.strip()


def build_router() -> CommandRouter:
    router = CommandRouter()

    # -- greetings / meta -------------------------------------------------
    @router.register(
        "greeting", keywords=("hello", "hi there", "hey"),
        help="Say hello.",
    )
    def cmd_hello(text, ctx):
        return f"Hello! I'm {ctx.config.assistant_name}. Say 'help' to see everything I can do."

    @router.register(
        "bye", keywords=("bye", "goodbye", "go to sleep"),
        help="Says goodbye (does not close the app - use the window's close button for that).",
    )
    def cmd_bye(text, ctx):
        return "Goodbye! I'm still here whenever you need me."

    # -- time / date / system ----------------------------------------------
    @router.register(
        "time", keywords=("what is the time", "what's the time", "current time", "what time is it"),
        help="Tells the current time.",
    )
    def cmd_time(text, ctx):
        return f"It's {datetime.now():%H:%M:%S}."

    @router.register(
        "date", keywords=("what is the date", "today's date", "what's the date", "current date"),
        help="Tells today's date.",
    )
    def cmd_date(text, ctx):
        return f"Today is {datetime.now():%A, %B %d, %Y}."

    @router.register(
        "system specs", keywords=("system details", "system specs", "system specification"),
        help="Shows CPU/RAM/OS/battery details.",
    )
    def cmd_specs(text, ctx):
        return ctx.system_info.specs()

    @router.register(
        "storage", keywords=("storage details", "disk space", "drive space"),
        help="Shows disk usage per drive.",
    )
    def cmd_storage(text, ctx):
        return ctx.system_info.storage()

    # -- power / windows control --------------------------------------------
    @router.register(
        "shutdown pc", keywords=("shutdown pc", "shutdown my pc", "pc shutdown", "shut down the pc"),
        help="Shuts down the computer (asks for confirmation first).", destructive=True,
    )
    def cmd_shutdown(text, ctx):
        if ctx.config.require_confirmation_for_destructive and not ctx.confirm(
            "Shut down the computer now?"
        ):
            return "Shutdown cancelled."
        return ctx.windows.shutdown()

    @router.register(
        "restart pc", keywords=("restart pc", "restart my pc", "pc restart", "reboot the pc"),
        help="Restarts the computer (asks for confirmation first).", destructive=True,
    )
    def cmd_restart(text, ctx):
        if ctx.config.require_confirmation_for_destructive and not ctx.confirm(
            "Restart the computer now?"
        ):
            return "Restart cancelled."
        return ctx.windows.restart()

    @router.register(
        "cancel shutdown", keywords=("cancel shutdown", "abort shutdown"),
        help="Cancels a pending shutdown/restart.",
    )
    def cmd_cancel_shutdown(text, ctx):
        return ctx.windows.cancel_shutdown()

    @router.register(
        "lock pc", keywords=("lock pc", "lock workstation", "lock computer", "lock windows"),
        help="Locks the workstation.",
    )
    def cmd_lock(text, ctx):
        return ctx.windows.lock()

    @router.register(
        "minimize windows", keywords=("minimize all", "show desktop", "minimize windows"),
        help="Minimizes all open windows.",
    )
    def cmd_minimize(text, ctx):
        return ctx.windows.minimize_all()

    @router.register(
        "restore windows", keywords=("restore windows", "restore minimized"),
        help="Restores minimized windows.",
    )
    def cmd_restore(text, ctx):
        return ctx.windows.restore_minimized()

    @router.register(
        "screenshot", keywords=("screenshot", "take a screenshot", "screen shot"),
        help="Opens the Windows snipping tool.",
    )
    def cmd_screenshot(text, ctx):
        return ctx.windows.screenshot()

    @router.register(
        "open settings", keywords=("open settings", "windows settings"),
        help="Opens Windows Settings.",
    )
    def cmd_open_settings(text, ctx):
        return ctx.windows.open_settings()

    @router.register(
        "open search", keywords=("open windows search", "open search"),
        help="Opens Windows Search.",
    )
    def cmd_open_search(text, ctx):
        return ctx.windows.open_search()

    @router.register(
        "start menu", keywords=("show start", "start menu"),
        help="Opens the Start menu.",
    )
    def cmd_start_menu(text, ctx):
        return ctx.windows.show_start_menu()

    # -- chrome control -------------------------------------------------------
    @router.register("chrome new tab", keywords=("chrome new tab",), help="Opens a new Chrome tab.")
    def cmd_chrome_new_tab(text, ctx):
        return ctx.chrome.new_tab()

    @router.register("chrome close tab", keywords=("chrome close tab",), help="Closes the current Chrome tab.")
    def cmd_chrome_close_tab(text, ctx):
        return ctx.chrome.close_tab()

    @router.register("chrome new window", keywords=("chrome new window",), help="Opens a new Chrome window.")
    def cmd_chrome_new_window(text, ctx):
        return ctx.chrome.new_window()

    @router.register("chrome incognito", keywords=("chrome incognito",), help="Opens a Chrome incognito window.")
    def cmd_chrome_incognito(text, ctx):
        return ctx.chrome.incognito()

    @router.register("chrome history", keywords=("chrome history",), help="Opens Chrome history.")
    def cmd_chrome_history(text, ctx):
        return ctx.chrome.history()

    @router.register("chrome downloads", keywords=("chrome downloads",), help="Opens Chrome downloads.")
    def cmd_chrome_downloads(text, ctx):
        return ctx.chrome.downloads()

    @router.register("chrome bookmark", keywords=("chrome bookmark",), help="Bookmarks the current page.")
    def cmd_chrome_bookmark(text, ctx):
        return ctx.chrome.bookmark_page()

    @router.register(
        "chrome switch tab", pattern=r"chrome switch tab (?:to\s*)?(\d+)",
        help="Switches Chrome tab, e.g. 'chrome switch tab to 3'.",
    )
    def cmd_chrome_switch_tab(text, ctx):
        match = re.search(r"chrome switch tab (?:to\s*)?(\d+)", text, re.IGNORECASE)
        if not match:
            return "Which tab number?"
        return ctx.chrome.switch_to_tab(int(match.group(1)))

    @router.register(
        "open website", keywords=("open website", "browse to", "browse", "go to website"),
        help="Opens a website, e.g. 'open website github'.",
    )
    def cmd_open_website(text, ctx):
        name = _strip_any(text, ("open website", "browse to", "browse", "go to website"))
        return ctx.chrome.open_site(name)

    @router.register(
        "youtube control",
        pattern=r"youtube (play|pause|resume|full ?screen|theater|theatre|forward|skip|rewind|back|mute|unmute|next|previous|volume up|volume down)",
        help="Controls YouTube playback in the focused tab, e.g. 'youtube pause'.",
    )
    def cmd_youtube(text, ctx):
        match = re.search(
            r"youtube (play|pause|resume|full ?screen|theater|theatre|forward|skip|rewind|back|mute|unmute|next|previous|volume up|volume down)",
            text,
            re.IGNORECASE,
        )
        action = match.group(1).lower() if match else ""
        return ctx.youtube.control(action)

    # -- app launching ---------------------------------------------------------
    @router.register(
        "register app", pattern=r"register app (.+?) (?:at|as|to) (.+)",
        help="Registers a custom app path, e.g. 'register app spotify at C:\\...\\Spotify.exe'.",
    )
    def cmd_register_app(text, ctx):
        match = re.search(r"register app (.+?) (?:at|as|to) (.+)", text, re.IGNORECASE)
        if not match:
            return "Use: register app <name> at <path>"
        return ctx.apps.register(match.group(1), match.group(2))

    @router.register(
        "open app", keywords=("open",),
        help="Opens an application by name, e.g. 'open notepad' (customize paths in Settings).",
    )
    def cmd_open_app(text, ctx):
        name = _strip_any(text, ("open",))
        return ctx.apps.open(name)

    # -- files & directories -----------------------------------------------------
    @router.register(
        "list folder", keywords=("list files in", "list folder", "show folder"),
        help="Lists the contents of a folder, e.g. 'list folder C:\\Users\\me\\Documents'.",
    )
    def cmd_list_dir(text, ctx):
        path = _strip_any(text, ("list files in", "list folder", "show folder"))
        return ctx.files.list_dir(path)

    @router.register(
        "create folder", keywords=("create folder", "make folder", "new folder"),
        help="Creates a folder, e.g. 'create folder D:\\Projects\\demo'.",
    )
    def cmd_create_folder(text, ctx):
        path = _strip_any(text, ("create folder", "make folder", "new folder"))
        return ctx.files.create_folder(path)

    @router.register(
        "create file", keywords=("create file", "make file", "new file"),
        help="Creates an empty file, e.g. 'create file D:\\notes.txt'.",
    )
    def cmd_create_file(text, ctx):
        path = _strip_any(text, ("create file", "make file", "new file"))
        return ctx.files.create_file(path)

    @router.register(
        "open path", keywords=("open folder", "open path"),
        help="Opens a folder/file in Explorer, e.g. 'open folder D:\\Projects'.",
    )
    def cmd_open_path(text, ctx):
        path = _strip_any(text, ("open folder", "open path"))
        return ctx.files.open_path(path)

    @router.register(
        "rename path", pattern=r"rename (.+?) to (.+)",
        help="Renames a file/folder, e.g. 'rename old.txt to new.txt'.",
    )
    def cmd_rename(text, ctx):
        match = re.search(r"rename (.+?) to (.+)", text, re.IGNORECASE)
        if not match:
            return "Use: rename <path> to <new name>"
        return ctx.files.rename(match.group(1), match.group(2))

    @router.register(
        "move path", pattern=r"move (.+?) to (.+)",
        help="Moves a file/folder, e.g. 'move D:\\a.txt to D:\\backup\\a.txt'.",
    )
    def cmd_move(text, ctx):
        match = re.search(r"move (.+?) to (.+)", text, re.IGNORECASE)
        if not match:
            return "Use: move <source> to <destination>"
        return ctx.files.move(match.group(1), match.group(2))

    @router.register(
        "delete path", keywords=("delete file", "delete folder", "delete"),
        help="Deletes a file/folder to the Recycle Bin (asks for confirmation).", destructive=True,
    )
    def cmd_delete(text, ctx):
        path = _strip_any(text, ("delete file", "delete folder", "delete"))
        if ctx.config.require_confirmation_for_destructive and not ctx.confirm(f"Delete '{path}'?"):
            return "Delete cancelled."
        return ctx.files.delete(path)

    @router.register(
        "find files", pattern=r"find files (.+) in (.+)",
        help="Searches for files, e.g. 'find files *.pdf in D:\\Downloads'.",
    )
    def cmd_find_files(text, ctx):
        match = re.search(r"find files (.+) in (.+)", text, re.IGNORECASE)
        if not match:
            return "Use: find files <pattern> in <folder>"
        return ctx.files.search_files(match.group(2), match.group(1))

    # -- command line ---------------------------------------------------------------
    @router.register(
        "run command", keywords=("run command", "cmd ", "execute command"),
        help="Runs a whitelisted shell command, e.g. 'run command ipconfig'.",
    )
    def cmd_run_command(text, ctx):
        command = _strip_any(text, ("run command", "cmd ", "execute command"))
        return ctx.shell.run(command)

    @router.register(
        "force run command", keywords=("force run",),
        help="Runs any shell command, bypassing the allowlist.",
    )
    def cmd_force_run_command(text, ctx):
        command = _strip_any(text, ("force run",))
        return ctx.shell.run(command, force=True)

    # -- web / info ---------------------------------------------------------------------
    @router.register(
        "google search", keywords=("search google for", "google search", "search google"),
        help="Searches Google, e.g. 'search google for python tutorials'.",
    )
    def cmd_google_search(text, ctx):
        query = _strip_any(text, ("search google for", "google search", "search google"))
        return web.google_search(query)

    @router.register(
        "youtube search", keywords=("search youtube for", "youtube search for"),
        help="Searches YouTube, e.g. 'search youtube for lofi beats'.",
    )
    def cmd_youtube_search(text, ctx):
        query = _strip_any(text, ("search youtube for", "youtube search for"))
        return web.youtube_search(query)

    @router.register(
        "wikipedia", keywords=("wikipedia", "search wikipedia for"),
        help="Gets a Wikipedia summary, e.g. 'wikipedia black holes'.",
    )
    def cmd_wikipedia(text, ctx):
        query = _strip_any(text, ("search wikipedia for", "wikipedia"))
        return web.wikipedia_summary(query)

    @router.register(
        "weather", keywords=("weather in", "weather of", "weather for"),
        help="Gets the weather, e.g. 'weather in Lahore'.",
    )
    def cmd_weather(text, ctx):
        city = _strip_any(text, ("weather in", "weather of", "weather for")) or ctx.config.weather_city
        return web.weather(city)

    @router.register("my ip", keywords=("my ip",), help="Shows your public IP address.")
    def cmd_my_ip(text, ctx):
        return web.my_ip_and_location()

    @router.register("my location", keywords=("my location",), help="Shows your approximate location.")
    def cmd_my_location(text, ctx):
        return web.my_ip_and_location()

    @router.register(
        "joke", keywords=("tell me a joke", "programming joke", "chuck joke", "joke"),
        help="Tells a joke (say 'programming joke' or 'chuck joke' for a category).",
    )
    def cmd_joke(text, ctx):
        category = "chuck" if "chuck" in text.lower() else "neutral"
        return web.tell_joke(category)

    # -- music -------------------------------------------------------------------------------
    @router.register("play music", keywords=("play ",), help="Plays a song from your music folder, e.g. 'play imagine'.")
    def cmd_play_music(text, ctx):
        query = _strip_any(text, ("play",))
        return music.play_music(query, ctx.config.music_dir)

    # -- virtual mouse -------------------------------------------------------------------------
    @router.register(
        "start virtual mouse", keywords=("start virtual mouse", "enable virtual mouse", "virtual mouse on"),
        help="Starts hand-tracking mouse control using the webcam.",
    )
    def cmd_vm_start(text, ctx):
        return ctx.virtual_mouse.start()

    @router.register(
        "stop virtual mouse", keywords=("stop virtual mouse", "disable virtual mouse", "virtual mouse off"),
        help="Stops the virtual mouse.",
    )
    def cmd_vm_stop(text, ctx):
        return ctx.virtual_mouse.stop()

    return router
