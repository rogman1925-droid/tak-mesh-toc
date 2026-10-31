"""Repair tests:
  R  packets OTS would accept but 1.0.6/1.0.7 dropped: zero padding, a trailing newline or junk, two or three events in
     one packet, a good event followed by a cut-off one. The REAL bridge forwards every good event (each once) into
     OpenTAKServer's own stream parser (tests/ots_intake_sim.py), drops only the broken pieces, and a clean packet
     still goes up byte for byte
  V  PyTAK version guard: 7.6.x starts; 7.5 / 8.0 / unknown stop with a clear fix; PYTAK_ANY_VERSION = 1 overrides
Usage: python3 repair_test.py [work dir]"""
import importlib, json, os, socket, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); PKG = os.path.dirname(HERE)
W = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mmb_repair_test"
os.makedirs(W, exist_ok=True)
ENV = dict(os.environ, PYTHONWARNINGS="ignore")
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:400]), flush=True)
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

sys.path[:0] = [PKG + "/vendor", PKG + "/bridge", PKG + "/common"]
import warnings; warnings.simplefilter("ignore")
B = importlib.import_module("tak_mesh_bridge")
TS = 'time="2026-10-22T18:00:00Z" start="2026-10-22T18:00:00Z" stale="2099-01-01T00:00:00Z"'
def ev(uid):
    return ('<event version="2.0" uid="%s" type="a-f-G-U-C" how="m-g" %s><point lat="38" lon="-104" hae="0" ce="9" le="9"/>'
            '<detail><contact callsign="%s"/></detail></event>' % (uid, TS, uid)).encode()

# ============================================================ unit: split_events
print("== split_events", flush=True)
g = ev("A")
CASES = [  # name, packet, expected good events, malformed?
    ("clean packet unchanged", g, [g], False),
    ("zero padding", g + b"\x00" * 40, [g], False),
    ("trailing newline (valid XML already: unchanged)", g + b"\r\n", [g + b"\r\n"], False),
    ("junk after", g + b"garbage!!", [g], False),
    ("two events", g + ev("B"), [g, ev("B")], False),
    ("three events, newlines, xml header", b'<?xml version="1.0"?>' + g + b"\n" + ev("B") + b"\n" + ev("C"), None, False),
    ("good then cut off", g + ev("B")[:-40], [g], False),
    ("cut off then good (OTS would lose both)", ev("B")[:-40] + g, [g], False),
    ("cut off only", g[:-30], [], True),
    ("DOCTYPE", b"<!DOCTYPE x [<!ENTITY a 'b'>]>" + g, [], True),
    ("bad UTF-8 only", g.replace(b'callsign="A', b'callsign="\xff'), [], True),
]
for name, pkt, want, malformed in CASES:
    got, why = B.split_events(pkt)
    if name.startswith("three"):
        ok = len(got) == 3 and all(B.bad_event(x) is None for x in got)
    else:
        ok = got == want
    ok = ok and ((not got) == malformed)
    check("unit %s -> %d good%s" % (name, len(got), ", " + why if why else ""), ok, (got, why))
check("unit a clean packet comes back as the same object (forwarded byte for byte)", B.split_events(g)[0][0] is g)

# ============================================================ R: real bridge -> OTS's parser
print("== R. real bridge into OTS's own stream parser", flush=True)
OP = free_port(); acc = W + "/accepted.txt"; open(acc, "w").close()
ots = subprocess.Popen([sys.executable, HERE + "/ots_intake_sim.py", str(OP), acc], stderr=open(W + "/ots.err", "w")); time.sleep(0.5)
open(W + "/r.ini", "w").write("[deck]\nCALLSIGN = DECK\nUID = DECK-r\nMESH_ADDR = 127.0.0.1\nGPS = off\n[bridge]\nCOT_URL = tcp://127.0.0.1:%d\n"
                              "STATUS_FILE = %s/rstatus.json\nMESH_CHAT_URL = off\nPING_INTERVAL = 0\nUP_MIN_INTERVAL = 0\nDEDUPE_SECONDS = 0\n" % (OP, W))
bp = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", W + "/r.ini"], stdout=open(W + "/rb.log", "w"), stderr=subprocess.STDOUT, env=ENV)
end = time.time() + 20
while time.time() < end and "Listening on udp+ro://239.2.3.1:6969" not in open(W + "/rb.log").read():
    time.sleep(0.2)
time.sleep(1)
mc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); mc.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
want, n_mal, n_rep = [], 0, 0
for i in range(60):
    k = i % 6
    if k == 0:
        pkt = ev("P%02d" % i) + b"\x00" * 32; want.append("P%02d" % i); n_rep += 1
    elif k == 1:
        pkt = ev("X%02d" % i)[:-40] + ev("N%02d" % i); want.append("N%02d" % i); n_rep += 1
    elif k == 2:
        pkt = ev("M%02da" % i) + ev("M%02db" % i); want += ["M%02da" % i, "M%02db" % i]; n_rep += 1
    elif k == 3:
        pkt = ev("H%02d" % i) + ev("X%02d" % i)[:-40]; want.append("H%02d" % i); n_rep += 1
    elif k == 4:
        pkt = ev("X%02d" % i)[:-30]; n_mal += 1
    else:
        pkt = ev("C%02d" % i); want.append("C%02d" % i)
    mc.sendto(pkt, ("239.2.3.1", 6969)); time.sleep(0.01)
time.sleep(3)
lines = open(acc).read().split()
got = [l for l in lines if not l.startswith("DECK") and l != "takPing"]
check("R OTS's parser got every good event (%d of %d)" % (len(set(got) & set(want)), len(want)), set(want) <= set(got), sorted(set(want) - set(got))[:10])
check("R each exactly once, none of the broken pieces (%d accepted)" % len(got), len(got) == len(want) and not any(l.startswith("X") for l in got), got[:10])
check("R OTS's parser still running", ots.poll() is None, open(W + "/ots.err").read()[-300:])
st = json.load(open(W + "/rstatus.json"))
check("R status: repaired %s (want %d), malformed %s (want %d)" % (st["mesh"].get("repaired"), n_rep, st["mesh"].get("malformed"), n_mal),
      st["mesh"].get("repaired") == n_rep and st["mesh"].get("malformed") == n_mal, st["mesh"])
check("R status reports the PyTAK version (%s)" % st.get("pytak"), (st.get("pytak") or "").startswith("7.6."), st.get("pytak"))
log = open(W + "/rb.log").read()
check("R the log names repairs (first 3 only) and the PyTAK version",
      log.count("Repaired a packet from 127.0.0.1") == 3 and "(tested version)" in log, log[-500:])
bp.terminate(); ots.terminate()

# ============================================================ V: version guard
print("== V. PyTAK version guard", flush=True)
real = B.pytak_version
for v, cfg, starts in (("7.6.1", {}, True), ("7.6.9", {}, True), ("7.5.2", {}, False), ("8.0.0", {}, False), ("", {}, False),
                       ("8.0.0", {"PYTAK_ANY_VERSION": "1"}, True), ("7.5.2", {"PYTAK_ANY_VERSION": "yes"}, True)):
    B.pytak_version = lambda v=v: v
    try:
        B.pytak_version_guard(cfg); ok, msg = True, ""
    except SystemExit as e:
        ok, msg = False, str(e)
    check("V PyTAK %r %s -> %s" % (v or "unknown", cfg or "", "starts" if starts else "stops"),
          ok == starts and (starts or ("vendor/" in msg and "PYTAK_ANY_VERSION" in msg)), msg)
B.pytak_version = real
print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
