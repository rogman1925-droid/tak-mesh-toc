import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
"""GPS features end to end with a FAKE gpsd (real gpsd JSON protocol on TCP):
  - MGRS + gps_tile states (unit)
  - dashboard: real dashboard_server.py -> /api/status gps tile in every state, events, page render
  - bridge: identity CoT to a fake TAK server carries the live GPS position; lost fix -> last spot, stale
  - Deck Chat: mesh position report + chat messages carry the GPS position; lost fix -> stale
Usage: python3 gps_test.py <deck_chat dir> <dashboard dir> <work dir>"""
import json, os, re, socket, subprocess, sys, threading, time, urllib.request
from datetime import datetime
DC, DB, W = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(W, exist_ok=True)
sys.path.insert(0, DB)
import dashboard_server as ds
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:400]), flush=True)
def wait(cond, t=10.0):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.25)
    return cond()
def iso_ts(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

# ---------------------------------------------------------------- fake gpsd
class FakeGpsd:
    def __init__(self):
        self.state = {"mode": 3, "lat": 36.0544, "lon": -112.1401, "alt": 2100.4, "eph": 3.2, "used": 9, "seen": 14, "devices": 1}
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0)); self.srv.listen(10); self.port = self.srv.getsockname()[1]
        self.clients = []
        threading.Thread(target=self.accept, daemon=True).start()
        threading.Thread(target=self.tick, daemon=True).start()
    def accept(self):
        while True:
            c, _ = self.srv.accept()
            c.sendall(b'{"class":"VERSION","release":"3.25","rev":"3.25","proto_major":3,"proto_minor":15}\n')
            self.clients.append(c)
    def send(self, obj):
        line = (json.dumps(obj) + "\n").encode()
        for c in list(self.clients):
            try: c.sendall(line)
            except OSError: self.clients.remove(c)
    def tick(self):
        while True:
            s = self.state
            devs = [{"class": "DEVICE", "path": "/dev/ttyACM0", "driver": "u-blox", "activated": "2026-10-20T20:00:00.000Z"}] * s["devices"]
            self.send({"class": "DEVICES", "devices": devs})
            sats = [{"PRN": i, "used": i < s["used"]} for i in range(s["seen"])]
            self.send({"class": "SKY", "nSat": s["seen"], "uSat": s["used"], "satellites": sats})
            self.send({"class": "SKY", "device": "/dev/ttyACM0", "hdop": 1.68, "pdop": 2.69})   # DOP-only SKY (real u-blox 7 does this)
            tpv = {"class": "TPV", "device": "/dev/ttyACM0", "mode": s["mode"], "time": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}
            if s["mode"] >= 2:
                tpv.update({"lat": s["lat"], "lon": s["lon"], "eph": s["eph"]})
            if s["mode"] == 3:
                tpv.update({"altMSL": s["alt"], "alt": s["alt"]})
            self.send(tpv)
            time.sleep(0.5)

# ---------------------------------------------------------------- unit: tile states
G = ds.GpsWatch()
now = time.time()
base = {"gpsd": True, "devices": 1, "mode": 3, "lat": 36.0544, "lon": -112.1401, "alt": 2100.4, "eph": 3.2, "used": 9, "seen": 14, "fix_ts": now - 1,
        "last": {"lat": 36.0544, "lon": -112.1401, "alt": 2100.4, "eph": 3.2, "ts": now - 1, "mode": 3}, "fix_age": 1.0}
t = ds.gps_tile(dict(base))
check("tile 3D FIX: green, 10-digit grid, '9 SAT · ±3 m · 1 s ago · 2100 m'",
      t == {"level": "ok", "fix": "3D FIX", "grid": "12S UE 97314 90583", "detail": "9 SAT · ±3 m · 1 s ago · 2100 m", "mode": 3}, t)
