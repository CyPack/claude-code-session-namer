#!/usr/bin/env bash
# cc-session-namer — cron entry-point.
# Isimsiz CC chat session'lara LLM ile PROJE: KONU ismi koyar, kronolojiyi korur.
# Cron saatleri install.sh ile ayarlanir (varsayilan gunde birkac kez).
set -u
# Cron HOME'u genelde set eder; emin olmak için kullanıcının home dizinine düş.
export HOME="${HOME:-$(getent passwd "$(id -un)" | cut -d: -f6)}"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
NDIR="$HOME/.cc-session-namer"
mkdir -p "$NDIR"
exec >> "$NDIR/namer.log" 2>&1
echo "===== cron-run $(date -Iseconds) ====="
timeout 1500 python3 "$NDIR/name_sessions.py" --commit --cron
echo "===== cron-run done rc=$? ====="
