import json
import config


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.json")
    c = config.Config()
    assert c["recordings_dir"] == "recordings"


def test_file_overrides_and_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"logging": "ERROR"}))
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    c = config.Config()
    assert c["logging"] == "ERROR"
    c["stems_dir"] = "elsewhere"
    c.save()
    assert config.Config()["stems_dir"] == "elsewhere"


def test_old_config_without_split_mode_gets_the_default(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"stems_dir": "stems", "logging": "INFO"}))  # as written before the key existed
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    assert config.Config()["split_mode"] == "6"
