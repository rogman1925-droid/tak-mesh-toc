import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
"""Phone-to-phone DMs across the bridge, end to end:
REAL RabbitMQ + mini_ots (OTS 1.7.13 mailbox logic) + REAL tak_mesh_bridge.py (DM_RELAY = 1) + REAL deck_chat.py
+ REAL mesh_mailroute.py + fake mesh phones (own 127.0.0.x address, multicast SA, TCP 4242 DM listener)
+ fake server phones (TCP to mini_ots).
Usage: python3 dm_relay_e2e.py <deck_chat bundle dir> <server dir> <work dir>"""
import json, os, re, socket, subprocess, sys, threading, time, urllib.request
B, S, W = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(W, exist_ok=True)
sys.path.insert(0, B); sys.path.insert(0, os.path.join(S, "tests"))
import deck_chat as dc
from mini_ots import MiniOTS
import pika

res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:500]), flush=True)

def rmq(*a): return subprocess.run(["rabbitmqctl", "-q"] + list(a), capture_output=True, text=True).stdout
def rules():
    return sorted(l.split("\t")[1] for l in rmq("list_bindings", "source_name", "routing_key", "destination_name").splitlines()
                  if l.startswith("dms\t") and l.endswith("\tDECK-testdeck") and l.split("\t")[1] not in ("DECK-testdeck",))
def all_dms_bindings():
    return [tuple(l.split("\t")) for l in rmq("list_bindings", "source_name", "routing_key", "destination_name").splitlines() if l.startswith("dms\t")]
def qdepth(q):
    for l in rmq("list_queues", "name", "messages").splitlines():
        n, _, v = l.partition("\t")
        if n == q: return int(v)
    return None
def wait(cond, t=8.0):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.2)
    return cond()

# clean broker state from earlier runs
c = pika.BlockingConnection(pika.ConnectionParameters("localhost")); ch = c.channel()
for q in ("DECK", "DECK-testdeck", "HQ 28", "F28", "ECHO", "U1", "ALPHA 1-5", "A15", "BRAVO-2", "B2"):
    try: ch.queue_delete(q)
    except Exception: ch = c.channel()
c.close()

stop = threading.Event()
ots = MiniOTS()
def mcast_sock(src):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1")); s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.bind((src, 0)); return s
def join(group, port):
    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    r.bind((group, port)); r.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(group) + socket.inet_aton("127.0.0.1")); r.settimeout(0.2)
    return r
def sa(uid, cs, ep):
    t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); st = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 300))
    return ('<event version="2.0" uid="%s" type="a-f-G-U-C" how="m-g" time="%s" start="%s" stale="%s"><point lat="1" lon="2" hae="0" ce="9" le="9"/>'
            '<detail><contact callsign="%s" endpoint="%s"/><takv platform="ATAK-CIV" device="x" os="x" version="x"/></detail></event>' % (uid, t, t, st, cs, ep)).encode()
def events(blobs):
    out = []
    for b in blobs:
        out += [e for e in re.findall(rb"<event.*?</event>", b, re.S)]
    return out
def chats(blobs): return [p for p in (dc.parse_chat(e) for e in events(blobs)) if p]

sa_heard, chat_heard = [], []
def listen(r, sink):
    while not stop.is_set():
        try: sink.append(r.recvfrom(65535))
        except socket.timeout: pass
threading.Thread(target=listen, args=(join("239.2.3.1", 6969), sa_heard), daemon=True).start()
threading.Thread(target=listen, args=(join("224.10.10.1", 17012), chat_heard), daemon=True).start()

class MeshPhone:
    def __init__(self, uid, cs, ip):
        self.uid, self.cs, self.ip, self.got = uid, cs, ip, []
        threading.Thread(target=self.srv, daemon=True).start(); threading.Thread(target=self.beacon, daemon=True).start()
    def srv(self):
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind((self.ip, 4242)); s.listen(5); s.settimeout(0.2)
        while not stop.is_set():
            try: c, _ = s.accept()
            except socket.timeout: continue
            c.settimeout(2); data = b""
            try:
                while True:
                    d = c.recv(65536)
                    if not d: break
                    data += d
            except socket.timeout: pass
            self.got.append(data); c.close()
    def beacon(self):
        m = mcast_sock(self.ip)
        while not stop.is_set():
            m.sendto(sa(self.uid, self.cs, "%s:4242:tcp" % self.ip), ("239.2.3.1", 6969)); time.sleep(1)
    def endpoint_of(self, cs):
        for data, addr in reversed(sa_heard):
            if ('callsign="%s"' % cs).encode() in data:
                m = re.search(rb'endpoint="([^"]+)"', data); return m.group(1).decode() if m else ""
        return ""
    def dm(self, to_uid, to_cs, text):
        """What ATAK does: TCP to the recipient's endpoint from its latest position report."""
        ep = self.endpoint_of(to_cs); ip, port, _ = ep.split(":")
        _, x = dc.build_geochat(self.cs, self.uid, text, to_uid=to_uid, to_callsign=to_cs)
        s = socket.create_connection((ip, int(port)), timeout=3, source_address=(self.ip, 0)); s.sendall(x); s.close()
        return ep
    def all_chat(self, text):
        _, x = dc.build_geochat(self.cs, self.uid, text); mcast_sock(self.ip).sendto(x, ("224.10.10.1", 17012))

