import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
"""Chat DMs end to end: real bridge + real deck_chat + fake OTS (OTS 1.7.13 routing rules:
first-<contact> identity per connection, group fan-out, <dest callsign> -> only that connection) +
fake mesh phone (multicast SA, TCP 4242 listener on 127.0.0.2) + fake server phone (FOXTROT)."""
import json, os, re, socket, subprocess, sys, threading, time, urllib.request
sys.path.insert(0, PKG + "/chat")
import deck_chat as dc
from playwright.sync_api import sync_playwright

W = "/tmp/mmb_dm"; _o.makedirs(W, exist_ok=True)
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:400]))

SKIP = ("OpenTAK ICU", "Meshtastic", "DMRCOT")
class FakeOTS:
    def __init__(self):
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0)); self.srv.listen(10); self.port = self.srv.getsockname()[1]
        self.conns = []; self.lock = threading.Lock(); self.log = []
        threading.Thread(target=self.accept, daemon=True).start()
    def accept(self):
        while True:
            try: s, a = self.srv.accept()
            except OSError: return
            c = {"sock": s, "uid": None, "cs": None, "deliver": False, "got": []}
            with self.lock: self.conns.append(c)
            threading.Thread(target=self.read, args=(c,), daemon=True).start()
    def read(self, c):
        buf = b""
        while True:
            try: d = c["sock"].recv(65536)
            except OSError: return
            if not d: return
            buf += d
            while b"</event>" in buf:
                i = buf.index(b"</event>") + 8
                ev, buf = buf[:i], buf[i:]
                self.handle(c, ev[ev.find(b"<event"):])
    def handle(self, c, ev):
        self.log.append((c.get("cs"), ev))
        if c["uid"] is None and b"<contact" in ev and b'callsign="' in ev:
            c["uid"] = re.search(rb'uid="([^"]+)"', ev).group(1).decode()
            c["cs"] = re.search(rb'callsign="([^"]+)"', ev).group(1).decode()
            m = re.search(rb'platform="([^"]*)"', ev)
            c["deliver"] = not (m and m.group(1).decode() in SKIP)
        dest = re.findall(rb'<dest[^>]*callsign="([^"]+)"', ev)
        with self.lock:
            if dest:
                targets = [t for t in self.conns if t["deliver"] and t["cs"] in [d.decode() for d in dest]]
            else:
                targets = [t for t in self.conns if t is not c and t["deliver"] and t["uid"] != c["uid"]]
        for t in targets:
            try: t["sock"].sendall(ev); t["got"].append(ev)
            except OSError: pass

def mcast_sock():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1")); s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    return s
def join(group, port):
    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    r.bind((group, port)); r.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(group) + socket.inet_aton("127.0.0.1")); r.settimeout(0.2)
    return r
def sa(uid, cs, ep):
    t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); st = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 300))
    return ('<event version="2.0" uid="%s" type="a-f-G-U-C" how="m-g" time="%s" start="%s" stale="%s"><point lat="1" lon="2" hae="0" ce="9" le="9"/>'
            '<detail><contact callsign="%s" endpoint="%s"/><takv platform="ATAK-CIV" device="x" os="x" version="x"/></detail></event>' % (uid, t, t, st, cs, ep)).encode()

stop = threading.Event()
ots = FakeOTS()
# mesh listeners (what mesh phones would hear)
sa_heard, chat_heard = [], []
def listen(r, sink):
    while not stop.is_set():
        try: sink.append(r.recvfrom(65535)[0])
        except socket.timeout: pass
threading.Thread(target=listen, args=(join("239.2.3.1", 6969), sa_heard), daemon=True).start()
threading.Thread(target=listen, args=(join("224.10.10.1", 17012), chat_heard), daemon=True).start()
# fake mesh phone ALPHA-1: DM listener on 127.0.0.2:4242
alpha_got = []; alpha_up = threading.Event(); alpha_up.set()
def alpha_srv():
    while not stop.is_set():
        if not alpha_up.is_set(): time.sleep(0.1); continue
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.2", 4242)); s.listen(5); s.settimeout(0.2)
        while alpha_up.is_set() and not stop.is_set():
            try: c, _ = s.accept()
            except socket.timeout: continue
            c.settimeout(2); data = b""
            try:
                while True:
                    d = c.recv(65536)
                    if not d: break
                    data += d
            except socket.timeout: pass
            alpha_got.append(data); c.close()
        s.close()
