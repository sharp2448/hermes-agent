"""Hindsight's client request must join PM, never revive the retired installer."""
import pytest


def test_missing_client_requests_pm_extra_and_surfaces_refusal(monkeypatch):
    import pm
    from pm.package import InstallError
    from plugins.memory.hindsight import _ensure_client_dependency

    requested = []
    def denied(extra):
        requested.append(extra)
        raise InstallError("venv", "lazy installs disabled")
    monkeypatch.setattr(pm, "ensure_import", denied)
    with pytest.raises(ImportError, match="lazy installs disabled"):
        _ensure_client_dependency()
    assert requested == ["hindsight"]


def test_old_client_repairs_only_locked_pm_extra_then_requires_restart(monkeypatch):
    import pm
    import importlib.metadata
    from plugins.memory.hindsight import _maybe_upgrade_client
    requested = []
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.1.0")
    monkeypatch.setattr(pm, "sync_venv", lambda extras: requested.extend(extras))
    with pytest.raises(ImportError, match="restart"):
        _maybe_upgrade_client()
    assert requested == ["hindsight"]


def test_setup_uses_only_pm_client_extra(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import pm
    from plugins.memory.hindsight import setup
    requested, saved = [], []
    monkeypatch.setattr(setup, "_select", lambda *a: "local_external")
    monkeypatch.setattr("builtins.input", lambda *a: "")
    monkeypatch.setattr(setup, "_secret_prompt", lambda *a: "")
    monkeypatch.setattr(setup, "_write_env", lambda *a: None)
    monkeypatch.setattr(setup._hs_templates, "run_template_step", lambda **kw: None)
    monkeypatch.setattr("hermes_cli.config.save_config", lambda config: None)
    monkeypatch.setattr(pm, "sync_venv", lambda extras, **kw: requested.append((extras, kw)))
    provider = SimpleNamespace(_config={"bank_id": "kept-bank"}, save_config=lambda cfg, home: saved.append(cfg))
    try:
        setup.run_setup(provider, str(tmp_path), {"memory": {}})
    except ImportError:
        pass  # A retired installer must not satisfy the PM admission contract below.
    assert requested == [(["hindsight"], {"explicit": True})]
    assert saved[0]["bank_id"] == "kept-bank"
    assert saved[0]["mode"] == "local_external"
