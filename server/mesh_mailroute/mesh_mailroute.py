#!/usr/bin/env python3
"""
mesh_mailroute.py - OpenTAKServer helper: lets server phones DM people on the OpenMANET mesh.

Runs ON THE OTS SERVER, next to RabbitMQ, using OTS's own Python (it has pika).

Why: OTS gives every connected phone a mailbox (RabbitMQ queue) and files a direct message under the
recipient's callsign (exchange "dms", routing key = callsign). Mesh phones aren't connected to OTS:
the deck's bridge (TAK MESH TOC) carries them all over ONE connection (DECK). So a DM addressed to a mesh
callsign matches no mailbox and RabbitMQ throws it away.

What it does: one "mail rule" per mesh callsign:  dms / <mesh callsign>  ->  DECK's mailbox.
The bridge then delivers the DM to that one phone over the mesh.
  - learns mesh callsigns from OTS's "firehose" (every CoT OTS receives, with the connection it came
    in on): position reports that arrive on the bridge's connection but aren't the bridge itself
  - never adds a rule for a callsign that has its own live OTS connection (it would get DMs twice),
    and removes ours if that phone later connects directly
  - re-adds its rules every RECHECK seconds (RabbitMQ forgets them when it restarts, because OTS's
    mailboxes aren't durable), as soon as DECK's mailbox exists again
  - drops a rule when that callsign hasn't been heard on the mesh for KEEP_HOURS
  - only ever touches rules it made itself (state file); OTS's own bindings are never changed
Normal server traffic is unaffected: a rule only matches DMs addressed to that mesh callsign, which
OTS would otherwise discard. With the deck off, those DMs wait in DECK's mailbox and expire with
OTS's normal message lifetime (OTS_RABBITMQ_TTL, 24 h by default).

    ~/.opentakserver_venv/bin/python mesh_mailroute.py [--config mesh_mailroute.ini]
    ~/.opentakserver_venv/bin/python mesh_mailroute.py --list        (show rules, then exit)
    ~/.opentakserver_venv/bin/python mesh_mailroute.py --remove-all  (undo everything, then exit)
"""
import argparse
import configparser
import json
import logging
import os
import re
import sys
import time

import pika

VERSION = "2.1"
log = logging.getLogger("mesh_mailroute")
DEFAULTS = {
    "RABBITMQ_HOST": "localhost",
    "RABBITMQ_USER": "guest",
    "RABBITMQ_PASSWORD": "guest",
    "BRIDGE_UID": "",                      # the bridge's OTS identity (= its mailbox name), e.g. DECK-mydeck
    "STATE_FILE": "~/.mesh_mailroute.json",
    "RECHECK": "30",
    "KEEP_HOURS": "24",
    "SELFTEST": "DECK-selftest",           # v2: the bridge sends a tiny message to this made-up callsign every
}                                          # minute; this helper answers it ("DM route" on the deck's dashboard)
UID_RE = re.compile(r'<event\b[^>]*?\suid="([^"]+)"')
TYPE_RE = re.compile(r'<event\b[^>]*?\stype="([^"]+)"')
CALL_RE = re.compile(r'<contact\b[^>]*?\scallsign="([^"]+)"')
SAFE_CS = re.compile(r"^[^\x00-\x1f]{1,64}$")