threading.Thread(target=alpha_srv, daemon=True).start()
def alpha_sa():
    m = mcast_sock()
    while not stop.is_set():
        m.sendto(sa("A1", "ALPHA-1", "127.0.0.2:4242:tcp"), ("239.2.3.1", 6969)); time.sleep(1)
threading.Thread(target=alpha_sa, daemon=True).start()

st = W + "/st.json"; hist = W + "/hist.jsonl"
for f in (st, hist):
    if os.path.exists(f): os.remove(f)
open(W + "/b.ini", "w").write("[bridge]\nCOT_URL = tcp://127.0.0.1:%d\nPYTAK_MULTICAST_LOCAL_ADDR = 127.0.0.1\nSTATUS_FILE = %s\nRETURN_PATH = 1\nLOCAL_CHAT_COPY = udp://127.0.0.1:17013\nLOCAL_SEND = udp://127.0.0.1:17014\nBRIDGE_UID = DECK-test\n" % (ots.port, st))
open(W + "/c.ini", "w").write("[chat]\nUID = DECK-test\nMESH_LOCAL_ADDR = 127.0.0.1\nHISTORY_FILE = %s\nBRIDGE_STATUS_FILE = %s\nMAX_SENDS_PER_10S = 30\nSA_INTERVAL = 15\n" % (hist, st))
env = dict(os.environ, PYTHONWARNINGS="ignore")
bp = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", W + "/b.ini"], stdout=open(W + "/b.log", "w"), stderr=subprocess.STDOUT, env=env)
cp = subprocess.Popen([sys.executable, PKG + "/chat/deck_chat.py", "--config", W + "/c.ini"], stdout=open(W + "/c.log", "w"), stderr=subprocess.STDOUT, env=env)
time.sleep(4)
# server phone FOXTROT
foxtrot = socket.create_connection(("127.0.0.1", ots.port)); foxtrot.settimeout(0.2); mom_got = []
def mom_rx():
    while not stop.is_set():
        try:
            d = foxtrot.recv(65536)
            if d: mom_got.append(d)
        except socket.timeout: pass
        except OSError: return
threading.Thread(target=mom_rx, daemon=True).start()
foxtrot.sendall(sa("SRV1", "FOXTROT", "*:-1:stcp"))
time.sleep(3)

def api(path, body=None):
    if body is None:
        return json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8090" + path, headers={"Host": "127.0.0.1:8090"}), timeout=3))
    r = urllib.request.Request("http://127.0.0.1:8090" + path, data=json.dumps(body).encode(), headers={"Host": "127.0.0.1:8090", "Content-Type": "application/json", "X-Deck": "1"}, method="POST")
    try: return json.load(urllib.request.urlopen(r, timeout=6))
    except urllib.error.HTTPError as e: return json.load(e)
def msgs(): return api("/api/messages?after=0")["messages"]

