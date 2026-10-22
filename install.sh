#!/usr/bin/env bash
# pistat installer — one-liner:
#
#   curl -fsSL https://raw.githubusercontent.com/thieuson1984/pistat/main/install.sh | bash
#
# Also works from a checkout (it then installs the local copy):
#
#   ./install.sh                 # user install into ~/.local
#   sudo ./install.sh --system   # /usr/local for every user
#   ./install.sh --uninstall
#
# It never needs root for a user install, is safe to re-run (updates in place),
# and stays PEP 668-friendly: if the system Python lacks textual/psutil it builds
# a private venv instead of pip-installing into the system interpreter.

set -eu

REPO="${PISTAT_REPO:-thieuson1984/pistat}"
REF="${PISTAT_REF:-main}"
PREFIX="${PISTAT_PREFIX:-$HOME/.local}"
PYTHON="${PISTAT_PYTHON:-}"
MODE="user"
ACTION="install"
NO_RUN=0
[ "${PISTAT_NO_RUN:-0}" = "1" ] && NO_RUN=1
MIN_TEXTUAL="0.47"
# marker printed above the PATH line this installer adds to shell startup files
PATH_MARK="# pistat installer: puts pistat on PATH"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'
else
  B=; D=; G=; Y=; R=; N=
fi
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$G" "$N" "$*"; }
info() { printf '%s·%s %s\n' "$D" "$N" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%s✗%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

usage() {
  cat <<EOF
${B}pistat installer${N}

  curl -fsSL https://raw.githubusercontent.com/$REPO/$REF/install.sh | bash

Options:
  --prefix DIR     install under DIR (default: \$HOME/.local)
  --system         install into /usr/local for all users (uses sudo if needed)
  --ref REF        git ref to install (branch/tag/commit, default: $REF)
  --python BIN     interpreter to use (default: first python3 >= 3.9 found)
  --no-run         do not start pistat after a successful install
  --uninstall      remove the launcher and the installed copy
  -h, --help       this text

Environment: PISTAT_PREFIX, PISTAT_REF, PISTAT_REPO, PISTAT_PYTHON,
             PISTAT_NO_RUN=1, NO_COLOR

If the repository is private, give the script a token (it is used for the
script's own downloads too):

  export GITHUB_TOKEN=\$(gh auth token)
  curl -fsSL -H "Authorization: token \$GITHUB_TOKEN" \\
    https://raw.githubusercontent.com/$REPO/$REF/install.sh | bash

Installs:
  <prefix>/lib/pistat/pistat.py          the dashboard (single file)
  <prefix>/lib/pistat/venv               private venv, only if deps are missing
  <prefix>/bin/pistat                    launcher

Uninstall later with:  $(basename "$0") --uninstall   (or install.sh --uninstall)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)    usage; exit 0 ;;
    --system)     MODE="system"; PREFIX="/usr/local"; shift ;;
    --prefix)     [ $# -ge 2 ] || die "--prefix needs a directory"
                  PREFIX="$2"; shift 2 ;;
    --prefix=*)   PREFIX="${1#*=}"; shift ;;
    --ref)        [ $# -ge 2 ] || die "--ref needs a value"
                  REF="$2"; shift 2 ;;
    --ref=*)      REF="${1#*=}"; shift ;;
    --python)     [ $# -ge 2 ] || die "--python needs a path"
                  PYTHON="$2"; shift 2 ;;
    --python=*)   PYTHON="${1#*=}"; shift ;;
    --no-run)     NO_RUN=1; shift ;;
    --uninstall|--remove) ACTION="uninstall"; shift ;;
    *)            warn "unknown option: $1"; usage; exit 2 ;;
  esac
done

