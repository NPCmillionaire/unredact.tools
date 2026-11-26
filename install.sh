#!/usr/bin/env bash
# unredact.tools installer
#
#   curl -fsSL https://raw.githubusercontent.com/NPCmillionaire/unredact.tools/main/install.sh | bash
#
# Options (pass after `bash -s --` when piping, e.g. `| bash -s -- --minimal`):
#   --minimal       metadata scanning only (no OCR / vision / city odds)
#   --no-system     don't install system packages (tesseract, ffmpeg); just the Python side
#   --with-ai       also install Ollama and pull the vision model for --scene / sun --auto
#   --ref REF       install a branch, tag or commit instead of main
#   --from DIR      install from a local checkout (for development)
#   --uninstall     remove unredact.tools
#   -y, --yes       don't ask before installing system packages
#
# What it does:
#   1. finds Python 3.10+ (installs it on macOS / with your package manager if missing)
#   2. installs tesseract (OCR) and ffmpeg (audio/video) with brew / apt / dnf / pacman / zypper / apk
#   3. creates an isolated virtualenv in ~/.local/share/unredact.tools (nothing touches system Python)
#   4. links the commands unredact.tools, unredact and unredact-file into ~/.local/bin
set -euo pipefail

REPO="https://github.com/NPCmillionaire/unredact.tools"
PREFIX="${UNREDACT_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/unredact.tools}"
BIN_DIR="${UNREDACT_BIN:-$HOME/.local/bin}"
VENV="$PREFIX/venv"
COMMANDS=(unredact.tools unredact unredact-file)
OLLAMA_MODEL="${UNREDACT_SCENE_MODEL:-qwen3-vl:8b}"

EXTRAS="all"; SYSTEM=1; AI=0; REF="main"; FROM=""; UNINSTALL=0; YES=0

# ------------------------------------------------------------------ output
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; D=$'\033[2m'; R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; O=$'\033[38;5;208m'; N=$'\033[0m'
else
  B=""; D=""; R=""; G=""; Y=""; O=""; N=""
fi
step() { printf '%s==>%s %s%s%s\n' "$O" "$N" "$B" "$*" "$N"; }
info() { printf '    %s%s%s\n' "$D" "$*" "$N"; }
ok()   { printf '    %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '    %s!%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'HELP'
unredact.tools installer

  curl -fsSL https://raw.githubusercontent.com/NPCmillionaire/unredact.tools/main/install.sh | bash
  curl -fsSL .../install.sh | bash -s -- [options]

options:
  --minimal       metadata scanning only (no OCR / vision / city odds)
  --no-system     don't install system packages (tesseract, ffmpeg)
  --with-ai       also install Ollama and pull the vision model (scan --scene, sun --auto)
  --ref REF       install a branch, tag or commit instead of main
  --from DIR      install from a local checkout (development)
  --uninstall     remove unredact.tools
  -y, --yes       don't ask before installing system packages

env: UNREDACT_HOME (install dir), UNREDACT_BIN (where commands go), UNREDACT_SCENE_MODEL
HELP
  exit 0
}

