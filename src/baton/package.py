"""How this copy of Baton was installed, and the commands that reinstall or remove it.

install.sh installs with pipx or uv from the main branch on GitHub; the commands here
are the same ones, so an update lands exactly where the installer put Baton.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO = "https://github.com/bradley-t-t/baton"
BRANCH = "main"
PACKAGE_NAMES = ("claude-baton", "baton", "claude-handoff")

# Where pipx and uv usually live, for hooks that run with a short PATH.
TOOL_DIRS = ("~/.local/bin", "~/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin")


def installer() -> str:
    """"pipx" or "uv" when that tool installed the running copy, else ""."""
    prefix = Path(sys.prefix)
    if (prefix / "pipx_metadata.json").exists():
        return "pipx"
    if (prefix / "uv-receipt.toml").exists():
        return "uv"
    return ""


def search_path() -> str:
    """PATH with the usual pipx and uv folders added, since pipx may need uv and git."""
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for d in TOOL_DIRS:
        d = str(Path(d).expanduser())
        if d not in dirs and Path(d).is_dir():
            dirs.append(d)
    return os.pathsep.join(dirs)


def tool(name: str) -> str:
    """The path of the pipx or uv program, or ""."""
    return shutil.which(name, path=search_path()) or ""


def source() -> str:
    """What install.sh installs from: the git URL, or GitHub's tarball without git."""
    if shutil.which("git"):
        return "git+%s.git@%s" % (REPO, BRANCH)
    return "%s/archive/%s.tar.gz" % (REPO, BRANCH)


def upgrade_command() -> list:
    """Reinstall from main with the tool that installed this copy, or [] when it can't."""
    how = installer()
    path = tool(how) if how else ""
    if how == "pipx" and path:
        return [path, "install", "--force", source()]
    if how == "uv" and path:
        return [path, "tool", "install", "--force", "--reinstall", source()]
    return []


def removal_command() -> list:
    """The command that removes the installed baton package, for how it was installed."""
    how = installer()
    name = Path(sys.prefix).name
    if how == "pipx":
        return ["pipx", "uninstall", name]
    if how == "uv":
        return ["uv", "tool", "uninstall", name]
    name = _installed_name()
    if name:
        return [sys.executable, "-m", "pip", "uninstall", "-y", name]
    return []


def _installed_name() -> str:
    """This project's distribution name in the running Python. PyPI has an unrelated
    "baton", so a name only counts when its command runs our code."""
    from importlib import metadata
    for name in PACKAGE_NAMES:
        try:
            eps = metadata.distribution(name).entry_points
        except Exception:  # noqa: BLE001
            continue
        if any(ep.value.startswith(("baton.", "claude_handoff.")) for ep in eps):
            return name
    return ""
