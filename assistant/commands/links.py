"""Websites, WordPress sites and personal links.

This is the pack that makes the "just add a link to the JSON" promise real:
every entry in ``websites.json`` / ``wordpress.json`` / ``personal_links.json``
becomes a command the moment the file is saved and ``refresh`` runs. No code
change, no restart.

Each of the three files stays separate on purpose (brief §4.1) - they are
different intents. "open my portfolio" should never be ambiguous with a
client's site of a similar name, and hand-editing a five-line
``personal_links.json`` is far nicer than scrolling a combined blob.
"""
from __future__ import annotations

import re

from assistant.automation.browser import open_url, parse_open_modifiers
from assistant.core.router import CommandRouter, Reply, make_command
from assistant.store.store import ConfigStore

_DOMAIN_RE = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")


def _known_browsers(store: ConfigStore) -> dict[str, str]:
    known = store.value("core", "browsers.known", {}) or {}
    return {str(k).lower(): str(v) for k, v in known.items()}


def _default_browser(store: ConfigStore) -> str:
    return str(store.value("core", "browsers.default", "chrome") or "chrome")


def _open(store: ConfigStore, url: str, text: str) -> str:
    """Open ``url``, applying any "in a new Edge window" style modifiers."""
    known = _known_browsers(store)
    _, request = parse_open_modifiers(text, list(known))
    return open_url(
        url,
        browser=request.browser,
        new_window=request.new_window,
        incognito=request.incognito,
        known=known,
        default_browser=_default_browser(store),
    )


def _site_triggers(name: str, extra: list[str] | None = None, prefixes=("open", "go to", "launch")) -> list[str]:
    name = " ".join(str(name).split()).lower()
    triggers = [f"{prefix} {name}" for prefix in prefixes]
    triggers.extend(extra or [])
    return triggers


def _entries(store: ConfigStore) -> list[tuple[str, str, str, list[str], str]]:
    """Flatten all three link files into ``(kind, name, url, triggers, help)``."""
    out: list[tuple[str, str, str, list[str], str]] = []

    for site in store.items("websites", "sites"):
        name, url = site.get("name"), site.get("url")
        if not name or not url:
            continue
        out.append(("website", name, url, _site_triggers(name, site.get("trigger")), f"Opens {url}"))

    for site in store.items("wordpress", "sites"):
        name, url = site.get("name"), site.get("url")
        if not name or not url:
            continue
        triggers = _site_triggers(name, site.get("trigger"))
        triggers.append(f"open {name} admin")
        out.append(("wordpress", name, url, triggers, f"WordPress: opens {url}"))

    for link in store.items("personal_links", "links"):
        name, url = link.get("name"), link.get("url")
        if not name or not url:
            continue
        low = str(name).lower()
        triggers = _site_triggers(name, link.get("trigger"))
        triggers += [f"open my {low}", f"show my {low}", f"go to my {low}"]
        out.append(("personal", name, url, triggers, f"Your {low}: {url}"))

    return out


