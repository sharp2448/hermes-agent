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
import pm


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
    monkeypatch.setattr(pm, "sync_venv", forbidden)
    monkeypatch.setattr("pm.client.sync_venv", forbidden)
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
    # Setup prepares the declared client-only extra through PM, not a sidecar install.
    from hermes_cli.memory_setup import memory_provider_dependency_inputs
    meta, inputs = memory_provider_dependency_inputs("hindsight")
    assert inputs == {"extras": ["hindsight"]}

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


def test_real_client_constructs_across_profiles_without_server_packages(isolated_home, monkeypatch):
    from importlib.metadata import distributions, version
    from plugins.memory import import_provider_module
    module = import_provider_module("hindsight")
    monkeypatch.setattr(module, "_fetch_hindsight_api_version", lambda *a: "0.6.1")
    installed = {dist.metadata["Name"].lower().replace("_", "-") for dist in distributions()}
    assert installed.isdisjoint({"hindsight-api-slim", "hindsight-api", "hindsight-all", "hindsight-embed", "pg0-embedded"})
    assert version("hindsight-client") == "0.6.1"
    other = isolated_home / "profiles" / "other"
    for home, bank in ((isolated_home, "sdk-a"), (other, "sdk-b"), (isolated_home, "sdk-a")):
        _seed_home(home, bank)
        before = (home / "hindsight" / "config.json").read_bytes()
        token = set_hermes_home_override(home)
        try:
            provider = load_memory_provider("hindsight", register_skills=False)
            provider.initialize(session_id="sdk-session", hermes_home=str(home), platform="cli")
            try:
                client = provider._get_client()
                assert type(client).__module__.startswith("hindsight_client")
                assert provider._bank_id == bank
                assert provider._mode == "local_external"
            finally:
                provider.shutdown()
        finally:
            reset_hermes_home_override(token)
        assert (home / "hindsight" / "config.json").read_bytes() == before


def test_profile_a_b_a_flushes_bounded_buffers_once_to_own_banks(isolated_home, monkeypatch):
    import sys
    from types import ModuleType
    from plugins.memory import import_provider_module
    calls = []
    class Client:
        def __init__(self, **kwargs):
            pass
        async def aretain_batch(self, **kwargs):
            calls.append(kwargs)
        async def aclose(self):
            pass
    sdk = ModuleType("hindsight_client")
    sdk.Hindsight = Client
    monkeypatch.setitem(sys.modules, "hindsight_client", sdk)
    module = import_provider_module("hindsight")
    monkeypatch.setattr(module, "_append_capability_cache", {})
    monkeypatch.setattr(module, "_fetch_hindsight_api_version", lambda *a: "0.6.1")
    homes = {"a": isolated_home, "b": isolated_home / "profiles" / "other"}
    providers, originals = {}, {}
    for name, home in homes.items():
        _seed_home(home, "bank-" + name)
        originals[name] = (home / "hindsight" / "config.json").read_bytes()
    try:
        for cycle, name in enumerate(("a", "b", "a")):
            home = homes[name]
            token = set_hermes_home_override(home)
            try:
                if name not in providers:
                    providers[name] = load_memory_provider("hindsight", register_skills=False)
                    providers[name].initialize(session_id=name, hermes_home=str(home), platform="cli")
                provider = providers[name]
                for turn in range(4):
                    provider.sync_turn(f"{name}-{cycle}-{turn}", "recorded")
                    assert len(provider._session_turns) < 3
                provider.on_session_switch(f"{name}-{cycle}-next")
                assert provider._session_turns == []
            finally:
                reset_hermes_home_override(token)
    finally:
        for name, provider in providers.items():
            token = set_hermes_home_override(homes[name])
            try:
                provider.shutdown()
            finally:
                reset_hermes_home_override(token)
    observed = []
    for call in calls:
        for item in call["items"]:
            assert item["update_mode"] == "append"
            for turn in json.loads(item["content"]):
                user = turn[0]["content"]
                observed.append(user)
                assert call["bank_id"] == "bank-" + user.removeprefix("User: ")[0]
    expected = [f"User: {name}-{cycle}-{turn}" for cycle, name in enumerate(("a", "b", "a")) for turn in range(4)]
    assert sorted(observed) == sorted(expected)
    for name, home in homes.items():
        assert (home / "hindsight" / "config.json").read_bytes() == originals[name]