class Router:
    def __init__(self, cfg):
        self.cfg = cfg
        self.deck = cfg["BRIDGE_UID"].strip()
        self.state_path = os.path.expanduser(cfg["STATE_FILE"])
        self.keep = float(cfg["KEEP_HOURS"]) * 3600
        self.recheck = max(5.0, float(cfg["RECHECK"]))
        st = cfg.get("SELFTEST", "DECK-selftest").strip()
        self.selftest = st if st.lower() not in ("off", "0", "no", "") and SAFE_CS.match(st) else ""
        self.mesh = {}          # callsign -> last time heard via the bridge
        self.mesh_uid = {}      # callsign -> that phone's UID (iTAK addresses DMs by UID, ATAK by callsign)
        self.rules = set()      # routing keys (callsigns and UIDs) we have bound (or want bound) to the deck's mailbox
        self.direct = set()     # callsigns OTS has a live connection for (no rule for them)
        self.conn = None
        self.ch = None
        self.watch = None
        self.load()

    # -- state
    def load(self):
        try:
            st = json.load(open(self.state_path))
            self.mesh = {k: float(v) for k, v in st.get("mesh", {}).items()}
            self.mesh_uid = {k: str(v) for k, v in st.get("mesh_uid", {}).items() if k in self.mesh}
            self.rules = set(st.get("rules", []))
        except (OSError, ValueError):
            pass

    def save(self):
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"mesh": self.mesh, "mesh_uid": self.mesh_uid, "rules": sorted(self.rules), "deck": self.deck}, f, indent=1)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            log.warning("could not save %s: %s", self.state_path, exc)

    # -- rabbit
    def connect(self):
        cred = pika.PlainCredentials(self.cfg["RABBITMQ_USER"], self.cfg["RABBITMQ_PASSWORD"])
        self.conn = pika.BlockingConnection(pika.ConnectionParameters(self.cfg["RABBITMQ_HOST"], credentials=cred,
                                                                      heartbeat=60))
        self.ch = self.conn.channel()
        # our own temporary queue on OTS's firehose; deleted automatically when we stop
        q = self.ch.queue_declare(queue="", exclusive=True, auto_delete=True,
                                  arguments={"x-max-length": 5000, "x-message-ttl": 60000})
        self.watch = q.method.queue
        self.ch.queue_bind(exchange="firehose", queue=self.watch, routing_key="")
        if self.selftest:                       # v2: the bridge's probes, addressed to the made-up callsign
            self.ch.queue_bind(exchange="dms", queue=self.watch, routing_key=self.selftest)
        self.ch.basic_consume(queue=self.watch, on_message_callback=self.on_cot, auto_ack=True)
        log.info("watching OTS's firehose for mesh callsigns behind %s", self.deck)

    def side(self):
        """A short-lived channel for checks that may fail (a failed check closes the channel)."""
        return self.conn.channel()

    def mailbox_exists(self, name):
        """(exists, consumers) for an OTS mailbox, without creating it."""
        c = self.side()
        try:
            r = c.queue_declare(queue=name, passive=True)
            return True, r.method.consumer_count
        except pika.exceptions.ChannelClosedByBroker:
            return False, 0
        finally:
            if c.is_open:
                c.close()

    def bind(self, cs):
        c = self.side()
        try:
            c.queue_bind(exchange="dms", queue=self.deck, routing_key=cs)
            return True
        except pika.exceptions.ChannelClosedByBroker as exc:
            log.debug("bind %s failed: %s", cs, exc)
            return False
        finally:
            if c.is_open:
                c.close()

    def unbind(self, cs):
        c = self.side()
        try:
            c.queue_unbind(exchange="dms", queue=self.deck, routing_key=cs)
        except pika.exceptions.ChannelClosedByBroker:
            pass
        finally:
            if c.is_open:
                c.close()

    # -- learning
    def on_cot(self, ch, method, props, body):
        try:
            b = json.loads(body)
        except ValueError:
            return
        conn_uid, cot = b.get("uid"), b.get("cot") or ""
        if method.exchange == "dms" and self.selftest and 'type="t-x-atb-st"' in cot and conn_uid == self.deck:
            self.answer(cot)                    # v2: the bridge's DM route check: send it straight back
            return
        if method.exchange != "firehose" or conn_uid != self.deck or 'type="a-' not in cot:
            return                              # only position reports carried by the bridge
        mu, mc = UID_RE.search(cot), CALL_RE.search(cot)
        if not (mu and mc) or mu.group(1) == self.deck:
            return                              # the bridge's own identity report
        cs, uid = mc.group(1), mu.group(1)
        if not SAFE_CS.match(cs):
            return
        new = cs not in self.mesh or (SAFE_CS.match(uid) and self.mesh_uid.get(cs) != uid)
        self.mesh[cs] = time.time()
        if SAFE_CS.match(uid):
            self.mesh_uid[cs] = uid
        if new:
            log.info("mesh phone seen behind the deck: %s (%s)", cs, self.mesh_uid.get(cs, "?"))
            self.reconcile()

    def answer(self, cot):
        """Put the bridge's probe into the deck's own mailbox, the way OTS files a DM for it (OTS drops a connection's
        OWN traffic from its mailbox, so the sender here is this helper, not the deck). It coming back proves: the
        bridge -> OTS -> RabbitMQ "dms" routing -> this helper (alive) -> the deck's mailbox -> the bridge."""
        try:
            self.ch.basic_publish(exchange="dms", routing_key=self.deck,
                                  body=json.dumps({"uid": "mesh_mailroute", "cot": cot}),
                                  properties=pika.BasicProperties(expiration="120000"))
            self.answered = getattr(self, "answered", 0) + 1
            if self.answered in (1, 100, 1000) or self.answered % 10000 == 0:
                log.info("DM route check from the bridge answered (%d so far)", self.answered)
        except pika.exceptions.AMQPError as exc:
            log.warning("could not answer the bridge's DM route check: %s", exc)

    # -- the rules
    def reconcile(self):
        now = time.time()
        for cs in [c for c, t in self.mesh.items() if now - t > self.keep]:
            del self.mesh[cs]
            self.mesh_uid.pop(cs, None)
            log.info("%s not heard on the mesh for %.0f h; forgetting it", cs, self.keep / 3600)
        deck_ok, deck_consumers = self.mailbox_exists(self.deck)
        want = set()
        for cs in self.mesh:
            uid = self.mesh_uid.get(cs)
            keys = [cs] + ([uid] if uid and uid != cs else [])
            # connected to OTS directly (under its callsign or its UID): OTS delivers to it itself
            if any(c > 0 for e, c in (self.mailbox_exists(k) for k in keys) if e):
                if cs not in self.direct:
                    log.info("%s is connected to the server directly; no mail rule for it", cs)
                self.direct.add(cs)
                continue
            self.direct.discard(cs)
            want.update(keys)               # a rule for the callsign (ATAK) AND the UID (iTAK)
        for cs in sorted(self.rules - want):
            self.unbind(cs)
            log.info("rule removed: %s", cs)
        if not deck_ok:
            if want:
                log.info("%s's mailbox doesn't exist yet (bridge not connected since the server started); "
                         "%d rule(s) waiting", self.deck, len(want))
            self.rules = want                   # re-added as soon as the mailbox is back
            self.save()
            return
        names = {u: c for c, u in self.mesh_uid.items()}
        for cs in sorted(want):
            if self.bind(cs) and cs not in self.rules:
                if cs in names:
                    log.info("rule added: DMs addressed to %s's UID %s -> %s (iTAK addresses DMs by UID)", names[cs], cs, self.deck)
                else:
                    log.info("rule added: DMs for %s -> %s (bridge delivers them on the mesh)", cs, self.deck)
        self.rules = want
        self.save()

    def run(self):
        while True:
            try:
                self.connect()
                self.reconcile()
                last = time.monotonic()
                while True:
                    self.conn.process_data_events(time_limit=1)
                    if time.monotonic() - last >= self.recheck:
                        self.reconcile()
                        last = time.monotonic()
            except (pika.exceptions.AMQPError, OSError) as exc:
                log.warning("RabbitMQ connection problem (%s); retrying in 10 s", exc.__class__.__name__)
                try:
                    if self.conn and self.conn.is_open:
                        self.conn.close()
                except Exception:
                    pass
                time.sleep(10)

    def remove_all(self):
        self.connect()
        for cs in sorted(self.rules):
            self.unbind(cs)
            print("removed", cs)
        self.rules = set()
        self.save()


