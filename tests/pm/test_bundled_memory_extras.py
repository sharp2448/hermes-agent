"""Configured bundled providers join the PM union without becoming catalog plugins."""
from pathlib import Path

import pytest


@pytest.fixture
def selection(tmp_path, monkeypatch):
    import pm.install as engine
    from pm import paths
    from pm.packages import Venv
    from hermes_constants import get_hermes_home

    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "tools"))
    monkeypatch.setattr(paths, "repo_root", lambda: root)
    home = get_hermes_home()
    calls = []
    def build(self, extras, **kwargs):
        calls.append(list(extras))
        from pm.environments import install_state_dir
        environment = install_state_dir(root) / "environments" / f"generation-{len(calls)}" / "venv"
        environment.mkdir(parents=True)
        (environment / "pyvenv.cfg").write_text("home = fixture\n")
        return {"environment": environment}
    monkeypatch.setattr(Venv, "apply", build)
    return engine, home, calls


def test_configured_client_admitted_without_legacy_install_and_retained(selection):
    engine, home, calls = selection
    engine.sync_venv(["web"], explicit=True)
    assert calls == [["web"]], "nonusers must not acquire the client"
    other = home / "profiles" / "other"
    other.mkdir(parents=True)
    config = other / "config.yaml"
    config.write_text("memory:\n  provider: hindsight\n")
    before = config.read_bytes()
    engine.sync_venv(explicit=True)
    assert calls[-1] == ["hindsight", "web"]
    assert engine.venv_is_current()
    assert config.read_bytes() == before
    config.write_text("memory:\n  provider: ''\n")
    engine.sync_venv(explicit=True)
    assert calls[-1] == ["hindsight", "web"]
    engine.sync_venv(repair=True, explicit=True)
    assert calls[-1] == ["hindsight", "web"]


def test_configured_provider_cannot_escape_frozen_feature_policy(selection, monkeypatch):
    from pm.features import write_features
    from pm.package import InstallError
    engine, home, calls = selection
    (home / "config.yaml").write_text("memory:\n  provider: hindsight\n")
    write_features(["web"])
    monkeypatch.setattr(engine, "lazy_installs_allowed", lambda: False)
    with pytest.raises(InstallError, match="frozen"):
        engine.sync_venv(["web"], explicit=True)
    assert calls == []


def test_same_named_home_plugin_cannot_expand_bundled_client_graph(selection):
    from pm.workspace import enabled_member_dirs
    engine, home, calls = selection
    (home / "config.yaml").write_text("memory:\n  provider: hindsight\n")
    plugin = home / "plugins" / "hindsight"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: hindsight\npip_dependencies:\n  - hindsight-embed==0.10.1\n")
    assert plugin not in enabled_member_dirs()
    engine.sync_venv(explicit=True)
    assert calls == [["hindsight"]]


@pytest.mark.parametrize("extras", [["web"], ["hindsight", "web"]], ids=["nonuser", "retained-client"])
@pytest.mark.parametrize("source_root", [False, True], ids=["default-probe", "source-launch-probe"])
def test_passive_currency_survives_broken_secondary_profile(selection, extras, source_root, caplog):
    """Real bundled discovery must agree with update's skip-broken-secondary policy."""
    from pm.environments import selected_venv
    from pm.paths import repo_root, runtime_facts_path
    from pm.plugin_inputs import Members

    engine, home, calls = selection
    root = repo_root()
    probe = {"project_root": root} if source_root else {}
    primary = home / "config.yaml"
    primary.write_text("plugins: {}\n")
    engine.sync_venv(extras, explicit=True)
    assert engine.venv_is_current(**probe) is True
    recorded = runtime_facts_path().read_bytes()
    selected = selected_venv(root)
    assert selected.is_dir()

    broken = home / "profiles" / "other" / "config.yaml"
    broken.parent.mkdir(parents=True)
    broken.write_text("plugins: [broken]\n")
    configs = {path: path.read_bytes() for path in (primary, broken)}
    # Admission remains strict, including a caller-supplied member list.
    for plugins in (None, Members([])):
        with pytest.raises(ValueError, match="plugins must be a mapping"):
            engine.sync_venv(explicit=True, plugins=plugins)
    engine.sync_venv(explicit=True, evict_incompatible_plugins=True)

    assert engine.venv_is_current(**probe) is True
    assert str(broken) in caplog.text
    assert runtime_facts_path().read_bytes() == recorded
    assert selected_venv(root) == selected
    assert calls == [extras], "passive inspection must neither rebuild nor add/remove extras"
    assert {path: path.read_bytes() for path in configs} == configs


