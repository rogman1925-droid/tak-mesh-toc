"""Fuzz TAK MESH TOC with malformed CoT.
Part A (unit): every parser gets N random/malformed inputs; any exception that escapes is recorded.
Part B (live): the REAL bridge + Deck Chat + dashboard run together; flood every listening socket with garbage,
then check each process is alive, memory stayed sane, and a valid chat/SA/DM still gets through.
Usage: python3 fuzz_test.py [package dir] [work dir] [unit_n] [live_n]"""
import json, math, os, random, socket, struct, subprocess, sys, threading, time, traceback, urllib.request
PKG = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = sys.argv[2] if len(sys.argv) > 2 else "/tmp/mmb_fuzz_test"
UNIT_N = int(sys.argv[3]) if len(sys.argv) > 3 else 20000
LIVE_N = int(sys.argv[4]) if len(sys.argv) > 4 else 20000
os.makedirs(W, exist_ok=True)
sys.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
import warnings; warnings.filterwarnings("ignore")
import tak_mesh_bridge as B
import deck_chat as C
import deckcommon as D
import takproto
from takproto.proto import TakMessage

rnd = random.Random(1337)
results, findings = [], []
def check(n, c, x=""):
    results.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:600]), flush=True)

# ------------------------------------------------------------------ generators of nasty input
VALID_SA = (b'<event version="2.0" uid="ANDROID-x1" type="a-f-G-U-C" how="m-g" time="2026-10-22T15:00:00Z" '
            b'start="2026-10-22T15:00:00Z" stale="2099-01-01T00:00:00Z"><point lat="38.1" lon="-104.2" hae="1" ce="9" le="9"/>'
            b'<detail><contact callsign="FUZZ-1" endpoint="10.41.0.50:4242:tcp"/><__group name="Cyan" role="HQ"/></detail></event>')
VALID_CHAT = C.build_geochat("FUZZ-2", "ANDROID-x2", "radio check")[1]
VALID_DM = C.build_geochat("FUZZ-3", "ANDROID-x3", "dm text", to_uid="ANDROID-srv", to_callsign="SRV-1")[1]
SEEDS = [VALID_SA, VALID_CHAT, VALID_DM]
NASTY = [b"", b"<", b"<event", b"</event>", b"<event></event>", b"\x00" * 64, b"\xff\xfe" * 50, b"<event " * 2000,
         b'<?xml version="1.0"?><!DOCTYPE e [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
         b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;"><!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]>'
         b'<event type="b-t-f" uid="x"><detail><remarks>&d;&d;&d;&d;&d;</remarks></detail></event>',
         b'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><event type="b-t-f" uid="x"><detail><remarks>&xxe;</remarks></detail></event>',
         b'<event type="b-t-f" uid="' + b"A" * 70000 + b'"><detail><remarks>x</remarks></detail></event>',
         b'<event type="a-f-G" uid="u"><point lat="NaN" lon="inf"/><detail><contact callsign="" endpoint="999.999.1.1:99999999999:tcp"/></detail></event>',
         b'<event type="a-f-G" uid="u"><detail><contact callsign="x" endpoint="10.0.0.1:-5:tcp"/></detail></event>',
         b'<event type="b-t-f"><detail><__chat chatroom="All Chat Rooms"/><remarks>' + b"<b>" * 5000 + b'</remarks></detail></event>',
         b"\xbf", b"\xbf\x01", b"\xbf\x01\xbf", b"\xbf\x01\xbf" + b"\xff" * 200, b"\xbf\x02\xbf\x00", b"\xbf" * 1000,
         "<event type=\"b-t-f\" uid=\"‮\u0000퟿\"><detail><remarks>\U0001F4A5</remarks></detail></event>".encode("utf-8", "surrogatepass")]

def proto_seed():
    m = TakMessage()
    if rnd.random() < 0.5:
        m.takControl.minProtoVersion = rnd.randint(0, 5); m.takControl.maxProtoVersion = rnd.randint(0, 5)
    else:
        m = takproto.xml2message(VALID_SA.decode()) if hasattr(takproto, "xml2message") else m
    return b"\xbf\x01\xbf" + m.SerializeToString()

