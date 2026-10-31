"""TAK Protocol (protobuf) mesh traffic through the REAL bridge (1.0.1):
  - a protobuf position report is converted and reaches the server, counted nowhere as skipped
  - a TAK Protocol announcement (takControl only) is NOT forwarded and counts as `control`, not `skipped`
  - an unknown 0xbf packet counts as skipped and the log says why
  - the bridge logs at startup that takproto is loaded
Usage: python3 proto_test.py [work dir]"""
import json, os, socket, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG + "/vendor")      # the bundled takproto / protobuf, the copies the bridge runs with
import takproto
from takproto.proto import TakMessage

W = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mmb_proto_test"
os.makedirs(W, exist_ok=True)
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:400]), flush=True)
def wait(cond, t=15.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False

srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 0)); srv.listen(5)
sport = srv.getsockname()[1]; got = []
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
                got.append(d)
        threading.Thread(target=rd, daemon=True).start()
threading.Thread(target=serve, daemon=True).start()

status = W + "/status.json"
if os.path.exists(status):
    os.remove(status)
ini = W + "/deck.ini"
open(ini, "w").write(f"""[deck]
CALLSIGN = DECK
UID = DECK-prototest
MESH_ADDR = 127.0.0.1
GPS = off
[bridge]
COT_URL = tcp://127.0.0.1:{sport}
STATUS_FILE = {status}
MESH_CHAT_URL = off
""")
bp = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", ini], stdout=open(W + "/b.log", "w"),
                      stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONWARNINGS="ignore"))
def send(data):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.sendto(bytes(data), ("239.2.3.1", 6969)); s.close()
def st():
    try:
        return json.load(open(status))
    except (OSError, ValueError):
        return {}
try:
    wait(lambda: b"DECK-prototest" in b"".join(got), 20)
    wait(lambda: "Listening on udp+ro://239.2.3.1:6969" in open(W + "/b.log").read(), 10)
    time.sleep(1)
    log0 = open(W + "/b.log").read()
    check("startup log says takproto is loaded", "takproto %s loaded" % takproto.__version__ in log0, log0[-500:])
    sa = ('<event version="2.0" uid="ANDROID-proto1" type="a-f-G-U-C" how="m-g" time="2026-10-21T15:00:00.000Z" '
          'start="2026-10-21T15:00:00.000Z" stale="2099-10-21T15:05:00.000Z"><point lat="38.8" lon="-104.8" hae="1800" ce="9" le="9"/>'
          '<detail><contact callsign="PROTO-1" endpoint="10.41.0.50:4242:tcp"/></detail></event>')
    send(takproto.xml2proto(sa))
    ok = wait(lambda: b'uid="ANDROID-proto1"' in b"".join(got), 10)
    check("protobuf position report converted and forwarded to the server", ok, b"".join(got)[-300:])
    check("...as XML with its callsign", b'callsign="PROTO-1"' in b"".join(got))
    m = TakMessage(); m.takControl.minProtoVersion = 1; m.takControl.maxProtoVersion = 1; m.takControl.contactUid = "ANDROID-proto1"
    for _ in range(3):
        send(b"\xbf\x01\xbf" + m.SerializeToString()); time.sleep(0.2)
    ok = wait(lambda: (st().get("mesh") or {}).get("control") == 3, 10)
    s = st().get("mesh") or {}
    check("3 announcements counted as control", ok, s)
    check("...and NOT as skipped", s.get("skipped") == 0, s)
    log1 = open(W + "/b.log").read()
    check("announcement logged once as 'not forwarded'", log1.count("TAK Protocol announcement from 127.0.0.1") == 1, log1[-500:])
    send(b"\xbf\x02\xbf\x00\x01\x02")
    ok = wait(lambda: (st().get("mesh") or {}).get("skipped") == 1, 10)
    check("an unknown 0xbf packet counts as skipped", ok, st().get("mesh"))
    log2 = open(W + "/b.log").read()
    check("...and the log says why (first bytes)", "not TAK Protocol v1 mesh (first bytes bf02bf000102)" in log2, log2[-400:])
    check("nothing else was forwarded for those", b"".join(got).count(b"<event") >= 2 and b"bf02" not in b"".join(got))
finally:
    bp.terminate()
    try:
        bp.wait(5)
    except Exception:
        bp.kill()
print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
