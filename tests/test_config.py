import pytest

from iracing_coach.config import DEFAULT_MODEL, ConfigError, load_config


def test_defaults_without_any_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg, _ = load_config(str(tmp_path / "missing.yaml")) if False else (load_config()[0], None)
    assert cfg["ollama"]["model"] == DEFAULT_MODEL
    assert cfg["ollama"]["num_ctx"] == 2048


def test_partial_file_is_merged_and_unknown_keys_ignored(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("ollama:\n  model: qwen2.5:7b\nold_setting: 1\n")
    cfg, used = load_config(str(f))
    assert cfg["ollama"]["model"] == "qwen2.5:7b"
    assert cfg["ollama"]["host"] == "http://localhost:11434"
    assert "old_setting" not in cfg and used == [f]


def test_bad_yaml_gives_friendly_error(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("ollama:\n\tmodel: x\n")
    with pytest.raises(ConfigError):
        load_config(str(f))


def test_values_are_clamped(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("telemetry:\n  poll_hz: 5000\ncoach:\n  units: furlongs\n")
    cfg, _ = load_config(str(f))
    assert cfg["telemetry"]["poll_hz"] == 120 and cfg["coach"]["units"] == "kmh"


def test_settings_file_in_the_wrong_encoding_gives_a_friendly_error(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_bytes("ollama:\n  model: caf\xe9\n".encode("cp1252"))      # Notepad 'ANSI' with an accent
    with pytest.raises(ConfigError, match="UTF-8"):
        load_config(str(f))


def test_settings_file_with_a_bom_loads(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_bytes(b"\xef\xbb\xbf" + b"ollama:\n  model: qwen2.5:7b\n")
    assert load_config(str(f))[0]["ollama"]["model"] == "qwen2.5:7b"
