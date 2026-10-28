"""How this copy of Baton was installed, the commands that reinstall or remove it, and
the PATH entry the installer adds: a line in shell startup files from install.sh, or a
folder in the Windows user PATH from install.ps1.

install.sh and install.ps1 install with pipx or uv from the main branch on GitHub; the commands here
are the same ones, so an update lands exactly where the installer put Baton.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import system

REPO = "https://github.com/bradley-t-t/baton"
BRANCH = "main"
PACKAGE_NAMES = ("claude-baton", "baton", "claude-handoff")

# Where pipx and uv usually live, for hooks that run with a short PATH. Folders that
# don't exist are skipped, so one list serves every system.
TOOL_DIRS = ("~/.local/bin", "~/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin",
             "~/AppData/Local/Microsoft/WinGet/Links", "~/scoop/shims")


def install_command() -> str:
    """The one-line installer for this system."""
    if system.windows():
        return "irm https://taylorurl.com/baton.ps1 | iex"
    return "curl -fsSL https://taylorurl.com/baton | sh"


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


# install.sh ends the PATH line it adds with this, so only Baton's own line is removed.
PATH_MARK = "# added by Baton"


def startup_files() -> list:
    """Every shell startup file install.sh may have added its PATH line to."""
    home = Path.home()
    zdot = Path(os.environ.get("ZDOTDIR") or home)
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    files = [zdot / ".zshrc", home / ".zshrc", home / ".bashrc", home / ".bash_profile",
             home / ".bash_login", home / ".profile", config / "fish" / "conf.d" / "baton.fish"]
    return list(dict.fromkeys(files))


def remove_path_lines() -> list:
    """Take Baton's PATH line out of the startup files, or on Windows its folder out of
    the user PATH. A file left empty that only Baton wrote (fish's conf.d/baton.fish) is
    deleted. Returns where it was removed from."""
    if system.windows():
        return ["your Windows user PATH"] if system.remove_user_path_entry() else []
    changed = []
    for f in startup_files():
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = text.splitlines(keepends=True)
        kept = [ln for ln in lines if not ln.rstrip().endswith(PATH_MARK)]
        if kept == lines:
            continue
        if not "".join(kept).strip() and f.name == "baton.fish":
            f.unlink()
        else:
            f.write_text("".join(kept), encoding="utf-8")
        changed.append(str(f))
    return changed


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
