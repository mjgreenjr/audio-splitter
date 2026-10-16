"""config.toml: the commented template, reading it, saving, and migrating older files."""
import json
import os
import tomllib
from pathlib import Path

import pytest

import config
from version import __version__

GOLDEN = Path(__file__).parent / "golden" / "config_template.txt"


@pytest.fixture
def path(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", p)
    return p


def uncomment_everything(text):
    out = []
    for line in text.splitlines():
        if line.startswith("# ") and " = " in line and not line.startswith("# ---"):
            key = line[2:].split(" = ")[0]
            if key in config.DEFAULTS:
                line = line[2:]
        out.append(line)
    return "\n".join(out)


# --- the template ----------------------------------------------------------------

def test_every_setting_has_a_description():
    for key, _default, description in config.SETTINGS:
        assert description.strip(), key


def test_the_template_is_valid_toml_with_the_version_stamp():
    data = tomllib.loads(config.default_template())
    assert data == {"meta": {"config_version": __version__}}


def test_the_template_lists_every_setting_commented_out_at_its_default():
    text = config.default_template()
    for key, default, description in config.SETTINGS:
        assert f"# {key} = {config.toml_value(default)}\n" in text
        assert description.split()[0] in text


def test_an_untouched_template_means_the_defaults(path):
    path.write_text(config.default_template())
    assert config.Config().data == config.DEFAULTS


def test_uncommenting_every_default_changes_nothing(path):
    path.write_text(uncomment_everything(config.default_template()))
    data = tomllib.loads(path.read_text())
    assert {k: v for k, v in data.items() if k != "meta"} == config.DEFAULTS
    assert config.Config().data == config.DEFAULTS


def test_golden_template_forces_a_version_bump_when_the_template_changes():
    golden = GOLDEN.read_text()
    golden_version = tomllib.loads(golden)["meta"]["config_version"]
    now = config.render(version=golden_version)
    if now != golden:
        assert config.parse_version(__version__)[:2] > config.parse_version(golden_version)[:2], (
            "The settings template changed since the last release (tests/golden/config_template.txt, "
            f"version {golden_version}). Bump the minor version in version.py and add a migration "
            "test; refresh the golden file only when cutting a release.")


def test_toml_values_round_trip():
    for value in ("plain", 'quo"te\\back', "ünï", "", 60, 0.05, True, -3):
        assert tomllib.loads(f"v = {config.toml_value(value)}")["v"] == value


# --- reading and saving ----------------------------------------------------------

def test_a_missing_file_is_created_once_with_the_defaults(path):
    c = config.Config()
    assert c.data == config.DEFAULTS and path.read_text() == config.default_template()
    path.write_text(path.read_text().replace('# logging = "DEBUG"', 'logging = "ERROR"'))
    assert config.Config()["logging"] == "ERROR"      # never overwritten


def test_save_writes_only_what_differs_from_the_defaults(path):
    c = config.Config()
    c["stems_dir"] = "elsewhere"
    c["logging"] = "DEBUG"                            # the default: stays commented
    c.save()
    data = tomllib.loads(path.read_text())
    assert data["stems_dir"] == "elsewhere" and "logging" not in data
    assert '# logging = "DEBUG"' in path.read_text()
    assert config.Config()["stems_dir"] == "elsewhere"


def test_setting_a_value_back_to_its_default_comments_it_out_again(path):
    c = config.Config()
    c["stems_dir"] = "elsewhere"
    c.save()
    c["stems_dir"] = "stems"
    c.save()
    assert "stems_dir" not in tomllib.loads(path.read_text())


def test_a_broken_file_is_backed_up_and_replaced(path):
    path.write_text("this is = = not toml")
    c = config.Config()
    assert c.data == config.DEFAULTS
    assert path.read_text() == config.default_template()
    assert path.with_name("config.toml.bak").read_text() == "this is = = not toml"


# --- migrating -------------------------------------------------------------------

OLD = ('# a comment I wrote\nstems_dir = "mine"\nlogging = "INFO"\n'
       'mystery = 5\n\n[meta]\nconfig_version = "0.0.1"\n')


def test_migration_keeps_what_you_set_and_stamps_the_new_version(path, monkeypatch):
    monkeypatch.setattr(config, "__version__", "0.2.0")
    path.write_text(OLD)
    assert config.migrate(path) == "migrated"
    data = tomllib.loads(path.read_text())
    assert data["stems_dir"] == "mine" and data["logging"] == "INFO"
    assert data["meta"]["config_version"] == "0.2.0"
    assert "# split_mode" in path.read_text()         # new template, defaults commented
    assert path.with_name("config.toml.bak").read_text() == OLD


def test_unknown_settings_move_to_the_ledger_and_survive_later_rebuilds(path, monkeypatch):
    monkeypatch.setattr(config, "__version__", "0.2.0")
    path.write_text(OLD)
    config.migrate(path)
    text = path.read_text()
    assert config.LEDGER_MARK in text and "# mystery = 5" in text
    assert "mystery" not in tomllib.loads(text)
    monkeypatch.setattr(config, "__version__", "0.3.0")
    config.migrate(path)
    assert "# mystery = 5" in path.read_text()


def test_migration_is_idempotent_and_leaves_current_and_patch_files_alone(path, monkeypatch):
    monkeypatch.setattr(config, "__version__", "0.2.0")
    path.write_text(OLD)
    config.migrate(path)
    once = path.read_text()
    assert config.migrate(path) is None and path.read_text() == once
    monkeypatch.setattr(config, "__version__", "0.2.5")   # a patch bump never migrates
    assert config.migrate(path) is None and path.read_text() == once


def test_a_file_from_a_newer_version_is_left_alone_and_never_saved_over(path, monkeypatch, caplog):
    path.write_text('stems_dir = "x"\nfuture = 1\n\n[meta]\nconfig_version = "9.0.0"\n')
    before = path.read_text()
    c = config.Config()
    assert c["stems_dir"] == "x"
    c["stems_dir"] = "y"
    c.save()
    assert path.read_text() == before
    assert not path.with_name("config.toml.bak").exists()


def test_a_file_without_a_stamp_counts_as_old(path):
    path.write_text('stems_dir = "mine"\n')
    assert config.migrate(path) == "migrated"
    assert tomllib.loads(path.read_text())["stems_dir"] == "mine"


def test_renamed_settings_are_ported_until_their_port_expires(path, monkeypatch):
    monkeypatch.setattr(config, "__version__", "0.2.0")
    monkeypatch.setattr(config, "RENAMES", {"old_dir": ("stems_dir", "0.5.0")})
    path.write_text('old_dir = "ported"\n\n[meta]\nconfig_version = "0.1.0"\n')
    config.migrate(path)
    assert tomllib.loads(path.read_text())["stems_dir"] == "ported"
    # the new name wins when both are set; the old one goes to the ledger
    path.write_text('old_dir = "a"\nstems_dir = "b"\n\n[meta]\nconfig_version = "0.1.0"\n')
    config.migrate(path)
    assert tomllib.loads(path.read_text())["stems_dir"] == "b"
    assert "# old_dir" in path.read_text() and "replaced by stems_dir" in path.read_text()
    # after the port expires the old name is just unknown
    monkeypatch.setattr(config, "__version__", "0.5.0")
    path.write_text('old_dir = "late"\n\n[meta]\nconfig_version = "0.4.0"\n')
    config.migrate(path)
    assert "stems_dir" not in tomllib.loads(path.read_text())


def test_the_file_mode_survives_a_migration(path, monkeypatch):
    monkeypatch.setattr(config, "__version__", "0.2.0")
    path.write_text(OLD)
    os.chmod(path, 0o600)
    config.migrate(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_parse_version_compares_as_numbers():
    assert config.parse_version("1.10.0") > config.parse_version("1.9.0")
    assert config.parse_version("junk") == (0, 0, 0) == config.parse_version(None)


# --- from the old config.json ----------------------------------------------------

def test_the_old_json_file_is_imported_once_and_kept_as_a_backup(path):
    old = path.with_name("config.json")
    old.write_text(json.dumps({
        "recordings_dir": "recordings", "stems_dir": "mine", "old_option": True,
        "logging": "INFO"}))
    c = config.Config()
    assert c["stems_dir"] == "mine" and c["logging"] == "INFO"
    data = tomllib.loads(path.read_text())
    assert "recordings_dir" not in data               # equal to the default: never chosen
    assert "# old_option = true" in path.read_text()
    assert not old.exists() and path.with_name("config.json.bak").exists()
    assert config.Config()["stems_dir"] == "mine"


# --- per released version ---------------------------------------------------------

def test_a_0_0_0_file_with_several_settings_migrates_to_the_current_version(path):
    path.write_text('stems_dir = "s"\nrecordings_dir = "r"\nsplit_mode = "4"\n'
                    'stem_load_timeout_s = 120\nlogging = "ERROR"\n')
    c = config.Config()
    assert c.data == {"stems_dir": "s", "recordings_dir": "r", "split_mode": "4",
                      "stem_load_timeout_s": 120, "logging": "ERROR"}
    assert tomllib.loads(path.read_text())["meta"]["config_version"] == __version__