def mutate(b):
    b = bytearray(b)
    for _ in range(rnd.randint(1, 12)):
        op = rnd.random()
        if not b:
            b = bytearray(rnd.randbytes(rnd.randint(1, 64)))
        elif op < 0.25:
            b[rnd.randrange(len(b))] = rnd.randrange(256)                  # flip a byte
        elif op < 0.40:
            i = rnd.randrange(len(b)); del b[i:i + rnd.randint(1, 40)]      # delete a chunk
        elif op < 0.55:
            i = rnd.randrange(len(b)); b[i:i] = rnd.randbytes(rnd.randint(1, 40))   # insert junk
        elif op < 0.65:
            i = rnd.randrange(len(b)); b[i:i] = rnd.choice([b'"', b"<", b">", b"&", b"&amp;", b"]]>", b"\x00", b"</event>", b"<event", b'endpoint="', b"\xbf\x01\xbf"])
        elif op < 0.72:
            b = b[:rnd.randrange(len(b) + 1)]                               # truncate
        elif op < 0.80:
            i = rnd.randrange(len(b)); b[i:i] = b[i:i + rnd.randint(1, 200)] * rnd.randint(2, 50)   # repeat a slice
        else:
            pass
    return bytes(b)

def gen():
    r = rnd.random()
    if r < 0.15:
        return rnd.choice(NASTY)
    if r < 0.25:
        return rnd.randbytes(rnd.randint(0, 3000))
    if r < 0.35:
        return mutate(proto_seed())
    return mutate(rnd.choice(SEEDS))

# ------------------------------------------------------------------ Part A: unit fuzz
ep = b"10.41.0.60:4243:tcp"
targets = {
    "chat.parse_chat": lambda d: C.parse_chat(d),
    "chat.parse_sa": lambda d: C.parse_sa(d),
    "chat.to_xml": lambda d: C.to_xml(d),
    "bridge.proto_kind (as called: inside try)": None,          # the bridge wraps these in try/except; checked for hangs only
    "bridge.point_contact_at": lambda d: B.point_contact_at(d, ep),
    "bridge.add_dest": lambda d: B.add_dest(d, "SRV-1"),
    "bridge.Peers.saw_mesh": lambda d: B.Peers.saw_mesh(d),
    "bridge.Peers.saw_server": lambda d: B.Peers.saw_server(d),
    "bridge.ServerToMesh._dm_targets": lambda d: B.ServerToMesh._dm_targets(d),
    "bridge.classify(type)": lambda d: B.classify((B.TYPE_RE.search(d).group(1) if B.TYPE_RE.search(d) else b"")),
}
print("== Part A: %d inputs into each parser" % UNIT_N, flush=True)
slowest = (0, None)
escaped = {}
for name, fn in targets.items():
    if fn is None:
        fn = lambda d: (B.proto_kind(d), B.proto_to_xml(d)) if d[:1] == b"\xbf" else None
        guarded = True
    else:
        guarded = False
    errs = {}
    for i in range(UNIT_N):
        d = gen()
        t0 = time.perf_counter()
        try:
            fn(d)
        except Exception as exc:
            if not guarded:
                k = type(exc).__name__ + ": " + str(exc)[:80]
                errs.setdefault(k, d[:120])
        dt = time.perf_counter() - t0
        if dt > slowest[0]:
            slowest = (dt, name)
    escaped[name] = errs
    check("unit: %s never raises on %d malformed inputs" % (name, UNIT_N), not errs, list(errs.items())[:3])
check("unit: no single input took over 0.5 s (no regex blow-ups / XML bombs)", slowest[0] < 0.5, slowest)
for x in (D.to_mgrs, ):
    bad = 0
    for _ in range(UNIT_N):
        lat = rnd.choice([rnd.uniform(-100, 100), float("nan"), float("inf"), -float("inf"), 1e308, None])
        lon = rnd.choice([rnd.uniform(-200, 200), float("nan"), float("inf"), None])
        try:
            to = x(lat, lon, rnd.randint(1, 5))
        except Exception:
            bad += 1
    check("unit: to_mgrs survives NaN/inf/out-of-range coordinates", bad == 0, "%d raised" % bad)
