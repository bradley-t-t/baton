#!/bin/sh
# Install Baton: curl -fsSL https://taylorurl.com/baton | sh
#
# Installs the baton command with pipx or uv (installing uv into your home folder if
# neither is there), removes an old pip install of claude-handoff, adds the bin folder
# to PATH in your login shell's startup file, then runs `baton setup` and
# `baton doctor`. Nothing is installed system-wide and sudo is never used. Set
# "install": {"remove_old": false} in ~/.config/baton/config.json to keep an old install.
#
# Arguments after `sh -s --` go to `baton setup`, for example:
#   curl -fsSL .../install.sh | sh -s -- --provider local --yes
# Environment: BATON_REF (branch, tag or commit; default main),
#              BATON_INSTALLER (pipx or uv, to choose one).

set -eu

REPO="https://github.com/bradley-t-t/baton"
PKG="claude-baton"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != "dumb" ]; then
    B=$(printf '\033[1m'); D=$(printf '\033[2m'); G=$(printf '\033[32m')
    Y=$(printf '\033[33m'); R=$(printf '\033[31m'); N=$(printf '\033[0m')
else
    B=""; D=""; G=""; Y=""; R=""; N=""
fi

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '%sok%s  %s\n' "$G" "$N" "$*"; }
note() { printf '%snote%s  %s\n' "$Y" "$N" "$*"; }
die()  { printf '%serror%s  %s\n' "$R" "$N" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# A terminal to ask questions on, even when this script comes in through a pipe.
TTY=""
if (exec </dev/tty) 2>/dev/null; then TTY=1; fi

# ask "Question" default(y|n) -> 0 for yes. Without a terminal the default is used.
ask() {
    if [ -z "$TTY" ]; then [ "$2" = y ]; return; fi
    if [ "$2" = y ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    printf '%s %s ' "$1" "$hint" >/dev/tty
    read -r reply </dev/tty || reply=""
    case "$reply" in
        [Yy]*) return 0 ;;
        [Nn]*) return 1 ;;
        *) [ "$2" = y ] ;;
    esac
}

is_mac() { [ "$(uname -s)" = Darwin ]; }

# On a Mac without the Command Line Tools, /usr/bin/python3 and /usr/bin/git only
# open an install dialog. Treat them as missing.
usable() {
    p=$(command -v "$1" 2>/dev/null) || return 1
    if is_mac && [ "$p" = "/usr/bin/$1" ] && ! xcode-select -p >/dev/null 2>&1; then
        return 1
    fi
    return 0
}

