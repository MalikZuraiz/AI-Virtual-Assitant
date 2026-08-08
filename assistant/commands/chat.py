"""Talking to the local model, and switching what it acts like.

Chat is opt-in by prefix: ``nova <anything>``. That is the whole routing
rule, and it exists so the assistant stays predictable - "open chrome" opens
Chrome, every time, and only "nova why is my pivot table slow" reaches the
model. Anything that matched no command *and* has no prefix still gets the
plain "I don't have a command for that" rather than a chatty guess.
"""
from __future__ import annotations

import re
import threading

from assistant.core.router import CommandRouter, Reply
from assistant.core.selection import Choice, offer
from assistant.store.store import ConfigStore


def _llm(ctx):
    return getattr(ctx, "llm", None)


def _streamed(ctx, llm, prompt: str) -> str:
    """Run a chat turn, showing the answer appear in the chat as it is written.

    The model produces roughly seven words a second, so waiting for a
    finished reply means half a minute of nothing. The answer-so-far is
    pushed into a live chat bubble instead, repainted a few times a second -
    often enough to read along, rarely enough not to thrash the widget.
    """
    import time

    buffer: list[str] = []
    last = [0.0]

    def _on_token(piece: str) -> None:
        buffer.append(piece)
        now = time.monotonic()
        if now - last[0] < 0.15:
            return
        last[0] = now
        ctx.stream("".join(buffer))

    ctx.progress(f"Thinking ({llm.model}, {llm.mode} mode)...")
    return llm.chat(prompt, on_token=_on_token)