# GpsWatch._msg with garbage gpsd JSON
g = D.GpsWatch(); bad = []
for _ in range(UNIT_N // 4):
    m = rnd.choice([{"class": "TPV", "mode": rnd.choice([0, 1, 2, 3, "3", None, -1, 99]), "lat": rnd.choice([1.0, None, "x", float("nan")]), "lon": rnd.choice([2.0, None, []])},
                    {"class": "SKY", "satellites": rnd.choice([None, [], [{}], [{"used": True}], "junk"]), "uSat": rnd.choice([3, None, "x"])},
                    {"class": "DEVICES", "devices": rnd.choice([None, [], [{}], "x"])}, {"class": rnd.randbytes(3).hex()}, {}])
    try:
        g._msg(m)
    except Exception as exc:
        bad.append(type(exc).__name__ + ": " + str(exc)[:60] + " <- " + json.dumps(m, default=str)[:100])
uniq = sorted(set(bad))
check("unit: GPS reader skips every garbage gpsd message without raising (%d messages)" % (UNIT_N // 4), not uniq, uniq[:4])
st = g.snapshot()
check("unit: ...and never stores a non-number or out-of-range position",
      all(st[k] is None or (isinstance(st[k], float) and math.isfinite(st[k])) for k in ("lat", "lon")) and isinstance(st["mode"], int), st)
# live: a fake gpsd sends good fixes with garbage in between; the reader must keep following it
gs = socket.socket(); gs.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); gs.bind(("127.0.0.1", 0)); gs.listen(5)
gconns = []
threading.Thread(target=lambda: [gconns.append(gs.accept()[0]) for _ in iter(int, 1)], daemon=True).start()
gw = D.GpsWatch("127.0.0.1", gs.getsockname()[1]); gw.start()
end = time.time() + 5
while not gconns and time.time() < end:
    time.sleep(0.1)
JUNK = [b'{"class":"TPV","mode":3,"lat":38.2,"lon":[]}', b"[]", b'"x"', b"42", b'{"class":"TPV","mode":[],"lat":1,"lon":2}',
        b'{"class":"TPV","mode":3,"lat":"NaN","lon":"inf"}', b'{"class":"TPV","mode":3,"lat":95,"lon":-104}',
        b'{"class":"SKY","satellites":[1,2,"x",null]}', b'{"class":"SKY","uSat":"lots"}', b'{"class":"DEVICES","devices":"x"}',
        b'{"class":"TPV","mode":3,"lat":38,"lon":-104,"eph":"x","epx":[]}', b'{"class":"TPV","mode":3,"lat":true,"lon":false}', b"\xff\xfe{"]
okfix = True
for k, junk in enumerate(JUNK):
    gconns[-1].sendall(junk + b"\n" + json.dumps({"class": "TPV", "mode": 3, "lat": 38.0 + k / 100, "lon": -104.0}).encode() + b"\n")
    time.sleep(0.15)
    live, pos = gw.position()
    okfix = okfix and live and pos and abs(pos["lat"] - (38.0 + k / 100)) < 1e-9
check("live: GPS reader keeps following gpsd through %d kinds of garbage (thread alive, newest fix each time)" % len(JUNK),
      gw.is_alive() and okfix and len(gconns) == 1, (gw.is_alive(), okfix, len(gconns), gw.snapshot()))

# ------------------------------------------------------------------ Part B: live flood
print("== Part B: live flood (%d packets per target)" % LIVE_N, flush=True)
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 0)); srv.listen(5)
sport = srv.getsockname()[1]; got = []; got_lock = threading.Lock()
def serve():
    while True:
        try:
            c, _ = srv.accept()
        except OSError:
            return
        def rd(c=c):
            while True:
                try:
                    d = c.recv(65536)
                except OSError:
                    return
                if not d:
                    return
                with got_lock:
                    got.append(d)
                    if len(got) > 5000:
                        del got[:2500]
        threading.Thread(target=rd, daemon=True).start()
threading.Thread(target=serve, daemon=True).start()

status = W + "/status.json"
for f in (status, W + "/hist.jsonl", W + "/ev.jsonl", W + "/dh.jsonl"):
    if os.path.exists(f):
        os.remove(f)
