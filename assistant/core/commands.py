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

from assistant.automation import clipboard
from assistant.automation.apps import AppNotFoundError
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


#: Rough grouping for the help listing. First matching prefix wins, so the
#: 111 commands here stay browsable without annotating every registration.
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chrome", ("chrome",)),
    ("youtube", ("youtube",)),
    ("windows/power", ("shutdown", "restart", "lock", "cancel shutdown", "sleep")),
    ("windows/desktop", (
        "minimize", "maximize", "restore", "snap", "switch window", "task view",
        "virtual desktop", "active window", "close active", "screenshot", "capture",
        "run dialog", "action center", "clipboard history", "emoji", "recycle bin",
        "toggle theme",
    )),
    ("windows/volume", ("volume", "mute")),
    ("windows/settings", ("open display", "open sound", "open wifi", "open network",
                          "open airplane", "open bluetooth", "open night", "open power",
                          "open battery", "open apps", "open personalization",
                          "open update", "open storage settings", "open privacy",
                          "open accounts", "open notifications")),
    ("apps", ("open app", "register app", "list running", "is ", "close app")),
    ("files", ("folder", "file", "rename", "move", "copy", "delete", "find files",
               "compress", "zip", "extract", "unzip", "open downloads", "open desktop",
               "open documents", "open pictures", "open videos", "open music")),
    ("shell", ("run command", "force run")),
    ("clipboard", ("clipboard",)),
    ("system", ("time", "date", "system", "storage", "uptime", "network", "processes", "gpu")),
    ("web", ("search", "wikipedia", "weather", "my ip", "my location", "joke")),
    ("entertainment", ("play music",)),
    ("vision", ("gesture",)),
)


def _assign_categories(router: CommandRouter) -> None:
    for command in router.commands:
        if command.category != "general":
            continue
        name = command.name.lower()
        for category, prefixes in _CATEGORY_RULES:
            if any(name.startswith(p) or p in name for p in prefixes):
                command.category = category
                break
        else:
            command.category = "pc control"


def register_builtins(router: CommandRouter) -> CommandRouter:
    """Register the PC-automation pack onto an existing router."""
    return build_router(router)


