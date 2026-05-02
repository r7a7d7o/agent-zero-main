#!/usr/bin/env bash
set -euo pipefail

SESSION="${A0_DESKTOP_SESSION:-agent-zero-desktop}"
BASE_DIR="${A0_BASE_DIR:-/a0}"
PROFILE_DIR="${A0_DESKTOP_PROFILE:-$BASE_DIR/tmp/_office/desktop/profiles/$SESSION}"
MANIFEST="${A0_DESKTOP_MANIFEST:-$BASE_DIR/tmp/_office/desktop/sessions/$SESSION.json}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

display_from_manifest() {
  if [ ! -f "$MANIFEST" ] || ! command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  python3 - "$MANIFEST" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        value = json.load(handle).get("display", "")
except Exception:
    value = ""
if value != "":
    print(value)
PY
}

DISPLAY_VALUE="${A0_DESKTOP_DISPLAY:-$(display_from_manifest || true)}"
DISPLAY_VALUE="${DISPLAY_VALUE:-120}"
case "$DISPLAY_VALUE" in
  :*) export DISPLAY="$DISPLAY_VALUE" ;;
  *) export DISPLAY=":$DISPLAY_VALUE" ;;
esac

export XAUTHORITY="${A0_DESKTOP_XAUTHORITY:-$PROFILE_DIR/.Xauthority}"
export HOME="${A0_DESKTOP_HOME:-$PROFILE_DIR}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-XFCE}"

command_name="${1:-help}"
shift || true

usage() {
  cat <<'EOF'
Usage: desktopctl.sh <command> [args]

Commands:
  env                         Print the X11 environment used for the Desktop.
  check                       Verify that xdotool can reach the Desktop display.
  location                    Print the current X pointer location.
  windows [PATTERN]           List visible window names matching PATTERN.
  focus PATTERN               Focus the first visible window matching PATTERN.
  key KEY...                  Send one or more xdotool key names.
  type TEXT                   Type text into the focused window.
  click X Y                   Move and click at X,Y in Desktop coordinates.
  dblclick X Y                Move and double-click at X,Y in Desktop coordinates.
  launch APP                  Launch writer, calc, impress, terminal, settings, or workdir.
  open-path [PATH]            Open PATH in Thunar, defaulting to /a0/usr/workdir.
  calc-set-cell FILE SHEET CELL VALUE
                              Open FILE in visible Calc, set SHEET!CELL, save, and verify.
  save                        Send Ctrl+S to the focused app.
EOF
}

require_xdotool() {
  if ! command -v xdotool >/dev/null 2>&1; then
    echo "xdotool is required for Desktop control." >&2
    exit 2
  fi
}

ensure_display() {
  require_xdotool
  if ! xdotool getmouselocation >/dev/null 2>&1; then
    echo "Desktop X display is not reachable. Open the Desktop surface first." >&2
    exit 2
  fi
}

run_detached() {
  ( "$@" >/tmp/a0-desktopctl.log 2>&1 & )
}

close_blocking_dialogs() {
  require_xdotool
  for title in "Document in Use"; do
    window_ids="$(xdotool search --onlyvisible --name "$title" 2>/dev/null || true)"
    printf '%s\n' "$window_ids" | while read -r window_id; do
      [ -n "$window_id" ] && xdotool windowclose "$window_id" >/dev/null 2>&1 || true
    done
  done
}

first_window() {
  pattern="$1"
  xdotool search --onlyvisible --name "$pattern" 2>/dev/null | head -n 1 || true
}

launch_app() {
  app="${1:-}"
  soffice="${SOFFICE:-$(command -v soffice || true)}"
  soffice="${soffice:-soffice}"
  case "$app" in
    writer)
      run_detached "$soffice" --norestore --nofirststartwizard --nolockcheck "-env:UserInstallation=file://$HOME" --writer
      ;;
    calc|spreadsheet)
      run_detached "$soffice" --norestore --nofirststartwizard --nolockcheck "-env:UserInstallation=file://$HOME" --calc
      ;;
    impress|presentation)
      run_detached "$soffice" --norestore --nofirststartwizard --nolockcheck "-env:UserInstallation=file://$HOME" --impress
      ;;
    terminal)
      run_detached xfce4-terminal --working-directory=/a0/usr/workdir
      ;;
    settings)
      run_detached xfce4-settings-manager
      ;;
    workdir|files|file-manager)
      run_detached thunar /a0/usr/workdir
      ;;
    *)
      echo "Unknown app: ${app:-<empty>}" >&2
      echo "Expected: writer, calc, impress, terminal, settings, or workdir." >&2
      exit 2
      ;;
  esac
}

case "$command_name" in
  help|-h|--help)
    usage
    ;;
  env)
    printf 'export DISPLAY=%q\n' "$DISPLAY"
    printf 'export XAUTHORITY=%q\n' "$XAUTHORITY"
    printf 'export HOME=%q\n' "$HOME"
    ;;
  check)
    ensure_display
    xdotool getmouselocation --shell
    ;;
  location)
    ensure_display
    xdotool getmouselocation --shell
    ;;
  windows)
    ensure_display
    pattern="${1:-.}"
    xdotool search --onlyvisible --name "$pattern" getwindowname %@ 2>/dev/null || true
    ;;
  focus)
    ensure_display
    pattern="${1:?focus requires a window name pattern}"
    window_id="$(first_window "$pattern")"
    if [ -z "$window_id" ]; then
      echo "No visible window matched: $pattern" >&2
      exit 1
    fi
    xdotool windowactivate --sync "$window_id"
    ;;
  key)
    ensure_display
    if [ "$#" -eq 0 ]; then
      echo "key requires at least one xdotool key name." >&2
      exit 2
    fi
    xdotool key --clearmodifiers "$@"
    ;;
  type)
    ensure_display
    text="$*"
    xdotool type --delay "${A0_DESKTOP_TYPE_DELAY_MS:-1}" -- "$text"
    ;;
  click)
    ensure_display
    x="${1:?click requires X}"
    y="${2:?click requires Y}"
    xdotool mousemove --sync "$x" "$y" click 1
    ;;
  dblclick)
    ensure_display
    x="${1:?dblclick requires X}"
    y="${2:?dblclick requires Y}"
    xdotool mousemove --sync "$x" "$y" click --repeat 2 --delay "${A0_DESKTOP_DBLCLICK_DELAY_MS:-150}" 1
    ;;
  launch)
    ensure_display
    launch_app "${1:-}"
    ;;
  open-path)
    ensure_display
    path="${1:-/a0/usr/workdir}"
    run_detached thunar "$path"
    ;;
  calc-set-cell)
    ensure_display
    file="${1:?calc-set-cell requires FILE}"
    sheet="${2:?calc-set-cell requires SHEET}"
    cell="${3:?calc-set-cell requires CELL}"
    shift 3
    if [ "$#" -eq 0 ]; then
      echo "calc-set-cell requires VALUE." >&2
      exit 2
    fi
    close_blocking_dialogs
    export PYTHONPATH="${PYTHONPATH:-/usr/lib/python3/dist-packages:/usr/lib/libreoffice/program:}"
    python3 "$SCRIPT_DIR/calc_set_cell.py" "$file" "$sheet" "$cell" "$@"
    ;;
  save)
    ensure_display
    xdotool key --clearmodifiers ctrl+s
    ;;
  *)
    echo "Unknown command: $command_name" >&2
    usage >&2
    exit 2
    ;;
esac
