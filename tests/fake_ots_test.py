import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
"""Fake OpenTAKServer that copies OTS 1.7.13's connection-identity rule, with the REAL bridge.

OTS rule (EudHandler.parse_device_info): the first CoT with <contact> on a connection sets its
identity; if that device's takv platform is OpenTAK ICU / Meshtastic / DMRCOT, no delivery queue
is set up, so that connection never receives anything. Traffic is delivered to every other
identified connection except back to the connection it came from.
"""
import os, re, socket, subprocess, sys, threading, time
sys.path.insert(0, PKG + "/dashboard")

W = "/tmp/mmb_ident"; _o.makedirs(W, exist_ok=True)
SKIP = ("OpenTAK ICU", "Meshtastic", "DMRCOT")
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:300]))


class FakeOTS:
    def __init__(self):
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0)); self.srv.listen(10); self.port = self.srv.getsockname()[1]
        self.conns = []; self.lock = threading.Lock(); self.accepts = 0
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try: s, a = self.srv.accept()
            except OSError: return
            c = {"sock": s, "uid": None, "deliver": False, "first": None, "rx": []}
            with self.lock: self.conns.append(c); self.accepts += 1
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
                ev = ev[ev.find(b"<event"):]
                self.handle(c, ev)

    def handle(self, c, ev):
        if c["first"] is None: c["first"] = ev
        if c["uid"] is None and b"<contact" in ev and b'callsign="' in ev:
            uid = re.search(rb'uid="([^"]+)"', ev).group(1).decode()
            if not uid.endswith("ping"):
                c["uid"] = uid
                m = re.search(rb'platform="([^"]*)"', ev)
                c["deliver"] = not (m and m.group(1).decode() in SKIP)
        with self.lock: targets = [t for t in self.conns if t is not c and t["deliver"] and t["uid"] != c["uid"]]
        for t in targets:
            try: t["sock"].sendall(ev)
            except OSError: pass


def sa(uid, cs, platform="ATAK-CIV", lat=1.0):
    t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); st = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 300))
    return ('<event version="2.0" uid="%s" type="a-f-G-U-C" how="m-g" time="%s" start="%s" stale="%s"><point lat="%s" lon="2" hae="0" ce="9" le="9"/>'
            '<detail><contact callsign="%s"/><takv platform="%s" device="x" os="x" version="x"/></detail></event>' % (uid, t, t, st, lat, cs, platform)).encode()


def run_case(identify):
    ots = FakeOTS()
    status = "%s/st_%d.json" % (W, identify)
    if os.path.exists(status): os.remove(status)
    ini = "%s/b_%d.ini" % (W, identify)
    open(ini, "w").write("[bridge]\nCOT_URL = tcp://127.0.0.1:%d\nPYTAK_MULTICAST_LOCAL_ADDR = 127.0.0.1\nSTATUS_FILE = %s\nRETURN_PATH = 1\nIDENTIFY = %d\nBRIDGE_UID = DECK-test\n" % (ots.port, status, identify))
    # mesh: ICU device chatters constantly (it will be "first" unless the bridge identifies itself)
    stop = threading.Event()
    def mesh_tx():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1")); s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        while not stop.is_set():
            s.sendto(sa("ICU-cam1", "CAM1", "OpenTAK ICU"), ("239.2.3.1", 6969))
            s.sendto(sa("ANDROID-mesh", "MESHPHONE", lat=3.0), ("239.2.3.1", 6969))
            time.sleep(0.05)
    threading.Thread(target=mesh_tx, daemon=True).start()
    # a phone listening on the mesh (does the server phone show up here?)
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    rx.bind(("239.2.3.1", 6969)); rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton("239.2.3.1") + socket.inet_aton("127.0.0.1")); rx.settimeout(0.2)
    mesh_heard = []
    def mesh_rx():
        while not stop.is_set():
            try: mesh_heard.append(rx.recvfrom(65535)[0])
            except socket.timeout: pass
    threading.Thread(target=mesh_rx, daemon=True).start()
    time.sleep(0.5)
    env = dict(os.environ, PYTHONWARNINGS="ignore")
    bp = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", ini], stdout=open("%s/b_%d.log" % (W, identify), "w"), stderr=subprocess.STDOUT, env=env)
    time.sleep(3)
    # the server phone connects directly to "OTS" and reports its position
    ph = socket.create_connection(("127.0.0.1", ots.port)); ph.settimeout(0.2)
    phone_got = []
    def ph_rx():
        while not stop.is_set():
            try:
                d = ph.recv(65536)
                if d: phone_got.append(d)
            except socket.timeout: pass
            except OSError: return
    threading.Thread(target=ph_rx, daemon=True).start()
    for _ in range(6):
        ph.sendall(sa("ANDROID-server", "SERVERPHONE", lat=5.0)); time.sleep(0.5)
    time.sleep(2)
    import json
    st = json.load(open(status)) if os.path.exists(status) else {}
    bridge = next((c for c in ots.conns if c["sock"].getpeername() != ph.getsockname() and c is not None and c["first"] is not None and c is not [x for x in ots.conns if x["uid"] == "ANDROID-server"][0]), None)
    out = {
        "bridge_first": bridge["first"][:160] if bridge else None,
        "bridge_identity": bridge["uid"] if bridge else None,
        "bridge_delivers": bridge["deliver"] if bridge else None,
        "server_rx": st.get("server", {}).get("rx_events"),
        "returned": st.get("return", {}).get("sent"),
        "mesh_saw_serverphone": any(b"ANDROID-server" in d for d in mesh_heard),
        "serverphone_saw_mesh": any(b"ANDROID-mesh" in d for d in phone_got),
        "serverphone_saw_deck_identity": any(b"DECK-test" in d for d in phone_got),
        "serverphone_saw_icu": any(b"ICU-cam1" in d for d in phone_got),
        "accepts": ots.accepts,
    }
    stop.set(); bp.terminate(); bp.wait(5); ph.close(); ots.srv.close()
    return out


print("== Case A: OLD behavior (IDENTIFY = 0), ICU talks first on the mesh")
a = run_case(0)
print("   ", {k: a[k] for k in ("bridge_identity", "bridge_delivers", "server_rx", "mesh_saw_serverphone")})
check("A reproduces your bug: bridge registered as the ICU device", a["bridge_identity"] == "ICU-cam1", a)
check("A reproduces your bug: OTS delivers nothing to the bridge (server rx 0)", not a["server_rx"], a["server_rx"])
check("A reproduces your bug: mesh never sees the server phone", not a["mesh_saw_serverphone"])

print("== Case B: NEW behavior (IDENTIFY = 1), same ICU-first mesh")
b = run_case(1)
print("   ", {k: b[k] for k in ("bridge_identity", "bridge_delivers", "server_rx", "returned", "mesh_saw_serverphone", "serverphone_saw_mesh")})
check("B: first CoT on the bridge connection is the DECK identity", b["bridge_first"] and b'uid="DECK-test"' in b["bridge_first"], b["bridge_first"])
check("B: OTS identifies the bridge as DECK-test with delivery on", b["bridge_identity"] == "DECK-test" and b["bridge_delivers"], b)
check("B: bridge now receives server traffic", (b["server_rx"] or 0) > 0, b["server_rx"])
check("B: server phone's position reaches the mesh", b["mesh_saw_serverphone"])
check("B: mesh phone's position reaches the server phone", b["serverphone_saw_mesh"])
check("B: the ICU device's reports still get forwarded (just not as the identity)", b["serverphone_saw_icu"])
check("B: one connection only (no extra connections)", b["accepts"] == 2, b["accepts"])
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