ini = W + "/deck.ini"
open(ini, "w").write(f"""[deck]
CALLSIGN = DECK
UID = DECK-fuzz
MESH_ADDR = 127.0.0.1
GPS = off
[bridge]
COT_URL = tcp://127.0.0.1:{sport}
STATUS_FILE = {status}
RETURN_PATH = 1
LOCAL_CHAT_COPY = udp://127.0.0.1:17013
LOCAL_SEND = udp://127.0.0.1:17014
DM_RELAY = 1
[chat]
LISTEN = 127.0.0.1:8791
HISTORY_FILE = {W}/hist.jsonl
BRIDGE_STATUS_FILE = {status}
DM_PORT = 4242
MAX_SENDS_PER_10S = 6
[dashboard]
PORT = 8792
LISTEN = 127.0.0.1
BRIDGE_INI = {ini}
BRIDGE_STATUS_FILE = {status}
EVENT_FILE = {W}/ev.jsonl
HISTORY_FILE = {W}/dh.jsonl
""")
env = dict(os.environ, PYTHONWARNINGS="ignore")
procs = {"bridge": subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", ini], stdout=open(W + "/b.log", "w"), stderr=subprocess.STDOUT, env=env),
         "chat": subprocess.Popen([sys.executable, PKG + "/chat/deck_chat.py", "--config", ini], stdout=open(W + "/c.log", "w"), stderr=subprocess.STDOUT, env=env),
         "dashboard": subprocess.Popen([sys.executable, PKG + "/dashboard/dashboard_server.py", "--config", ini], stdout=open(W + "/d.log", "w"), stderr=subprocess.STDOUT, env=env)}

def rss_mb(p):
    try:
        for ln in open("/proc/%d/status" % p.pid):
            if ln.startswith("VmRSS:"):
                return int(ln.split()[1]) / 1024
    except OSError:
        return None
def wait(cond, t=20):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.25)
    return False
def http(url, data=None, headers=None, timeout=3):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return None, str(e).encode()

