"""Per-user update channel, shared with Desktop (not profile configuration)."""
import json
import os
import re
from pathlib import Path


class UpdateChannelError(ValueError):
    """An enrolled update channel cannot safely be used."""


def _validate_branch(branch: object, source: str) -> str:
    # Branch names, not arbitrary revision expressions/refspecs or git options.
    if (not isinstance(branch, str) or not branch or branch == "HEAD"
            or branch.startswith("-") or branch.endswith(".")
            or re.search(r"[\s\x00-\x1f\x7f~^:?*\[\\]", branch)
            or ".." in branch or "@{" in branch
            or any(not part or part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))):
        raise UpdateChannelError(f"Invalid update branch in {source}; select a non-empty Git branch name.")
    return branch


def update_config_path() -> Path:
    from hermes_cli.gui_uninstall import desktop_userdata_dir

    override = os.environ.get("HERMES_DESKTOP_USER_DATA_DIR")
    directory = (Path(override).resolve() if override else
                 Path(str(desktop_userdata_dir()) + os.environ.get("HERMES_DATA_DIR_SUFFIX", "")))
    return directory / "updates.json"


def resolve_update_branch(explicit: str | None = None, *, config_path: Path | None = None) -> str | None:
    """Explicit CLI branch > target Desktop policy; None preserves upstream policy."""
    if explicit is not None:
        return _validate_branch(explicit, "--branch")
    path = config_path if config_path is not None else update_config_path()
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        if not path.is_symlink():
            return None
        raise UpdateChannelError(f"Cannot read update channel {path}: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise UpdateChannelError(f"Cannot read update channel {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise UpdateChannelError(f"Invalid update channel {path}: expected a JSON object.")
    return _validate_branch(config["branch"], str(path)) if "branch" in config else None
