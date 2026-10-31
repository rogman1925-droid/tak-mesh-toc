#!/usr/bin/env bash
# install_server.sh - put mesh_mailroute on the OpenTAKServer box as a service.
#   bash install_server.sh --dry-run            show what it would do
#   bash install_server.sh [--deck-uid DECK-<deck hostname>]
# Runs as your normal user (sudo only for the service). Uses OpenTAKServer's own Python (it has pika).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"; DEST="$HOME/mesh_mailroute"; DRY=0; DECK_UID=""
[ "$SRC" = "$DEST" ] && { echo "Unzip this somewhere other than ~/mesh_mailroute (that's where it installs), e.g.:"; echo "  cd ~ && mv mesh_mailroute mesh_mailroute_src && bash mesh_mailroute_src/install_server.sh"; exit 1; }
while [ $# -gt 0 ]; do case "$1" in --dry-run) DRY=1 ;; --deck-uid) DECK_UID="$2"; shift ;; *) echo "unknown option $1"; exit 1 ;; esac; shift; done
[ "$(id -u)" = 0 ] && { echo "Run this as your normal user (not root). It uses sudo only where needed."; exit 1; }
say()  { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
act()  { if [ "$DRY" = 1 ]; then printf '   [dry-run] %s\n' "$*"; else printf '   > %s\n' "$*"; "$@"; fi; }

say "Checking the server"
systemctl cat opentakserver >/dev/null 2>&1 || { echo "   opentakserver service not found: run this on the OTS server"; exit 1; }
EXE="$(systemctl cat opentakserver | sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' | head -1)"
PY="$(dirname "$EXE")/python"
[ -x "$PY" ] || { echo "   OpenTAKServer's python not found next to $EXE"; exit 1; }
"$PY" -c "import pika" 2>/dev/null || { echo "   $PY has no pika module"; exit 1; }
note "OpenTAKServer python: $PY (pika ok)"
if [ -z "$DECK_UID" ]; then
  DECK_UID="$(sudo rabbitmqctl -q list_queues name 2>/dev/null | grep -E '^DECK-' | head -1 || true)"
  if [ -n "$DECK_UID" ]; then note "found the bridge's mailbox: $DECK_UID"
  elif [ -f "$DEST/mesh_mailroute.ini" ]; then note "bridge mailbox not found right now; keeping BRIDGE_UID from your existing .ini"
  else
    echo "   The deck's bridge has not connected to this server yet, so its name isn't known."
    echo "   Start the bridge on the deck first, or give it:  bash install_server.sh --deck-uid DECK-<deck hostname>"
    echo "   (meshtoc check on the deck shows it: 'Deck identity ... UID DECK-...')"; exit 1
  fi
fi

say "1/3 Files -> $DEST"
act mkdir -p "$DEST"
act install -m 755 "$SRC/mesh_mailroute.py" "$DEST/mesh_mailroute.py"
act install -m 644 "$SRC/README.md" "$DEST/README.md"
if [ -f "$DEST/mesh_mailroute.ini" ]; then note "keeping your existing $DEST/mesh_mailroute.ini"
elif [ "$DRY" = 1 ]; then note "[dry-run] would write $DEST/mesh_mailroute.ini (BRIDGE_UID = $DECK_UID)"
else
  "$PY" - "$SRC/mesh_mailroute.ini.example" "$DEST/mesh_mailroute.ini" "$DECK_UID" "$HOME/ots/config.yml" <<'PY'
import os, re, sys
src, dst, uid, otscfg = sys.argv[1:5]
s = open(src).read().replace("BRIDGE_UID = DECK-yourdeck", "BRIDGE_UID = " + uid)
if os.path.exists(otscfg):                       # same RabbitMQ login as OpenTAKServer, if it was changed
    t = open(otscfg).read()
    for k, key in (("OTS_RABBITMQ_USERNAME", "RABBITMQ_USER"), ("OTS_RABBITMQ_PASSWORD", "RABBITMQ_PASSWORD")):
        m = re.search(r"^\s*%s\s*:\s*['\"]?([^'\"\n#]+)" % k, t, re.M)
        if m:
            s = re.sub(r"^%s = .*$" % key, "%s = %s" % (key, m.group(1).strip()), s, flags=re.M)
open(dst, "w").write(s); os.chmod(dst, 0o600)
print("   wrote", dst, "(BRIDGE_UID = %s; RabbitMQ login taken from OpenTAKServer's settings if set)" % uid)
PY
fi

say "2/3 Service mesh-mailroute"
UNIT="$(mktemp)"
cat > "$UNIT" <<EOF
[Unit]
Description=Mail rules so server phones can DM mesh phones (TAK MESH TOC)
After=rabbitmq-server.service opentakserver.service
Wants=rabbitmq-server.service

[Service]
User=$(id -un)
ExecStart=$PY $DEST/mesh_mailroute.py --config $DEST/mesh_mailroute.ini
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
act sudo install -m 644 "$UNIT" /etc/systemd/system/mesh-mailroute.service
rm -f "$UNIT"
act sudo systemctl daemon-reload
act sudo systemctl enable mesh-mailroute
act sudo systemctl restart mesh-mailroute

say "3/3 Done"
note "check it:   journalctl -u mesh-mailroute -n 20 --no-pager"
note "rules:      $PY $DEST/mesh_mailroute.py --config $DEST/mesh_mailroute.ini --list"
note "undo:       bash '$SRC/uninstall_server.sh'"
[ "$DRY" = 1 ] && note "That was a dry run: nothing was changed."
exit 0
