"""Configuration loading. Every setting has a safe default, so a missing or
partial config file never crashes the app."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULT_MODEL = "llama3.2:3b"

DEFAULTS: dict = {
    "ollama": {
        "host": "http://localhost:11434",
        "model": DEFAULT_MODEL,
        "temperature": 0.4,
        "max_tokens": 60,
        "num_ctx": 2048,        # small context window = far less VRAM
        "keep_alive": "30m",    # keep the model loaded between laps
        "timeout_seconds": 12,
    },
    "coach": {
        "use_llm": True,
        "fallback_to_template": True,
        "units": "kmh",
    },
    "telemetry": {"poll_hz": 60},
    "analysis": {
        "num_segments": 20,
        "min_valid_lap_seconds": 15,
        "max_valid_lap_seconds": 900,
        "top_findings_to_report": 2,
        "min_time_loss_seconds": 0.04,
        "ignore_offtrack_laps": True,
    },
    "voice": {
        "enabled": True,
        "rate": 185,
        "volume": 1.0,
        "voice_name_contains": "",
    },
    "behavior": {
        "tip_every_n_laps": 1,
        "speak_baseline_message": True,
        "announce_new_best": True,
        "speak_session_summary_on_exit": True,
    },
}


class ConfigError(Exception):
    pass


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        elif key in out:
            out[key] = value
        # unknown keys are ignored on purpose (old config files keep working)
    return out


def _read_yaml(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:      # -sig: Notepad's "UTF-8 with BOM" is fine
            data = yaml.safe_load(f)
    except UnicodeDecodeError as e:
        raise ConfigError(
            f"{path.name} isn't saved as UTF-8 text. In Notepad use File > Save As and set Encoding to UTF-8.") from e
    except OSError as e:
        raise ConfigError(f"{path.name} couldn't be read: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(
            f"{path.name} has a formatting problem: {e}\n"
            "Tip: YAML needs spaces (not tabs) for indentation, and a space after each colon."
        ) from e
    return data if isinstance(data, dict) else {}


def _sanitize(cfg: dict) -> dict:
    cfg["coach"]["units"] = cfg["coach"]["units"] if cfg["coach"]["units"] in ("kmh", "mph") else "kmh"
    cfg["telemetry"]["poll_hz"] = max(10, min(120, int(cfg["telemetry"]["poll_hz"])))
    cfg["analysis"]["num_segments"] = max(8, min(60, int(cfg["analysis"]["num_segments"])))
    cfg["analysis"]["top_findings_to_report"] = max(1, int(cfg["analysis"]["top_findings_to_report"]))
    cfg["behavior"]["tip_every_n_laps"] = max(1, int(cfg["behavior"]["tip_every_n_laps"]))
    cfg["ollama"]["host"] = str(cfg["ollama"]["host"]).rstrip("/")
    return cfg


def default_config() -> dict:
    """A fresh copy of the built-in defaults (used by the Pro app)."""
    return _sanitize(copy.deepcopy(DEFAULTS))


def load_from_paths(paths) -> tuple[dict, list[Path]]:
    """Defaults <- each existing file in order. Used by the Pro app to honour the same
    config.yaml / config.local.yaml the CLI uses."""
    cfg = copy.deepcopy(DEFAULTS)
    used: list[Path] = []
    for path in paths:
        path = Path(path)
        if path.is_file():
            cfg = _merge(cfg, _read_yaml(path))
            used.append(path)
    try:
        return _sanitize(cfg), used
    except (TypeError, ValueError) as e:
        raise ConfigError(f"A config value has the wrong type: {e}") from e


def load_config(explicit: str | None = None) -> tuple[dict, list[Path]]:
    """Defaults <- config.yaml <- config.local.yaml (or just --config).

    config.local.yaml is git-ignored, so users can keep their own settings
    when they update the project.
    """
    cfg = copy.deepcopy(DEFAULTS)
    used: list[Path] = []

    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(f"Config file not found: {explicit}")
        candidates = [path]
    else:
        roots = [Path.cwd(), Path(__file__).resolve().parent.parent]
        root = next((r for r in roots if (r / "config.yaml").is_file()), None)
        candidates = []
        if root:
            candidates = [root / "config.yaml", root / "config.local.yaml"]

    for path in candidates:
        if path.is_file():
            cfg = _merge(cfg, _read_yaml(path))
            used.append(path)

    try:
        return _sanitize(cfg), used
    except (TypeError, ValueError) as e:
        raise ConfigError(f"A config value has the wrong type: {e}") from e
