import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
"""Dashboard v4.2 server check: reads the bridge's link from the kernel, never opens connections."""
import json, os, socket, subprocess, sys, threading, time, urllib.request
sys.path.insert(0, PKG + "/dashboard")
import dashboard_server as ds

res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:300]))

# 1. parser on real-format ss output
sample = ("0      0      10.41.0.60:43210 100.64.0.9:8089\n"
          "\t cubic wscale:7,7 rto:240 rtt:38.512/4.1 ato:40 mss:1288\n"
          "0      0      10.41.0.60:5555 1.2.3.4:443\n"
          "\t cubic rtt:9.9/1 ato:40\n")
check("parser: finds the server link and its kernel RTT", ds.parse_ss_link(sample, "100.64.0.9:8089") == (True, 38.5), ds.parse_ss_link(sample, "100.64.0.9:8089"))
check("parser: no link -> (False, None)", ds.parse_ss_link(sample, "100.64.0.9:9999") == (False, None))
check("parser: doesn't borrow the next socket's RTT", ds.parse_ss_link("0 0 a:1 100.64.0.9:8089\n0 0 a:2 b:3\n\t rtt:5/1\n", "100.64.0.9:8089") == (True, None))

# 2. live: fake OTS + a held "bridge" connection + the real dashboard
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 0)); srv.listen(10)
port = srv.getsockname()[1]; accepts = []
def acc():
    while True:
        try: c, a = srv.accept()
        except OSError: return
        accepts.append(a)
threading.Thread(target=acc, daemon=True).start()
bridge = socket.create_connection(("127.0.0.1", port))
W = "/tmp/mmb_dash_link"; os.makedirs(W, exist_ok=True)
ini = ("[dashboard]\nPORT = 8099\nLISTEN = 127.0.0.1\nBRIDGE_STATUS_FILE = {w}/bridge_status.json\nBRIDGE_INI = {w}/none.ini\n"
       "SERVER_HOST = 127.0.0.1\nSERVER_PORT = {port}\nEVENT_FILE = {w}/ev.jsonl\nHISTORY_FILE = {w}/hist.jsonl\nCAPTURE_DIR = {w}/caps\n"
       "NODE_IP = 127.0.0.1\nGPS = off\n").format(w=W, port=port)
open(W + "/d.ini", "w").write(ini)
dp = subprocess.Popen([sys.executable, PKG + "/dashboard/dashboard_server.py", "--config", W + "/d.ini"], stdout=open(W + "/d.log", "w"), stderr=subprocess.STDOUT)
def status():
    return json.load(urllib.request.urlopen("http://127.0.0.1:8099/api/status", timeout=3))
try:
    time.sleep(15)
    s = status()["server"]
    check("live: no new connections to the server port in 15 s (only the 'bridge' one)", len(accepts) == 1, len(accepts))
    check("live: link shown ESTABLISHED with a kernel RTT", s["established"] and s["rtt_ms"] is not None and s["tile"] == "ok", s)
    bridge.close(); time.sleep(5)
    s = status()["server"]
    check("live: bridge link down but host answers ping -> warn, still reachable", (not s["established"]) and s["reachable"] and s["tile"] == "warn", s)
    check("live: still no probe connections after the link dropped", len(accepts) == 1, len(accepts))
finally:
    dp.terminate()
# 3. unreachable host
ini2 = ini.replace("SERVER_HOST = 127.0.0.1", "SERVER_HOST = 192.0.2.77").replace("PORT = 8099", "PORT = 8096")
open(W + "/d2.ini", "w").write(ini2)
dp = subprocess.Popen([sys.executable, PKG + "/dashboard/dashboard_server.py", "--config", W + "/d2.ini"], stdout=open(W + "/d2.log", "w"), stderr=subprocess.STDOUT)
try:
    time.sleep(6)
    s = json.load(urllib.request.urlopen("http://127.0.0.1:8096/api/status", timeout=3))["server"]
    check("live: no link and no ping reply -> bad / unreachable", s["tile"] == "bad" and not s["reachable"], s)
finally:
    dp.terminate()
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