class ServerPhone:
    def __init__(self, uid, cs):
        self.uid, self.cs, self.got = uid, cs, []
        self.connect()
    def connect(self):
        self.s = socket.create_connection(("127.0.0.1", ots.port)); self.s.settimeout(0.2); self.alive = True
        threading.Thread(target=self.rx, daemon=True).start()
        self.s.sendall(sa(self.uid, self.cs, "*:-1:stcp"))
    def rx(self):
        while not stop.is_set() and self.alive:
            try:
                d = self.s.recv(65536)
                if d: self.got.append(d)
                elif d == b"": return
            except socket.timeout: pass
            except OSError: return
    def dm(self, to_cs, text, extra_dests=()):
        _, x = dc.build_geochat(self.cs, self.uid, text, to_uid=to_cs, to_callsign=to_cs, via_server=True)
        if extra_dests:
            x = x.replace(b"</marti>", b"".join(b'<dest callsign="%s"/>' % d.encode() for d in extra_dests) + b"</marti>")
        self.s.sendall(x)
    def all_chat(self, text):
        _, x = dc.build_geochat(self.cs, self.uid, text); self.s.sendall(x)
    def resend(self): self.s.sendall(sa(self.uid, self.cs, "*:-1:stcp"))
    def close(self): self.alive = False; self.s.close()

st = W + "/st.json"; hist = W + "/hist.jsonl"; state = W + "/mailroute.json"
for f in (st, hist, state):
    if os.path.exists(f): os.remove(f)
open(W + "/b.ini", "w").write("[bridge]\nCOT_URL = tcp://127.0.0.1:%d\nPYTAK_MULTICAST_LOCAL_ADDR = 127.0.0.1\nSTATUS_FILE = %s\nRETURN_PATH = 1\n"
                              "LOCAL_CHAT_COPY = udp://127.0.0.1:17013\nLOCAL_SEND = udp://127.0.0.1:17014\nBRIDGE_UID = DECK-testdeck\nDM_RELAY = 1\n" % (ots.port, st))
open(W + "/c.ini", "w").write("[chat]\nUID = DECK-testdeck\nMESH_LOCAL_ADDR = 127.0.0.1\nHISTORY_FILE = %s\nBRIDGE_STATUS_FILE = %s\nMAX_SENDS_PER_10S = 30\nSA_INTERVAL = 15\n" % (hist, st))
open(W + "/m.ini", "w").write("[mailroute]\nBRIDGE_UID = DECK-testdeck\nSTATE_FILE = %s\nRECHECK = 3\n" % state)
env = dict(os.environ, PYTHONWARNINGS="ignore")
procs = {}
def start_bridge():
    procs["b"] = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", W + "/b.ini"], stdout=open(W + "/b.log", "a"), stderr=subprocess.STDOUT, env=env)
def start_helper():
    procs["m"] = subprocess.Popen([sys.executable, S + "/mesh_mailroute.py", "--config", W + "/m.ini"], stdout=open(W + "/m.log", "a"), stderr=subprocess.STDOUT, env=env)
start_helper(); start_bridge()
procs["c"] = subprocess.Popen([sys.executable, B + "/deck_chat.py", "--config", W + "/c.ini"], stdout=open(W + "/c.log", "w"), stderr=subprocess.STDOUT, env=env)
time.sleep(4)
alpha = MeshPhone("A15", "ALPHA 1-5", "127.0.0.2")
bravo = MeshPhone("B2", "BRAVO-2", "127.0.0.3")
f28 = ServerPhone("F28", "HQ 28")
echo_ = ServerPhone("U1", "ECHO")
time.sleep(3); f28.resend(); echo_.resend(); time.sleep(4)

def api(path):
    return json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8090" + path, headers={"Host": "127.0.0.1:8090"}), timeout=3))