t = ds.gps_tile(dict(base, mode=2, alt=None, used=4, eph=18.4))
check("tile 2D FIX: amber, '... · no alt'", t["level"] == "warn" and t["fix"] == "2D FIX" and t["detail"].endswith("no alt") and "4 SAT" in t["detail"] and "±18 m" in t["detail"], t)
t = ds.gps_tile(dict(base, mode=1, fix_age=240, last=dict(base["last"], ts=now - 240)))
check("tile NO FIX with a last fix: amber, last grid, 'last fix 4 min ago · STALE'", t["level"] == "warn" and t["fix"] == "NO FIX" and t["grid"] == "12S UE 97314 90583" and t["detail"] == "last fix 4 min ago · STALE", t)
t = ds.gps_tile(dict(base, mode=3, fix_age=30))
check("tile: a fix older than 10 s counts as lost (no frozen 'live' grid)", t["fix"] == "NO FIX" and "STALE" in t["detail"], t)
t = ds.gps_tile(dict(base, mode=1, last=None, fix_ts=None, fix_age=None))
check("tile NO FIX, never had one: dashes, 'searching · 14 sats seen'", t["grid"] == "-- -- ----- -----" and t["detail"] == "searching · 14 sats seen", t)
t = ds.gps_tile(dict(base, devices=0))
check("tile NO GPS (puck unplugged): red, 'puck not detected · check USB'", t["level"] == "bad" and t["fix"] == "NO GPS" and "check USB" in t["detail"], t)
t = ds.gps_tile(dict(base, gpsd=False))
check("tile NO GPS (gpsd not running): red, says so", t["level"] == "bad" and "gpsd not running" in t["detail"], t)
check("grid digits setting: 8-digit", ds.gps_tile(dict(base), 4)["grid"] == "12S UE 9731 9058")

# ---------------------------------------------------------------- fake TAK server (captures the bridge's CoT)
class Ots:
    def __init__(self):
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0)); self.srv.listen(5); self.port = self.srv.getsockname()[1]; self.events = []
        threading.Thread(target=self.accept, daemon=True).start()
    def accept(self):
        while True:
            c, _ = self.srv.accept(); threading.Thread(target=self.read, args=(c,), daemon=True).start()
    def read(self, c):
        buf = b""
        while True:
            d = c.recv(65536)
            if not d: return
            buf += d
            while b"</event>" in buf:
                i = buf.index(b"</event>") + 8; self.events.append(buf[:i]); buf = buf[i:]
    def ident(self):
        return [e for e in self.events if b'platform="TAK MESH TOC"' in e]

def mjoin(group, port):
    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    r.bind((group, port)); r.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(group) + socket.inet_aton("127.0.0.1")); r.settimeout(0.2)
    return r
mesh_sa, mesh_chat = [], []
stop = threading.Event()
def listen(r, sink):
    while not stop.is_set():
        try: sink.append(r.recvfrom(65535)[0])
        except socket.timeout: pass
threading.Thread(target=listen, args=(mjoin("239.2.3.1", 6969), mesh_sa), daemon=True).start()
threading.Thread(target=listen, args=(mjoin("224.10.10.1", 17012), mesh_chat), daemon=True).start()

gpsd = FakeGpsd(); ots = Ots()
env = dict(os.environ, PYTHONWARNINGS="ignore")
st = W + "/st.json"
for f in (st, W + "/hist.jsonl", W + "/ev.jsonl", W + "/dh.jsonl"):
    if os.path.exists(f): os.remove(f)
open(W + "/b.ini", "w").write("[bridge]\nCOT_URL = tcp://127.0.0.1:%d\nPYTAK_MULTICAST_LOCAL_ADDR = 127.0.0.1\nSTATUS_FILE = %s\nRETURN_PATH = 1\n"
                              "LOCAL_CHAT_COPY = udp://127.0.0.1:17013\nLOCAL_SEND = udp://127.0.0.1:17014\nBRIDGE_UID = DECK-testdeck\nDM_RELAY = 1\n"
                              "GPS = gpsd\nGPS_HOST = 127.0.0.1:%d\nGPS_INTERVAL = 5\nBRIDGE_TYPE = a-f-G-U-C-I\n" % (ots.port, st, gpsd.port))
