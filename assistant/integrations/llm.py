"""Local LLM chat with switchable personas.

Two rules keep this from making the assistant unpredictable:

* **It only runs when you ask for it.** Nothing is routed here implicitly -
  you prefix a message with ``nova`` (configurable). Everything else still
  goes through the rule-based router, so "open chrome" can never turn into a
  chatbot paraphrasing what opening Chrome would be like.
* **It never performs actions.** It answers; commands act.

Personas live in ``config/personas.json`` - the system prompt, temperature
and description of each mode are data, so adding "study mode" is a JSON edit,
not a code change. Your own profile is injected into every mode so answers
are framed for you specifically rather than a generic global average.

Local because there are no paid APIs in this project: Ollama serving a small
quantised model on CPU.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("assistant.llm")

DEFAULT_MODEL = "qwen2.5:1.5b-instruct"
DEFAULT_URL = "http://localhost:11434"
#: How long a reachability check is trusted before being retried. Short
#: enough that starting Ollama after the assistant just works.
AVAILABILITY_TTL = 20.0

#: Keep the model resident between questions. Measured on this machine, a
#: cold request spends 3-6s just reloading weights before generating a single
#: token; with this it is paid once.
KEEP_ALIVE = "30m"

#: Context window. The default (larger) window makes prompt processing
#: noticeably slower on CPU for no benefit - these are short chats, not
#: document analysis.
NUM_CTX = 2048

#: Ceiling on reply length. This CPU generates ~6.5 tokens/second, so 500
#: tokens is over a minute of waiting; 220 keeps a reply around 30 seconds.
#: Individual personas can lower it further.
DEFAULT_NUM_PREDICT = 220


@dataclass
class Persona:
    name: str
    system: str
    description: str = ""
    temperature: float = 0.7
    #: Extra line appended to every reply, e.g. a verification caveat.
    footer: str = ""
    #: Asks a follow-up question before the first message (personality mode).
    asks_for: str = ""
    #: Whether to inject the user's profile. Off for the playful modes: a
    #: small model handed a biography tends to recite it back instead of
    #: staying in character, which is exactly how "ragebait mode" turned into
    #: an interview about the user's degree.
    use_profile: bool = True
    #: Per-persona reply ceiling; short for banter, longer for writing help.
    max_tokens: int = DEFAULT_NUM_PREDICT

    @classmethod
    def from_entry(cls, name: str, entry: dict) -> "Persona":
        return cls(
            name=name,
            system=str(entry.get("system") or ""),
            description=str(entry.get("description") or ""),
            temperature=float(entry.get("temperature", 0.7)),
            footer=str(entry.get("footer") or ""),
            asks_for=str(entry.get("asks_for") or ""),
            use_profile=bool(entry.get("use_profile", True)),
            max_tokens=int(entry.get("max_tokens", DEFAULT_NUM_PREDICT)),
        )


@dataclass
class LocalLLM:
    """Ollama client with personas and short-term memory.

    Never raises: an unreachable server, a missing model or a timeout all
    come back as a readable sentence, because this runs inside a chat window
    where a traceback helps nobody.
    """

    enabled: bool = False
    base_url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    mode: str = "default"
    prefix: str = "nova"
    timeout: int = 180
    history_turns: int = 6
    profile: str = ""
    personas: dict[str, Persona] = field(default_factory=dict)
    persona_note: str = ""
    _history: deque = field(default_factory=lambda: deque(maxlen=12), repr=False)
    _checked: bool | None = field(default=None, repr=False)
    _checked_at: float = field(default=0.0, repr=False)

    # -- construction -----------------------------------------------------
    @classmethod
    def from_config(cls, store) -> "LocalLLM":
        cfg = store.value("core", "llm", {}) or {}
        doc = store.get("personas")
        personas = {
            name: Persona.from_entry(name, entry)
            for name, entry in (doc.get("modes") or {}).items()
            if isinstance(entry, dict)
        }
        return cls(
            enabled=bool(cfg.get("enabled", False)),
            base_url=str(cfg.get("base_url") or DEFAULT_URL),
            model=str(cfg.get("model") or DEFAULT_MODEL),
            mode=str(doc.get("active") or "default"),
            prefix=str(cfg.get("prefix") or "nova").lower(),
            history_turns=int(cfg.get("history_turns", 6)),
            profile=str(doc.get("profile") or ""),
            personas=personas,
            persona_note=str(doc.get("note") or ""),
        )

    # -- personas ---------------------------------------------------------
    @property
    def persona(self) -> Persona:
        return self.personas.get(self.mode) or Persona(
            name="default", system="You are a helpful, concise desktop assistant."
        )

    def set_mode(self, name: str) -> Persona | None:
        """Switch persona. Tolerates typos - "memo mode" means "meme mode"."""
        key = name.strip().lower().replace(" mode", "").strip()
        if key not in self.personas:
            key = self._closest_mode(key)
        if key not in self.personas:
            return None
        self.mode = key
        self._history.clear()  # a new persona starts a new conversation
        return self.personas[key]

    def _closest_mode(self, key: str) -> str:
        """Nearest persona name, if it is close enough to be unambiguous."""
        if not key:
            return ""
        try:
            from rapidfuzz import process

            match = process.extractOne(key, list(self.personas), score_cutoff=70)
            return match[0] if match else ""
        except ImportError:  # pragma: no cover
            from difflib import get_close_matches

            hits = get_close_matches(key, list(self.personas), n=1, cutoff=0.7)
            return hits[0] if hits else ""

    def mode_names(self) -> list[str]:
        return sorted(self.personas)

    def reset(self) -> None:
        self._history.clear()

    # -- availability -----------------------------------------------------
    def available(self, recheck: bool = False) -> bool:
        if not self.enabled:
            return False
        fresh = (time.monotonic() - self._checked_at) < AVAILABILITY_TTL
        if self._checked is not None and fresh and not recheck:
            return self._checked
        self._checked_at = time.monotonic()
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=4) as response:
                self._checked = response.status == 200
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            logger.info("Ollama not reachable at %s (%s)", self.base_url, exc)
            self._checked = False
        return bool(self._checked)

    def installed_models(self) -> list[str]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=4) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return []
        return [m.get("name", "") for m in data.get("models", [])]

    def diagnose(self) -> str:
        """Explain precisely why chat is not working. Used by 'chat status'."""
        if not self.enabled:
            return (
                "Local chat is switched off. Set llm.enabled to true in "
                "config/core.json (or say 'enable chat') and then 'refresh'."
            )
        if not self.available(recheck=True):
            return (
                f"I can't reach Ollama at {self.base_url}.\n"
                "Start it (run `ollama serve`, or launch the Ollama app) and try again.\n"
                "If it's running on another port, set llm.base_url in config/core.json."
            )
        models = self.installed_models()
        if not models:
            return f"Ollama is running but has no models. Run: ollama pull {self.model}"
        if self.model not in models and not any(m.startswith(self.model.split(":")[0]) for m in models):
            return (
                f"Ollama is running but '{self.model}' isn't installed.\n"
                f"Available: {', '.join(models)}\n"
                f"Either run `ollama pull {self.model}` or set llm.model to one of those."
            )
        return (
            f"Chat is ready - {self.model} via Ollama, mode '{self.mode}'.\n"
            f"Say '{self.prefix} <anything>' to talk to it.\n"
            f"{self.model_advice()}"
        )

    def model_advice(self) -> str:
        """Honest note on what this machine can actually run well.

        Persona quality is mostly a function of model size, and a 1.5B model
        will drift out of character no matter how the prompt is written. But
        a 3B model needs headroom this laptop does not currently have, and
        recommending one that swaps would make everything worse.
        """
        try:
            import psutil

            free_gb = psutil.virtual_memory().available / 1e9
        except Exception:  # noqa: BLE001
            return ""
        small = "1.5b" in self.model.lower() or ":1b" in self.model.lower()
        if small and free_gb >= 4.5:
            return (
                f"You have {free_gb:.1f} GB free - a bigger model would stay in "
                "character much better. Try: ollama pull qwen2.5:3b-instruct, "
                "then 'model to qwen2.5:3b-instruct'."
            )
        if small:
            return (
                f"Note: {free_gb:.1f} GB RAM free, so a 1.5B model is the right "
                "size here. It will drift out of character sometimes - that is the "
                "model, not the prompt. A 3B model after the RAM upgrade will be "
                "noticeably better at modes like ragebait and personality."
            )
        return ""

    # -- chat -------------------------------------------------------------
    def _build_system(self, persona: Persona) -> str:
        parts = [persona.system]
        if self.profile and persona.use_profile:
            parts.append(f"About the person you are talking to:\n{self.profile}")
        if self.persona_note:
            parts.append(self.persona_note)
        return "\n\n".join(p for p in parts if p)

    def warm(self) -> None:
        """Ask Ollama to load the model now, so the next question is fast.

        Fire-and-forget: a zero-token generate makes the server page the
        weights in and hold them for ``KEEP_ALIVE``. Called after enabling
        chat or switching model, off the GUI thread.
        """
        if not self.enabled:
            return
        payload = json.dumps(
            {"model": self.model, "prompt": "", "stream": False, "keep_alive": KEEP_ALIVE}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=120).close()
            logger.info("Warmed %s", self.model)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            logger.info("Could not warm %s (%s)", self.model, exc)

    def chat(self, prompt: str, on_token: Callable[[str], None] | None = None) -> str:
        """Ask the model. ``on_token`` receives the reply as it is generated.

        Streaming matters more than raw speed here: this CPU produces about
        6-7 tokens a second, so a long answer takes half a minute no matter
        what. Watching it arrive is the difference between "slow" and
        "frozen" - and a partial answer is still returned if the connection
        dies mid-reply.
        """
        if not self.enabled:
            return (
                "Local chat is off. Say 'enable chat' (I'll flip it in config/core.json) "
                "or set llm.enabled to true yourself, then 'refresh'."
            )
        if not self.available():
            return self.diagnose()

        persona = self.persona
        messages = [{"role": "system", "content": self._build_system(persona)}]
        for role, content in list(self._history)[-self.history_turns * 2:]:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "keep_alive": KEEP_ALIVE,
                "options": {
                    "temperature": persona.temperature,
                    "num_predict": persona.max_tokens,
                    "num_ctx": NUM_CTX,
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        chunks: list[str] = []
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    if not raw.strip():
                        continue
                    try:
                        event = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        continue
                    piece = str((event.get("message") or {}).get("content") or "")
                    if piece:
                        chunks.append(piece)
                        if on_token:
                            on_token(piece)
                    if event.get("done"):
                        break
                    if time.monotonic() - started > self.timeout:
                        chunks.append("\n(cut off - taking too long)")
                        break
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            self._checked = None
            if exc.code == 404:
                return (
                    f"Ollama doesn't have '{self.model}'. Run: ollama pull {self.model}\n"
                    f"(Installed: {', '.join(self.installed_models()) or 'none'})"
                )
            return f"Ollama returned {exc.code}. {body}"
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            self._checked = None
            partial = "".join(chunks).strip()
            if partial:
                return f"{partial}\n\n(connection dropped partway through)"
            return (
                f"The model didn't answer ({exc}). It may still be loading - "
                "the first reply after a restart can take a while on CPU."
            )

        answer = "".join(chunks).strip()
        if not answer:
            return "(the model returned nothing)"

        self._history.append(("user", prompt))
        self._history.append(("assistant", answer))
        if persona.footer:
            answer = f"{answer}\n\n{persona.footer}"
        return answer
