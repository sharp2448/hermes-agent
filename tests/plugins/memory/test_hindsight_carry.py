"""Temporary fork carry: real bundled discovery, no catalog migration or live daemon.

Keep the previously working local_external provider until catalog parity is verified.
Only external I/O boundaries are doubled; config, discovery and migration are real.
"""

import json
import socket
from pathlib import Path

import pytest

from hermes_cli import memory_provider_migration as migration
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from plugins.memory import find_provider_dir, load_memory_provider
from tools import lazy_deps


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    home = user_home / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    token = set_hermes_home_override(home)

    def forbidden(*args, **kwargs):
        pytest.fail("carry tests must not install packages or contact a live endpoint")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(lazy_deps, "install_specs", forbidden)
    monkeypatch.setattr(lazy_deps, "_venv_pip_install", forbidden)
    try:
        yield home
    finally:
        reset_hermes_home_override(token)


def _seed_home(home, bank):
    (home / "hindsight").mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "memory:\n  provider: hindsight\nsecurity:\n  allow_lazy_installs: false\n",
        encoding="utf-8",
    )
    settings = {
        "mode": "local_external",
        "api_url": "http://hindsight-carry.invalid:18888",
        "bank_id": bank,
        "memory_mode": "hybrid",
        "recall_types": ["observation", "world"],
        "retain_every_n_turns": 3,
        "retain_async": False,
        "timeout": 19,
    }
    (home / "hindsight" / "config.json").write_text(json.dumps(settings), encoding="utf-8")
    return settings


def test_bundled_provider_prevents_catalog_migration(isolated_home, monkeypatch):
    other_home = isolated_home / "profiles" / "synthetic"
    settings = {
        isolated_home: _seed_home(isolated_home, "carry-bank-a"),
        other_home: _seed_home(other_home, "carry-bank-b"),
    }
    catalog_queries, installs = [], []
    monkeypatch.setattr(
        migration, "catalog_source", lambda name: catalog_queries.append(name) or name
    )
    bundled = Path(__file__).resolve().parents[3] / "plugins" / "memory" / "hindsight"
    for home in (isolated_home, other_home, isolated_home):
        config_paths = (home / "config.yaml", home / "hindsight" / "config.json")
        before = [path.read_bytes() for path in config_paths]
        result = migration.migrate_home(
            home, install=lambda name: installs.append(name) or {"ok": True}
        )
        assert installs == [], "bundled Hindsight must prevent catalog installation"
        assert catalog_queries == []
        assert result is None
        assert migration.provider_present("hindsight", home)
        token = set_hermes_home_override(home)
        try:
            assert find_provider_dir("hindsight") == bundled
            provider = load_memory_provider("hindsight", register_skills=False)
            assert provider is not None, "bundled Hindsight must load through real discovery"
            assert type(provider).__module__ == "plugins.memory.hindsight"
            assert provider.is_available()
            provider.initialize(session_id="carry-session", hermes_home=str(home), platform="cli")
            try:
                assert provider._mode == settings[home]["mode"]
                assert provider._api_url == settings[home]["api_url"]
                assert provider._bank_id == settings[home]["bank_id"]
                assert provider._recall_types == settings[home]["recall_types"]
                assert provider._timeout == settings[home]["timeout"]
                assert provider._client is None  # external mode never starts an embedded daemon
            finally:
                provider.shutdown()
        finally:
            reset_hermes_home_override(token)
        assert [path.read_bytes() for path in config_paths] == before
        assert not (home / "plugins" / "hindsight").exists()


