"""API keys in ~/.config/baton/secrets.env: KEY=value lines, readable only by you (0600).

The hooks source this file before they run, so a key there is in the environment
the providers read. Values are never printed.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def secrets_path() -> Path:
    from .install import secrets_env_path
    return secrets_env_path()


def read(path: Path = None) -> dict:
    """KEY -> value from the secrets file (the new one, else the claude-handoff one)."""
    from .install import old_secrets_env_path
    path = path or secrets_path()
    if not path.exists() and old_secrets_env_path().exists():
        path = old_secrets_env_path()
    out = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for raw in text.splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        val = m.group(2).strip()
        try:
            parts = shlex.split(val)
            val = parts[0] if parts else ""
        except ValueError:
            pass
        out[m.group(1)] = val
    return out


def write(name: str, value: str, path: Path = None) -> Path:
    """Set NAME=value in the secrets file, keeping other lines. The file is 0600."""
    path = path or secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    new = "%s=%s" % (name, shlex.quote(value))
    kept, done = [], False
    for raw in lines:
        m = LINE.match(raw)
        if m and m.group(1) == name:
            if not done:
                kept.append(new)
                done = True
            continue
        kept.append(raw)
    if not done:
        kept.append(new)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(kept) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def mode(path: Path = None):
    """The file's permission bits, or None when it doesn't exist."""
    path = path or secrets_path()
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return None


def has_key(env_name: str) -> bool:
    if not env_name:
        return True
    return bool(os.environ.get(env_name) or read().get(env_name))


def load_into_env():
    """Put keys from the secrets file into os.environ when they aren't set already."""
    for k, v in read().items():
        os.environ.setdefault(k, v)
