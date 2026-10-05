"""Target-owned Git policy stays distinct from upstream release feeds."""
import json

import pytest

from tests.hermes_cli.test_update_channel_git import enrolled_git, git  # noqa: F401


def test_passive_backend_uses_enrollment_before_release_policy(enrolled_git, tmp_path):
    from hermes_cli.source_check import check_for_updates
    from hermes_cli.update_channel import install_id

    env = enrolled_git
    home = tmp_path / "profile"
    home.mkdir()
    (home / "config.yaml").write_text(
        f"update:\n  installs:\n    {install_id(env.repo)}:\n      channel: stable\n")
    status = check_for_updates(install_root=env.repo, home=home, force=True)
    assert status.get("targetSha") == env.target, status
    assert status.get("branch") == "stable"
    assert "channel" not in status


def test_missing_enrolled_branch_cannot_heal_even_when_merged(enrolled_git, tmp_path):
    from hermes_cli.source_check import check_for_updates

    env = enrolled_git
    git(env.repo, "fetch", "origin")
    git(env.remote, "merge", "--ff-only", "stable")
    git(env.remote, "branch", "-D", "stable")
    original = env.channel.read_bytes()
    status = check_for_updates(install_root=env.repo, home=tmp_path, branch_config_path=env.channel, force=True)
    assert status.get("error") == "branch-missing", status
    assert "stable" in status["message"]
    assert env.channel.read_bytes() == original


@pytest.mark.parametrize("value", ["{", "null", '{"branch":null}', '{"branch":""}'])
def test_passive_invalid_policy_is_not_an_absent_policy(enrolled_git, tmp_path, value):
    from hermes_cli.source_check import check_for_updates

    env = enrolled_git
    env.channel.write_text(value)
    status = check_for_updates(install_root=env.repo, home=tmp_path, force=True)
    assert status.get("error") == "update-channel-invalid", status
    assert not status.get("updateAvailable")


def test_enrolling_same_branch_invalidates_unenrolled_cached_result(enrolled_git, tmp_path):
    from hermes_cli.source_check import check_for_updates
    env = enrolled_git
    env.channel.unlink()
    first = check_for_updates(install_root=env.repo, home=tmp_path)
    assert first["branch"] == "main" and first["behind"] == 0
    env.channel.write_text('{"branch":"main"}')
    git(env.remote, "branch", "-m", "main", "retired")
    enrolled = check_for_updates(install_root=env.repo, home=tmp_path)
    assert enrolled.get("error") == "branch-missing", enrolled


def test_selected_branch_cannot_be_satisfied_by_a_nested_ref_tail(enrolled_git, tmp_path):
    from hermes_cli.source_check import check_for_updates
    env = enrolled_git
    git(env.remote, "branch", "other/refs/heads/stable", "stable")
    git(env.remote, "branch", "-D", "stable")
    status = check_for_updates(install_root=env.repo, home=tmp_path, force=True)
    assert status.get("error") == "branch-missing", status
