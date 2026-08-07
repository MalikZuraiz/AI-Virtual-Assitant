"""Optional local-LLM fallback for open-ended chat.

Strictly a **fallback**, and strictly **local**. The rule-based router is the
brain (project brief §2): anything actionable must resolve to a real command
so it is predictable and testable. This only catches messages that matched no
command at all - "what's a good name for this function?" rather than "open
chrome".

Local because there are no paid APIs anywhere in this project, and because on
an 8GB laptop the realistic option is a small quantised model served by
Ollama. The interface is wired up now and disabled by default, so switching
it on after the RAM upgrade is a config edit, not a code change.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("assistant.llm")

DEFAULT_MODEL = "qwen2.5:1.5b-instruct"
DEFAULT_URL = "http://localhost:11434"
DEFAULT_SYSTEM = (
    "You are a concise desktop assistant. Answer in at most three short "
    "sentences. If the user is asking you to perform an action on their PC, "
    "say you don't have a command for that yet."
)


class LocalLLM:
    """Thin Ollama client. Never raises - an unreachable server means 'off'."""

    def __init__(
        self,
        enabled: bool = True,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        system_prompt: str = DEFAULT_SYSTEM,
        timeout: int = 90,
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.system_prompt = system_prompt
        self.timeout = timeout
        self._checked: bool | None = None

    @classmethod
    def from_config(cls, store) -> "LocalLLM":
        cfg = store.value("core", "llm", {}) or {}
        return cls(
            enabled=bool(cfg.get("enabled", False)),
            base_url=str(cfg.get("base_url") or DEFAULT_URL),
            model=str(cfg.get("model") or DEFAULT_MODEL),
            system_prompt=str(cfg.get("system_prompt") or DEFAULT_SYSTEM),
        )

    # -- availability -----------------------------------------------------
    def available(self, recheck: bool = False) -> bool:
        if not self.enabled:
            return False
        if self._checked is not None and not recheck:
            return self._checked
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=3) as response:
                self._checked = response.status == 200
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            logger.info("Local LLM not reachable at %s (%s)", self.base_url, exc)
            self._checked = False
        return self._checked

    def installed_models(self) -> list[str]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=3) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return []
        return [m.get("name", "") for m in data.get("models", [])]

    # -- chat -------------------------------------------------------------
    def chat(self, prompt: str) -> str:
        """Ask the model. Returns a friendly message rather than raising."""
        if not self.available():
            return (
                "Open chat isn't switched on. To enable it: install Ollama, run "
                f"`ollama pull {self.model}`, then set llm.enabled to true in "
                "config/core.json and say 'refresh'."
            )
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "system": self.system_prompt,
                "stream": False,
                "options": {"temperature": 0.4, "num_predict": 220},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            self._checked = False
            return f"The local model didn't answer ({exc})."
        return str(data.get("response") or "").strip() or "(the model returned nothing)"
