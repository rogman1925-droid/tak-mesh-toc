"""1.0.7 server-only start and switching (REAL bridge + Deck Chat, fake OTS, a fake mesh cable made with a veth pair and
a network namespace holding a mesh phone). Needs root and `ip netns`; skips otherwise.
  1 deck boots with the node unplugged: bridge stays up and reaches the server; Deck Chat's All Chat goes to the server
    ("SRV"), server chat and DMs work both ways
  2 node plugged in: mesh side comes up by itself; mesh <-> server both ways; All Chat now goes over the mesh and
    reaches the server exactly once
  3 unplugged again -> server-only again;  4 plugged in again -> mesh again
  5 other neighbours missing at boot: gpsd and MediaMTX started late are picked up
Usage: python3 boot_test.py [work dir]"""
import json, os, shutil, socket, struct, subprocess, sys, threading, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); PKG = os.path.dirname(HERE)
W = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mmb_boot_test"
os.makedirs(W, exist_ok=True)
for f in os.listdir(W):
    p = os.path.join(W, f)
    if os.path.isfile(p):
        os.remove(p)
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:500]), flush=True)
def wait(cond, t=20, step=0.2):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False
def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True)
NS, DECK_IF, PH_IF, DECK_IP, PH_IP = "atbns", "atbva", "atbvb", "10.98.0.111", "10.98.0.50"
CAN_NET = os.geteuid() == 0 and shutil.which("ip") and sh("ip", "netns", "add", NS).returncode == 0
T = 'time="2026-10-22T18:00:00Z" start="2026-10-22T18:00:00Z" stale="2099-01-01T00:00:00Z"'

class FakeOTS:
    def __init__(self):
        self.s = socket.socket(); self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(("127.0.0.1", 0)); self.s.listen(5); self.port = self.s.getsockname()[1]
        self.conns, self.events, self.lock = [], [], threading.Lock()
        threading.Thread(target=self._acc, daemon=True).start()
    def _acc(self):
        while True:
            c, _ = self.s.accept(); self.conns.append(c)
            threading.Thread(target=self._rd, args=(c,), daemon=True).start()
    def _rd(self, c):
        buf = b""
        while True:
            try:
                d = c.recv(65536)
            except OSError:
                return
            if not d:
                return
            buf += d
            while b"</event>" in buf:
                ev, _, buf = buf.partition(b"</event>"); ev += b"</event>"
                if b'type="t-x-c-t"' in ev:
                    try:
                        c.sendall(ev)
                    except OSError:
                        pass
                    continue
                with self.lock:
                    self.events.append((time.time(), ev))
    def push(self, data):
        for c in list(self.conns):
            try:
                c.sendall(data)
            except OSError:
                pass
    def count(self, needle):
        with self.lock:
            return sum(1 for _, e in self.events if needle in e)

def sa(uid, cs, ep, n=0):
    return ('<event version="2.0" uid="%s" type="a-f-G-U-C" how="m-g" %s><point lat="38" lon="-104" hae="0" ce="9" le="9"/>'
            '<detail><contact callsign="%s" endpoint="%s"/><remarks>n=%d</remarks></detail></event>' % (uid, T, cs, ep, n)).encode()
def chat(uid, cs, text):
    return ('<event version="2.0" uid="GeoChat.%s.All Chat Rooms.%s" type="b-t-f" how="h-g-i-g-o" %s><point lat="0" lon="0" hae="0" ce="9" le="9"/>'
            '<detail><__chat parent="RootContactGroup" groupOwner="false" messageId="%s" chatroom="All Chat Rooms" id="All Chat Rooms" senderCallsign="%s">'
            '<chatgrp uid0="%s" uid1="All Chat Rooms" id="All Chat Rooms"/></__chat><link uid="%s" type="a-f-G-U-C" relation="p-p"/>'
            '<remarks source="BAO.F.ATAK.%s" to="All Chat Rooms" time="2026-10-22T18:00:00Z">%s</remarks></detail></event>'
            % (uid, text, T, text, cs, uid, uid, uid, text)).encode()

if not CAN_NET:
    print("SKIP  sections 1-4 (needs root and ip netns)")
