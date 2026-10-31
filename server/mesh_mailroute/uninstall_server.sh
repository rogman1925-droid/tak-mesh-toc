#!/usr/bin/env bash
# uninstall_server.sh - remove every mail rule mesh_mailroute made, then the service. OTS is not touched.
set -uo pipefail
DEST="$HOME/mesh_mailroute"
EXE="$(systemctl cat opentakserver | sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' | head -1)"; PY="$(dirname "$EXE")/python"
sudo systemctl disable --now mesh-mailroute 2>/dev/null || true
[ -f "$DEST/mesh_mailroute.py" ] && "$PY" "$DEST/mesh_mailroute.py" --config "$DEST/mesh_mailroute.ini" --remove-all
sudo rm -f /etc/systemd/system/mesh-mailroute.service && sudo systemctl daemon-reload
echo "removed: service and its mail rules. Files left in $DEST (delete them by hand if you like)."
