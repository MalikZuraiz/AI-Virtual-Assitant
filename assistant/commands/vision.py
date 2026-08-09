"""Gesture commands.

Bindings come from ``config/gestures.json``, so rebinding a gesture - or
pointing one at an assistant command instead of a hotkey - never needs a code
change. ``rebind <gesture> to <keys>`` writes that file for you.

There is deliberately no cursor control here. Continuous pointer tracking was
tried and removed: on a webcam it is jittery, clicks land where your hand was
rather than where you meant, and it fights the physical mouse. Discrete poses
("this is a fist") are a far easier signal to get right, and a gesture that
fires the correct shortcut every time is worth more than a cursor that almost
works.
"""
from __future__ import annotations

import re

from assistant.core.router import CommandRouter
from assistant.core.selection import Choice, offer
from assistant.store.store import ConfigStore

#: Friendly names for the poses, shown in listings. Kept in lockstep with
#: assistant.vision.gestures.POSES - finger counts only, nothing motion-based.
POSE_HELP = {
    "fist": "closed hand, no fingers up",
    "one": "1 finger (index)",
    "two": "2 fingers (index + middle)",
    "three": "3 fingers (index + middle + ring)",
    "four": "4 fingers, thumb tucked in",
    "stop": "5 fingers held together, thumb out - flat 'stop' hand",
    "open_palm": "5 fingers spread apart (neutral - fires nothing, resets the last gesture)",
}

#: Short spoken/chat form of each pose name - used to prefix "what just
#: happened" announcements, so "opened a new desktop" reads as "2 fingers:
#: opened a new desktop" instead of a bare, context-free action name.
POSE_SHORT = {
    "fist": "fist",
    "one": "1 finger",
    "two": "2 fingers",
    "three": "3 fingers",
    "four": "4 fingers",
    "stop": "stop sign",
    "open_palm": "open palm",
}


def _gesture_controller(ctx):
    """Build the controller lazily and keep it on the context."""
    existing = getattr(ctx, "gestures", None)
    if existing is not None:
        return existing

    from assistant.vision.actions import ActionRunner
    from assistant.vision.gestures import GestureController

    doc = ctx.store.get("gestures")
    tts = getattr(ctx, "tts", None)
    runner = ActionRunner(
        on_command=getattr(ctx, "run_command", None),
        on_stop_speaking=(tts.silence if tts is not None else None),
    )
    def _on_fired(name: str, message: str) -> None:
        # Chat + speech, from the camera thread, regardless of whether a job
        # happens to be bound - see Context.announce for why this can't just
        # be ctx.progress (thread-local; the camera loop is never job-bound).
        short = POSE_SHORT.get(name, name.replace("_", " "))
        ctx.announce(f"Gesture ({short}): {message}", speak=True)

    def _on_status(text: str) -> None:
        # Lifecycle bookkeeping (camera ready/stopped) - worth a chat line,
        # not worth interrupting speech for on every single one.
        ctx.announce(text, speak=False)

    controller = GestureController(
        on_action=runner,
        bindings=doc.get("bindings") or {},
        camera_index=int(doc.get("camera_index", 0)),
        fps_limit=int(doc.get("fps_limit", 15)),
        hold_frames=int(doc.get("hold_frames", 5)),
        cooldown=float(doc.get("cooldown_seconds", 1.0)),
        confidence=float(doc.get("confidence", 0.5)),
        on_status=_on_status,
        on_fired=_on_fired,
    )
    controller.action_runner = runner  # so stop() can release a held Alt
    ctx.gestures = controller
    return controller