def deckchat_has(text): return any(text in m["text"] for m in api("/api/messages?after=0")["messages"])
def on_mesh_multicast(text, since): return any(text.encode() in d for d, a in chat_heard[since:])

try:
    # ---- the helper's rules
    check("helper made a rule for each mesh phone's callsign AND UID (ALPHA 1-5/A15, BRAVO-2/B2)", wait(lambda: rules() == ["A15", "ALPHA 1-5", "B2", "BRAVO-2"]), rules())
    check("no rule for server phones or DECK itself", not any(r in ("HQ 28", "F28", "ECHO", "U1", "DECK", "DECK-testdeck") for r in rules()))
    check("OTS's own DM bindings untouched", ("dms", "HQ 28", "HQ 28") in all_dms_bindings() and ("dms", "ECHO", "ECHO") in all_dms_bindings())

    # ---- addresses
    f28_mesh = [d for d, a in sa_heard if b'callsign="HQ 28"' in d]
    check("on the mesh, HQ 28's DM address points at the deck's relay port (127.0.0.1:4243)", f28_mesh and b'endpoint="127.0.0.1:4243:tcp"' in f28_mesh[-1], f28_mesh[-1:])
    roster = {r["callsign"]: r for r in api("/api/info")["roster"]}
    check("Deck Chat still lists HQ 28 as a SERVER contact", roster.get("HQ 28", {}).get("path") == "server", roster.get("HQ 28"))
    check("Deck Chat lists ALPHA 1-5 as a MESH contact", roster.get("ALPHA 1-5", {}).get("path") == "mesh", roster.get("ALPHA 1-5"))

    # ---- server -> mesh
    nc = len(chat_heard)
    f28.dm("ALPHA 1-5", "rule test 1")
    check("SERVER -> MESH: HQ 28's DM reaches ALPHA 1-5's phone", wait(lambda: any(p["text"] == "rule test 1" for p in chats(alpha.got))), alpha.got[-1:])
    p = [p for p in chats(alpha.got) if p["text"] == "rule test 1"]
    check("...exactly once", len(p) == 1, len(p))
    check("...unchanged: a normal DM from HQ 28 (uid F28) in the ALPHA 1-5 chat", p and p[0]["from"] == "HQ 28" and p[0]["from_uid"] == "F28" and p[0]["room"] == "ALPHA 1-5", p)
    check("...BRAVO-2 doesn't get it", not any("rule test 1" in x["text"] for x in chats(bravo.got)))
    check("...it is NOT broadcast on the mesh", not on_mesh_multicast("rule test 1", nc))
    check("...it does NOT show on the deck's screen", not deckchat_has("rule test 1"))
    check("...ECHO doesn't get it", not any("rule test 1" in x["text"] for x in chats(echo_.got)))

    # ---- mesh -> server
    nc = len(chat_heard)
    # ---- iTAK addresses DMs by the recipient's UID, not its callsign
    _, x = dc.build_geochat("HQ 28", "F28", "itak style by uid", to_uid="A15", to_callsign="ALPHA 1-5", via_server=True)
    x = re.sub(rb'<dest callsign="[^"]*"/>', b'<dest uid="A15"/>', x)
    check("(test setup) the iTAK-style DM carries only <dest uid>", b'<dest uid="A15"/>' in x and b"<dest callsign" not in x, x[-300:])
    since_i = len(chat_heard)
    f28.s.sendall(x)
    check("SERVER -> MESH, iTAK style (dest uid): reaches ALPHA 1-5's phone", wait(lambda: any(p["text"] == "itak style by uid" for p in chats(alpha.got))))
    check("...exactly once, BRAVO-2 and ECHO don't get it, not broadcast on the mesh",
          sum(1 for p in chats(alpha.got) if p["text"] == "itak style by uid") == 1 and not any("itak style by uid" in x["text"] for x in chats(bravo.got) + chats(echo_.got))
          and not on_mesh_multicast("itak style by uid", since_i))
    ep = alpha.dm("F28", "HQ 28", "rule test reply")
    check("MESH -> SERVER: ALPHA 1-5's ATAK sends the reply to the deck (the address it was given)", ep == "127.0.0.1:4243:tcp", ep)
    check("...HQ 28 receives it", wait(lambda: any(p["text"] == "rule test reply" for p in chats(f28.got))), f28.got[-1:])
    q = [p for p in chats(f28.got) if p["text"] == "rule test reply"]
    check("...exactly once, unchanged: from ALPHA 1-5 (uid A15), in the HQ 28 chat", len(q) == 1 and q[0]["from"] == "ALPHA 1-5" and q[0]["from_uid"] == "A15" and q[0]["room"] == "HQ 28", q)
    check("...ECHO doesn't get it", not any("rule test reply" in x["text"] for x in chats(echo_.got)))
    check("...not broadcast on the mesh, not on the deck's screen", not on_mesh_multicast("rule test reply", nc) and not deckchat_has("rule test reply"))

    # ---- a real back-and-forth
    for i in range(3):
        f28.dm("ALPHA 1-5", "ping %d" % i); time.sleep(0.4); alpha.dm("F28", "HQ 28", "pong %d" % i); time.sleep(0.4)
    ok = wait(lambda: all(any(p["text"] == "ping %d" % i for p in chats(alpha.got)) and any(p["text"] == "pong %d" % i for p in chats(f28.got)) for i in range(3)))
    dup = any(sum(1 for p in chats(alpha.got) if p["text"] == "ping %d" % i) != 1 or sum(1 for p in chats(f28.got) if p["text"] == "pong %d" % i) != 1 for i in range(3))
    check("3 rounds back and forth: everything arrives, nothing twice", ok and not dup)
    bravo.dm("U1", "ECHO", "bravo to echo_"); echo_.dm("BRAVO-2", "echo_ to bravo")
    check("a second pair (BRAVO-2 <-> ECHO) works at the same time", wait(lambda: any(p["text"] == "bravo to echo_" for p in chats(echo_.got)) and any(p["text"] == "echo_ to bravo" for p in chats(bravo.got))))

    # ---- one message to several people
    f28.dm("ALPHA 1-5", "group note", extra_dests=("BRAVO-2", "ECHO"))
    wait(lambda: any(p["text"] == "group note" for p in chats(echo_.got)))
    cnt = (sum(1 for p in chats(alpha.got) if p["text"] == "group note"), sum(1 for p in chats(bravo.got) if p["text"] == "group note"), sum(1 for p in chats(echo_.got) if p["text"] == "group note"))
    check("one message to ALPHA 1-5 + BRAVO-2 + ECHO: each gets it once", wait(lambda: (sum(1 for p in chats(alpha.got) if p["text"] == "group note"), sum(1 for p in chats(bravo.got) if p["text"] == "group note"), sum(1 for p in chats(echo_.got) if p["text"] == "group note")) == (1, 1, 1)), cnt)

    # ---- normal traffic unchanged
    nc = len(chat_heard)
    alpha.all_chat("mesh all chat"); f28.all_chat("server all chat")
    check("All Chat Rooms mesh -> server still works", wait(lambda: any(p["text"] == "mesh all chat" for p in chats(f28.got)) and any(p["text"] == "mesh all chat" for p in chats(echo_.got))))
    check("All Chat Rooms server -> mesh still works (broadcast as before)", wait(lambda: on_mesh_multicast("server all chat", nc)))
    f28.dm("ECHO", "server to server dm")
    check("server <-> server DM unchanged (ECHO once, not on the mesh)", wait(lambda: sum(1 for p in chats(echo_.got) if p["text"] == "server to server dm") == 1) and not on_mesh_multicast("server to server dm", 0))
    f28.dm("DECK", "for the deck operator")
    check("DM to DECK still lands in Deck Chat (operator), not on the mesh", wait(lambda: deckchat_has("for the deck operator")) and not on_mesh_multicast("for the deck operator", 0))
    f28.dm("GHOST", "nobody home")
    time.sleep(1.5)
    check("DM to an unknown callsign: dropped by OTS as today, nothing on the mesh", not on_mesh_multicast("nobody home", 0) and not any("nobody home" in p["text"] for p in chats(alpha.got) + chats(bravo.got)))

    # ---- deck off
    procs["b"].terminate(); procs["b"].wait(5); time.sleep(2)
    check("deck off: the mail rules stay (nothing to redo)", rules() == ["A15", "ALPHA 1-5", "B2", "BRAVO-2"], rules())
    n_u = len(chats(echo_.got))
    f28.dm("ECHO", "deck off server dm"); f28.all_chat("deck off all chat")
    check("deck off: server phones' DMs and All Chat work normally", wait(lambda: any(p["text"] == "deck off server dm" for p in chats(echo_.got)) and any(p["text"] == "deck off all chat" for p in chats(echo_.got))))
    d0 = qdepth("DECK-testdeck") or 0
    f28.dm("ALPHA 1-5", "sent while the deck was off")
    check("deck off: a DM for ALPHA 1-5 waits in DECK's mailbox (1 message), nothing else piles up from DMs", wait(lambda: (qdepth("DECK-testdeck") or 0) >= d0 + 1), (d0, qdepth("DECK-testdeck")))
    start_bridge(); time.sleep(3)
    check("deck back on: the waiting DM is delivered to ALPHA 1-5 (within OTS's 24 h)", wait(lambda: any(p["text"] == "sent while the deck was off" for p in chats(alpha.got)), 12))
    f28.dm("ALPHA 1-5", "after deck restart")
    check("deck back on: new DMs flow again without touching anything", wait(lambda: any(p["text"] == "after deck restart" for p in chats(alpha.got)), 10))

    # ---- a rule goes missing (like after a RabbitMQ restart)
    c = pika.BlockingConnection(pika.ConnectionParameters("localhost")); c.channel().queue_unbind(exchange="dms", queue="DECK-testdeck", routing_key="ALPHA 1-5"); c.close()
    check("rule removed behind the helper's back -> helper puts it back within a few seconds", wait(lambda: "ALPHA 1-5" in rules(), 10), rules())

    # ---- ALPHA 1-5 connects to the server directly (e.g. over cell)
    direct = ServerPhone("A15", "ALPHA 1-5"); time.sleep(1)
    check("ALPHA 1-5 connects to the server directly -> helper drops both its rules", wait(lambda: "ALPHA 1-5" not in rules() and "A15" not in rules(), 10), rules())
    check("...BRAVO-2's rules stay", "BRAVO-2" in rules() and "B2" in rules())
    na, nd = len(chats(alpha.got)), len(chats(direct.got))
    f28.dm("ALPHA 1-5", "while connected directly")
    time.sleep(3)
    got_direct = sum(1 for p in chats(direct.got) if p["text"] == "while connected directly")
    got_mesh = sum(1 for p in chats(alpha.got) if p["text"] == "while connected directly")
    check("...the DM arrives once (via its own server connection), not twice", got_direct == 1 and got_mesh == 0, (got_direct, got_mesh))
    direct.close(); time.sleep(1)
    check("ALPHA 1-5 disconnects from the server again -> both rules come back", wait(lambda: "ALPHA 1-5" in rules() and "A15" in rules(), 12), rules())

    # ---- helper off: nothing breaks
    procs["m"].terminate(); procs["m"].wait(5)
    f28.dm("ECHO", "helper off dm")
    check("helper stopped: normal server DMs unaffected", wait(lambda: any(p["text"] == "helper off dm" for p in chats(echo_.got))))
    f28.dm("ALPHA 1-5", "helper off but rule still there")
    check("helper stopped: existing rules keep working", wait(lambda: any(p["text"] == "helper off but rule still there" for p in chats(alpha.got)), 10))

    # ---- undo
    out = subprocess.run([sys.executable, S + "/mesh_mailroute.py", "--config", W + "/m.ini", "--remove-all"], capture_output=True, text=True, env=env).stdout
    check("--remove-all removes exactly its own rules", rules() == [] and ("dms", "HQ 28", "HQ 28") in all_dms_bindings() and ("dms", "DECK-testdeck", "DECK-testdeck") in all_dms_bindings(), (out, rules()))
    f28.dm("ALPHA 1-5", "after undo")
    time.sleep(2)
    check("after undo: back to today's behavior (server -> mesh DM goes nowhere)", not any(p["text"] == "after undo" for p in chats(alpha.got)))
    f28.dm("ECHO", "after undo normal")
    check("after undo: normal mail fine", wait(lambda: any(p["text"] == "after undo normal" for p in chats(echo_.got))))
finally:
    stop.set()
    for p in procs.values():
        try: p.terminate()
        except Exception: pass
    time.sleep(0.5)
blog = open(W + "/b.log").read(); mlog = open(W + "/m.log").read()
check("bridge log shows the DM relay listening and relaying both ways", "DM relay listening" in blog and "server -> mesh ALPHA 1-5" in blog and "mesh 127.0.0.2 -> server HQ 28" in blog, blog[-1500:])
check("helper log explains what it did (rules added/removed, direct connection)", "rule added" in mlog and "connected to the server directly" in mlog, mlog[-1500:])
check("after the deck restart the waiting DM was held until ALPHA 1-5 was heard, then delivered", "holding it up to" in blog and "(was held)" in blog, [l for l in blog.splitlines() if "held" in l or "holding" in l][-3:])
check("no tracebacks in bridge, helper or Deck Chat logs", "Traceback" not in blog + mlog + open(W + "/c.log").read(), [l for l in (blog + mlog).splitlines() if "Error" in l][:5])
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
