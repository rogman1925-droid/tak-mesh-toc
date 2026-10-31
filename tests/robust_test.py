"""Robustness tests (the REAL bridge, Deck Chat and dashboard):
  C  deck.ini: a setting written twice, a Windows BOM, "%" in the callsign: all three programs start;
     `check`'s duplicate finder reports the lines; a TLS client certificate whose password has "%" connects
     (real PEM certificate + encrypted key + TLS test server; skipped if openssl is missing)
  M  malformed mesh packets (cut off, invalid UTF-8, broken &, control characters) are dropped at the deck and counted;
     OpenTAKServer's own stream parser (tests/ots_intake_sim.py) then loses no good report
Usage: python3 robust_test.py [work dir]"""
import importlib.util, json, os, shutil, socket, ssl, subprocess, sys, threading, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); PKG = os.path.dirname(HERE)
W = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mmb_robust_test"
os.makedirs(W, exist_ok=True)
ENV = dict(os.environ, PYTHONWARNINGS="ignore")
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:400]), flush=True)
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
def up(url):
    try:
        return urllib.request.urlopen(url, timeout=1).status == 200
    except Exception:
        return False

# ============================================================ C. deck.ini
print("== C. deck.ini", flush=True)
sink = socket.socket(); sink.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); sink.bind(("127.0.0.1", 0)); sink.listen(50)
SP = sink.getsockname()[1]
threading.Thread(target=lambda: [sink.accept() for _ in iter(int, 1)], daemon=True).start()
CP, DP = free_port(), free_port()
BASE = f"""[deck]
CALLSIGN = DECK
UID = DECK-robust
MESH_ADDR = 127.0.0.1
GPS = off
[bridge]
COT_URL = tcp://127.0.0.1:{SP}
STATUS_FILE = {W}/status.json
PING_INTERVAL = 0
[chat]
LISTEN = 127.0.0.1:{CP}
HISTORY_FILE = {W}/h.jsonl
BRIDGE_STATUS_FILE = {W}/status.json
DM_PORT = {free_port()}
SA_SEND = 0
[dashboard]
PORT = {DP}
LISTEN = 127.0.0.1
BRIDGE_INI = {W}/deck.ini
BRIDGE_STATUS_FILE = {W}/status.json
EVENT_FILE = {W}/ev.jsonl
HISTORY_FILE = {W}/dh.jsonl
"""
def start_all(raw):
    open(W + "/deck.ini", "wb").write(raw)
    if os.path.exists(W + "/status.json"):
        os.remove(W + "/status.json")
    ps = {"bridge": subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", W + "/deck.ini"], stdout=open(W + "/b.log", "w"), stderr=subprocess.STDOUT, env=ENV),
          "chat": subprocess.Popen([sys.executable, PKG + "/chat/deck_chat.py", "--config", W + "/deck.ini"], stdout=open(W + "/c.log", "w"), stderr=subprocess.STDOUT, env=ENV),
          "dashboard": subprocess.Popen([sys.executable, PKG + "/dashboard/dashboard_server.py", "--config", W + "/deck.ini"], stdout=open(W + "/d.log", "w"), stderr=subprocess.STDOUT, env=ENV)}
    end = time.time() + 10
    while time.time() < end and not (os.path.exists(W + "/status.json") and up("http://127.0.0.1:%d/healthz" % CP) and up("http://127.0.0.1:%d/healthz" % DP)):
        time.sleep(0.2)
    time.sleep(0.5)
    ok = {k: p.poll() is None for k, p in ps.items()}
    ok["bridge"] = ok["bridge"] and os.path.exists(W + "/status.json")
    ok["chat"] = ok["chat"] and up("http://127.0.0.1:%d/healthz" % CP)
    ok["dashboard"] = ok["dashboard"] and up("http://127.0.0.1:%d/healthz" % DP)
    for p in ps.values():
        if p.poll() is None:
            p.terminate()
    for p in ps.values():
        try:
            p.wait(5)
        except Exception:
            p.kill()
    return ok
for name, raw in (("a setting written twice", BASE.replace("CALLSIGN = DECK\n", "CALLSIGN = OLD\nCALLSIGN = DECK\n").encode()),
                  ("saved with a Windows BOM", b"\xef\xbb\xbf" + BASE.encode()),
                  ("'%' in the callsign", BASE.replace("CALLSIGN = DECK\n", "CALLSIGN = 100%\n").encode())):
    ok = start_all(raw)
    check("C %s: bridge, Deck Chat and dashboard all start" % name, all(ok.values()), ok)
st = json.load(open(W + "/status.json")) if os.path.exists(W + "/status.json") else {}
spec = importlib.util.spec_from_file_location("atb_tool", PKG + "/tools/manet_mesh_bridge.py")
tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
open(W + "/dup.ini", "w").write("[deck]\nCALLSIGN = OLD\nMESH_ADDR = 10.41.0.60\ncallsign = DECK\n[bridge]\n# COT_URL = x\nCOT_URL = tcp://a:1\nCOT_URL = tcp://b:2\n")
check("C check's duplicate finder names the setting and the lines",
      tool.config_duplicates(W + "/dup.ini") == [("deck", "CALLSIGN", [2, 4]), ("bridge", "COT_URL", [7, 8])], tool.config_duplicates(W + "/dup.ini"))
