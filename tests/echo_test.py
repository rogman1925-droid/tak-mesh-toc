import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
"""echo_test.py: a phone that moves from the server to the mesh is forwarded to the server straight away,
while server reports put back on the mesh (by this deck or another deck) are still never sent back up.

The real bridge, a fake TAK server, and fake phones / a fake second deck on the mesh position group.
Before 3.1.3 the bridge dropped every mesh report from a UID it had itself put on the mesh in the last 10 minutes,
so a phone that had just left the server was invisible to the server (and got no server -> mesh DMs) for 10 min."""
import os, re, socket, subprocess, sys, threading, time
from datetime import datetime, timedelta, timezone

W = "/tmp/mmb_echo"; os.makedirs(W, exist_ok=True)
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:300]))

# ---- fake TAK server: records what the bridge sends, can push events down
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 0)); srv.listen(5)
sport = srv.getsockname()[1]
got = []; conns = []
def serve():
    while True:
        try: c, _ = srv.accept()
        except OSError: return
        conns.append(c)
        def rd(c=c):
            while True:
                try: d = c.recv(65536)
                except OSError: return
                if not d: return
                got.append(d)
        threading.Thread(target=rd, daemon=True).start()
threading.Thread(target=serve, daemon=True).start()

GROUP = ("239.2.3.1", 6969)
def mesh_send(data):                                  # a phone or another deck on the mesh
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.sendto(data, GROUP); s.close()

# a listener on the mesh, to see what the bridge puts there
heard = []
lst = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
lst.bind(GROUP); lst.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(GROUP[0]) + socket.inet_aton("127.0.0.1"))
lst.settimeout(0.3)
def listen():
    while True:
        try: heard.append(lst.recvfrom(65535)[0])
        except socket.timeout: continue
        except OSError: return
threading.Thread(target=listen, daemon=True).start()

T0 = datetime.now(timezone.utc)
def stamp(sec):
    return (T0 + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
def sa(uid, cs, t, ep="10.41.0.77:4242:tcp"):
    st = (T0 + timedelta(seconds=t + 120)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return ('<?xml version="1.0"?><event version="2.0" uid="%s" type="a-f-G-U-C" how="m-g" time="%s" start="%s" stale="%s">'
            '<point lat="35.0" lon="-79.0" hae="0" ce="9" le="9"/><detail><contact callsign="%s" endpoint="%s"/>'
            '<__group name="Cyan" role="Team Member"/></detail></event>' % (uid, stamp(t), stamp(t), st, cs, ep)).encode()
def up(uid, t):                                       # did the server get this exact report (uid + time) from the bridge?
    blob = b"".join(got)
    return any(uid.encode() in e and stamp(t).encode() in e for e in blob.split(b"</event>"))
def wait_up(uid, t, secs=8):
    end = time.time() + secs
    while time.time() < end:
        if up(uid, t): return True
        time.sleep(0.2)
    return False

open(W + "/bridge.ini", "w").write(f"""[bridge]
COT_URL = tcp://127.0.0.1:{sport}
PYTAK_MULTICAST_LOCAL_ADDR = 127.0.0.1
STATUS_FILE = {W}/status.json
OUTBOX_FILE = {W}/outbox.jsonl
RETURN_PATH = 1
DM_RELAY = 0
PING_INTERVAL = 0
""")
bridge = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", W + "/bridge.ini"],
                          stdout=open(W + "/bridge.log", "w"), stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONWARNINGS="ignore"))
try:
    for _ in range(60):
        if conns: break
        time.sleep(0.2)
    check("bridge connected to the fake server", bool(conns))
    time.sleep(1.5)

    X, CS = "ANDROID-super4", "SUPER-4"
    # 1. the phone is on the SERVER: the server sends its position down; the bridge puts it on the mesh
    conns[0].sendall(sa(X, CS, 1))
    end = time.time() + 6
    while time.time() < end and not any(X.encode() in h and stamp(1).encode() in h for h in heard):
        time.sleep(0.2)
    check("1 server phone's position is put on the mesh (return path)", any(X.encode() in h and stamp(1).encode() in h for h in heard))
    time.sleep(2)
    check("1 ...and that copy is NOT sent back up to the server (own echo)", not up(X, 1))

    # 2. a SECOND deck on the same mesh puts the same server report on the mesh (its own contact endpoint)
    mesh_send(sa(X, CS, 1, ep="10.41.0.99:4243:tcp"))
    time.sleep(2.5)
    check("2 another deck's copy of a server report is NOT sent up either", not up(X, 1))

    # 3. the phone switches to the MESH: its own new report must reach the server straight away
    mesh_send(sa(X, CS, 5))
    check("3 phone that just left the server: its mesh report reaches the server at once (no 10-min blackout)", wait_up(X, 5))

    # 4. a phone on BOTH: the server keeps sending copies, the phone keeps reporting on the mesh
    conns[0].sendall(sa(X, CS, 9))
    time.sleep(1.0)
    mesh_send(sa(X, CS, 12))
    check("4 phone on both: its newer mesh report still goes up", wait_up(X, 12, 10))

    # 5. a phone that never touched the server: unchanged behaviour
    mesh_send(sa("ANDROID-havoc2", "HAVOC-2", 3))
    check("5 an ordinary mesh phone is forwarded as before", wait_up("ANDROID-havoc2", 3))

    # 6. a server report with NO time stamp: the old rule (UID recently put on the mesh) still stops its echo
    nt = ('<event version="2.0" uid="ANDROID-notime" type="a-f-G-U-C" how="m-g"><point lat="35" lon="-79" hae="0" ce="9" le="9"/>'
          '<detail><contact callsign="NOTIME" endpoint="10.41.0.70:4242:tcp"/></detail></event>').encode()
    conns[0].sendall(nt)
    time.sleep(2.5)
    check("6 no time stamp: its echo is still not sent back up (fallback rule)", b"ANDROID-notime" not in b"".join(got))
    check("bridge still running", bridge.poll() is None)
finally:
    bridge.terminate()
    try: bridge.wait(5)
    except subprocess.TimeoutExpired: bridge.kill()

print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
