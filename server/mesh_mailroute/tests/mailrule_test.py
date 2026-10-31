import sys
"""Mail-rule test on a REAL RabbitMQ broker, copying OpenTAKServer 1.7.13's own mailbox code:
  EudHandler.parse_device_info: queue_declare(callsign), queue_declare(uid), bind groups __ANON__.OUT,
      bind dms uid->uid and callsign->callsign, consume both queues
  EudHandler.on_message: send body["cot"] to the socket unless body["uid"] == own uid
  EudHandler.unbind_rabbitmq_queues: on disconnect unbind its OWN bindings (queues stay)
  cot_parser.route_cot: <dest callsign=X> -> dms exchange, routing key X, expiration OTS_RABBITMQ_TTL;
      no dest (TCP, no user) -> groups exchange, __ANON__.OUT
The 'mail rule' = one extra binding on the dms exchange: routing key <mesh callsign> -> DECK's uid mailbox."""
import json, subprocess, threading, time
import pika

res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:300]))

TTL = "86400000"
def conn():
    return pika.BlockingConnection(pika.ConnectionParameters("localhost"))

def setup_exchanges():
    c = conn(); ch = c.channel()
    for name, kind in (("dms", "direct"), ("cot_parser", "direct"), ("groups", "topic"), ("missions", "topic")):
        ch.exchange_declare(name, durable=True, exchange_type=kind)
    c.close()

class FakeEud:
    """One phone (or the bridge) connected to OTS, doing what EudHandler does."""
    def __init__(self, uid, callsign):
        self.uid, self.cs, self.got, self.bound = uid, callsign, [], []
        self.c = conn(); self.ch = self.c.channel()
        self.ch.queue_declare(queue=self.cs); self.ch.queue_declare(queue=self.uid)
        for ex, q, rk in (("groups", self.uid, "__ANON__.OUT"), ("dms", self.uid, self.uid), ("dms", self.cs, self.cs)):
            self.ch.queue_bind(exchange=ex, queue=q, routing_key=rk); self.bound.append((ex, q, rk))
        for q in (self.cs, self.uid):
            self.ch.basic_consume(queue=q, on_message_callback=self.on_message, auto_ack=True)
        self.stop = False
        self.t = threading.Thread(target=self.loop, daemon=True); self.t.start()
    def on_message(self, ch, m, p, body):
        b = json.loads(body)
        if b["uid"] != self.uid:
            self.got.append(b["cot"])
    def loop(self):
        while not self.stop:
            self.c.process_data_events(time_limit=0.1)
    def disconnect(self):
        self.stop = True; self.t.join()
        for ex, q, rk in self.bound:                     # unbind_rabbitmq_queues: only its own bindings
            self.ch.queue_unbind(exchange=ex, queue=q, routing_key=rk)
        self.c.close()

class Parser:
    """cot_parser.route_cot"""
    def __init__(self):
        self.c = conn(); self.ch = self.c.channel()
    def dm(self, sender_uid, to_callsign, text, ttl=TTL):
        self.ch.basic_publish(exchange="dms", routing_key=to_callsign,
                              body=json.dumps({"uid": sender_uid, "cot": "DM %s->%s: %s" % (sender_uid, to_callsign, text)}),
                              properties=pika.BasicProperties(expiration=ttl))
    def group(self, sender_uid, text):
        self.ch.basic_publish(exchange="groups", routing_key="__ANON__.OUT",
                              body=json.dumps({"uid": sender_uid, "cot": "ALL %s: %s" % (sender_uid, text)}),
                              properties=pika.BasicProperties(expiration=TTL))

def add_rule(cs, queue="DECK-testdeck"):
    c = conn(); ch = c.channel(); ch.queue_bind(exchange="dms", queue=queue, routing_key=cs); c.close()
def del_rule(cs, queue="DECK-testdeck"):
    c = conn(); ch = c.channel(); ch.queue_unbind(exchange="dms", queue=queue, routing_key=cs); c.close()
def rmq(*a):
    return subprocess.run(["rabbitmqctl", "-q"] + list(a), capture_output=True, text=True).stdout
def bindings():
    return [l.split("\t") for l in rmq("list_bindings", "source_name", "routing_key", "destination_name").splitlines() if l.startswith("dms\t")]
def qdepth(q):
    for l in rmq("list_queues", "name", "messages").splitlines():
        n, _, v = l.partition("\t")
        if n == q: return int(v)
    return None
def has(eud, text): return [x for x in eud.got if text in x]
def settle(): time.sleep(0.6)

setup_exchanges()
for q in ("DECK", "DECK-testdeck", "FOXTROT", "SRV1", "ECHO", "SRV2", "ALPHA-1", "A1"):
    try:
        c = conn(); c.channel().queue_delete(q); c.close()
    except Exception: pass
P = Parser()
deck = FakeEud("DECK-testdeck", "DECK"); foxtrot = FakeEud("SRV1", "FOXTROT"); echo_ = FakeEud("SRV2", "ECHO")
settle()

# ---- before the rule (today)
P.dm("SRV1", "ALPHA-1", "t0 to alpha"); settle()
check("TODAY: FOXTROT's DM to mesh ALPHA-1 reaches nobody (no mailbox for ALPHA-1)", not has(deck, "t0") and not has(echo_, "t0") and not has(foxtrot, "t0"))
P.dm("SRV1", "ECHO", "t0 to echo"); P.group("SRV1", "t0 all"); settle()
base = {"echo_dm": len(has(echo_, "t0 to echo")), "deck_all": len(has(deck, "t0 all")), "echo_all": len(has(echo_, "t0 all"))}
check("TODAY: normal DM FOXTROT->ECHO delivered once; group message reaches DECK and ECHO once", base == {"echo_dm": 1, "deck_all": 1, "echo_all": 1}, base)