try:
    info = api("/api/info")
    deck_sa = [x for x in sa_heard if b'uid="DECK-test"' in x and b'platform="TAK MESH TOC Chat"' in x]
    check("deck announces itself on the mesh (contact + DM endpoint 127.0.0.1:4242:tcp)", deck_sa and b'endpoint="127.0.0.1:4242:tcp"' in deck_sa[-1], deck_sa[-1:] )
    check("with no position set, deck's mesh report is sent already stale (no marker)", deck_sa and re.search(rb'start="([^"]+)"', deck_sa[-1]).group(1) == re.search(rb'stale="([^"]+)"', deck_sa[-1]).group(1))
    check("DM listener up on 4242", info["dm"]["listening"] is True, info["dm"])
    srv_saw_deckchat_sa = any(b'platform="TAK MESH TOC Chat"' in ev for _, ev in ots.log)
    check("bridge does NOT forward the deck's mesh report to the server (server knows DECK via bridge identity)", not srv_saw_deckchat_sa)
    roster = {r["callsign"]: r for r in info["roster"]}
    check("roster: ALPHA-1 reachable directly over the mesh", roster.get("ALPHA-1", {}).get("path") == "mesh", roster.get("ALPHA-1"))
    check("roster: FOXTROT (server phone) reachable through the server", roster.get("FOXTROT", {}).get("path") == "server", roster.get("FOXTROT"))

    # deck -> mesh phone DM
    n_chat = len(chat_heard); n_srv = len(ots.log)
    r = api("/api/send", {"text": "alpha, direct to you", "to": "A1"})
    time.sleep(1.5)
    got = [x for x in alpha_got if b"alpha, direct to you" in x]
    check("deck -> ALPHA-1 DM accepted and sent", r.get("ok") and r.get("status") == "sent", r)
    check("ALPHA-1's phone received it over TCP 4242", len(got) == 1, alpha_got[-2:])
    p = dc.parse_chat(got[0]) if got else {}
    check("it is a proper DM: chatroom=ALPHA-1, id/uid1=A1, from DECK", p and p["room"] == "ALPHA-1" and p["room_id"] == "A1" and p["to_uid"] == "A1" and p["from"] == "DECK", p)
    check("deck -> mesh DM did NOT go on mesh multicast chat", not any(b"alpha, direct to you" in x for x in chat_heard[n_chat:]))
    check("deck -> mesh DM did NOT go to the server", not any(b"alpha, direct to you" in ev for _, ev in ots.log[n_srv:]))
    mine = [m for m in msgs() if m["text"] == "alpha, direct to you"]
    check("shown in the ALPHA-1 thread, marked delivered", mine and mine[0]["thread"] == "A1" and mine[0]["delivered"] and mine[0]["dm"], mine)

    # deck -> server phone DM
    n_chat = len(chat_heard); n_mom = len(mom_got)
    r = api("/api/send", {"text": "foxtrot, private", "to": "FOXTROT"})
    time.sleep(1.5)
    check("deck -> FOXTROT DM accepted and sent", r.get("ok") and r.get("status") == "sent", r)
    mg = [x for x in mom_got[n_mom:] if b"foxtrot, private" in x]
    check("FOXTROT received it through the server", len(mg) == 1, mom_got[n_mom:])
    check("server DM carries <marti><dest callsign=FOXTROT>", mg and b'<dest callsign="FOXTROT"/>' in mg[0])
    other = [c for c in ots.conns if c["cs"] not in ("FOXTROT", "DECK")]
    check("server DM did NOT go on the mesh", not any(b"foxtrot, private" in x for x in chat_heard[n_chat:]))

    # mesh phone -> deck DM over TCP 4242
    _, x = dc.build_geochat("ALPHA-1", "A1", "deck, alpha here privately", to_uid="DECK-test", to_callsign="DECK")
    s = socket.create_connection(("127.0.0.1", 4242)); s.sendall(x); s.close(); time.sleep(1)
    got = [m for m in msgs() if m["text"] == "deck, alpha here privately"]
    check("ALPHA-1 -> deck DM received on 4242, in ALPHA-1's thread", got and got[0]["dm"] and got[0]["thread"] == "A1" and not got[0]["mine"], got)

    # server phone -> deck DM
    n_chat = len(chat_heard)
    _, x = dc.build_geochat("FOXTROT", "SRV1", "deck, foxtrot here privately", to_uid="DECK-test", to_callsign="DECK", via_server=True)
    foxtrot.sendall(x); time.sleep(1.5)
    got = [m for m in msgs() if m["text"] == "deck, foxtrot here privately"]
    check("FOXTROT -> deck DM arrives (OTS -> bridge -> Deck Chat), in FOXTROT's thread", got and got[0]["dm"] and got[0]["thread"] == "SRV1" and got[0]["src"] == "server", got)
    check("FOXTROT -> deck DM is NOT rebroadcast onto the mesh", not any(b"deck, foxtrot here privately" in x for x in chat_heard[n_chat:]))

    # All Chat Rooms still fine
    n_chat = len(chat_heard)
    r = api("/api/send", {"text": "all stations, radio check"}); time.sleep(1.5)
    check("All Chat Rooms still goes to the mesh", any(b"all stations, radio check" in x for x in chat_heard[n_chat:]))
    check("...and to FOXTROT via the server", any(b"all stations, radio check" in x for x in mom_got))
    allm = [m for m in msgs() if m["text"] == "all stations, radio check"]
    check("...shown in the ALL thread", allm and allm[0]["thread"] == "ALL")

    # outbox: ALPHA-1 unreachable -> DM queued, does not block All Chat; then delivered when back
    alpha_up.clear(); time.sleep(0.6)
    r = api("/api/send", {"text": "alpha, you there?", "to": "A1"})
    check("DM to unreachable mesh phone -> queued with a reason", r.get("ok") and r.get("status") == "queued" and "can't reach" in r.get("note", ""), r)
    n_chat = len(chat_heard)
    r2 = api("/api/send", {"text": "all: still working"}); time.sleep(1)
    check("a stuck DM does not block All Chat Rooms", r2.get("status") == "sent" and any(b"all: still working" in x for x in chat_heard[n_chat:]), r2)
    alpha_up.set(); time.sleep(5)
    check("when ALPHA-1 is back, the queued DM is delivered by the outbox", any(b"alpha, you there?" in x for x in alpha_got))
    check("unknown contact refused clearly", "no contact" in api("/api/send", {"text": "x", "to": "NOBODY"}).get("error", ""))

    # ---- UI: tabs
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 720})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:8090/"); pg.wait_for_timeout(6000)
        tabs = pg.locator("#tabs button").all_inner_texts()
        check("UI: tabs for ALL plus the two DM conversations", any("ALL" in t for t in tabs) and any("ALPHA-1" in t for t in tabs) and any("FOXTROT" in t for t in tabs), tabs)
        check("UI: ALL view hides DMs", pg.locator(".m:not(.hidden)", has_text="foxtrot, private").count() == 0 and pg.locator(".m:not(.hidden)", has_text="all stations").count() == 1)
        pg.locator("#rlist .r", has_text="FOXTROT").click(); pg.wait_for_timeout(400)
        check("UI: clicking FOXTROT in ON NET opens the FOXTROT conversation", pg.locator(".m:not(.hidden)", has_text="foxtrot, private").count() == 1 and pg.locator(".m:not(.hidden)", has_text="all stations").count() == 0)
        check("UI: placeholder says direct message to FOXTROT", "FOXTROT" in pg.get_attribute("#text", "placeholder"))
        pg.keyboard.type("typed in the FOXTROT tab"); pg.keyboard.press("Enter"); pg.wait_for_timeout(1500)
        check("UI: Enter in a DM tab sends a DM to FOXTROT through the server", any(b"typed in the FOXTROT tab" in x and b'<dest callsign="FOXTROT"/>' in x for x in mom_got))
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        check("UI: Esc (empty box) returns to ALL", "All Chat Rooms" in pg.get_attribute("#text", "placeholder"))
        _, x = dc.build_geochat("ALPHA-1", "A1", "second private note", to_uid="DECK-test", to_callsign="DECK")
        s = socket.create_connection(("127.0.0.1", 4242)); s.sendall(x); s.close(); pg.wait_for_timeout(2500)
        badge = pg.locator("#tabs button", has_text="ALPHA-1").locator(".badge")
        check("UI: new DM while on ALL shows an unread badge on its tab", badge.count() == 1 and badge.inner_text() == "1", pg.locator("#tabs").inner_text())
        check("UI: and a hint telling you who wrote", "ALPHA-1" in pg.inner_text("#hint"))
        pg.keyboard.type("/dm alpha-1 replying via command"); pg.keyboard.press("Enter"); pg.wait_for_timeout(1500)
        check("UI: /dm NAME text opens the thread and sends", any(b"replying via command" in x for x in alpha_got) and "ALPHA-1" in pg.get_attribute("#text", "placeholder"))
        pg.screenshot(path=W + "/dm_tab.png")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300); pg.screenshot(path=W + "/all_tab.png")
        check("UI: no page errors", not errs, errs)
        b.close()
finally:
    stop.set(); cp.terminate(); bp.terminate()
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