def register(router: CommandRouter, store: ConfigStore) -> None:
    # -- one command per configured link (rebuilt on every refresh) --------
    def provider():
        commands = []
        for kind, name, url, triggers, help_text in _entries(store):
            def handler(text, ctx, _url=url, _name=name):
                return f"{_open(ctx.store, _url, text)}"

            commands.append(
                make_command(
                    name=f"open {name}",
                    triggers=triggers,
                    handler=handler,
                    help=help_text,
                    category=f"links/{kind}",
                    payload={"url": url, "kind": kind},
                )
            )
        return commands

    router.add_provider(provider)

    # -- adding links is a one-liner (brief §4.3: only new *commands* need the wizard)
    @router.register(
        "add website",
        pattern=r"^\s*(?:add|save|remember)\s+(?:the\s+)?(?:website|site|link|url)\s+(.+?)\s+(?:as|=|->|to)\s+(.+?)\s*$",
        help="add website https://example.com as example  (or: add website example as https://example.com)",
        category="links/manage",
    )
    def cmd_add_website(text, ctx):
        match = re.search(
            r"(?:website|site|link|url)\s+(.+?)\s+(?:as|=|->|to)\s+(.+?)\s*$", text, re.IGNORECASE
        )
        left, right = match.group(1).strip(), match.group(2).strip()
        # Accept either order - "add site <url> as <name>" and the reverse.
        if _DOMAIN_RE.match(left) or "://" in left:
            url, name = left, right
        else:
            name, url = left, right
        if not (_DOMAIN_RE.match(url) or "://" in url):
            return f"'{url}' doesn't look like a URL. Try: add website github.com as github"
        ctx.store.append("websites", "sites", {"name": name.lower(), "url": url, "trigger": []})
        ctx.router.rebuild()
        return f"Saved. 'open {name.lower()}' now opens {url} - live right away, no restart."

    @router.register(
        "add wordpress site",
        pattern=r"^\s*add\s+wordpress\s+(?:site\s+)?(.+?)\s+(?:as|=|->|to)\s+(.+?)\s*$",
        help="add wordpress site clientsite as https://clientsite.com/wp-admin",
        category="links/manage",
    )
    def cmd_add_wordpress(text, ctx):
        match = re.search(r"add\s+wordpress\s+(?:site\s+)?(.+?)\s+(?:as|=|->|to)\s+(.+?)\s*$", text, re.IGNORECASE)
        name, url = match.group(1).strip().lower(), match.group(2).strip()
        ctx.store.append("wordpress", "sites", {"name": name, "url": url, "trigger": []})
        ctx.router.rebuild()
        return f"Saved WordPress site '{name}' -> {url}. Try 'open {name}'."

    @router.register(
        "add personal link",
        pattern=r"^\s*add\s+(?:my\s+)?(?:personal\s+)?link\s+(.+?)\s+(?:as|=|->|to)\s+(.+?)\s*$",
        help="add link portfolio as https://myportfolio.com",
        category="links/manage",
    )
    def cmd_add_personal(text, ctx):
        match = re.search(r"link\s+(.+?)\s+(?:as|=|->|to)\s+(.+?)\s*$", text, re.IGNORECASE)
        name, url = match.group(1).strip().lower(), match.group(2).strip()
        ctx.store.append("personal_links", "links", {"name": name, "url": url, "trigger": []})
        ctx.router.rebuild()
        return f"Saved. 'open my {name}' now opens {url}."

    @router.register(
        "list websites",
        keywords=("list websites", "list my sites", "what sites do you know", "show my links", "list links"),
        help="Lists every site and link you've registered.",
        category="links/manage",
        instant=True,
    )
    def cmd_list_sites(text, ctx):
        entries = _entries(ctx.store)
        if not entries:
            return "No sites registered yet. Try: add website github.com as github"
        buckets: dict[str, list[str]] = {}
        for kind, name, url, _triggers, _help in entries:
            buckets.setdefault(kind, []).append(f"  - {name}: {url}")
        lines = []
        for kind in ("website", "wordpress", "personal"):
            if kind in buckets:
                lines.append(f"[{kind}]")
                lines.extend(sorted(buckets[kind]))
        lines.append("\nSay 'open <name>', or add 'in a new window' / 'in edge' / 'in incognito'.")
        return "\n".join(lines)

    # -- typing a bare URL should just work --------------------------------
    @router.register(
        "open url",
        pattern=r"^\s*(?:open|go to|launch|browse)\s+((?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?)\s*(?:in .+)?$",
        help="open example.com  -  opens any address directly.",
        category="links",
    )
    def cmd_open_url(text, ctx):
        match = re.search(r"(?:open|go to|launch|browse)\s+((?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?)", text, re.IGNORECASE)
        return _open(ctx.store, match.group(1), text)

    @router.register(
        "set default browser",
        pattern=r"^\s*(?:set\s+)?default\s+browser\s+(?:to\s+)?(\w+)\s*$",
        help="default browser to edge  -  which browser 'in a new window' uses.",
        category="links/manage",
    )
    def cmd_default_browser(text, ctx):
        name = re.search(r"browser\s+(?:to\s+)?(\w+)", text, re.IGNORECASE).group(1).lower()
        known = _known_browsers(ctx.store)
        if name not in known:
            return f"I only know: {', '.join(sorted(known))}. Add it under browsers.known in config/core.json."
        ctx.store.set_value("core", "browsers.default", name)
        return Reply(f"Default browser is now {name.title()}.")
