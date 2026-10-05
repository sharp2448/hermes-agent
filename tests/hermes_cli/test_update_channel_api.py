"""Exercise the authenticated update API through the real channel/check resolvers."""
import io
import json
import subprocess
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def enrolled_backend(tmp_path, monkeypatch):
    import hermes_cli.source_check as source_check
    import hermes_cli.config as config
    import hermes_cli.web_server as server
    import hermes_cli.web_server_files as files
    import hermes_cli.web_server_gateway as actions

    data = tmp_path / "desktop-data"
    data.mkdir()
    channel = data / "updates.json"
    channel.write_text('{"branch":"stable"}', encoding="utf-8")
    monkeypatch.setenv("HERMES_DESKTOP_USER_DATA_DIR", str(data))
    repo = tmp_path / "checkout"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "main")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "stable")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "stable")
    target = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    git(repo, "remote", "add", "origin", "https://github.com/test-owner/channel-fork.git")
    monkeypatch.setattr(server, "PROJECT_ROOT", repo)
    monkeypatch.setattr(config, "get_project_root", lambda: repo)
    monkeypatch.setattr(config, "detect_install_method", lambda *a: "git")
    monkeypatch.setattr(files, "_dashboard_local_update_managed_externally", lambda: False)
    monkeypatch.setattr(actions, "_ACTION_PROCS", {})
    monkeypatch.setattr(actions, "_ACTION_RESULTS", {})
    monkeypatch.setattr(actions, "_ACTION_IDS", {})
    monkeypatch.setattr(actions, "_ACTION_COMMANDS", {})
    monkeypatch.setattr(actions, "_ACTION_LOG_DIR", tmp_path / "logs")
    client = TestClient(server.app)
    client.headers[server._SESSION_HEADER_NAME] = server._SESSION_TOKEN
    return SimpleNamespace(client=client, channel=channel, repo=repo, head=head, target=target)


def test_api_checks_the_target_channel_and_invalidates_channel_and_origin_cache(enrolled_backend, monkeypatch):
    env = enrolled_backend
    requests = []

    def urlopen(request, **kwargs):
        url = request.full_url
        requests.append(url)
        if "/branches/" in url:
            branch = url.rsplit("/", 1)[-1]
            return io.BytesIO(json.dumps({"name": branch, "commit": {"sha": env.target if branch == "stable" else env.head}}).encode())
        return io.BytesIO(json.dumps({"ahead_by": 1, "commits": [{"sha": env.target, "commit": {"message": "stable"}}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    checked = env.client.get("/api/hermes/update/check").json()
    assert checked["update_available"] is True
    assert checked["behind"] == 1
    assert checked["commits"][0]["sha"] == env.target[:7]
    assert all("/repos/test-owner/channel-fork/" in url for url in requests)
    env.channel.write_text('{"branch":"main"}', encoding="utf-8")
    assert env.client.get("/api/hermes/update/check").json()["behind"] == 0
    env.channel.write_text('{"branch":"stable"}', encoding="utf-8")
    assert env.client.get("/api/hermes/update/check").json()["behind"] == 1
    requests.clear()
    git(env.repo, "remote", "set-url", "origin", "https://github.com/other-owner/channel-fork.git")
    assert env.client.get("/api/hermes/update/check").json()["behind"] == 1
    assert requests and all("/repos/other-owner/channel-fork/" in url for url in requests)


def test_api_update_cannot_retarget_backend_and_bad_channel_refuses(enrolled_backend, monkeypatch):
    import argparse
    from unittest.mock import patch
    from hermes_cli.main_install_repair import _resolve_update_branch
    from hermes_cli.subcommands.update import build_update_parser

    env = enrolled_backend
    branches = []
    real_popen = subprocess.Popen

    def spawn(cmd, **kwargs):
        if kwargs.get("cwd") != str(env.repo) or cmd[-1:] != ["update"]:
            return real_popen(cmd, **kwargs)
        # Exercise the real action spawner, argv and child environment, stopping
        # only the mutating subprocess body. The parser/resolver remain real.
        parser = argparse.ArgumentParser()
        build_update_parser(parser.add_subparsers(), cmd_update=lambda args: None)
        with patch.dict("os.environ", kwargs["env"], clear=True):
            branches.append(_resolve_update_branch(parser.parse_args(["update"])))
        return SimpleNamespace(pid=12345, poll=lambda: 0)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    from hermes_cli.web_server import app

    assert TestClient(app).post("/api/hermes/update").status_code == 401
    assert branches == []
    assert env.client.post("/api/hermes/update", json={"branch": "main"}).json()["ok"] is True
    assert branches == ["stable"]
    env.channel.write_text('{"branch":null}', encoding="utf-8")
    refused = env.client.post("/api/hermes/update").json()
    assert refused["ok"] is False
    assert "updates.json" in refused["message"]
    assert branches == ["stable"]
    checked = env.client.get("/api/hermes/update/check").json()
    assert checked["can_apply"] is False
    assert "updates.json" in checked["message"]


def test_api_missing_selected_branch_is_actionable(enrolled_backend, monkeypatch):
    from urllib.error import HTTPError

    env = enrolled_backend

    def missing(request, **kwargs):
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", missing)
    checked = env.client.get("/api/hermes/update/check?force=true").json()
    assert checked["can_apply"] is False
    assert "stable" in checked["message"]
    assert "test-owner/channel-fork" in checked["message"]
    # The non-GitHub passive path must use the exact selected ref too.
    git(env.repo, "remote", "set-url", "origin", str(env.repo))
    git(env.repo, "branch", "-D", "stable")
    checked = env.client.get("/api/hermes/update/check?force=true").json()
    assert checked["can_apply"] is False
    assert "stable" in checked["message"] and "missing" in checked["message"]
    assert env.channel.read_text(encoding="utf-8") == '{"branch":"stable"}'


def test_github_tag_with_enrolled_name_is_not_a_selected_branch(enrolled_backend, monkeypatch):
    from urllib.error import HTTPError
    env = enrolled_backend
    def only_tag(request, **kwargs):
        if "/commits/" in request.full_url:
            return io.BytesIO(env.target.encode())  # GitHub can resolve a tag here.
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", only_tag)
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    status = env.client.get("/api/hermes/update/check?force=true").json()
    assert status["can_apply"] is False
    assert "stable" in status["message"]