while [ $# -gt 0 ]; do
  case "$1" in
    --minimal)   EXTRAS="" ;;
    --no-system) SYSTEM=0 ;;
    --with-ai)   AI=1 ;;
    --ref)       REF="${2:?--ref needs a value}"; shift ;;
    --from)      FROM="${2:?--from needs a directory}"; shift ;;
    --uninstall) UNINSTALL=1 ;;
    -y|--yes)    YES=1 ;;
    -h|--help)   usage ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
  step "Removing unredact.tools"
  for c in "${COMMANDS[@]}"; do
    if [ -L "$BIN_DIR/$c" ] && [[ "$(readlink "$BIN_DIR/$c")" == "$VENV"/* ]]; then
      rm -f "$BIN_DIR/$c"; ok "removed $BIN_DIR/$c"
    fi
  done
  if [ -d "$PREFIX" ]; then rm -rf "$PREFIX"; ok "removed $PREFIX"; fi
  info "system packages (tesseract, ffmpeg) and Ollama were left alone"
  exit 0
fi

printf '\n  %sunredact.tools installer%s  %ssee what your files give away. then scrub it.%s\n\n' "$B$O" "$N" "$D" "$N"

# ------------------------------------------------------------ platform
OS="$(uname -s)"
PM=""
SUDO=""
if [ "$(id -u)" -ne 0 ] && have sudo; then SUDO="sudo"; fi
case "$OS" in
  Darwin) PM="brew" ;;
  Linux)
    for p in apt-get dnf pacman zypper apk; do
      if have "$p"; then PM="$p"; break; fi
    done ;;
  *) warn "unsupported OS $OS: skipping system packages"; SYSTEM=0 ;;
esac

confirm() {  # confirm "question" -> 0 yes / 1 no; defaults to yes when not interactive
  [ "$YES" = 1 ] && return 0
  if [ -r /dev/tty ]; then
    printf '    %s [Y/n] ' "$1" > /dev/tty
    local a; read -r a < /dev/tty || a=""
    case "$a" in n|N|no|NO) return 1 ;; esac
  fi
  return 0
}

pkg_install() {  # pkg_install <brew> <apt> <dnf> <pacman> <zypper> <apk>
  local name
  case "$PM" in
    brew)    name="$1" ;;
    apt-get) name="$2" ;;
    dnf)     name="$3" ;;
    pacman)  name="$4" ;;
    zypper)  name="$5" ;;
    apk)     name="$6" ;;
    *) return 1 ;;
  esac
  [ -z "$name" ] && return 1
  case "$PM" in
    brew)    brew install $name ;;
    apt-get) $SUDO apt-get install -y -qq $name ;;
    dnf)     $SUDO dnf install -y -q $name ;;
    pacman)  $SUDO pacman -S --needed --noconfirm $name ;;
    zypper)  $SUDO zypper -q install -y $name ;;
    apk)     $SUDO apk add -q $name ;;
  esac
}

if [ "$PM" = "brew" ] && ! have brew; then
  warn "Homebrew isn't installed; system packages will be skipped."
  info "install it from https://brew.sh and re-run, or continue with --no-system"
  PM=""; SYSTEM=0
fi
if [ -n "$PM" ] && [ "$PM" != "brew" ] && [ -z "$SUDO" ] && [ "$(id -u)" -ne 0 ]; then
  warn "no sudo: system packages will be skipped"; SYSTEM=0
fi

# ---------------------------------------------------------------- python
step "Checking Python"
find_python() {
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if have "$c" && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}
PY="$(find_python || true)"
if [ -z "$PY" ]; then
  [ "$SYSTEM" = 1 ] || die "Python 3.10+ is required (re-run without --no-system to install it)"
  confirm "Python 3.10+ not found. Install it with $PM?" || die "Python 3.10+ is required"
  [ "$PM" = "apt-get" ] && $SUDO apt-get update -qq
  pkg_install python@3.12 "python3 python3-venv" python3 python python3 python3 || true
  PY="$(find_python || true)"
  [ -n "$PY" ] || die "couldn't install Python 3.10+; install it yourself and re-run"
fi
ok "$("$PY" --version) at $PY"
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  if [ "$PM" = "apt-get" ] && [ "$SYSTEM" = 1 ]; then
    info "installing python3-venv"
    $SUDO apt-get install -y -qq "python3-venv" >/dev/null || $SUDO apt-get install -y -qq "python$("$PY" -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')-venv" >/dev/null
  else
    die "this Python has no venv/ensurepip module (Debian/Ubuntu: apt install python3-venv)"
  fi
fi

# ---------------------------------------------------------- system packages
if [ "$SYSTEM" = 1 ] && [ -n "$EXTRAS" ]; then
  step "System packages"
  need=()
  have tesseract || need+=("tesseract (OCR: text, plates)")
  have ffmpeg || need+=("ffmpeg (audio/video)")
  if [ ${#need[@]} -eq 0 ]; then
    ok "tesseract and ffmpeg already installed"
  elif confirm "Install ${need[*]} with $PM?"; then
    [ "$PM" = "apt-get" ] && $SUDO apt-get update -qq
    have tesseract || { pkg_install tesseract tesseract-ocr tesseract tesseract tesseract-ocr tesseract-ocr >/dev/null \
      && ok "tesseract" || warn "couldn't install tesseract: OCR and plate checks will be skipped"; }
    have ffmpeg || { pkg_install ffmpeg ffmpeg ffmpeg-free ffmpeg ffmpeg ffmpeg >/dev/null \
      && ok "ffmpeg" || warn "couldn't install ffmpeg: audio/video support will be skipped"; }
  else
    info "skipped: OCR, plate and audio/video checks need these later"
  fi
fi

# ------------------------------------------------------------ the package
step "Installing unredact.tools"
mkdir -p "$PREFIX" "$BIN_DIR"
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV" || die "couldn't create a virtualenv at $VENV"
fi
"$VENV/bin/python" -m pip install -q --upgrade pip >/dev/null
if [ -n "$FROM" ]; then
  SRC="$(cd "$FROM" && pwd)"
  SPEC="$SRC"
  info "from local checkout $SRC"
else
  SPEC="git+$REPO@$REF"
  info "from $REPO ($REF)"
  have git || warn "git not found: pip will need it to fetch from GitHub"
fi
[ -n "$EXTRAS" ] && SPEC="unredact-tools[$EXTRAS] @ $( [ -n "$FROM" ] && printf 'file://%s' "$SPEC" || printf '%s' "$SPEC" )"
"$VENV/bin/python" -m pip install -q --upgrade "$SPEC" || die "pip install failed"
VERSION="$("$VENV/bin/python" -c 'import unredact_tools; print(unredact_tools.__version__)')"
ok "unredact.tools $VERSION in $VENV"

for c in "${COMMANDS[@]}"; do
  if [ -e "$BIN_DIR/$c" ] && ! [ -L "$BIN_DIR/$c" ]; then
    warn "$BIN_DIR/$c exists and isn't ours; leaving it alone"
    continue
  fi
  ln -sf "$VENV/bin/$c" "$BIN_DIR/$c"
done
ok "commands: ${COMMANDS[*]} -> $BIN_DIR"

# ---------------------------------------------------------------- AI (opt-in)
if [ "$AI" = 1 ]; then
  step "AI scene review (Ollama + $OLLAMA_MODEL)"
  if ! have ollama; then
    if [ "$OS" = "Darwin" ] && have brew; then
      brew install ollama >/dev/null && ok "ollama"
    elif [ "$OS" = "Linux" ]; then
      confirm "Install Ollama with its official script (ollama.com/install.sh)?" \
        && curl -fsSL https://ollama.com/install.sh | sh
    fi
  fi
  if have ollama; then
    if ! curl -fsS http://localhost:11434/api/version >/dev/null 2>&1; then
      info "starting the Ollama server in the background"
      (ollama serve >/dev/null 2>&1 &) ; sleep 3
    fi
    ollama pull "$OLLAMA_MODEL" && ok "model $OLLAMA_MODEL ready" \
      || warn "couldn't pull $OLLAMA_MODEL; try later: ollama pull $OLLAMA_MODEL"
  else
    warn "Ollama not installed; --scene and sun --auto won't work until it is (ollama.com)"
  fi
fi

# ---------------------------------------------------------------- self-check
step "Checking the install"
"$VENV/bin/unredact.tools" --version >/dev/null || die "the installed command doesn't run"
feat() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else info "– $1 ($3)"; fi; }
feat "metadata, PDF, Office scanning" "true" ""
feat "visual checks (codes, faces, pixelation, printer dots, camera ID)" \
  "'$VENV/bin/python' -c 'import cv2, zxingcpp'" "re-run without --minimal"
feat "OCR + license plates" "have tesseract && '$VENV/bin/python' -c 'import cv2'" \
  "needs tesseract + the visual extra"
feat "audio/video" "have ffmpeg" "install ffmpeg"
feat "city odds for sun" "'$VENV/bin/python' -c 'import geonamescache'" "re-run without --minimal"
feat "AI scene review" "have ollama" "re-run with --with-ai"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    shell_rc="$HOME/.profile"
    case "${SHELL:-}" in
      */zsh)  shell_rc="$HOME/.zshrc" ;;
      */bash) shell_rc="$HOME/.bashrc" ;;
      */fish) shell_rc="$HOME/.config/fish/config.fish" ;;
    esac
    warn "$BIN_DIR is not on your PATH. Add it with:"
    if [[ "$shell_rc" == *fish* ]]; then
      printf '      fish_add_path %s\n' "$BIN_DIR"
    else
      printf '      echo '\''export PATH="%s:$PATH"'\'' >> %s && source %s\n' "$BIN_DIR" "$shell_rc" "$shell_rc"
    fi ;;
esac

printf '\n  %sDone.%s Try: %sunredact.tools scan photo.jpg%s   (or just %sunredact%s for help)\n' \
  "$G$B" "$N" "$B" "$N" "$B" "$N"
printf '  %supdate: re-run this installer · uninstall: add --uninstall%s\n\n' "$D" "$N"