def register(router: CommandRouter, store: ConfigStore) -> None:
    prefix = str(store.value("core", "llm.prefix", "nova") or "nova").lower()

    @router.register(
        "chat",
        pattern=rf"^\s*(?:{re.escape(prefix)}|hey\s+{re.escape(prefix)})\b[,:]?\s+(.+)$",
        help=f"{prefix} <anything>  -  talk to the local model in the current mode.",
        category="chat",
        priority=6,
    )
    def cmd_chat(text, ctx):
        prompt = re.search(
            rf"^\s*(?:{re.escape(prefix)}|hey\s+{re.escape(prefix)})\b[,:]?\s+(.+)$",
            text,
            re.IGNORECASE | re.DOTALL,
        ).group(1).strip()

        llm = _llm(ctx)
        if llm is None:
            return "Chat isn't wired up on this build."

        # "nova office mode" should switch, not be answered as a question.
        switch = re.fullmatch(r"(?:switch to |go |use )?([\w ]+?)\s*mode", prompt, re.IGNORECASE)
        if switch:
            return _switch_mode(ctx, switch.group(1))

        return Reply(_streamed(ctx, llm, prompt), speak=True)

    @router.register(
        "ask",
        # Requires a real question after it, so "chat status" and "chat modes"
        # still reach their own commands rather than being sent to the model.
        pattern=r"^\s*ask\s*:?\s+(?!status\b)(.+)$",
        help="ask why is my pivot table slow  -  same as the prefix, different word.",
        category="chat",
        priority=6,
    )
    def cmd_ask(text, ctx):
        prompt = re.search(r"^\s*ask\s*:?\s+(.+)$", text, re.IGNORECASE | re.DOTALL).group(1).strip()
        llm = _llm(ctx)
        if llm is None:
            return "Chat isn't wired up on this build."
        return Reply(_streamed(ctx, llm, prompt), speak=True)

    @router.register(
        "chat mode",
        pattern=r"^\s*(?:switch to\s+|go\s+|set\s+|enable\s+)?([\w ]+?)\s+mode\s*$",
        help="office mode / fun mode / islamic mode / podcast mode  -  switches how chat replies.",
        category="chat",
        instant=True,
    )
    def cmd_mode(text, ctx):
        name = re.search(
            r"^\s*(?:switch to\s+|go\s+|set\s+|enable\s+)?([\w ]+?)\s+mode\s*$", text, re.IGNORECASE
        ).group(1)
        return _switch_mode(ctx, name)

    @router.register(
        "list chat modes",
        keywords=("list modes", "what modes", "chat modes", "show modes", "which modes"),
        help="Lists every chat persona you can switch to.",
        category="chat",
        instant=True,
    )
    def cmd_list_modes(text, ctx):
        llm = _llm(ctx)
        if llm is None or not llm.personas:
            return "No chat modes configured - see config/personas.json."

        def _use(choice, inner_ctx) -> str:
            return _switch_mode(inner_ctx, choice.payload)

        choices = [
            Choice(
                f"{name}{'   ← current' if name == llm.mode else ''}",
                name,
                llm.personas[name].description,
            )
            for name in llm.mode_names()
        ]
        return offer(
            ctx,
            f"{len(choices)} chat modes (currently '{llm.mode}'):",
            choices,
            actions={"use": _use, "open": _use, "run": _use},
            default_action="use",
            hint=f"Say a number to switch, or '<name> mode'. Then talk with '{llm.prefix} <message>'.",
        )

    @router.register(
        "chat status",
        keywords=("chat status", "is chat working", "ollama status", "llm status", "check ollama"),
        help="Explains exactly why local chat is or isn't working.",
        category="chat",
    )
    def cmd_chat_status(text, ctx):
        llm = _llm(ctx)
        if llm is None:
            return "Chat isn't wired up on this build."
        return llm.diagnose()

    @router.register(
        "enable chat",
        keywords=("enable chat", "turn on chat", "enable ollama", "turn on ollama", "enable llm"),
        help="Switches on the local model and checks it can be reached.",
        category="chat",
    )
    def cmd_enable_chat(text, ctx):
        llm = _llm(ctx)
        if llm is None:
            return "Chat isn't wired up on this build."
        ctx.store.set_value("core", "llm.enabled", True)
        llm.enabled = True
        note = llm.diagnose()
        # Load the weights now rather than making the first real question
        # pay the 3-6 second cold start.
        threading.Thread(target=llm.warm, name="llm-warm", daemon=True).start()
        return f"Local chat enabled.\n{note}\nWarming the model so the first answer isn't slow."

    @router.register(
        "reset chat",
        keywords=("reset chat", "forget the conversation", "clear chat memory", "new conversation"),
        help="Clears the model's short-term memory of this conversation.",
        category="chat",
        instant=True,
    )
    def cmd_reset(text, ctx):
        llm = _llm(ctx)
        if llm is None:
            return "Chat isn't wired up on this build."
        llm.reset()
        return f"Fresh start - I've forgotten our conversation. Still in '{llm.mode}' mode."

    @router.register(
        "set chat model",
        pattern=r"^\s*(?:set\s+)?(?:chat\s+|llm\s+|ollama\s+)?model\s+(?:to\s+)?([\w.:\-]+)\s*$",
        help="model to llama3.2:3b  -  switches which Ollama model answers.",
        category="chat",
    )
    def cmd_set_model(text, ctx):
        name = re.search(r"model\s+(?:to\s+)?([\w.:\-]+)\s*$", text, re.IGNORECASE).group(1)
        llm = _llm(ctx)
        if llm is None:
            return "Chat isn't wired up on this build."
        installed = llm.installed_models()
        if installed and name not in installed:
            return f"Ollama doesn't have '{name}'. Installed: {', '.join(installed)}"
        ctx.store.set_value("core", "llm.model", name)
        llm.model = name
        llm.reset()
        return f"Chat model is now {name}."

    @router.register(
        "list chat models",
        keywords=("list models", "what models", "installed models", "ollama models"),
        help="Lists the models Ollama has installed.",
        category="chat",
    )
    def cmd_list_models(text, ctx):
        llm = _llm(ctx)
        if llm is None:
            return "Chat isn't wired up on this build."
        models = llm.installed_models()
        if not models:
            return f"Ollama has no models (or isn't running). Try: ollama pull {llm.model}"

        def _use(choice, inner_ctx) -> str:
            inner_ctx.store.set_value("core", "llm.model", choice.payload)
            llm.model = choice.payload
            llm.reset()
            return f"Chat model is now {choice.payload}."

        return offer(
            ctx,
            f"{len(models)} model(s) installed (using {llm.model}):",
            [Choice(m + ("   ← current" if m == llm.model else ""), m) for m in models],
            actions={"use": _use, "open": _use, "run": _use},
            default_action="use",
        )


def _switch_mode(ctx, name: str) -> str:
    llm = _llm(ctx)
    if llm is None:
        return "Chat isn't wired up on this build."
    persona = llm.set_mode(name)
    if persona is None:
        return (
            f"I don't have a '{name.strip()}' mode. "
            f"Available: {', '.join(llm.mode_names())}.\n"
            "Add your own by copying a block in config/personas.json."
        )
    ctx.store.set_value("personas", "active", persona.name)
    lines = [f"Switched to {persona.name} mode - {persona.description}"]
    if persona.asks_for:
        lines.append(persona.asks_for)
    lines.append(f"Talk to it with: {llm.prefix} <message>")
    return "\n".join(lines)