def register(router: CommandRouter, store: ConfigStore) -> None:
    @router.register(
        "start gestures",
        keywords=(
            "start gestures", "enable gestures", "gesture control on",
            "turn on gestures", "start gesture control", "hand gestures on",
        ),
        help="Starts hand-gesture control (desktops, windows, volume, screenshots).",
        category="gestures",
    )
    def cmd_start(text, ctx):
        controller = _gesture_controller(ctx)
        # Pick up any hand-edited bindings without needing a restart.
        doc = ctx.store.get("gestures")
        controller.bindings = doc.get("bindings") or {}
        controller.recogniser.hold_frames = int(doc.get("hold_frames", 5))
        controller.recogniser.cooldown = float(doc.get("cooldown_seconds", 1.0))
        return controller.start()

    @router.register(
        "stop gestures",
        keywords=(
            "stop gestures", "disable gestures", "gesture control off",
            "turn off gestures", "stop gesture control", "hand gestures off",
        ),
        help="Stops gesture control and frees the webcam.",
        category="gestures",
    )
    def cmd_stop(text, ctx):
        controller = getattr(ctx, "gestures", None)
        if controller is None:
            return "Gesture control wasn't running."
        runner = getattr(controller, "action_runner", None)
        if runner is not None:
            runner.shutdown()  # make sure Alt is never left held down
        return controller.stop()

    @router.register(
        "list gestures",
        keywords=("list gestures", "what gestures", "show gestures", "gesture list", "my gestures"),
        help="Lists every gesture and what it does.",
        category="gestures",
        instant=True,
    )
    def cmd_list(text, ctx):
        bindings = ctx.store.get("gestures").get("bindings") or {}
        if not bindings:
            return "No gestures bound. See config/gestures.json."
        lines = ["Gestures (hold a pose ~1/3 second):"]
        for name, binding in bindings.items():
            how = POSE_HELP.get(name, name.replace("_", " "))
            what = binding.get("label") or binding.get("keys") or binding.get("command") or "-"
            lines.append(f"  {how:<42} -> {what}")
        doc = ctx.store.get("gestures")
        lines.append(
            f"\nHold {doc.get('hold_frames', 5)} frames to confirm; "
            f"{doc.get('cooldown_seconds', 1.0)}s cooldown between gestures."
        )
        lines.append("Rebind with: rebind two to ctrl+windows+d")
        return "\n".join(lines)

    @router.register(
        "rebind gesture",
        pattern=r"^\s*(?:rebind|bind|map|set)\s+(?:gesture\s+)?([\w ]+?)\s+to\s+(.+?)\s*$",
        help="rebind two to ctrl+windows+d   /   rebind three to command: what's my day",
        category="gestures",
    )
    def cmd_rebind(text, ctx):
        match = re.search(
            r"(?:rebind|bind|map|set)\s+(?:gesture\s+)?([\w ]+?)\s+to\s+(.+?)\s*$", text, re.IGNORECASE
        )
        gesture = match.group(1).strip().lower().replace(" ", "_")
        target = match.group(2).strip()

        known = set(ctx.store.get("gestures").get("bindings") or {}) | set(POSE_HELP)
        if gesture not in known:
            return (
                f"I don't have a gesture called '{gesture}'.\n"
                f"Available: {', '.join(sorted(known))}"
            )

        if target.lower().startswith(("command:", "say:", "run:")):
            command = target.split(":", 1)[1].strip()
            binding = {"action": "command", "command": command, "label": command}
        elif target.lower() in {"switch window", "alt tab", "alt+tab"}:
            binding = {"action": "switch_window", "label": "switch window"}
        elif target.lower() in {"none", "nothing", "off"}:
            binding = {"action": "none", "label": "(unbound)"}
        else:
            binding = {"action": "hotkey", "keys": target, "label": target}

        def _mutate(doc: dict) -> None:
            doc.setdefault("bindings", {})[gesture] = binding

        ctx.store.update("gestures", _mutate)
        controller = getattr(ctx, "gestures", None)
        if controller is not None:
            controller.bindings = ctx.store.get("gestures").get("bindings") or {}
        return (
            f"'{POSE_HELP.get(gesture, gesture)}' now does: {binding.get('label')}\n"
            "Live immediately - no restart."
        )

    @router.register(
        "gesture sensitivity",
        pattern=r"^\s*gestures?\s+(?:are\s+)?(too\s+)?(sensitive|slow|fast|twitchy|sluggish|jumpy)\s*$",
        help="gestures too sensitive / gestures too slow  -  retunes hold time and cooldown.",
        category="gestures",
        instant=True,
    )
    def cmd_tune(text, ctx):
        word = re.search(
            r"(sensitive|slow|fast|twitchy|sluggish|jumpy)", text, re.IGNORECASE
        ).group(1).lower()
        doc = ctx.store.get("gestures")
        hold = int(doc.get("hold_frames", 5))
        cooldown = float(doc.get("cooldown_seconds", 1.0))

        if word in {"sensitive", "twitchy", "jumpy", "fast"}:
            # Firing by accident: demand a longer hold and a longer gap.
            hold, cooldown = min(hold + 3, 20), min(cooldown + 0.4, 4.0)
            note = "I'll need the pose held longer before acting."
        else:
            hold, cooldown = max(hold - 2, 3), max(cooldown - 0.3, 0.5)
            note = "I'll react sooner."

        ctx.store.set_value("gestures", "hold_frames", hold)
        ctx.store.set_value("gestures", "cooldown_seconds", round(cooldown, 2))
        controller = getattr(ctx, "gestures", None)
        if controller is not None:
            controller.recogniser.hold_frames = hold
            controller.recogniser.cooldown = cooldown
        return f"{note} (hold {hold} frames, {cooldown:.1f}s cooldown)"
