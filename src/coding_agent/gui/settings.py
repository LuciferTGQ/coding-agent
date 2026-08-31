"""Persistent desktop defaults stored beside, but separate from, sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

from coding_agent.config import DEFAULT_MODEL, SUPPORTED_MODELS
from coding_agent.gui.i18n import LANGUAGES


MODELS = SUPPORTED_MODELS
REASONING_EFFORTS = ("low", "high", "max")


@dataclass(frozen=True, slots=True)
class AppSettings:
    language: str = "zh"
    model: str = DEFAULT_MODEL
    reasoning_effort: str = "high"
    max_steps: int = 24


class SettingsStore:
    def __init__(self, root: str | Path) -> None:
        self.path = Path(root).expanduser().resolve() / "settings.json"

    def load(self) -> AppSettings:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return AppSettings()
        if not isinstance(payload, dict):
            return AppSettings()
        language = payload.get("language", "zh")
        model = payload.get("model", MODELS[0])
        effort = payload.get("reasoning_effort", "high")
        max_steps = payload.get("max_steps", 24)
        if language not in LANGUAGES:
            language = "zh"
        if model not in MODELS:
            model = MODELS[0]
        if effort not in REASONING_EFFORTS:
            effort = "high"
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
            max_steps = 24
        return AppSettings(language, model, effort, max_steps)

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
