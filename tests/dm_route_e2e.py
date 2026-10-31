"""DM route check, end to end: REAL RabbitMQ + mini_ots (OTS 1.7.13 mailbox logic) + REAL mesh_mailroute 1.1 + REAL
tak_mesh_bridge 1.0.9 (DM_RELAY = 1, DM_SELFTEST_INTERVAL = 15).
  1  the bridge's probe goes up, OTS routes it, the helper answers, it comes back: status dm_route = ok, with a round trip
  2  the probe is never routed to the mesh, and the helper added no mail rule for it
  3  helper stopped: the route goes BROKEN within two intervals, with a clear log line
  4  helper back: OK again, "after being broken"
  5  DM_SELFTEST = off: state off, no probes sent
Usage: python3 dm_route_e2e.py <bridge dir> <server dir> <work dir>"""
import json, os, socket, struct, subprocess, sys, threading, time
B, S, W = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(W, exist_ok=True)
sys.path.insert(0, os.path.join(S, "tests"))
from mini_ots import MiniOTS
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:500]), flush=True)
def rmq(*a): return subprocess.run(["rabbitmqctl", "-q"] + list(a), capture_output=True, text=True).stdout
def rules():
    return sorted(l.split("\t")[1] for l in rmq("list_bindings", "source_name", "routing_key", "destination_name").splitlines()
                  if l.startswith("dms\t") and l.endswith("\tDECK-testdeck") and l.split("\t")[1] != "DECK-testdeck")
def wait(cond, t=8.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.25)
    return cond()
ots = MiniOTS()
st = W + "/st.json"; state = W + "/mailroute.json"
if os.path.exists(state):
    os.remove(state)
def write_b(**kw):
    extra = "".join("%s = %s\n" % kv for kv in kw.items())
    open(W + "/b.ini", "w").write("[bridge]\nCOT_URL = tcp://127.0.0.1:%d\nPYTAK_MULTICAST_LOCAL_ADDR = 127.0.0.1\nSTATUS_FILE = %s\nRETURN_PATH = 1\n"
                                  "BRIDGE_UID = DECK-testdeck\nDM_RELAY = 1\nDM_SELFTEST_INTERVAL = 15\nPING_INTERVAL = 5\nOUTBOX_FILE = off\n%s" % (ots.port, st, extra))
write_b()
open(W + "/m.ini", "w").write("[mailroute]\nBRIDGE_UID = DECK-testdeck\nSTATE_FILE = %s\nRECHECK = 3\n" % state)
env = dict(os.environ, PYTHONWARNINGS="ignore")
procs = {}
def start_bridge():
    procs["b"] = subprocess.Popen([sys.executable, "-S", B + "/tak_mesh_bridge.py", W + "/b.ini"], stdout=open(W + "/b.log", "a"), stderr=subprocess.STDOUT, env=env)
def start_helper():
    procs["m"] = subprocess.Popen([sys.executable, S + "/mesh_mailroute.py", "--config", W + "/m.ini"], stdout=open(W + "/m.log", "a"), stderr=subprocess.STDOUT, env=env)
def status():
    try:
        return json.load(open(st))
    except Exception:
        return {}
def route():
    return status().get("dm_route") or {}
def blog(): return open(W + "/b.log").read()
def mlog(): return open(W + "/m.log").read()
# listen on the mesh groups: the probe must never appear there
heard = []
def listener(group, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", port)); s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton("127.0.0.1")))
    while True:
        d, a = s.recvfrom(65535); heard.append(d)
for g, p in (("239.2.3.1", 6969), ("224.10.10.1", 17012)):
    threading.Thread(target=listener, args=(g, p), daemon=True).start()
t0 = time.time()
start_helper(); start_bridge()
try:
    check("1 bridge says the check is on", wait(lambda: "DM route check ON" in blog(), 15), blog()[-400:])
    check("1 state 'checking' first", wait(lambda: route().get("state") == "checking", 10), route())
    ok1 = wait(lambda: route().get("state") == "ok" and route().get("rtt_ms") is not None, 40)
    check("1 within ~2 intervals: OK, with a round trip (%s ms)" % route().get("rtt_ms"), ok1, route())
    check("1 the helper logged the answer", "DM route check from the bridge answered" in mlog(), mlog()[-300:])
    check("1 the bridge logged it", "DM route OK" in blog(), blog()[-300:])
    check("2 the helper added no mail rule for the made-up callsign", "DECK-selftest" not in rules(), rules())
    check("2 the probe never went out on the mesh", not any(b"t-x-atb-st" in d for d in heard), len(heard))
    sent0 = route().get("sent", 0)
    procs["m"].terminate(); procs["m"].wait(5)
    check("3 helper stopped: BROKEN within two intervals (+1 tick)", wait(lambda: route().get("state") == "broken", 60), route())
    check("3 the log says why and what to check", "DM route BROKEN" in blog() and "mesh_mailroute" in blog(), blog()[-500:])
    check("3 the bridge keeps probing while broken", wait(lambda: route().get("sent", 0) > sent0 + 1, 40), route())
    start_helper()
    check("4 helper back: OK again", wait(lambda: route().get("state") == "ok", 45), route())
    check("4 ...logged as recovered", "after being broken" in blog(), blog()[-300:])
    procs["b"].terminate(); procs["b"].wait(5)
    write_b(DM_SELFTEST="off")
    n_m = mlog().count("answered")
    start_bridge()
    check("5 DM_SELFTEST = off: state off", wait(lambda: route().get("state") == "off", 15), route())
    time.sleep(20)
    check("5 ...and no probes were sent (helper answered nothing new)", mlog().count("answered") == n_m and route().get("sent", 0) == 0, (mlog().count("answered"), route()))
finally:
    for p in procs.values():
        try: p.terminate()
        except Exception: pass
    time.sleep(0.5)
print("\n%d/%d passed  (%.0f s)" % (sum(res), len(res), time.time() - t0))
sys.exit(0 if all(res) else 1)