else:
    try:
        sh("ip", "link", "add", DECK_IF, "type", "veth", "peer", "name", PH_IF)
        sh("ip", "link", "set", PH_IF, "netns", NS)
        sh("ip", "netns", "exec", NS, "sh", "-c", "ip addr add %s/24 dev %s && ip link set %s up && ip link set lo up && ip route add 224.0.0.0/4 dev %s" % (PH_IP, PH_IF, PH_IF, PH_IF))
        sh("ip", "link", "set", DECK_IF, "up")           # cable in the deck, but the node is "off": no address yet
        srv = FakeOTS()
        CP = 18911
        ini = W + "/deck.ini"; st = W + "/status.json"
        open(ini, "w").write(f"""[deck]
CALLSIGN = DECK
UID = DECK-boot
MESH_ADDR = {DECK_IP}
GPS = off
[bridge]
COT_URL = tcp://127.0.0.1:{srv.port}
STATUS_FILE = {st}
RETURN_PATH = 1
DM_RELAY = 1
PING_INTERVAL = 5
UP_MIN_INTERVAL = 0
LOCAL_CHAT_COPY = udp://127.0.0.1:17013
LOCAL_SEND = udp://127.0.0.1:17014
[chat]
LISTEN = 127.0.0.1:{CP}
HISTORY_FILE = {W}/h.jsonl
BRIDGE_STATUS_FILE = {st}
DM_PORT = 4242
SA_SEND = 1
SA_INTERVAL = 5
MAX_SENDS_PER_10S = 50
""")
        env = dict(os.environ, PYTHONWARNINGS="ignore")
        bp = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", ini], stdout=open(W + "/b.log", "w"), stderr=subprocess.STDOUT, env=env)
        cp = subprocess.Popen([sys.executable, PKG + "/chat/deck_chat.py", "--config", ini], stdout=open(W + "/c.log", "w"), stderr=subprocess.STDOUT, env=env)
        # the mesh phone: reports every second, listens for what the deck puts on the mesh
        heard = W + "/phone.txt"; open(heard, "w").close()
        phone_src = r'''
import socket, struct, sys, threading, time
out = open(sys.argv[1], "a"); ip = sys.argv[2]
def listen(port, grp):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port)); s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("4s4s", socket.inet_aton(grp), socket.inet_aton(ip)))
    while True:
        d, a = s.recvfrom(70000)
        if a[0] != ip:
            out.write("%f %s\n" % (time.time(), d.decode(errors="replace").replace("\n", " "))); out.flush()
threading.Thread(target=listen, args=(6969, "239.2.3.1"), daemon=True).start()
threading.Thread(target=listen, args=(17012, "224.10.10.1"), daemon=True).start()
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
T = 'time="2026-10-22T18:00:00Z" start="2026-10-22T18:00:00Z" stale="2099-01-01T00:00:00Z"'
n = 0
while True:
    n += 1
    try:
        tx.sendto(('<event version="2.0" uid="ANDROID-ph" type="a-f-G-U-C" how="m-g" %s><point lat="38" lon="-104" hae="0" ce="9" le="9"/><detail><contact callsign="PHONE" endpoint="%s:4242:tcp"/><remarks>n=%d</remarks></detail></event>' % (T, ip, n)).encode(), ("239.2.3.1", 6969))
    except OSError:
        pass
    time.sleep(1)
'''
        open(W + "/phone.py", "w").write(phone_src)
        ph = subprocess.Popen(["ip", "netns", "exec", NS, sys.executable, W + "/phone.py", heard, PH_IP])
        def status():
            try:
                return json.load(open(st))
            except (OSError, ValueError):
                return {}
        def api(path, body=None):
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (CP, path), data=json.dumps(body).encode() if body else None,
                                         headers={"X-Deck": "1", "Content-Type": "application/json", "Host": "127.0.0.1:%d" % CP})
            try:
                with urllib.request.urlopen(req, timeout=4) as r:
                    return json.loads(r.read())
            except Exception as e:
                return {"error": str(e)}
        def msgs():
            return (api("/api/messages?after=0") or {}).get("messages") or []
        def phone_heard(needle, since):
            return any(float(l.split()[0]) > since and needle in l for l in open(heard) if l.strip())
        def phone_dm(text, port=4242):
            src = ("import socket;s=socket.create_connection(('%s',%d),timeout=3);s.sendall(b'<event version=\"2.0\" uid=\"GeoChat.ANDROID-ph.DECK.%s\" type=\"b-t-f\" how=\"h-g-i-g-o\" "
                   "time=\"2026-10-22T18:00:00Z\" start=\"2026-10-22T18:00:00Z\" stale=\"2099-01-01T00:00:00Z\"><point lat=\"0\" lon=\"0\" hae=\"0\" ce=\"9\" le=\"9\"/><detail>"
                   "<__chat parent=\"RootContactGroup\" groupOwner=\"false\" messageId=\"%s\" chatroom=\"DECK\" id=\"DECK-boot\" senderCallsign=\"PHONE\"><chatgrp uid0=\"ANDROID-ph\" "
                   "uid1=\"DECK-boot\" id=\"DECK-boot\"/></__chat><remarks>%s</remarks></detail></event>');s.close();print('ok')" % (DECK_IP, port, text, text, text))
            return sh("ip", "netns", "exec", NS, sys.executable, "-c", src).stdout.strip() == "ok"

        def server_only_checks(tag):
            t0 = time.time()
            check("%s: bridge running and connected to the server" % tag, bp.poll() is None and wait(lambda: srv.count(b"DECK-boot") > 0, 20), open(W + "/b.log").read()[-600:])
            check("%s: bridge status says it is waiting for the mesh" % tag, wait(lambda: (status().get("mesh") or {}).get("ready") is False and (status().get("mesh") or {}).get("waiting_since", 0) > 0, 15), status().get("mesh"))
            srv.push(sa("ANDROID-foxtrot", "FOXTROT", "*:-1:stcp"))
            wait(lambda: any(r.get("callsign") == "FOXTROT" for r in (api("/api/info").get("roster") or [])), 10)
            r = api("/api/send", {"text": "allchat-" + tag})
            check("%s: All Chat from Deck Chat is sent (not held)" % tag, r.get("ok") and r.get("status") == "sent", r)
            check("%s: ...and reaches the server once" % tag, wait(lambda: srv.count(("allchat-" + tag).encode()) == 1, 8) and (time.sleep(1.5) or srv.count(("allchat-" + tag).encode()) == 1), srv.count(("allchat-" + tag).encode()))
            m = [x for x in msgs() if x.get("text") == "allchat-" + tag]
            check("%s: ...marked as sent to the server only" % tag, m and m[-1].get("path") == "server-all", m[-1:] if m else None)
            srv.push(chat("ANDROID-foxtrot", "FOXTROT", "fromserver-" + tag))
            check("%s: a server chat shows in Deck Chat" % tag, wait(lambda: any(x.get("text") == "fromserver-" + tag for x in msgs()), 8))
            r = api("/api/send", {"text": "dm-" + tag, "to": "ANDROID-foxtrot"})
            check("%s: a DM to a server person goes to the server" % tag, r.get("ok") and wait(lambda: srv.count(("dm-" + tag).encode()) == 1, 8), r)

        def mesh_checks(tag):
            t0 = time.time()
            check("%s: the mesh side comes up by itself (within 15 s)" % tag, wait(lambda: (status().get("mesh") or {}).get("ready") is True, 15), open(W + "/b.log").read()[-500:])
            check("%s: phone's position reaches the server" % tag, wait(lambda: srv.count(b"ANDROID-ph") > 0 and any(t > t0 for t, e in srv.events if b"ANDROID-ph" in e), 15))
            srv.push(sa("ANDROID-foxtrot", "FOXTROT", "*:-1:stcp", 7))
            check("%s: a server person's position reaches the phone" % tag, wait(lambda: phone_heard("ANDROID-foxtrot", t0), 10))
            check("%s: the phone hears the deck's own position report" % tag, wait(lambda: phone_heard("DECK-boot", t0), 15))
            wait(lambda: api("/api/info").get("dm", {}).get("listening") is True or True, 1)
            r = api("/api/send", {"text": "allchat-" + tag})
            check("%s: All Chat goes over the mesh: the phone gets it" % tag, r.get("ok") and wait(lambda: phone_heard("allchat-" + tag, t0), 8), r)
            time.sleep(3)
            check("%s: ...and the server gets it exactly once (no duplicate)" % tag, srv.count(("allchat-" + tag).encode()) == 1, srv.count(("allchat-" + tag).encode()))
            ok = wait(lambda: phone_dm("phonedm-" + tag), 20, 2)
            check("%s: the phone can DM the deck" % tag, ok and wait(lambda: any(x.get("text") == "phonedm-" + tag for x in msgs()), 8))
            check("%s: the DM relay port (4243) is open on the mesh" % tag, wait(lambda: sh("ip", "netns", "exec", NS, sys.executable, "-c", "import socket;socket.create_connection(('%s',4243),timeout=2);print('ok')" % DECK_IP).stdout.strip() == "ok", 15, 1))

        time.sleep(6)
        print("== 1. boot with the node unplugged", flush=True)
        server_only_checks("1 unplugged at boot")
        print("== 2. node plugged in", flush=True)
        sh("ip", "addr", "add", DECK_IP + "/24", "dev", DECK_IF)
        mesh_checks("2 plugged in")
        print("== 3. unplugged again", flush=True)
        sh("ip", "addr", "flush", "dev", DECK_IF)
        time.sleep(6)
        r = api("/api/send", {"text": "allchat-3"})
        check("3 unplugged again: All Chat goes to the server only", r.get("ok") and r.get("status") == "sent" and wait(lambda: srv.count(b"allchat-3") == 1, 8), r)
        check("3 ...bridge still running", bp.poll() is None)
        print("== 4. plugged in again", flush=True)
        sh("ip", "addr", "add", DECK_IP + "/24", "dev", DECK_IF)
        t0 = time.time()
        check("4 plugged in again: phone's position reaches the server", wait(lambda: any(t > t0 + 1 for t, e in srv.events if b"ANDROID-ph" in e), 15))
        r = api("/api/send", {"text": "allchat-4"})
        check("4 ...and All Chat goes over the mesh again", r.get("ok") and wait(lambda: phone_heard("allchat-4", t0), 10), r)
        for name, p, f in (("bridge", bp, "b.log"), ("Deck Chat", cp, "c.log")):
            t = open(W + "/" + f).read()
            check("%s: never crashed, no tracebacks" % name, p.poll() is None and "Traceback" not in t, t[t.find("Traceback"):][:400] if "Traceback" in t else "exited")
        ph.terminate(); cp.terminate(); bp.terminate()
    finally:
        sh("ip", "link", "del", DECK_IF)
        sh("ip", "netns", "del", NS)