def test_broken_primary_profile_still_fails_closed(selection):
    from pm.environments import selected_venv
    from pm.paths import repo_root, runtime_facts_path

    engine, home, calls = selection
    engine.sync_venv(["web"], explicit=True)
    recorded = runtime_facts_path().read_bytes()
    selected = selected_venv(repo_root())
    config = home / "config.yaml"
    config.write_text("plugins: [broken]\n")
    for probe in ({}, {"project_root": repo_root()}):
        with pytest.raises(ValueError, match="plugins must be a mapping"):
            engine.venv_is_current(**probe)
    for evict in (False, True):
        with pytest.raises(ValueError, match="plugins must be a mapping"):
            engine.sync_venv(explicit=True, evict_incompatible_plugins=evict)
    assert runtime_facts_path().read_bytes() == recorded
    assert selected_venv(repo_root()) == selected
    assert calls == [["web"]]
    assert config.read_text() == "plugins: [broken]\n"


def test_outdated_client_lazy_refusal_preserves_previous_generation(selection, monkeypatch):
    """The real provider request reaches PM policy without changing its selected graph."""
    import importlib.metadata
    import socket
    import subprocess

    from pm import receipt
    from pm.environments import selected_venv, site_packages
    from pm.package import InstallError
    from pm.paths import repo_root, runtime_facts_path
    from plugins.memory.hindsight import _maybe_upgrade_client

    engine, home, calls = selection
    engine.sync_venv(["web"], explicit=True)
    selected = selected_venv(repo_root())
    recorded = runtime_facts_path().read_bytes()
    # Model a booted generation carrying an old SDK, using real metadata lookup
    # and the real selected-environment check, not a stubbed sync or policy.
    site = site_packages(selected)
    metadata = site / "hindsight_client-0.1.0.dist-info" / "METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Metadata-Version: 2.1\nName: hindsight-client\nVersion: 0.1.0\n")
    monkeypatch.syspath_prepend(str(site))
    assert importlib.metadata.version("hindsight-client") == "0.1.0"
    config = home / "config.yaml"
    config.write_text("memory:\n  provider: hindsight\nsecurity:\n  allow_lazy_installs: false\n")
    before = config.read_bytes()
    monkeypatch.delenv("HERMES_DISABLE_LAZY_INSTALLS", raising=False)
    # Execute the real public client + engine in-process; only worker transport
    # and the fixture's generation builder are substituted.
    monkeypatch.setattr("pm.client.is_runtime", lambda: True)

    def forbidden(*args, **kwargs):
        pytest.fail("lazy refusal must not provision packages or contact a service")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    assert engine.lazy_installs_allowed() is False
    with pytest.raises(ImportError, match="lazy installs are disabled") as error:
        _maybe_upgrade_client()
    assert isinstance(error.value.__cause__, InstallError)
    assert "hindsight" in str(error.value)
    assert runtime_facts_path().read_bytes() == recorded
    assert selected_venv(repo_root()) == selected
    assert calls == [["web"]]
    assert config.read_bytes() == before
    refused = receipt.latest()
    assert refused["outcome"] == "failed"
    assert refused["refusal"]["code"] == "lazy-install"


def test_bundles_leave_carried_memory_client_opt_in():
    from pm.features import opt_in_extras
    root = Path(__file__).resolve().parents[2]
    assert "hindsight" in opt_in_extras(root)


def test_changed_provider_during_build_cannot_publish_reduced_graph(selection, monkeypatch):
    from pm.packages import Venv
    from pm.lock import Facts
    from pm.paths import runtime_facts_path
    engine, home, calls = selection
    original = Venv.apply
    def changed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        (home / "config.yaml").write_text("memory:\n  provider: hindsight\n")
        return result
    monkeypatch.setattr(Venv, "apply", changed)
    with pytest.raises(ValueError, match="Dependency inputs changed"):
        engine.sync_venv(["web"], explicit=True)
    assert Facts(runtime_facts_path()).get("venv") is None