try:
    wait(lambda: os.path.exists(status), 25)
    wait(lambda: http("http://127.0.0.1:8791/healthz")[0] == 200, 20)
    wait(lambda: http("http://127.0.0.1:8792/healthz")[0] == 200, 20)
    time.sleep(2)
    base = {k: rss_mb(p) for k, p in procs.items()}
    print("   memory before (MB):", {k: round(v or 0, 1) for k, v in base.items()}, flush=True)

    mc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mc.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
    mc.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    lo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    def tcp_blast(port, n):
        sent = 0
        for _ in range(n):
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=2)
                for _ in range(rnd.randint(1, 4)):
                    s.sendall(gen())
                s.close(); sent += 1
            except OSError:
                pass
        return sent
    t0 = time.time()
    for i in range(LIVE_N):
        d = gen()[:65000]
        try:
            mc.sendto(d, ("239.2.3.1", 6969))
            mc.sendto(gen()[:65000], ("224.10.10.1", 17012))
            lo.sendto(gen()[:65000], ("127.0.0.1", 17014))
            lo.sendto(gen()[:65000], ("127.0.0.1", 17013))
        except OSError:
            pass
        if i % 2000 == 0:
            time.sleep(0.05)
    udp_s = time.time() - t0
    n4242 = tcp_blast(4242, LIVE_N // 20); n4243 = tcp_blast(4243, LIVE_N // 20)
    # slow-loris style: many idle connections held open
    holds = []
    for port in (4242, 4243):
        for _ in range(200):
            try:
                holds.append(socket.create_connection(("127.0.0.1", port), timeout=2))
            except OSError:
                break
    held = len(holds)
    # unique-callsign flood (memory growth check)
    for i in range(LIVE_N):
        sa = VALID_SA.replace(b"FUZZ-1", b"U%07d" % i).replace(b"ANDROID-x1", b"ANDROID-u%07d" % i)
        try:
            mc.sendto(sa, ("239.2.3.1", 6969))
        except OSError:
            pass
        if i % 2000 == 0:
            time.sleep(0.05)
    # HTTP fuzz
    hbad = 0
    for i in range(LIVE_N // 20):
        port = rnd.choice([8791, 8792])
        path = "/" + rnd.choice(["", "api/status", "api/messages?after=" + rnd.choice(["-1", "x", "9" * 40]), "api/send", "api/outbox",
                                 "captures/../../etc/passwd", "captures/%2e%2e%2fdeck.ini", "api/snapshot", "api/restart", "..%2f" * 20, "x" * 3000])
        data = rnd.choice([None, b"{", b"[]", json.dumps({"text": "x" * 20000}).encode(), rnd.randbytes(500)])
        hdr = rnd.choice([{}, {"Host": "evil.example"}, {"X-Deck": "1"}, {"X-Deck": "1", "Content-Type": "application/json"}, {"Origin": "http://evil.example"}])
        code, body = http("http://127.0.0.1:%d%s" % (port, path), data, hdr, timeout=3)
        if code is None:
            hbad += 1
        if b"root:" in body or b"COT_URL" in body:
            findings.append("HTTP leak: %s -> %s" % (path, body[:80]))
    time.sleep(3)
    for s in holds:
        s.close()
    time.sleep(2)

    alive = {k: p.poll() is None for k, p in procs.items()}
    check("live: bridge still running after the flood", alive["bridge"], open(W + "/b.log").read()[-800:])
    check("live: Deck Chat still running after the flood", alive["chat"], open(W + "/c.log").read()[-800:])
    check("live: dashboard still running after the flood", alive["dashboard"], open(W + "/d.log").read()[-800:])
    after = {k: rss_mb(p) for k, p in procs.items()}
    print("   memory after  (MB):", {k: round(v or 0, 1) for k, v in after.items()}, flush=True)
    for k in after:
        if base[k] and after[k]:
            growth = after[k] - base[k]
            check("live: %s memory growth under the flood is bounded (%.1f MB)" % (k, growth), growth < 60, growth)
    check("live: Deck Chat page answers", http("http://127.0.0.1:8791/healthz")[0] == 200)
    check("live: dashboard answers", http("http://127.0.0.1:8792/healthz")[0] == 200)
    check("live: HTTP fuzz never hung a server (%d requests)" % (LIVE_N // 20), hbad == 0, hbad)
    check("live: no file contents leaked through HTTP paths", not any(f.startswith("HTTP leak") for f in findings), findings)
    # still functional?
    with got_lock:
        got.clear()
    ok_sa = VALID_SA.replace(b"FUZZ-1", b"AFTER-1").replace(b"ANDROID-x1", b"ANDROID-after1")
    mc.sendto(ok_sa, ("239.2.3.1", 6969))
    check("live: a valid position report still reaches the server", wait(lambda: any(b"ANDROID-after1" in g for g in list(got)), 10))
    ok_chat = C.build_geochat("AFTER-2", "ANDROID-after2", "still alive?")[1]
    mc.sendto(ok_chat, ("224.10.10.1", 17012))
    def chat_has():
        c, b = http("http://127.0.0.1:8791/api/messages?after=0")
        return c == 200 and b"still alive?" in b
    check("live: a valid chat still reaches Deck Chat and the server", wait(chat_has, 10) and wait(lambda: any(b"still alive?" in g for g in list(got)), 10))
    st = json.load(open(status))
    print("   bridge status counters:", json.dumps(st.get("mesh"))[:300], flush=True)
    print("   UDP flood took %.1f s; TCP connections: 4242=%d 4243=%d; idle connections held=%d" % (udp_s, n4242, n4243, held), flush=True)
    for name in ("bridge", "chat", "dashboard"):
        log = open(W + "/%s.log" % name[0]).read()
        tb = log.count("Traceback")
        print("   %s log: %d tracebacks, %d lines" % (name, tb, log.count("\n")), flush=True)
        if tb:
            i = log.index("Traceback")
            findings.append("%s logged tracebacks: %s" % (name, log[i:i + 500].replace("\n", " | ")))
finally:
    for p in procs.values():
        p.terminate()
    for p in procs.values():
        try:
            p.wait(5)
        except Exception:
            p.kill()

print("\nFINDINGS:")
for f in findings or ["none"]:
    print(" - " + f)
print("\n%d/%d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