open(W + "/c.ini", "w").write("[chat]\nUID = DECK-testdeck\nMESH_LOCAL_ADDR = 127.0.0.1\nHISTORY_FILE = %s\nBRIDGE_STATUS_FILE = %s\nGPS_HOST = 127.0.0.1:%d\nSA_GPS_INTERVAL = 5\nSA_INTERVAL = 60\nSA_TYPE = a-f-G-U-C-I\n" % (W + "/hist.jsonl", st, gpsd.port))
open(W + "/d.ini", "w").write("[dashboard]\nLISTEN = 127.0.0.1\nPORT = 8786\nBRIDGE_STATUS_FILE = %s\nBRIDGE_INI = %s\nEVENT_FILE = %s\nHISTORY_FILE = %s\nGPS_HOST = 127.0.0.1:%d\nNODE_IP = 127.0.0.9\n" % (st, W + "/b.ini", W + "/ev.jsonl", W + "/dh.jsonl", gpsd.port))
procs = [subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", W + "/b.ini"], stdout=open(W + "/b.log", "w"), stderr=subprocess.STDOUT, env=env),
         subprocess.Popen([sys.executable, DC + "/deck_chat.py", "--config", W + "/c.ini"], stdout=open(W + "/c.log", "w"), stderr=subprocess.STDOUT, env=env),
         subprocess.Popen([sys.executable, DB + "/dashboard_server.py", "--config", W + "/d.ini", "--port", "8786", "--listen", "127.0.0.1"], stdout=open(W + "/d.log", "w"), stderr=subprocess.STDOUT, env=env)]
