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
    monkeypatch.setenv("PYTHONPATH", str(repo))
    monkeypatch.setattr("hermes_cli.config.detect_install_method", lambda *a: "git")
    # Stop at the code-update boundary; no backup, deps, inventory or service work.
    monkeypatch.setattr(update_cmd, "_begin_update_receipt_and_plan", lambda args: None)
    monkeypatch.setattr(main, "_run_pre_update_backup", lambda args: None)
    monkeypatch.setattr(main, "_pause_windows_gateways_for_update", lambda: None)
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


def test_enrolled_main_check_never_uses_upstream(enrolled_git, capsys):
    from hermes_cli.main import cmd_update
    env = enrolled_git
    env.channel.write_text('{"branch":"main"}')
    upstream = env.repo.parent / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-b", "main")
    git(upstream, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "unrelated upstream")
    git(env.repo, "remote", "add", "upstream", str(upstream))
    cmd_update(SimpleNamespace(check=True))
    assert "Fetching from upstream" not in capsys.readouterr().out
    assert git(env.repo, "rev-parse", "origin/main") == git(env.remote, "rev-parse", "main")


def test_enrolled_main_apply_does_not_sync_an_unreviewed_upstream(enrolled_git, monkeypatch):
    from hermes_cli import main
    env = enrolled_git
    env.channel.write_text('{"branch":"main"}')
    synced = []
    monkeypatch.setattr(main, "_sync_with_upstream_if_needed", lambda *a, **kw: synced.append(True))
    main.cmd_update(SimpleNamespace(yes=True, no_backup=True, no_gateway_restart=True))
    assert synced == []
    assert git(env.repo, "rev-parse", "HEAD") == git(env.remote, "rev-parse", "main")


def test_enrolled_main_cannot_fall_back_to_official_zip(enrolled_git, monkeypatch):
    from hermes_cli import update_cmd_zip, update_cmd
    env = enrolled_git
    env.channel.write_text('{"branch":"main"}')
    downloads = []
    monkeypatch.setattr(update_cmd_zip, "_abort_zip_update_if_dirty_tree", lambda: None)
    monkeypatch.setattr(update_cmd_zip, "_download_and_swap_zip", lambda *a: downloads.append(a))
    monkeypatch.setattr(update_cmd, "_complete_source_update", lambda *a: None)
    with pytest.raises(SystemExit):
        update_cmd_zip._update_via_zip(SimpleNamespace(branch=None), completion_request={})
    assert downloads == []
