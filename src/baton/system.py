"""What differs on Windows: where files live, background processes, the shell that
runs a provider's script, and the user PATH.

Everything else in Baton is the same on every system. These functions decide at call
time, so tests can stand in for Windows by patching os.name.
"""

from __future__ import annotations

import ntpath
import os
import shutil
import subprocess
from pathlib import Path

# Process creation flags from the Windows API, spelled out so this module also loads
# where the subprocess module doesn't define them.
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# install.ps1 records the folder it added to the user PATH here, so `baton uninstall
# --all` takes out that entry and never one the person or another tool added.
REGISTRY_KEY = r"Software\Baton"
PATH_VALUE = "PathAdded"


def windows() -> bool:
    return os.name == "nt"


def config_home() -> Path:
    """XDG_CONFIG_HOME when set, else %APPDATA% on Windows and ~/.config elsewhere."""
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"])
    if windows() and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"])
    return Path.home() / ".config"


def state_home() -> Path:
    """XDG_STATE_HOME when set, else %LOCALAPPDATA% on Windows and ~/.local/state
    elsewhere."""
    if os.environ.get("XDG_STATE_HOME"):
        return Path(os.environ["XDG_STATE_HOME"])
    if windows() and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"])
    return Path.home() / ".local" / "state"


def start_detached(command, **popen_args) -> subprocess.Popen:
    """Start a process that outlives this one, with no terminal and no input or output.

    On Windows it also leaves this process's job, when the job allows that, since some
    hosts (an SSH session, for one) end every process in their job when they close."""
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, close_fds=True)
    if not windows():
        return subprocess.Popen(command, start_new_session=True, **quiet, **popen_args)
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(command, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
                                **quiet, **popen_args)
    except OSError:  # the job doesn't allow leaving it
        return subprocess.Popen(command, creationflags=flags, **quiet, **popen_args)


def run_after_exit(commands: list, delay: int = 3) -> None:
    """Run the commands one after another, in a detached cmd.exe, once this process has
    exited. Windows keeps a running program's files locked, so Baton can't reinstall or
    remove itself while it is running. A command that fails stops the rest."""
    chain = " && ".join(subprocess.list2cmdline(c) for c in commands)
    # ping is the usual way to wait in cmd.exe: one ping a second, the first at once.
    line = "ping -n %d 127.0.0.1 >nul & %s" % (delay + 1, chain)
    start_detached('cmd.exe /d /s /c "%s"' % line)


def git_bash() -> str:
    """Git for Windows' bash.exe, as Claude Code finds it, or "". The bash.exe in
    System32 is WSL's, which runs in Linux with Linux paths, so it doesn't count."""
    candidates = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or ""]
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent  # <Git>\cmd\git.exe or <Git>\bin\git.exe
        candidates.append(str(root / "bin" / "bash.exe"))
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            sub = ("Programs", "Git") if var == "LOCALAPPDATA" else ("Git",)
            candidates.append(str(Path(base, *sub, "bin", "bash.exe")))
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return ""


def python_command() -> str:
    """The name that starts Python here. On Windows, python3 is often only the Store's
    placeholder under WindowsApps, which runs nothing."""
    for name in ("python3", "python", "py"):
        found = shutil.which(name)
        if found and not (windows() and "windowsapps" in found.lower()):
            return name
    return "python3"


def powershell() -> str:
    """PowerShell 7 (pwsh) when it's installed, else Windows PowerShell."""
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"


def script_shell() -> str:
    """The language a provider's script is written in: "bash" everywhere except on a
    Windows computer without Git Bash, where it's "powershell"."""
    if windows() and not git_bash():
        return "powershell"
    return "bash"


def script_command(path: str) -> list:
    """The command that runs the script file at path in script_shell()."""
    if script_shell() == "powershell":
        return [powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", path]
    return [git_bash() if windows() else "bash", path]


def _same_folder(a: str, b: str) -> bool:
    """Windows paths compare without case and without a trailing backslash."""
    def norm(p: str) -> str:
        return ntpath.normcase(ntpath.normpath(p.strip().rstrip("\\/")))
    return norm(a) == norm(b)


def remove_user_path_entry() -> str:
    """Take the folder install.ps1 added out of the Windows user PATH. Returns the
    folder, or "" when install.ps1 didn't add one or it's already gone."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as k:
            folder = winreg.QueryValueEx(k, PATH_VALUE)[0]
    except OSError:
        return ""
    removed = ""
    access = winreg.KEY_READ | winreg.KEY_WRITE
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, access) as k:
        try:
            value, kind = winreg.QueryValueEx(k, "Path")
        except OSError:
            value, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in value.split(";") if p]
        kept = [p for p in parts if not _same_folder(ntpath.expandvars(p), folder)]
        if kept != parts:
            winreg.SetValueEx(k, "Path", 0, kind, ";".join(kept))
            removed = folder
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY)
    except OSError:
        pass
    if removed:
        _announce_environment_change()
    return removed


def _announce_environment_change() -> None:
    """Tell open programs the user environment changed, as setx does, so new terminals
    started from Explorer see the new PATH."""
    import ctypes
    from ctypes import wintypes
    result = wintypes.DWORD()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000,
                                             ctypes.byref(result))


_vt_mode = None


def ansi_colors() -> bool:
    """Whether the console shows ANSI colors. Windows 10 and later do once the console's
    escape-sequence mode is on, which this turns on the first time it's asked."""
    global _vt_mode
    if not windows():
        return True
    if _vt_mode is None:
        _vt_mode = False
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # standard output
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                _vt_mode = bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
        except Exception:  # noqa: BLE001
            pass
    return _vt_mode