# Pythons that might hold an old pip install of claude-handoff or baton.
pythons() {
    seen=""
    for p in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        case "$p" in
            /*) [ -x "$p" ] || continue ;;
            *) usable "$p" || continue; p=$(command -v "$p") ;;
        esac
        if is_mac && [ "$p" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then
            continue
        fi
        real=$("$p" -c 'import os,sys; print(os.path.realpath(sys.executable))' 2>/dev/null) || continue
        case " $seen " in *" $real "*) continue ;; esac
        seen="$seen $real"
        printf '%s\n' "$p"
    done
}

# Names of this project's packages installed with pip in that Python. PyPI has an
# unrelated package called baton, so a name only counts when it was installed from a
# folder or git (direct_url.json) and its command runs baton.cli or claude_handoff.
OLD_FINDER='
import sys
try:
    from importlib import metadata
except ImportError:
    sys.exit(0)
for name in ("claude-handoff", "baton", "claude-baton"):
    try:
        d = metadata.distribution(name)
    except Exception:
        continue
    eps = [e.value for e in d.entry_points]
    ours = any(v.startswith(("baton.cli", "claude_handoff.")) for v in eps)
    if name == "claude-handoff" or (ours and d.read_text("direct_url.json")):
        print(name)
'

# "install": {"remove_old": false} in the config keeps an old install. Default: remove.
remove_old_wanted() {
    cfg="${BATON_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/baton/config.json}"
    [ -f "$cfg" ] || return 0
    for py in $(pythons); do
        "$py" - "$cfg" <<'PY' 2>/dev/null && return 0
import json, sys
try:
    c = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
sys.exit(1 if ((c.get("install") or {}).get("remove_old", True) is False) else 0)
PY
        return 1
    done
    return 0
}

cleanup_old() {
    step "Checking for an older install"
    found=""
    for py in $(pythons); do
        names=$("$py" -c "$OLD_FINDER" 2>/dev/null) || continue
        [ -n "$names" ] || continue
        found=1
        names=$(printf '%s' "$names" | tr '\n' ' ')
        say "Found ${B}${names% }${N} installed with pip in $py"
        if [ -z "$REMOVE_OLD" ]; then
            note "Kept it (install.remove_old is false). Its baton command may hide the new one."
            continue
        fi
        # shellcheck disable=SC2086
        if out=$("$py" -m pip uninstall -y $names 2>&1); then
            ok "Uninstalled $names"
        elif printf '%s' "$out" | grep -q externally-managed; then
            # The package was installed by hand, so removing it is safe here.
            # shellcheck disable=SC2086
            if "$py" -m pip uninstall -y --break-system-packages $names >/dev/null 2>&1; then
                ok "Uninstalled $names"
            else
                note "Couldn't uninstall $names. Run: $py -m pip uninstall $names"
            fi
        else
            note "Couldn't uninstall $names. Run: $py -m pip uninstall $names"
        fi
    done
    [ -n "$found" ] || ok "None found"
}

pipx_has() { have pipx && NO_COLOR=1 pipx list --short 2>/dev/null | grep -q "^$PKG "; }
uv_has()   { have uv && uv tool list --color never 2>/dev/null | grep -q "^$PKG "; }

choose_installer() {
    step "Choosing an installer"
    case "${BATON_INSTALLER:-}" in
        pipx) have pipx || die "BATON_INSTALLER=pipx but pipx isn't installed"; INSTALLER=pipx; return ;;
        uv)   have uv || install_uv; INSTALLER=uv; return ;;
        "") ;;
        *) die "BATON_INSTALLER must be pipx or uv" ;;
    esac
    if pipx_has; then INSTALLER=pipx
    elif uv_has; then INSTALLER=uv
    elif have pipx; then INSTALLER=pipx
    elif have uv; then INSTALLER=uv
    else
        say "Baton installs with pipx or uv, and neither is here."
        say "uv is a single program from Astral. Its installer puts it in ~/.local/bin."
        ask "Install uv now?" y || die "Install pipx or uv, then run this again."
        install_uv
        INSTALLER=uv
    fi
    ok "Using $INSTALLER"
}

install_uv() {
    say "Installing uv from https://astral.sh/uv"
    if have curl; then
        curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null \
            || die "The uv installer failed."
    elif have wget; then
        wget -qO- https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null \
            || die "The uv installer failed."
    else
        die "Need curl or wget to install uv."
    fi
    for d in "${XDG_BIN_HOME:-}" "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        if [ -n "$d" ] && [ -x "$d/uv" ]; then PATH="$d:$PATH"; export PATH; break; fi
    done
    have uv || die "uv was installed but can't be found. Open a new shell and run this again."
    ok "Installed $(uv --version)"
}

source_url() {
    ref="${BATON_REF:-main}"
    if usable git; then
        printf 'git+%s.git@%s\n' "$REPO" "$ref"
    else
        # No git: GitHub's tarball of the same ref works without it.
        printf '%s/archive/%s.tar.gz\n' "$REPO" "$ref"
    fi
}

# baton/package.py runs these same install commands when Baton updates itself.
install_baton() {
    url=$(source_url)
    step "Installing Baton"
    say "${D}from $url${N}"
    if [ "$INSTALLER" = pipx ]; then
        pipx_has && say "Baton is already installed with pipx, so it's reinstalled."
        pipx install --force "$url" >/dev/null 2>"$LOG" || { cat "$LOG" >&2; die "pipx install failed."; }
        BIN_DIR=$(NO_COLOR=1 pipx environment --value PIPX_BIN_DIR 2>/dev/null || printf '%s' "$HOME/.local/bin")
    else
        uv_has && say "Baton is already installed with uv, so it's reinstalled."
        uv tool install --force --reinstall "$url" >/dev/null 2>"$LOG" || { cat "$LOG" >&2; die "uv tool install failed."; }
        BIN_DIR=$(NO_COLOR=1 uv tool dir --bin --color never)
    fi
    [ -x "$BIN_DIR/baton" ] || die "Installed, but $BIN_DIR/baton is missing."
    ok "$("$BIN_DIR/baton" --version) in $BIN_DIR"
}

# Startup files get one line, marked so `baton uninstall --all` can take it out again.
PATH_MARK="# added by Baton"

# The login shell's name: zsh, bash, fish, sh...
login_shell() {
    sh_path="${SHELL:-}"
    if [ -z "$sh_path" ]; then
        if is_mac; then
            sh_path=$(dscl . -read "/Users/$(id -un)" UserShell 2>/dev/null | awk '{print $2}')
        elif have getent; then
            sh_path=$(getent passwd "$(id -un)" | cut -d: -f7)
        fi
    fi
    basename "${sh_path:-sh}"
}

# The startup files that shell reads, one per line. Terminal on macOS opens login
# shells, so bash there needs its login file as well as .bashrc.
path_files() {
    case "$1" in
        zsh) printf '%s\n' "${ZDOTDIR:-$HOME}/.zshrc" ;;
        bash)
            printf '%s\n' "$HOME/.bashrc"
            for f in .bash_profile .bash_login .profile; do
                if [ -f "$HOME/$f" ]; then printf '%s\n' "$HOME/$f"; return; fi
            done
            if is_mac; then printf '%s\n' "$HOME/.bash_profile"; fi ;;
        fish) printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d/baton.fish" ;;
        *) printf '%s\n' "$HOME/.profile" ;;
    esac
}

# The line itself; $PATH is written literally, for the shell to expand.
# shellcheck disable=SC2016
path_line() {
    if [ "$1" = fish ]; then
        printf 'contains -- "%s" $PATH; or set -gx PATH "%s" $PATH  %s\n' "$BIN_DIR" "$BIN_DIR" "$PATH_MARK"
    else
        printf 'case ":$PATH:" in *":%s:"*) ;; *) export PATH="%s:$PATH" ;; esac  %s\n' \
            "$BIN_DIR" "$BIN_DIR" "$PATH_MARK"
    fi
}

# Put BIN_DIR on PATH in the login shell's startup files. Nothing is written when the
# PATH you ran this with already has it, or when a file already has Baton's line.
# (pipx ensurepath picks the file from the shell running it, which is this sh, so it
# wrote ~/.profile, which zsh never reads.)
check_path() {
    case ":$ORIG_PATH:" in
        *":$BIN_DIR:"*) return ;;
    esac
    PATH="$BIN_DIR:$PATH"; export PATH
    NEW_SHELL=1
    step "PATH"
    shell=$(login_shell)
    line=$(path_line "$shell")
    files=$(path_files "$shell")
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        if [ -f "$f" ] && grep -F "$PATH_MARK" "$f" | grep -qF "$BIN_DIR"; then
            ok "$BIN_DIR is already added in $f"
            continue
        fi
        mkdir -p "$(dirname "$f")"
        # Start on a new line if the file doesn't end with one.
        if [ -s "$f" ] && [ -n "$(tail -c 1 "$f")" ]; then printf '\n' >>"$f"; fi
        if printf '%s\n' "$line" >>"$f"; then
            ok "Added $BIN_DIR to PATH in $f"
        else
            note "Couldn't write $f. Add $BIN_DIR to your PATH yourself."
        fi
    done <<EOF
$files
EOF
}

run_setup() {
    if [ -n "$TTY" ]; then
        step "Setting up"
        "$BIN_DIR/baton" setup "$@" </dev/tty || die "baton setup didn't finish. Run \`baton setup\` to try again."
    elif [ $# -gt 0 ]; then
        step "Setting up"
        "$BIN_DIR/baton" setup "$@" || die "baton setup failed."
    else
        note "No terminal to ask questions on, so setup was skipped."
        say "Finish with: baton setup"
        say "Or without questions, for example: baton setup --provider local --yes"
        return 1
    fi
}

main() {
    say "${B}Baton installer${N}"
    ORIG_PATH="$PATH"
    NEW_SHELL=""
    LOG=$(mktemp "${TMPDIR:-/tmp}/baton-install.XXXXXX")
    trap 'rm -f "$LOG"' EXIT INT TERM
    [ -n "$TTY" ] || note "No terminal found, so nothing will be asked."
    REMOVE_OLD=1
    remove_old_wanted || REMOVE_OLD=""

    cleanup_old
    choose_installer
    install_baton
    check_path
    if run_setup "$@"; then
        step "Checking"
        "$BIN_DIR/baton" doctor || true
        printf '\n%sDone.%s Start a new Claude Code session to use Baton.\n' "$B" "$N"
        say "To remove it later: baton uninstall --all"
    fi
    if [ -n "$NEW_SHELL" ]; then
        printf '\n%sOpen a new terminal (or run: exec %s -l) before using the baton command.%s\n' \
            "$B" "$(login_shell)" "$N"
    fi
}

# BATON_INSTALL_FUNCTIONS=1 only defines the functions, for the tests.
[ -n "${BATON_INSTALL_FUNCTIONS:-}" ] || main "$@"
