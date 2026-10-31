"""The bridge outbox, end to end with the REAL bridge and a small TAK-server stand-in that answers pings:
  A  chats from the mesh reach the server; once a later ping is answered they are confirmed (outbox empty)
  B  the server stops reading and answering, then drops the link: chats written into the dead connection stay
     unconfirmed on disk; the bridge exits (as on the deck, where systemd restarts it)
  C  at the next start every unconfirmed chat is sent again, after the identity; the server has every chat at
     least once; the outbox empties again; the status file reports the replay
  D  too-old chats are dropped at start (OUTBOX_HOURS); a corrupt line in the file is ignored; OUTBOX_MAX caps it
  S  300 chats waiting at start (more than PyTAK's 100-message send queue): the bridge still starts, all arrive in order
  E  with pings off, "written" counts as delivered (nothing piles up); OUTBOX_FILE = off disables it all
  F  the "chats waiting" row never shows during normal traffic (confirmed within a ping interval)
Usage: python3 outbox_e2e.py [work dir]"""
import base64, json, os, re, socket, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); PKG = os.path.dirname(HERE)
W = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mmb_outbox_e2e"
os.makedirs(W, exist_ok=True)
ENV = dict(os.environ, PYTHONWARNINGS="ignore")
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:500]), flush=True)
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

class Server:
    """Accepts one bridge connection at a time, splits the stream at </event>, answers pings (echo), records the
    rest. mode: "ok" (read + answer), "deaf" (neither), "closed" (not listening)."""
    def __init__(self):
        self.ls = socket.socket(); self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(("127.0.0.1", 0)); self.ls.listen(5); self.port = self.ls.getsockname()[1]
        self.mode = "ok"; self.got = []; self.conns = 0; self.cur = None; self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()
    def _accept(self):
        while True:
            c, _ = self.ls.accept()
            with self.lock:
                self.cur = c; self.conns += 1
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()
    def _serve(self, c):
        buf = b""
        c.settimeout(0.2)
        while True:
            if self.mode == "deaf" and self.cur is c:
                time.sleep(0.05); continue
            try:
                d = c.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            if not d:
                return
            buf += d
            while b"</event>" in buf:
                ev, buf = buf.split(b"</event>", 1); ev += b"</event>"
                if b'type="t-x-c-t"' in ev:
                    try:
                        c.sendall(ev)
                    except OSError:
                        return
                else:
                    self.got.append(ev)
    def drop(self):
        with self.lock:
            c, self.cur = self.cur, None
        if c is not None:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()
    def chats(self):
        return [re.search(rb'messageId="([^"]+)"', e).group(1).decode() for e in self.got if b'type="b-t-f"' in e]

srv = Server()
INI = W + "/deck.ini"; STATUS = W + "/status.json"; OUT = W + "/outbox.jsonl"
def write_ini(**kw):
    o = {"PING_INTERVAL": "1", "OUTBOX_FILE": OUT, "OUTBOX_HOURS": "24", "OUTBOX_MAX": "5000"}
    o.update(kw)
    extra = "".join("%s = %s\n" % kv for kv in o.items())
    open(INI, "w").write("[deck]\nCALLSIGN = DECK\nUID = DECK-ob\nMESH_ADDR = 127.0.0.1\nGPS = off\n[bridge]\nCOT_URL = tcp://127.0.0.1:%d\n"
                         "STATUS_FILE = %s\nMESH_CHAT_URL = off\nUP_MIN_INTERVAL = 0\nDEDUPE_SECONDS = 0\n%s" % (srv.port, STATUS, extra))
LOGN = [0]
def start_bridge():
    LOGN[0] += 1
    p = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", INI], stdout=open(W + "/b%d.log" % LOGN[0], "w"), stderr=subprocess.STDOUT, env=ENV)
    end = time.time() + 20
    while time.time() < end and "Listening on udp+ro://239.2.3.1:6969" not in open(W + "/b%d.log" % LOGN[0]).read():
        time.sleep(0.1)
    time.sleep(0.5)
    return p
def blog():
    return open(W + "/b%d.log" % LOGN[0]).read()
def status():
    try:
        return json.load(open(STATUS))
    except Exception:
        return {}