# ---- add the rule
add_rule("ALPHA-1"); settle()
check("rule visible: dms  ALPHA-1 -> DECK-testdeck", ["dms", "ALPHA-1", "DECK-testdeck"] in bindings(), bindings())
P.dm("SRV1", "ALPHA-1", "t1 meet at the truck"); settle()
check("WITH RULE: FOXTROT's DM to ALPHA-1 lands in DECK's mailbox -> down the bridge connection, once", len(has(deck, "t1 meet at the truck")) == 1, deck.got[-2:])
check("...nobody else gets it", not has(echo_, "t1") and not has(foxtrot, "t1"))
check("...and it arrives unchanged (still addressed to ALPHA-1, from FOXTROT)", has(deck, "t1") and has(deck, "t1")[0] == "DM SRV1->ALPHA-1: t1 meet at the truck")

# ---- regular mail unaffected
P.dm("SRV1", "ECHO", "t2 echo_ dm"); P.dm("SRV2", "FOXTROT", "t2 foxtrot dm"); P.dm("SRV1", "DECK", "t2 deck dm"); P.group("SRV2", "t2 all"); settle()
check("normal DM FOXTROT->ECHO: only ECHO, once", len(has(echo_, "t2 echo_ dm")) == 1 and not has(deck, "t2 echo_ dm") and not has(foxtrot, "t2 echo_ dm"))
check("normal DM ECHO->FOXTROT: only FOXTROT, once", len(has(foxtrot, "t2 foxtrot dm")) == 1 and not has(deck, "t2 foxtrot dm"))
check("DM to DECK still works, once", len(has(deck, "t2 deck dm")) == 1)
check("group traffic unchanged: DECK and FOXTROT once each, sender excluded", len(has(deck, "t2 all")) == 1 and len(has(foxtrot, "t2 all")) == 1 and not has(echo_, "t2 all"))

# ---- DECK DOWN
deck.disconnect(); settle()
check("deck down: OTS removes only its own rules; ours stays", ["dms", "ALPHA-1", "DECK-testdeck"] in bindings() and not any(b[1] == "DECK" for b in bindings()), bindings())
P.dm("SRV1", "ECHO", "t3 echo_ dm"); P.dm("SRV2", "FOXTROT", "t3 foxtrot dm"); P.group("SRV1", "t3 all"); settle()
check("deck down: FOXTROT<->ECHO DMs still delivered normally", len(has(echo_, "t3 echo_ dm")) == 1 and len(has(foxtrot, "t3 foxtrot dm")) == 1)
check("deck down: group traffic between server phones still normal", len(has(echo_, "t3 all")) == 1)
d0 = qdepth("DECK-testdeck")
P.dm("SRV1", "ALPHA-1", "t3 while deck is off"); settle()
check("deck down: a DM to ALPHA-1 waits in DECK's mailbox (1 more message), nothing else affected", qdepth("DECK-testdeck") == d0 + 1, (d0, qdepth("DECK-testdeck")))
P.dm("SRV1", "ALPHA-1", "t3 short ttl", ttl="1500"); time.sleep(2.5)
check("an expired message still counts until it reaches the front of the mailbox (RabbitMQ removes expired messages lazily)", qdepth("DECK-testdeck") == d0 + 2, qdepth("DECK-testdeck"))

# ---- DECK BACK
deck = FakeEud("DECK-testdeck", "DECK"); time.sleep(1.5)
check("deck back: the DM sent while it was off is delivered (store and forward, within 24 h)", len(has(deck, "t3 while deck is off")) == 1, deck.got)
check("...the expired one is not", not has(deck, "t3 short ttl"))
P.dm("SRV1", "ALPHA-1", "t4 after reconnect"); settle()
check("deck back: rule still works without re-adding it", len(has(deck, "t4 after reconnect")) == 1)

# ---- ALPHA-1 connects to the server directly (e.g. over cell) -> would get it twice
alpha = FakeEud("A1", "ALPHA-1"); settle()
P.dm("SRV1", "ALPHA-1", "t5 dup check"); settle()
both = (len(has(alpha, "t5")), len(has(deck, "t5")))
check("if ALPHA-1 also connects to the server directly, the rule would duplicate (both get it) -> the helper must drop the rule then", both == (1, 1), both)
directly = any(b[1] == "ALPHA-1" and b[2] == "ALPHA-1" for b in bindings())
check("...and that situation is detectable: OTS bound ALPHA-1's own mailbox", directly)
del_rule("ALPHA-1"); P.dm("SRV1", "ALPHA-1", "t6 after drop"); settle()
check("rule removed -> ALPHA-1 gets it once, the deck doesn't", len(has(alpha, "t6")) == 1 and not has(deck, "t6"))
alpha.disconnect()

# ---- undo is clean
add_rule("ALPHA-1"); del_rule("ALPHA-1")
check("undo: removing the rule leaves no trace in dms bindings", not any(b[1] == "ALPHA-1" and b[2] == "DECK-testdeck" for b in bindings()))

# ---- broker restart (server reboot)
add_rule("ALPHA-1")
for e in (deck, foxtrot, echo_): e.stop = True
time.sleep(0.5)
subprocess.run(["rabbitmqctl", "-q", "stop_app"]); subprocess.run(["rabbitmqctl", "-q", "start_app"]); time.sleep(3)
check("after a RabbitMQ restart the rule is gone (OTS mailboxes aren't durable) -> must be re-added automatically",
      not any(b[1] == "ALPHA-1" for b in bindings()), bindings())
try:
    add_rule("ALPHA-1"); ok = False
except Exception as e:
    ok = "NOT_FOUND" in str(e) or "404" in str(e)
check("...and it can't be re-added until DECK's mailbox exists again (bridge reconnected)", ok)
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
