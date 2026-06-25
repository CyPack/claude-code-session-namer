#!/usr/bin/env bash
# Installer for cc-session-namer.
# - copies the scripts into ~/.cc-session-namer/
# - runs the self-test (must be GREEN)
# - installs an idempotent crontab entry (preserving any existing crontab)
#
# Re-runnable: running it again updates the scripts and won't duplicate the cron line.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.cc-session-namer"
# Default schedule: a handful of times per day. Override with CRON_HOURS env var.
CRON_HOURS="${CRON_HOURS:-1,7,12,15,17,18,20,22,23}"

echo "==> Installing cc-session-namer to $DEST"
mkdir -p "$DEST"
cp "$SRC_DIR/name_sessions.py" "$DEST/name_sessions.py"
cp "$SRC_DIR/run-namer.sh"     "$DEST/run-namer.sh"
chmod +x "$DEST/run-namer.sh" "$DEST/name_sessions.py"

echo "==> Running self-test (pure functions)"
python3 "$DEST/name_sessions.py" --selftest

echo "==> Dry-run preview (calls the LLM, writes nothing)"
echo "    Skipped here. Run manually to preview:"
echo "      python3 $DEST/name_sessions.py --dry-run"

echo "==> Installing crontab entry (idempotent)"
LINE="0 $CRON_HOURS * * * $DEST/run-namer.sh"
TMP="$(mktemp)"
# Preserve existing crontab (if any), drop any previous namer line, then add ours.
( crontab -l 2>/dev/null | grep -v "$DEST/run-namer.sh" || true; echo "# cc-session-namer — auto-name unnamed chats (chronology-safe)"; echo "$LINE" ) > "$TMP"
crontab "$TMP"
rm -f "$TMP"

echo "==> Done."
echo "   Cron: $LINE"
echo "   Verify: crontab -l | grep cc-session-namer"
echo "   First manual run: python3 $DEST/name_sessions.py --dry-run"