def wait(cond, t=10.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()
mc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); mc.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
TS = 'time="2026-10-22T18:00:00Z" start="2026-10-22T18:00:00Z" stale="2099-01-01T00:00:00Z"'
def chat(mid):
    return ('<event version="2.0" uid="GeoChat.ANDROID-x1.All Chat Rooms.%s" type="b-t-f" how="h-g-i-g-o" %s><point lat="38" lon="-104" hae="0" ce="9" le="9"/>'
            '<detail><__chat parent="RootContactGroup" groupOwner="false" messageId="%s" chatroom="All Chat Rooms" id="All Chat Rooms" senderCallsign="ALPHA">'
            '<chatgrp uid0="ANDROID-x1" uid1="All Chat Rooms" id="All Chat Rooms"/></__chat><remarks source="BAO.F.ATAK.ANDROID-x1" to="All Chat Rooms" time="2026-10-22T18:00:00Z">msg %s</remarks></detail></event>'
            % (mid, TS, mid, mid)).encode()
def pos(i):
    return ('<event version="2.0" uid="ANDROID-x1" type="a-f-G-U-C" how="m-g" %s><point lat="38" lon="-104" hae="0" ce="9" le="9"/><detail><contact callsign="ALPHA" endpoint="127.0.0.1:4242:tcp"/></detail></event>' % TS).encode()
def send(mid):
    mc.sendto(chat(mid), ("239.2.3.1", 6969)); time.sleep(0.01)
def outbox_lines():
    try:
        return [json.loads(l) for l in open(OUT) if l.strip()]
    except FileNotFoundError:
        return []
def unconfirmed_ids():
    L = outbox_lines(); ok = {l["ok"] for l in L if "ok" in L and False} if False else {l["ok"] for l in L if "ok" in l}
    return [l["i"] for l in L if "i" in l and l["i"] not in ok]

# ============================================================ A
print("== A. normal traffic is confirmed by ping answers", flush=True)
if os.path.exists(OUT):
    os.remove(OUT)
write_ini()
bp = start_bridge()
sentA = ["a%02d" % i for i in range(20)]
for m in sentA:
    send(m)
mc.sendto(pos(0), ("239.2.3.1", 6969))
check("A the server got all 20 chats", wait(lambda: set(srv.chats()) >= set(sentA)), (len(srv.chats()), srv.chats()[:5]))
check("A the chats were kept while in flight (status saw them pending)", wait(lambda: status().get("outbox", {}).get("pending", 0) > 0 or not unconfirmed_ids(), 4), status().get("outbox"))
check("A confirmed within a ping interval: outbox pending back to 0", wait(lambda: status().get("outbox", {}).get("pending") == 0 and not unconfirmed_ids(), 6), status().get("outbox"))
check("A the outbox file has an 'ok' for every chat", wait(lambda: not unconfirmed_ids(), 3), unconfirmed_ids())
# F
check("F after a ping answer nothing is left unconfirmed (status waiting=0, unconfirmed=0)",
      wait(lambda: status()["outbox"].get("waiting") == 0 and status()["outbox"].get("unconfirmed") == 0, 5), status()["outbox"])

# ============================================================ B
print("== B. the server goes deaf, then drops the link", flush=True)
srv.mode = "deaf"
time.sleep(0.5)
sentB = ["b%02d" % i for i in range(15)]
for m in sentB:
    send(m)
time.sleep(2.5)                                   # written into the dead connection; pings unanswered
unc = unconfirmed_ids()
check("B the 15 chats written into the dead link are NOT marked delivered (%d unconfirmed on disk)" % len(unc), len(unc) == 15, unc)
check("B status shows them (pending %s, unconfirmed %s)" % (status()["outbox"].get("pending"), status()["outbox"].get("unconfirmed")),
      status()["outbox"].get("pending") == 15, status()["outbox"])
srv.drop(); srv.mode = "ok"
check("B the bridge exits when the server drops the link", wait(lambda: bp.poll() is not None, 15), blog()[-300:])
check("B ...and says why", "Server link down" in blog(), blog()[-300:])
got_before = set(srv.chats())
check("B (sanity) the server never read those 15", not (set(sentB) & got_before), set(sentB) & got_before)

# ============================================================ C
print("== C. next start: unconfirmed chats are sent again", flush=True)
bp = start_bridge()
check("C the server now has every chat at least once (%d of 35)" % len(set(srv.chats()) & set(sentA + sentB)),
      wait(lambda: set(srv.chats()) >= set(sentA + sentB)), sorted(set(sentA + sentB) - set(srv.chats())))