def test_local_external_client_appends_each_turn_once(isolated_home, monkeypatch):
    import importlib.metadata
    import sys
    import tomllib
    from types import ModuleType

    from packaging.requirements import Requirement

    from hermes_cli.memory_setup import _install_dependencies
    from plugins.memory import import_provider_module

    settings = _seed_home(isolated_home, "carry-append-bank")
    # Fail explicitly for missing carry wiring, not for an optional SDK import.
    assert "memory.hindsight" in lazy_deps.LAZY_DEPS, "bundled client needs its lazy dependency entry"
    root = Path(__file__).resolve().parents[3]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]
    assert "hindsight" in extras, "the opt-in client-only installation extra must remain available"
    extra = [Requirement(spec) for spec in extras["hindsight"]]
    assert [req.name for req in extra] == ["hindsight-client"]
    assert "hermes-agent[hindsight]" not in extras["all"]

    # Exercise the actual setup dependency reader: external mode needs only the client.
    requests = []
    with monkeypatch.context() as install_patch:
        install_patch.setattr(
            lazy_deps, "install_specs",
            lambda specs, **kw: requests.extend(specs) or lazy_deps.InstallSpecsResult(ok=True),
        )
        _install_dependencies("hindsight", force=True)
    assert [Requirement(spec).name for spec in requests] == ["hindsight-client"]
    floor = next(spec.version for spec in extra[0].specifier if spec.operator == "==")
    for spec in (*requests, *lazy_deps.LAZY_DEPS["memory.hindsight"]):
        assert floor in Requirement(spec).specifier

    # Stand in only for the external SDK and /version response. The provider's
    # lazy ensure, config loader, queue, writer, serialization and flush are real.
    calls, closes, probes = [], [], []

    class RecordingClient:
        def __init__(self, **kwargs):
            self.options = kwargs

        async def aretain_batch(self, **kwargs):
            calls.append(kwargs)

        async def aclose(self):
            closes.append(self)

    sdk = ModuleType("hindsight_client")
    sdk.Hindsight = RecordingClient
    monkeypatch.setitem(sys.modules, "hindsight_client", sdk)
    real_version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata, "version",
        lambda name: "0.9.0" if name == "hindsight-client" else real_version(name),
    )
    module = import_provider_module("hindsight")
    monkeypatch.setattr(module, "_append_capability_cache", {})
    monkeypatch.setattr(
        module, "_fetch_hindsight_api_version",
        lambda url, key: probes.append(url) or "0.6.1",
    )
    provider = load_memory_provider("hindsight", register_skills=False)
    assert provider is not None
    monkeypatch.setattr(
        provider, "_start_embedded_daemon",
        lambda: pytest.fail("local_external must not manage the daemon"),
    )
    provider.initialize(session_id="carry-session", hermes_home=str(isolated_home), platform="cli")
    try:
        client = provider._get_client()  # real ensure() must accept a newer compatible SDK
        assert client.options == {"base_url": settings["api_url"], "timeout": float(settings["timeout"])}
        assert provider._mode == "local_external"
        for i in range(7):
            provider.sync_turn(f"user {i}", f"assistant {i}")
            assert len(provider._session_turns) == (i + 1) % settings["retain_every_n_turns"]
            assert provider._last_retained_turn_count == 0
        # Two batches already queued; switch flushes ONLY the outstanding tail
        # under the old document. Another switch has nothing left to re-send.
        provider.on_session_switch("carry-next", parent_session_id="carry-session", reset=True)
        assert provider._session_turns == []
        provider.on_session_switch("carry-empty")
    finally:
        provider.shutdown()  # drains the real writer before inspecting captured payloads
    provider.sync_turn("after shutdown", "must not be retained")
    assert provider._writer_thread is not None and not provider._writer_thread.is_alive()
    assert len(calls) == 3
    assert probes == [settings["api_url"]]
    assert closes == [client]
    shipped = []
    for call in calls:
        assert call["bank_id"] == settings["bank_id"]
        assert call["document_id"] == "carry-session"
        assert call["retain_async"] is settings["retain_async"]
        item, = call["items"]
        assert item["update_mode"] == "append"
        assert item["metadata"]["session_id"] == "carry-session"
        assert "session:carry-session" in item["tags"]
        shipped.extend(message["content"] for turn in json.loads(item["content"]) for message in turn)
    assert shipped == [text for i in range(7) for text in (f"User: user {i}", f"Assistant: assistant {i}")]
