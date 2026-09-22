"""The installed user's Desktop channel is also the bare CLI/backend policy."""
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def channel_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.delenv("HERMES_DESKTOP_USER_DATA_DIR", raising=False)
    from hermes_cli.gui_uninstall import desktop_userdata_dir
    path = desktop_userdata_dir() / "updates.json"
    path.parent.mkdir(parents=True)
    return path


def test_bare_cli_uses_persistent_channel_and_explicit_override(channel_file, tmp_path, monkeypatch):
    from hermes_cli.main_install_repair import _resolve_update_branch

    bare = SimpleNamespace()
    assert _resolve_update_branch(bare) == "main"
    channel_file.write_text('{"branch":"stable","other":"keep"}', encoding="utf-8")
    assert _resolve_update_branch(bare) == "stable"
    assert _resolve_update_branch(SimpleNamespace(branch="release/test")) == "release/test"
    override = tmp_path / "desktop-override"
    override.mkdir()
    (override / "updates.json").write_text('{"branch":"preview"}', encoding="utf-8")
    monkeypatch.setenv("HERMES_DESKTOP_USER_DATA_DIR", str(override))
    assert _resolve_update_branch(bare) == "preview"
    assert channel_file.read_text(encoding="utf-8") == '{"branch":"stable","other":"keep"}'


@pytest.mark.parametrize("content", ['{', 'null', '[]', '{"branch":null}', '{"branch":3}',
                                     '{"branch":""}', '{"branch":" "}', '{"branch":"--all"}',
                                     '{"branch":"a:b"}', '{"branch":"a..b"}', 'directory'])
def test_invalid_channel_never_defaults_to_main(channel_file, content):
    from hermes_cli.main_install_repair import _resolve_update_branch

    if content == "directory":
        channel_file.mkdir()
    else:
        channel_file.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="updates.json"):
        _resolve_update_branch(SimpleNamespace())
    # Explicit recovery does not silently rewrite a broken persistent selection.
    assert _resolve_update_branch(SimpleNamespace(branch="main")) == "main"


def test_cli_rejects_bad_channel_before_any_update_work(channel_file, monkeypatch, capsys):
    from hermes_cli import main, update_cmd

    channel_file.write_text('{"branch":null}', encoding="utf-8")
    monkeypatch.setattr("hermes_cli.config.detect_install_method", lambda *args: "git")
    monkeypatch.setattr(update_cmd, "_cmd_update_impl", lambda *a, **k: pytest.fail("must refuse before update work"))
    with pytest.raises(SystemExit) as error:
        main.cmd_update(SimpleNamespace())
    assert error.value.code == 1
    assert "updates.json" in capsys.readouterr().out