first = srv.got[len(got_before):] if False else None
idx_ident = next((k for k, e in enumerate(srv.got) if b"DECK-ob" in e and b'type="a-' in e and k >= len(got_before)), None)
idx_first_replay = next((k for k, e in enumerate(srv.got) if k >= len(got_before) and b'messageId="b' in e), None)
check("C the identity went up before the replayed chats", idx_ident is not None and idx_first_replay is not None and idx_ident < idx_first_replay, (idx_ident, idx_first_replay))
dups = len(srv.chats()) - len(set(srv.chats()))
check("C no chat was sent twice (%d duplicates)" % dups, dups == 0, dups)
check("C status: replayed 15, pending back to 0", wait(lambda: status()["outbox"].get("replayed") == 15 and status()["outbox"].get("pending") == 0, 8), status()["outbox"])
check("C the log says so", "15 chat(s) still waiting" in blog() and "15 waiting chat(s) sent again" in blog(), blog()[-500:])
check("C the outbox file was compacted (no stale lines left)", wait(lambda: not unconfirmed_ids(), 3) and len(outbox_lines()) <= 2 * 35, len(outbox_lines()))
bp.terminate(); bp.wait(5)

# ============================================================ D
print("== D. old chats, a corrupt line, the cap", flush=True)
now = time.time()
with open(OUT, "w") as f:
    f.write(json.dumps({"i": 1, "t": now - 3 * 86400, "d": base64.b64encode(chat("old1")).decode()}) + "\n")
    f.write("this is not json\n")
    f.write(json.dumps({"i": 2, "t": now - 60, "d": base64.b64encode(chat("new2")).decode()}) + "\n")
    f.write(json.dumps({"i": 3, "t": now - 50, "d": base64.b64encode(chat("new3")).decode()}) + "\n")
    f.write(json.dumps({"ok": 3}) + "\n")
n0 = len(srv.chats())
bp = start_bridge()
check("D the fresh unconfirmed chat is replayed, the 3-day-old one is dropped, the confirmed one is not resent",
      wait(lambda: "new2" in srv.chats()[n0:], 8) and wait(lambda: status()["outbox"].get("replayed") == 1, 5) and "old1" not in srv.chats()[n0:] and "new3" not in srv.chats()[n0:],
      (srv.chats()[n0:], status().get("outbox")))
check("D status: expired 1", status()["outbox"].get("expired") == 1, status()["outbox"])
check("D the corrupt line did not stop it (log mentions 1 too old)", "1 too old" in blog(), blog()[-400:])
bp.terminate(); bp.wait(5)
os.remove(OUT)
write_ini(OUTBOX_MAX="10")
srv.mode = "deaf"
bp = start_bridge()
for i in range(25):
    send("cap%02d" % i)
time.sleep(2)
st = status()["outbox"]
check("D OUTBOX_MAX = 10 keeps only the newest 10 (pending %s, expired %s)" % (st.get("pending"), st.get("expired")), st.get("pending") == 10 and st.get("expired") == 15, st)
srv.drop(); srv.mode = "ok"; wait(lambda: bp.poll() is not None, 15)

# ============================================================ S
print("== S. a big outbox at start (more than PyTAK's 100-message send queue)", flush=True)
write_ini()
now = time.time()
with open(OUT, "w") as f:
    for i in range(300):
        f.write(json.dumps({"i": i + 1, "t": now - 300 + i, "d": base64.b64encode(chat("big%03d" % i)).decode()}) + "\n")
n0 = len(srv.chats())
bp = start_bridge()
check("S the bridge starts and writes its status with 300 waiting chats (1.0.9 hung here)", wait(lambda: status().get("outbox", {}).get("replayed") == 300, 20), (status().get("outbox"), blog()[-300:]))
check("S the server gets all 300, in order, and the outbox empties", wait(lambda: len(set(c for c in srv.chats()[n0:] if c.startswith("big"))) == 300 and status()["outbox"].get("pending") == 0, 30)
      and [c for c in srv.chats()[n0:] if c.startswith("big")] == ["big%03d" % i for i in range(300)], (len(srv.chats()) - n0, status().get("outbox")))
bp.terminate(); bp.wait(5)

# ============================================================ E
print("== E. pings off; outbox off", flush=True)
os.remove(OUT)
write_ini(PING_INTERVAL="0")
bp = start_bridge()
for i in range(5):
    send("np%d" % i)
check("E pings off: written = delivered, nothing piles up", wait(lambda: status()["outbox"].get("pending") == 0 and len(srv.chats()) and "np4" in srv.chats(), 6), status().get("outbox"))
bp.terminate(); bp.wait(5)
write_ini(OUTBOX_FILE="off")
bp = start_bridge()
check("E OUTBOX_FILE = off: status says disabled, log says OFF", status().get("outbox") == {"enabled": False} and "Outbox OFF" in blog(), status().get("outbox"))
bp.terminate(); bp.wait(5)
print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