# ================================================================ 5. other neighbours late at boot
print("== 5. gpsd and MediaMTX started after the deck", flush=True)
gport, mport, dport = 18921, 18922, 18923
ini = W + "/late.ini"; open(ini, "w").write(f"""[deck]
CALLSIGN = DECK
MESH_ADDR = 127.0.0.1
GPS = gpsd
GPS_HOST = 127.0.0.1:{gport}
[bridge]
STATUS_FILE = {W}/late_status.json
[dashboard]
PORT = {dport}
LISTEN = 127.0.0.1
BRIDGE_INI = {ini}
BRIDGE_STATUS_FILE = {W}/late_status.json
EVENT_FILE = {W}/ev.jsonl
HISTORY_FILE = {W}/dh.jsonl
MEDIAMTX_API = http://127.0.0.1:{mport}
""")
dp = subprocess.Popen([sys.executable, PKG + "/dashboard/dashboard_server.py", "--config", ini], stdout=open(W + "/d.log", "w"), stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONWARNINGS="ignore"))
def dstat():
    try:
        return json.load(urllib.request.urlopen("http://127.0.0.1:%d/api/status" % dport, timeout=3))
    except Exception:
        return {}
wait(lambda: dstat() != {}, 15)
check("5 dashboard starts with no gpsd and no MediaMTX", dstat() != {})
time.sleep(3)
gs = socket.socket(); gs.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); gs.bind(("127.0.0.1", gport)); gs.listen(5)
def gpsd():
    while True:
        c, _ = gs.accept()
        def h(c=c):
            try:
                while True:
                    c.sendall(b'{"class":"TPV","mode":3,"lat":38.1,"lon":-104.1}\n'); time.sleep(0.5)
            except OSError:
                pass
        threading.Thread(target=h, daemon=True).start()
threading.Thread(target=gpsd, daemon=True).start()
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class MTX(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"items": [{"name": "cam", "ready": True, "inboundBytes": 5, "tracks": ["H264"]}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
m = ThreadingHTTPServer(("127.0.0.1", mport), MTX); threading.Thread(target=m.serve_forever, daemon=True).start()
check("5 gpsd started late: the dashboard picks up the fix", wait(lambda: "3D" in json.dumps(dstat().get("gps") or {}), 20), dstat().get("gps"))
check("5 MediaMTX started late: the dashboard lists the stream", wait(lambda: len((dstat().get("video") or {}).get("paths") or []) == 1, 20), dstat().get("video"))
dp.terminate()
print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