# TLS client certificate whose key password has "%" in it (PEM, the format the bridge uses)
if shutil.which("openssl"):
    T = W + "/tls"; os.makedirs(T, exist_ok=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", T + "/key.pem", "-out", T + "/cert.pem", "-days", "2",
                    "-subj", "/CN=test", "-passout", "pass:Tak%2024!"], capture_output=True)
    os.chmod(T + "/key.pem", 0o600)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(T + "/cert.pem", T + "/key.pem", password="Tak%2024!")
    ls = socket.socket(); ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); ls.bind(("127.0.0.1", 0)); ls.listen(5)
    got = []
    def tls_srv():
        while True:
            c, _ = ls.accept()
            try:
                t = ctx.wrap_socket(c, server_side=True); got.append(t.recv(65536))
            except Exception:
                pass
    threading.Thread(target=tls_srv, daemon=True).start()
    open(T + "/deck.ini", "w").write("[deck]\nCALLSIGN = DECK\nUID = DECK-tls\nMESH_ADDR = 127.0.0.1\nGPS = off\n[bridge]\nCOT_URL = tls://127.0.0.1:%d\n"
                                     "PYTAK_TLS_CLIENT_CERT = %s/cert.pem\nPYTAK_TLS_CLIENT_KEY = %s/key.pem\nPYTAK_TLS_CLIENT_PASSWORD = Tak%%2024!\n"
                                     "PYTAK_TLS_CLIENT_CAFILE = %s/cert.pem\nPYTAK_TLS_DONT_CHECK_HOSTNAME = 1\nSTATUS_FILE = off\nPING_INTERVAL = 0\n" % (ls.getsockname()[1], T, T, T))
    bp = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", T + "/deck.ini"], stdout=open(T + "/b.log", "w"), stderr=subprocess.STDOUT, env=ENV)
    end = time.time() + 15
    while time.time() < end and not any(b"DECK-tls" in g for g in got):
        time.sleep(0.2)
    bp.terminate()
    check("C TLS: PEM certificate + key whose password has '%', server verified by its CA: bridge connects and identifies",
          any(b"DECK-tls" in g for g in got), open(T + "/b.log").read()[-400:])
else:
    print("SKIP  C TLS '%' password (openssl not installed)")

# ============================================================ M. malformed mesh packets
print("== M. malformed mesh packets", flush=True)
OP = free_port(); acc = W + "/accepted.txt"; open(acc, "w").close()
ots = subprocess.Popen([sys.executable, HERE + "/ots_intake_sim.py", str(OP), acc], stderr=open(W + "/ots.err", "w")); time.sleep(0.5)
open(W + "/m.ini", "w").write("[deck]\nCALLSIGN = DECK\nUID = DECK-m\nMESH_ADDR = 127.0.0.1\nGPS = off\n[bridge]\nCOT_URL = tcp://127.0.0.1:%d\n"
                              "STATUS_FILE = %s/mstatus.json\nMESH_CHAT_URL = off\nPING_INTERVAL = 0\nUP_MIN_INTERVAL = 0\n" % (OP, W))
bp = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", W + "/m.ini"], stdout=open(W + "/mb.log", "w"), stderr=subprocess.STDOUT, env=ENV)
end = time.time() + 20
while time.time() < end and "Listening on udp+ro://239.2.3.1:6969" not in open(W + "/mb.log").read():
    time.sleep(0.2)
time.sleep(1)
mc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); mc.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
TS = 'time="2026-10-22T18:00:00Z" start="2026-10-22T18:00:00Z" stale="2099-01-01T00:00:00Z"'
def good(i):
    return ('<event version="2.0" uid="GOOD-%04d" type="a-f-G-U-C" how="m-g" %s><point lat="38" lon="-104" hae="0" ce="9" le="9"/><detail><contact callsign="G%d"/></detail></event>' % (i, TS, i)).encode()
BAD = {"cut off": lambda i: good(i)[:-30], "invalid UTF-8": lambda i: good(i).replace(b'callsign="G', b'callsign="\xff\xfeG'),
       "broken &": lambda i: good(i).replace(b'callsign="G', b'callsign="R&D G'), "control character": lambda i: good(i).replace(b'callsign="G', b'callsign="\x07G')}
n_bad = 0
for name, mk in BAD.items():
    before = len(open(acc).read().split()); n_good = 0
    for i in range(200):
        if i % 20 == 10:
            mc.sendto(mk(i).replace(b"GOOD-", b"BAD-"), ("239.2.3.1", 6969)); n_bad += 1
        else:
            mc.sendto(good(i), ("239.2.3.1", 6969)); n_good += 1
        time.sleep(0.002)
    time.sleep(2.5)
    lines = open(acc).read().split()[before:]
    got = sum(1 for l in lines if l.startswith("GOOD-")); bad = sum(1 for l in lines if l.startswith("BAD-"))
    check("M %s: the server's parser gets all %d good reports and none of the bad ones (got %d good, %d bad)" % (name, n_good, got, bad),
          got == n_good and bad == 0 and ots.poll() is None, open(W + "/ots.err").read()[-300:])
time.sleep(2.5)
st = json.load(open(W + "/mstatus.json"))
check("M every bad packet counted as malformed (%s of %d)" % (st["mesh"].get("malformed"), n_bad), st["mesh"].get("malformed") == n_bad, st["mesh"])
log = open(W + "/mb.log").read()
check("M the log says who sent it and why (first 3 only)", log.count("Dropped a malformed packet from 127.0.0.1") == 3 and "not complete XML" in log, log[-400:])
bp.terminate(); ots.terminate()
print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