def status():
    return json.load(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8786/api/status", headers={"Host": "127.0.0.1:8786"}), timeout=3))
def dsa():
    return [x for x in mesh_sa if b'platform="TAK MESH TOC Chat"' in x]
try:
    time.sleep(4)
    # ---- 3D fix
    check("dashboard: GPS tile 3D FIX with the right grid", wait(lambda: (status().get("gps") or {}).get("fix") == "3D FIX"), status().get("gps"))
    g = status()["gps"]
    check("dashboard: tile detail '9 SAT · ±3 m · .. s ago · 2100 m' (DOP-only sky reports don't zero the count)", g["grid"] == "12S UE 97314 90583" and g["detail"].startswith("9 SAT · ±3 m ·") and g["detail"].endswith("2100 m"), g)
    check("bridge: identity to the server carries the GPS position", wait(lambda: any(b'lat="36.0544"' in e and b'lon="-112.1401"' in e for e in ots.ident()), 15), ots.ident()[-1:])
    e = [x for x in ots.ident() if b'lat="36.0544"' in x][-1].decode()
    stale, start = iso_ts(re.search(r'stale="([^"]+)"', e).group(1)), iso_ts(re.search(r'start="([^"]+)"', e).group(1))
    check("bridge: live = not stale (stale 3 x GPS_INTERVAL ahead), how=m-g, altitude and accuracy filled", 10 <= stale - start <= 20 and 'how="m-g"' in e and 'hae="2100.4"' in e and 'ce="3.2"' in e, e)
    check("bridge: map symbol from BRIDGE_TYPE", 'type="a-f-G-U-C-I"' in e)
    check("bridge: still says 'reach me through the server' (so DMs keep working)", 'endpoint="*:-1:stcp"' in e)
    n = len(ots.ident()); time.sleep(11)
    check("bridge: with a fix it re-sends about every GPS_INTERVAL (5 s here)", len(ots.ident()) - n >= 2, len(ots.ident()) - n)
    check("Deck Chat: mesh position report carries the GPS position", wait(lambda: any(b'lat="36.0544"' in x for x in dsa()), 15), dsa()[-1:])
    x = [y for y in dsa() if b'lat="36.0544"' in y][-1].decode()
    stale, start = iso_ts(re.search(r'stale="([^"]+)"', x).group(1)), iso_ts(re.search(r'start="([^"]+)"', x).group(1))
    check("Deck Chat: live report not stale, keeps its mesh DM endpoint", stale > start + 5 and 'endpoint="127.0.0.1:4242:tcp"' in x and 'how="m-g"' in x, x)
    check("Deck Chat: map symbol from SA_TYPE (same as the server side)", 'type="a-f-G-U-C-I"' in x, x)
    req = urllib.request.Request("http://127.0.0.1:8090/api/send", data=json.dumps({"text": "gps stamped"}).encode(),
                                 headers={"Host": "127.0.0.1:8090", "Content-Type": "application/json", "X-Deck": "1"}, method="POST")
    urllib.request.urlopen(req, timeout=5); time.sleep(1.5)
    chatx = [y for y in mesh_chat if b"gps stamped" in y]
    check("Deck Chat: chat messages are stamped with the GPS position", chatx and b'lat="36.0544"' in chatx[0], chatx[:1])
    # ---- move
    gpsd.state.update(lat=36.06, lon=-112.13)
    check("moving: dashboard grid follows", wait(lambda: status()["gps"]["grid"] != "12S UE 97314 90583", 8), status()["gps"])
    check("moving: server map position follows", wait(lambda: any(b'lat="36.06"' in e for e in ots.ident()), 12))
    check("moving: mesh map position follows", wait(lambda: any(b'lat="36.06"' in y for y in dsa()), 12))
    # ---- 2D
    gpsd.state.update(mode=2, used=4, eph=18.0)
    check("2D fix: amber tile, 'no alt'", wait(lambda: status()["gps"]["fix"] == "2D FIX" and status()["gps"]["level"] == "warn" and status()["gps"]["detail"].endswith("no alt"), 8), status()["gps"])
    # ---- lose the fix
    gpsd.state.update(mode=1)
    check("fix lost: tile NO FIX, last grid, 'STALE'", wait(lambda: status()["gps"]["fix"] == "NO FIX" and "STALE" in status()["gps"]["detail"] and status()["gps"]["grid"] == ds.to_mgrs(36.06, -112.13), 20), status()["gps"])
    def last_ident():
        e = ots.ident()[-1].decode(); return e, iso_ts(re.search(r'stale="([^"]+)"', e).group(1)), iso_ts(re.search(r'start="([^"]+)"', e).group(1))
    check("fix lost: bridge sends DECK's last spot ALREADY STALE (greys out, doesn't vanish or jump)",
          wait(lambda: (lambda e, s_, t_: 'lat="36.06"' in e and s_ <= t_ + 0.01)(*last_ident()), 25), ots.ident()[-1:])
    n = len(ots.ident()); time.sleep(12)
    check("fix lost: no more rapid re-sends (back to IDENTIFY_INTERVAL)", len(ots.ident()) - n == 0, len(ots.ident()) - n)
    def last_dsa():
        x = dsa()[-1].decode(); return x, iso_ts(re.search(r'stale="([^"]+)"', x).group(1)), iso_ts(re.search(r'start="([^"]+)"', x).group(1))
    check("fix lost: Deck Chat's mesh report = last spot, already stale", wait(lambda: (lambda x, s_, t_: 'lat="36.06"' in x and s_ <= t_ + 0.01)(*last_dsa()), 25), dsa()[-1:])
    # ---- fix back
    gpsd.state.update(mode=3, used=8)
    check("fix back: tile 3D FIX again", wait(lambda: status()["gps"]["fix"] == "3D FIX", 8))
    check("fix back: live position to the server again", wait(lambda: (lambda e, s_, t_: s_ > t_ + 5)(*last_ident()), 15))
    # ---- unplug
    gpsd.state.update(devices=0, mode=0)
    check("puck unplugged: red NO GPS, 'puck not detected · check USB'", wait(lambda: status()["gps"]["fix"] == "NO GPS" and "check USB" in status()["gps"]["detail"], 8), status()["gps"])
    ev = [e["text"] for e in status()["events"]]
    check("dashboard event log notes fix gained/lost and the puck going away", any("GPS fix lost" in t for t in ev) and any("GPS fix (" in t for t in ev) and any("GPS puck not available" in t for t in ev), ev[:12])
    # ---- gpsd gone
    gpsd.srv.close()
    for c in gpsd.clients: c.close()
    check("gpsd stopped: red NO GPS, 'gpsd not running'", wait(lambda: "gpsd not running" in status()["gps"]["detail"], 12), status()["gps"])
    check("everything else keeps running (bridge, Deck Chat, dashboard alive)", all(p.poll() is None for p in procs))
finally:
    stop.set()
    for p in procs: p.terminate()
    time.sleep(0.5)
logs = "".join(open(W + f).read() for f in ("/b.log", "/c.log", "/d.log"))
check("no tracebacks in bridge / Deck Chat / dashboard logs", "Traceback" not in logs, [l for l in logs.splitlines() if "Error" in l or "Trace" in l][:5])
check("logs say what GPS is doing", "GPS: following gpsd" in open(W + "/b.log").read() and "GPS fix" in open(W + "/b.log").read() and "GPS: following gpsd" in open(W + "/c.log").read())
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