def load_cfg(path):
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read_dict({"mailroute": DEFAULTS})
    if path and os.path.exists(os.path.expanduser(path)):
        cp.read(os.path.expanduser(path))
    return cp["mailroute"]


def main():
    ap = argparse.ArgumentParser(description="mail rules so server phones can DM mesh phones")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "mesh_mailroute.ini"))
    ap.add_argument("--list", action="store_true", help="show the rules and mesh callsigns, then exit")
    ap.add_argument("--remove-all", action="store_true", help="remove every rule this helper made, then exit")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s mailroute: %(message)s")
    logging.getLogger("pika").setLevel(logging.CRITICAL)
    cfg = load_cfg(a.config)
    if not cfg["BRIDGE_UID"].strip() or cfg["BRIDGE_UID"].strip() == "DECK-yourdeck":
        sys.exit("BRIDGE_UID is not set in %s: put the deck bridge's UID there (DECK-<deck hostname>; "
                 "meshtoc check on the deck shows it)" % a.config)
    r = Router(cfg)
    if a.list:
        print("deck mailbox:", r.deck)
        print("rules:", ", ".join(sorted(r.rules)) or "(none)")
        for cs, t in sorted(r.mesh.items()):
            print("  mesh %-24s UID %-28s last heard %.0f min ago" % (cs, r.mesh_uid.get(cs, "?"), (time.time() - t) / 60))
        return
    if a.remove_all:
        r.remove_all()
        return
    log.info("mesh_mailroute %s starting (deck mailbox %s)", VERSION, r.deck)
    r.run()


if __name__ == "__main__":
    main()