# a relative --prefix would make the launcher depend on the caller's cwd
case "$PREFIX" in
  /*) : ;;
  *)  PREFIX="$(pwd)/${PREFIX#./}" ;;
esac

APP_DIR="$PREFIX/lib/pistat"
BIN_DIR="$PREFIX/bin"
LAUNCHER="$BIN_DIR/pistat"
SUDO=""
if [ "$MODE" = "system" ] && [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "--system needs root (and sudo is not installed)"
  SUDO="sudo"
fi

# ---------------------------------------------------------------- uninstall ---
if [ "$ACTION" = "uninstall" ]; then
  if [ -e "$LAUNCHER" ]; then $SUDO rm -f "$LAUNCHER"; ok "removed $LAUNCHER"; else info "no launcher at $LAUNCHER"; fi
  if [ -d "$APP_DIR" ]; then $SUDO rm -rf "$APP_DIR"; ok "removed $APP_DIR"; else info "no files at $APP_DIR"; fi
  # take back the PATH line the installer added (marker line + the export below it)
  for rc in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.zshrc" "$HOME/.profile"; do
    [ -f "$rc" ] || continue
    grep -qF "$PATH_MARK" "$rc" 2>/dev/null || continue
    sed -i "/$PATH_MARK/,+1d" "$rc" 2>/dev/null \
      && ok "cleaned PATH line from $rc"
  done
  if [ -f "$HOME/.config/fish/conf.d/pistat.fish" ]; then
    rm -f "$HOME/.config/fish/conf.d/pistat.fish" && ok "removed the fish PATH entry"
  fi
  info "preferences kept: ${XDG_CONFIG_HOME:-$HOME/.config}/pistat/config.json"
  say ""
  ok "pistat uninstalled"
  exit 0
fi

# ------------------------------------------------------------------ helpers ---
fetch() {   # fetch URL DEST  (falls back to a token for private repos)
  local url="$1" dest="$2" tok=""
  if command -v curl >/dev/null 2>&1; then
    if curl -fsSL --retry 3 --connect-timeout 15 "$url" -o "$dest" 2>/dev/null; then
      return 0
    fi
    tok="$(github_token || true)"
    [ -n "$tok" ] || return 1
    curl -fsSL --retry 3 --connect-timeout 15 \
         -H "Authorization: token $tok" "$url" -o "$dest"
  elif command -v wget >/dev/null 2>&1; then
    if wget -q -O "$dest" "$url" 2>/dev/null; then
      return 0
    fi
    tok="$(github_token || true)"
    [ -n "$tok" ] || return 1
    wget -q -O "$dest" --header="Authorization: token $tok" "$url"
  else
    die "need curl or wget on PATH"
  fi
}

github_token() {   # $GITHUB_TOKEN / $GH_TOKEN / gh auth token, whichever exists
  if [ -n "${GITHUB_TOKEN:-}" ]; then printf '%s' "$GITHUB_TOKEN"; return 0; fi
  if [ -n "${GH_TOKEN:-}" ]; then printf '%s' "$GH_TOKEN"; return 0; fi
  if command -v gh >/dev/null 2>&1; then
    gh auth token 2>/dev/null && return 0
  fi
  return 1
}

pick_python() {
  local cands="$PYTHON python3 python3.13 python3.12 python3.11 python3.10 python3.9"
  local c p
  for c in $cands; do
    [ -n "$c" ] || continue
    p="$(command -v "$c" 2>/dev/null)" || continue
    if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      printf '%s' "$p"; return 0
    fi
  done
  return 1
}

deps_ok() {  # deps_ok PYTHON -> 0 if textual/rich/psutil are importable and new enough
  "$1" - "$MIN_TEXTUAL" >/dev/null 2>&1 <<'PY'
import sys
import textual, rich, psutil                       # noqa: F401

def ver(text):
    parts = []
    for bit in text.split(".")[:3]:
        digits = "".join(ch for ch in bit if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)

want = tuple(int(x) for x in sys.argv[1].split("."))
raise SystemExit(0 if ver(textual.__version__) >= want else 1)
PY
}

# --------------------------------------------------------------- interpreter --
PY="$(pick_python)" || die "no python3 >= 3.9 found (apt install python3)"
info "python   $PY ($("$PY" -c 'import platform;print(platform.python_version())'))"

SELF="${BASH_SOURCE[0]:-}"
SRC_DIR=""
if [ -n "$SELF" ] && [ -f "$SELF" ]; then
  SRC_DIR="$(cd "$(dirname "$SELF")" && pwd)"
  info "source   $SRC_DIR (local checkout)"
else
  info "source   https://github.com/$REPO @ $REF"
fi

# -------------------------------------------------------------------- files ---
info "target   $APP_DIR"
$SUDO mkdir -p "$APP_DIR" "$BIN_DIR"
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t pistat)"
trap 'rm -rf "$TMP"' EXIT INT TERM

grab() {  # grab FILENAME DEST
  if [ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/$1" ]; then
    cp "$SRC_DIR/$1" "$2"
  else
    fetch "https://raw.githubusercontent.com/$REPO/$REF/$1" "$2" \
      || die "download failed: $1 (check repo/ref: $REPO@$REF)"
  fi
}

for f in pistat.py requirements.txt README.md; do
  grab "$f" "$TMP/$f"
  [ -s "$TMP/$f" ] || die "$f came back empty"
done
"$PY" -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$TMP/pistat.py" \
  || die "downloaded pistat.py is not valid Python"
$SUDO cp "$TMP/pistat.py" "$TMP/requirements.txt" "$TMP/README.md" "$APP_DIR/"
ok "files    pistat.py + README.md in $APP_DIR"

# ------------------------------------------------------------- dependencies ---
RUN_PY="$PY"
if deps_ok "$PY"; then
  ok "deps     textual/rich/psutil already available to $PY"
else
  VENV="$APP_DIR/venv"
  info "deps     building a private venv (system python is missing textual)"
  if [ ! -x "$VENV/bin/python" ]; then
    if ! $SUDO "$PY" -m venv "$VENV" 2>"$TMP/venv.err"; then
      sed 's/^/    /' "$TMP/venv.err" >&2 || true
      die "could not create a venv. On Debian/Ubuntu: sudo apt install python3-venv"
    fi
  fi
  $SUDO "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
      -r "$TMP/requirements.txt" \
    || die "pip install failed (offline? proxy?) — see the error above"
  deps_ok "$VENV/bin/python" || die "venv installed but textual is still unusable"
  RUN_PY="$VENV/bin/python"
  ok "deps     textual/rich/psutil in $VENV"
fi

# ---------------------------------------------------------------- launcher ---
if [ -e "$LAUNCHER" ] && ! grep -q "pistat launcher" "$LAUNCHER" 2>/dev/null; then
  $SUDO cp "$LAUNCHER" "$LAUNCHER.bak"
  warn "existing $LAUNCHER was not ours — saved as pistat.bak"
fi
$SUDO tee "$LAUNCHER" >/dev/null <<EOF
#!/bin/sh
# pistat launcher — installed by install.sh (repo: $REPO, ref: $REF)
exec "$RUN_PY" "$APP_DIR/pistat.py" "\$@"
EOF
$SUDO chmod 0755 "$LAUNCHER"
ok "launcher $LAUNCHER"

# ---------------------------------------------------------- shell integration ---
ensure_path() {
  # `pistat` must work from any directory. The launcher itself is absolute, so
  # the only thing missing on a fresh machine is $BIN_DIR on PATH: add it to the
  # startup file of every shell we can find, exactly once.
  case ":$PATH:" in
    *":$BIN_DIR:"*)
      ok "path     $BIN_DIR already on \$PATH"
      return 0 ;;
  esac
  if [ "$MODE" = "system" ]; then
    warn "$BIN_DIR is not on \$PATH — add it to /etc/profile by hand"
    return 0
  fi

  added=""
  for rc in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.zshrc" "$HOME/.profile"; do
    [ -f "$rc" ] || continue
    grep -qF "$PATH_MARK" "$rc" 2>/dev/null && continue
    if printf '\n%s\nexport PATH="%s:$PATH"\n' "$PATH_MARK" "$BIN_DIR" >>"$rc" 2>/dev/null; then
      added="$added $(basename "$rc")"
    fi
  done
  if [ -z "$added" ]; then          # no startup file matched: create ~/.profile
    if printf '%s\nexport PATH="%s:$PATH"\n' "$PATH_MARK" "$BIN_DIR" >>"$HOME/.profile" 2>/dev/null; then
      added=" .profile"
    fi
  fi
  # fish does not read any of the above
  if [ -d "$HOME/.config/fish" ]; then
    fish_conf="$HOME/.config/fish/conf.d/pistat.fish"
    if [ ! -f "$fish_conf" ] || ! grep -qF "$PATH_MARK" "$fish_conf" 2>/dev/null; then
      mkdir -p "$(dirname "$fish_conf")" 2>/dev/null || true
      if printf '%s\nset -gx PATH "%s" $PATH\n' "$PATH_MARK" "$BIN_DIR" >>"$fish_conf" 2>/dev/null; then
        added="$added fish"
      fi
    fi
  fi

  if [ -n "$added" ]; then
    ok "path     added $BIN_DIR to:$added"
    info "         'pistat' works from any directory in a new shell (this one: exec \$SHELL -l)"
  else
    warn "could not edit your shell startup files — add this line yourself:"
    say "  export PATH=\"$BIN_DIR:\$PATH\""
  fi
}

# ------------------------------------------------------------------- verify ---
VER="$("$LAUNCHER" --version 2>&1)" || die "launcher does not run: $VER"
ok "verify   $VER"
( cd / && "$LAUNCHER" --version >/dev/null 2>&1 ) \
  || die "launcher only works from its own directory — the paths are wrong"
ok "verify   runs from any directory"
if ! "$LAUNCHER" --json -i 0.2 >"$TMP/snap.json" 2>"$TMP/snap.err"; then
  sed 's/^/    /' "$TMP/snap.err" >&2 || true
  die "snapshot test failed — run '$LAUNCHER --once' to see why"
fi
"$RUN_PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["processes"]["count"] > 0' \
  "$TMP/snap.json" || die "JSON snapshot looked wrong"
ok "verify   live snapshot ($("$RUN_PY" -c 'import json,sys;print(json.load(open(sys.argv[1]))["processes"]["count"])' "$TMP/snap.json") processes)"

# ------------------------------------------------------------------ epilogue ---
say ""
ensure_path
say "${B}Installed${N} — $LAUNCHER"
say "  pistat               interactive TUI (q to quit)"
say "  pistat --once        one plain-text snapshot"
say "  pistat --json -i 0.5 machine-readable, good for cron/monitoring"
say "  pistat --themes      list themes (^p → Change theme in the TUI)"
say "  pistat --help        every flag and key"
say "${D}Your theme is remembered in \$XDG_CONFIG_HOME/pistat/config.json${N}"
say "${D}Remove it with: bash install.sh --uninstall${PREFIX:+ --prefix $PREFIX}${N}"

# --------------------------------------------------------------- start it up ---
# Go straight into the TUI; asking the user to run it again after installing is
# just busywork. Skipped with --no-run / PISTAT_NO_RUN=1 or without a terminal.
if [ "$NO_RUN" = "0" ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
  say ""
  say "${D}starting pistat — q quits, ^p → Change theme${N}"
  exec "$LAUNCHER" </dev/tty >/dev/tty 2>&1
elif [ "$NO_RUN" = "0" ]; then
  info "no terminal attached — start it yourself with: pistat"
fi
