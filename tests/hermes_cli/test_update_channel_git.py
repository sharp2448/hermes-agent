"""Real local remotes exercise CLI check/apply without deps or service changes."""
import subprocess
from types import SimpleNamespace

import pytest


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def enrolled_git(tmp_path, monkeypatch):
    from hermes_cli import main, update_cmd

    remote = tmp_path / "origin"
    remote.mkdir()
    git(remote, "init", "-b", "main")
    git(remote, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "main")
    git(remote, "branch", "stable")
    repo = tmp_path / "checkout"
    git(tmp_path, "clone", "--single-branch", "--branch", "main", str(remote), str(repo))
    # Enrollment preserves the installer's main mapping and adds the selected ref.
    git(repo, "config", "--add", "remote.origin.fetch", "+refs/heads/stable:refs/remotes/origin/stable")
    git(remote, "checkout", "stable")
    git(remote, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "promoted")
    target = git(remote, "rev-parse", "stable")
    git(remote, "checkout", "main")
    channel = tmp_path / "updates.json"
    channel.write_text('{"branch":"stable"}', encoding="utf-8")
    monkeypatch.setenv("HERMES_DESKTOP_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "PROJECT_ROOT", repo)
    monkeypatch.setattr("hermes_cli.config.detect_install_method", lambda *a: "git")
    # Stop at the code-update boundary; no backup, deps, inventory or service work.
    monkeypatch.setattr(update_cmd, "_begin_update_receipt_and_plan", lambda args: None)
    monkeypatch.setattr(main, "_run_pre_update_backup", lambda args: None)
    monkeypatch.setattr(main, "_pause_windows_gateways_for_update", lambda: None)
    monkeypatch.setattr(main, "_capture_active_lazy_features", lambda: [])
    monkeypatch.setattr(main, "_capture_active_tool_dependencies", lambda: [])
    stopped = []
    monkeypatch.setattr(update_cmd, "_finish_already_up_to_date", lambda *a, **k: stopped.append(a[1]))
    monkeypatch.setattr(update_cmd, "_apply_pulled_update", lambda *a, **k: stopped.append(a[1]))
    return SimpleNamespace(repo=repo, remote=remote, channel=channel, target=target, stopped=stopped)


def test_bare_cli_check_fetches_selected_tracking_ref_and_apply_lands_on_it(enrolled_git, capsys):
    from hermes_cli.main import cmd_update
    env = enrolled_git
    original = git(env.repo, "rev-parse", "HEAD")
    cmd_update(SimpleNamespace(check=True))
    assert "origin/stable" in capsys.readouterr().out
    assert git(env.repo, "rev-parse", "origin/stable") == env.target
    assert git(env.repo, "rev-parse", "HEAD") == original
    cmd_update(SimpleNamespace(yes=True, no_backup=True, no_gateway_restart=True))
    assert git(env.repo, "rev-parse", "HEAD") == env.target
    assert git(env.repo, "branch", "--show-current") == "stable"
    assert env.stopped == ["stable"]
    assert git(env.repo, "status", "--porcelain") == ""
    # The next normal update must fetch and merge the newly promoted tip too.
    git(env.remote, "checkout", "stable")
    git(env.remote, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "next promotion")
    promoted = git(env.remote, "rev-parse", "HEAD")
    cmd_update(SimpleNamespace(yes=True, no_backup=True, no_gateway_restart=True))
    assert git(env.repo, "rev-parse", "HEAD") == promoted
    assert env.stopped == ["stable", "stable"]


def test_missing_selected_ref_fails_check_and_apply_despite_stale_tracking_ref(enrolled_git, capsys):
    from hermes_cli.main import cmd_update
    env = enrolled_git
    git(env.repo, "fetch", "origin", "stable")
    git(env.remote, "branch", "-D", "stable")
    original = git(env.repo, "rev-parse", "HEAD")
    for args in (SimpleNamespace(check=True), SimpleNamespace(yes=True, no_backup=True, no_gateway_restart=True)):
        with pytest.raises(SystemExit) as error:
            cmd_update(args)
        assert error.value.code == 1
        assert "stable" in capsys.readouterr().out
        assert git(env.repo, "rev-parse", "HEAD") == original
    assert env.channel.read_text(encoding="utf-8") == '{"branch":"stable"}'
    assert env.stopped == []