def build_router(router: CommandRouter | None = None) -> CommandRouter:
    router = router if router is not None else CommandRouter()

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

    @router.register(
        "uptime", keywords=("system uptime", "uptime"),
        help="Shows how long the system has been running.",
    )
    def cmd_uptime(text, ctx):
        return ctx.system_info.uptime()

    @router.register(
        "network info", keywords=("network info", "my network", "network status"),
        help="Shows active network interfaces and local IPs.",
    )
    def cmd_network_info(text, ctx):
        return ctx.system_info.network_info()

    @router.register(
        "top processes", keywords=("top processes", "what's using memory", "biggest processes"),
        help="Lists the top processes by memory usage.",
    )
    def cmd_top_processes(text, ctx):
        return ctx.system_info.top_processes()

    @router.register(
        "gpu info", keywords=("gpu info", "graphics card"),
        help="Shows detected GPU(s).",
    )
    def cmd_gpu_info(text, ctx):
        return ctx.system_info.gpu_info()

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

    @router.register(
        "capture screenshot", keywords=("capture screenshot", "save screenshot", "full screenshot"),
        help="Captures the whole screen and saves it to Pictures\\Nova Screenshots.",
    )
    def cmd_capture_screenshot(text, ctx):
        return ctx.windows.capture_screenshot()

    @router.register(
        "empty recycle bin", keywords=("empty recycle bin", "empty the recycle bin"),
        help="Empties the Recycle Bin (asks for confirmation).", destructive=True,
    )
    def cmd_empty_recycle_bin(text, ctx):
        if ctx.config.require_confirmation_for_destructive and not ctx.confirm(
            "Empty the Recycle Bin? This can't be undone."
        ):
            return "Cancelled."
        return ctx.windows.empty_recycle_bin()

    @router.register(
        "toggle theme", keywords=("toggle theme", "toggle dark mode", "switch theme"),
        help="Switches Windows between light and dark mode.",
    )
    def cmd_toggle_theme(text, ctx):
        return ctx.windows.toggle_theme()

    @router.register(
        "active window", keywords=("active window", "what am i working on", "current window"),
        help="Shows the title of the currently focused window.",
    )
    def cmd_active_window(text, ctx):
        return ctx.windows.active_window()

    @router.register(
        "close active window", keywords=("close active window", "close this window"),
        help="Closes the currently focused window.",
    )
    def cmd_close_active_window(text, ctx):
        return ctx.windows.close_active_window()

    @router.register(
        "volume up", keywords=("volume up", "increase volume", "turn up the volume"),
        help="Turns the system volume up.",
    )
    def cmd_volume_up(text, ctx):
        return ctx.windows.volume_up()

    @router.register(
        "volume down", keywords=("volume down", "decrease volume", "turn down the volume"),
        help="Turns the system volume down.",
    )
    def cmd_volume_down(text, ctx):
        return ctx.windows.volume_down()

    @router.register(
        "mute volume", keywords=("mute volume", "unmute volume", "toggle mute"),
        help="Toggles system mute.",
    )
    def cmd_mute_volume(text, ctx):
        return ctx.windows.mute_toggle()

    @router.register(
        "current volume", keywords=("current volume", "what's the volume", "volume level"),
        help="Reports the current system volume percentage.",
    )
    def cmd_current_volume(text, ctx):
        return ctx.windows.get_volume()

    @router.register(
        "set volume", pattern=r"set volume to (\d{1,3})",
        help="Sets the system volume to a percentage, e.g. 'set volume to 50'.",
    )
    def cmd_set_volume(text, ctx):
        match = re.search(r"set volume to (\d{1,3})", text, re.IGNORECASE)
        if not match:
            return "Use: set volume to <0-100>"
        return ctx.windows.set_volume(int(match.group(1)))

    @router.register(
        "snap left", keywords=("snap window left", "snap left"),
        help="Snaps the active window to the left half of the screen.",
    )
    def cmd_snap_left(text, ctx):
        return ctx.windows.snap_left()

    @router.register(
        "snap right", keywords=("snap window right", "snap right"),
        help="Snaps the active window to the right half of the screen.",
    )
    def cmd_snap_right(text, ctx):
        return ctx.windows.snap_right()

    @router.register(
        "maximize window", keywords=("maximize window", "maximize active window"),
        help="Maximizes the active window.",
    )
    def cmd_maximize_window(text, ctx):
        return ctx.windows.maximize_active()

    @router.register(
        "minimize active window", keywords=("minimize active window", "minimize this window"),
        help="Minimizes just the active window (see also 'minimize windows' for all of them).",
    )
    def cmd_minimize_active_window(text, ctx):
        return ctx.windows.minimize_active()

    @router.register(
        "switch window", keywords=("switch window", "alt tab"),
        help="Switches to the next window (Alt+Tab).",
    )
    def cmd_switch_window(text, ctx):
        return ctx.windows.switch_window()

    @router.register(
        "task view", keywords=("task view",),
        help="Opens Task View.",
    )
    def cmd_task_view(text, ctx):
        return ctx.windows.task_view()

    @router.register(
        "new virtual desktop", keywords=("new virtual desktop", "create virtual desktop"),
        help="Creates a new virtual desktop.",
    )
    def cmd_new_virtual_desktop(text, ctx):
        return ctx.windows.new_virtual_desktop()

    @router.register(
        "close virtual desktop", keywords=("close virtual desktop",),
        help="Closes the current virtual desktop.",
    )
    def cmd_close_virtual_desktop(text, ctx):
        return ctx.windows.close_virtual_desktop()

    @router.register(
        "next virtual desktop", keywords=("next virtual desktop",),
        help="Switches to the next virtual desktop.",
    )
    def cmd_next_virtual_desktop(text, ctx):
        return ctx.windows.next_virtual_desktop()

    @router.register(
        "previous virtual desktop", keywords=("previous virtual desktop",),
        help="Switches to the previous virtual desktop.",
    )
    def cmd_previous_virtual_desktop(text, ctx):
        return ctx.windows.previous_virtual_desktop()

    @router.register(
        "open run dialog", keywords=("open run dialog", "open run"),
        help="Opens the Windows Run dialog.",
    )
    def cmd_open_run_dialog(text, ctx):
        return ctx.windows.open_run_dialog()

    @router.register(
        "open action center", keywords=("open action center",),
        help="Opens Action Center / Notifications.",
    )
    def cmd_open_action_center(text, ctx):
        return ctx.windows.open_action_center()

    @router.register(
        "open clipboard history", keywords=("open clipboard history", "clipboard history"),
        help="Opens Windows' native clipboard history panel.",
    )
    def cmd_open_clipboard_history(text, ctx):
        return ctx.windows.open_clipboard_history()

    @router.register(
        "open emoji panel", keywords=("open emoji panel", "emoji panel"),
        help="Opens the Windows emoji panel.",
    )
    def cmd_open_emoji_panel(text, ctx):
        return ctx.windows.open_emoji_panel()

    @router.register(
        "open settings page", pattern=r"open (.+) settings",
        help="Opens a specific Settings page, e.g. 'open display settings', 'open wifi settings'.",
    )
    def cmd_open_settings_page(text, ctx):
        match = re.search(r"open (.+) settings", text, re.IGNORECASE)
        if not match:
            return "Use: open <page> settings"
        return ctx.windows.open_settings_page(match.group(1))

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

    @router.register("chrome reopen tab", keywords=("chrome reopen tab", "chrome reopen closed tab"), help="Reopens the last closed Chrome tab.")
    def cmd_chrome_reopen_tab(text, ctx):
        return ctx.chrome.reopen_closed_tab()

    @router.register("chrome next tab", keywords=("chrome next tab",), help="Switches to the next Chrome tab.")
    def cmd_chrome_next_tab(text, ctx):
        return ctx.chrome.next_tab()

    @router.register("chrome previous tab", keywords=("chrome previous tab",), help="Switches to the previous Chrome tab.")
    def cmd_chrome_previous_tab(text, ctx):
        return ctx.chrome.previous_tab()

    @router.register("chrome close window", keywords=("chrome close window",), help="Closes the current Chrome window.")
    def cmd_chrome_close_window(text, ctx):
        return ctx.chrome.close_window()

    @router.register("chrome reload", keywords=("chrome reload", "chrome refresh"), help="Reloads the current page.")
    def cmd_chrome_reload(text, ctx):
        return ctx.chrome.reload()

    @router.register("chrome hard reload", keywords=("chrome hard reload",), help="Hard-reloads the page, bypassing cache.")
    def cmd_chrome_hard_reload(text, ctx):
        return ctx.chrome.hard_reload()

    @router.register("chrome zoom in", keywords=("chrome zoom in",), help="Zooms in on the page.")
    def cmd_chrome_zoom_in(text, ctx):
        return ctx.chrome.zoom_in()

    @router.register("chrome zoom out", keywords=("chrome zoom out",), help="Zooms out on the page.")
    def cmd_chrome_zoom_out(text, ctx):
        return ctx.chrome.zoom_out()

    @router.register("chrome reset zoom", keywords=("chrome reset zoom",), help="Resets page zoom to 100%.")
    def cmd_chrome_reset_zoom(text, ctx):
        return ctx.chrome.zoom_reset()

    @router.register("chrome print", keywords=("chrome print",), help="Opens the print dialog.")
    def cmd_chrome_print(text, ctx):
        return ctx.chrome.print_page()

    @router.register("chrome find", keywords=("chrome find",), help="Opens find-in-page.")
    def cmd_chrome_find(text, ctx):
        return ctx.chrome.find_in_page()

    @router.register("chrome address bar", keywords=("chrome address bar",), help="Focuses the address bar.")
    def cmd_chrome_address_bar(text, ctx):
        return ctx.chrome.focus_address_bar()

    @router.register("chrome view source", keywords=("chrome view source",), help="Opens the page source.")
    def cmd_chrome_view_source(text, ctx):
        return ctx.chrome.view_source()

    @router.register("chrome dev tools", keywords=("chrome dev tools", "chrome developer tools"), help="Opens Developer Tools.")
    def cmd_chrome_dev_tools(text, ctx):
        return ctx.chrome.dev_tools()

    @router.register("chrome full screen", keywords=("chrome full screen",), help="Toggles full screen.")
    def cmd_chrome_full_screen(text, ctx):
        return ctx.chrome.full_screen()

    @router.register("chrome clear browsing data", keywords=("chrome clear browsing data",), help="Opens clear browsing data.")
    def cmd_chrome_clear_data(text, ctx):
        return ctx.chrome.clear_browsing_data()

    @router.register("chrome task manager", keywords=("chrome task manager",), help="Opens the browser's task manager.")
    def cmd_chrome_task_manager(text, ctx):
        return ctx.chrome.browser_task_manager()

    @router.register("chrome bookmarks manager", keywords=("chrome bookmarks manager",), help="Opens the bookmarks manager.")
    def cmd_chrome_bookmarks_manager(text, ctx):
        return ctx.chrome.bookmarks_manager()

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
        pattern=r"youtube (play|pause|resume|full ?screen|theater|theatre|forward|skip|rewind|back|mute|unmute|next|previous|volume up|volume down|captions|subtitles|speed up|speed down|miniplayer|restart)",
        help="Controls YouTube playback in the focused tab, e.g. 'youtube pause', 'youtube captions', 'youtube speed up'.",
    )
    def cmd_youtube(text, ctx):
        match = re.search(
            r"youtube (play|pause|resume|full ?screen|theater|theatre|forward|skip|rewind|back|mute|unmute|next|previous|volume up|volume down|captions|subtitles|speed up|speed down|miniplayer|restart)",
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
        help="Opens an application by name, e.g. 'open notepad' (customize paths in Settings). "
        "Falls back to opening it as a website if it's not a known app (e.g. 'open youtube').",
    )
    def cmd_open_app(text, ctx):
        name = _strip_any(text, ("open",))
        try:
            return ctx.apps.open(name)
        except AppNotFoundError:
            # Not a registered app and not resolvable on PATH - a very
            # common reason someone says "open <name>" is a website
            # (open youtube, open github, ...), so try that before failing.
            return ctx.chrome.open_site(name)

    @router.register(
        "list running apps", keywords=("list running apps", "what's running", "running processes"),
        help="Lists currently running processes.",
    )
    def cmd_list_running(text, ctx):
        return ctx.apps.list_running()

    @router.register(
        "is app running", pattern=r"is (.+) running",
        help="Checks whether an app/process is running, e.g. 'is spotify running'.",
    )
    def cmd_is_running(text, ctx):
        match = re.search(r"is (.+) running", text, re.IGNORECASE)
        if not match:
            return "Use: is <name> running"
        return ctx.apps.is_running(match.group(1))

    @router.register(
        "close app", keywords=("close app", "quit app", "kill app"),
        help="Closes a running application by name, e.g. 'close app notepad'.",
    )
    def cmd_close_app(text, ctx):
        name = _strip_any(text, ("close app", "quit app", "kill app"))
        return ctx.apps.close(name)

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
        "open path", keywords=("open folder", "open directory", "open path"),
        help="Opens a folder by name or path, e.g. 'open folder office'.",
    )
    def cmd_open_path(text, ctx):
        from assistant.commands.tools import open_folder_by_name

        path = _strip_any(text, ("open folder", "open directory", "open path"))
        # A real path opens straight away; a bare name ("office") gets
        # searched for instead of the old "'office' does not exist".
        return open_folder_by_name(ctx, path)

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

    @router.register(
        "copy path", pattern=r"copy (.+?) to (.+)",
        help="Copies a file/folder, e.g. 'copy D:\\a.txt to D:\\backup\\a.txt'.",
    )
    def cmd_copy(text, ctx):
        match = re.search(r"copy (.+?) to (.+)", text, re.IGNORECASE)
        if not match:
            return "Use: copy <source> to <destination>"
        return ctx.files.copy(match.group(1), match.group(2))

    @router.register(
        "compress path", keywords=("compress folder", "compress file", "zip folder", "zip file"),
        help="Compresses a file/folder into a .zip, e.g. 'zip folder D:\\Projects\\demo'.",
    )
    def cmd_compress(text, ctx):
        path = _strip_any(text, ("compress folder", "compress file", "zip folder", "zip file"))
        return ctx.files.compress(path)

    @router.register(
        "extract archive", keywords=("extract archive", "unzip"),
        help="Extracts a .zip archive, e.g. 'unzip D:\\Projects\\demo.zip'.",
    )
    def cmd_extract(text, ctx):
        path = _strip_any(text, ("extract archive", "unzip"))
        return ctx.files.extract(path)

    @router.register(
        "file info", keywords=("file info", "file details"),
        help="Shows size/modified/created details for a file or folder.",
    )
    def cmd_file_info(text, ctx):
        path = _strip_any(text, ("file info", "file details"))
        return ctx.files.file_info(path)

    @router.register("open downloads", keywords=("open downloads",), help="Opens your Downloads folder.")
    def cmd_open_downloads(text, ctx):
        return ctx.files.quick_folder("downloads")

    @router.register(
        "open desktop folder",
        keywords=("open desktop folder", "open my desktop", "open desktop"),
        help="Opens your Desktop folder.",
    )
    def cmd_open_desktop(text, ctx):
        return ctx.files.quick_folder("desktop")

    @router.register(
        "open documents folder",
        keywords=("open documents folder", "open my documents", "open documents"),
        help="Opens your Documents folder.",
    )
    def cmd_open_documents(text, ctx):
        return ctx.files.quick_folder("documents")

    @router.register(
        "open pictures folder",
        keywords=("open pictures folder", "open pictures"),
        help="Opens your Pictures folder.",
    )
    def cmd_open_pictures(text, ctx):
        return ctx.files.quick_folder("pictures")

    @router.register(
        "open videos folder",
        keywords=("open videos folder", "open videos"),
        help="Opens your Videos folder.",
    )
    def cmd_open_videos(text, ctx):
        return ctx.files.quick_folder("videos")

    @router.register(
        "open music folder",
        keywords=("open music folder", "open music"),
        help="Opens your Music folder.",
    )
    def cmd_open_music_folder(text, ctx):
        return ctx.files.quick_folder("music")

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
        "read clipboard", keywords=("read clipboard", "what's on my clipboard", "show clipboard"),
        help="Shows the current clipboard contents.",
    )
    def cmd_read_clipboard(text, ctx):
        return clipboard.read_clipboard()

    @router.register(
        "copy to clipboard", keywords=("copy to clipboard",),
        help="Copies text to the clipboard, e.g. 'copy to clipboard hello world'.",
    )
    def cmd_write_clipboard(text, ctx):
        value = _strip_any(text, ("copy to clipboard",))
        return clipboard.write_clipboard(value)

    @router.register(
        "clear clipboard", keywords=("clear clipboard",),
        help="Clears the clipboard.",
    )
    def cmd_clear_clipboard(text, ctx):
        return clipboard.clear_clipboard()

    @router.register(
        "joke", keywords=("tell me a joke", "programming joke", "chuck joke", "joke"),
        help="Tells a joke (say 'programming joke' or 'chuck joke' for a category).",
    )
    def cmd_joke(text, ctx):
        category = "chuck" if "chuck" in text.lower() else "neutral"
        return web.tell_joke(category)

    # -- music -----------------------------------------------------------------------------
    # NOTE: "play <anything>" is handled by assistant/commands/media.py, which
    # searches every configured library (video and audio) and offers numbered
    # matches. This one stays for the explicit "play from my music folder"
    # phrasing only - a bare "play " keyword here used to swallow every play
    # command and send it to the Windows Music folder.
    @router.register(
        "play from music folder",
        keywords=("play from my music folder", "play from music folder"),
        help="Plays a song from the music folder configured in Settings.",
    )
    def cmd_play_music(text, ctx):
        query = _strip_any(text, ("play from my music folder", "play from music folder", "play"))
        return music.play_music(query, ctx.config.music_dir)

    # Hand tracking lives entirely in the gesture pack now - see
    # assistant/commands/vision.py. Cursor control was removed on purpose.

    _assign_categories(router)
    return router
