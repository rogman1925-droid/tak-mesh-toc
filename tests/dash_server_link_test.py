"""Dashboard SERVER LINK panel with the bridge's "link" status (in Chromium, REAL dashboard_server, fake status file):
normal / silent dropout / just reconnected / server without ping answers / bridge reconnecting / older bridge (no rows).
Needs: playwright + Chromium. Usage: python3 dash_server_link_test.py [work dir]"""
import asyncio, json, os, socket, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); PKG = os.path.dirname(HERE)
W = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mmb_dash_link2"
os.makedirs(W, exist_ok=True)
from playwright.async_api import async_playwright
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:400]), flush=True)
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
# a local "server" with one open connection, so the dashboard sees an ESTABLISHED link like on the deck
lsn = socket.socket(); lsn.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); lsn.bind(("127.0.0.1", 0)); lsn.listen(1)
SPORT = lsn.getsockname()[1]
cli = socket.create_connection(("127.0.0.1", SPORT)); acc, _ = lsn.accept()
status = W + "/status.json"; ini = W + "/deck.ini"
open(ini, "w").write(f"""[deck]
CALLSIGN = DECK
MESH_ADDR = 127.0.0.1
GPS = off
[bridge]
COT_URL = tcp://127.0.0.1:{SPORT}
STATUS_FILE = {status}
[dashboard]
PORT = {PORT}
LISTEN = 127.0.0.1
BRIDGE_INI = {ini}
BRIDGE_STATUS_FILE = {status}
EVENT_FILE = {W}/ev.jsonl
HISTORY_FILE = {W}/dh.jsonl
""")
state = {"link": None}
def writer():
    while True:
        now = time.time()
        st = {"schema": 1, "pid": os.getpid(), "now": now, "started": now - 600, "uptime": 600,
              "mesh": {"events": 5}, "return": {"enabled": True}, "server": {"rx_events": 9, "last_rx_ts": now - 2},
              "per_minute": {"up": {"total": 0}, "down": {"total": 0}, "total": 0}}
        lk = state["link"]
        if lk is not None:
            st["link"] = {k: (now - v if k.endswith("_ago") else v) for k, v in lk.items()}
            for a, b in (("since_ago", "since"), ("pong_ago", "last_pong_ts"), ("down_ago", "prev_down_ts")):
                if a in st["link"]:
                    st["link"][b] = st["link"].pop(a)
        tmp = status + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, status)
        time.sleep(0.5)
threading.Thread(target=writer, daemon=True).start()
srv = subprocess.Popen([sys.executable, PKG + "/dashboard/dashboard_server.py", "--config", ini], stdout=open(W + "/d.log", "w"), stderr=subprocess.STDOUT)
BASE = {"state": "up", "reason": "", "answers_pings": True, "rtt_ms": 41.7, "ping_interval": 20, "timeout": 60, "starts": 1, "prev_reason": ""}
CASES = [
    ("normal", dict(BASE, since_ago=8040, pong_ago=6, starts=3, prev_reason="the server closed the connection", down_ago=8040),
     ["server answers", "41.7 ms", "for 2h 14m", "2 since boot", "the server closed the connection"], [], "CONNECTED"),
    ("silent dropout", dict(BASE, since_ago=2820, pong_ago=41), ["NONE for 4", "reconnects at 60"], ["reconnects\n"], "NO ANSWER"),
    ("just reconnected", dict(BASE, rtt_ms=52.3, since_ago=18, pong_ago=3, starts=4, prev_reason="no answer from the server for 61 s", down_ago=23),
     ["just reconnected", "3 since boot", "no answer from the server for 61 s"], [], None),
    ("first start", dict(BASE, since_ago=18, pong_ago=3), ["18 s"], ["just reconnected", "since boot"], None),
    ("no ping answers", dict(BASE, answers_pings=False, rtt_ms=None, since_ago=3600, pong_ago=None), ["no ping answers (watchdog off)"], ["NONE for"], None),
    ("bridge reconnecting", dict(BASE, state="down", reason="the server closed the connection", since_ago=100, pong_ago=30),
     ["reconnecting: the server closed the connection"], ["connected\n"], None),
    ("older bridge", None, [], ["server answers", "reconnects"], None),
]
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 800})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        for name, lk, want, nope, hdr in CASES:
            if lk is not None:
                lk = {k: v for k, v in lk.items() if v is not None or k == "rtt_ms"}
                if lk.get("pong_ago") is None:
                    lk.pop("pong_ago", None); lk["last_pong_ts"] = 0
            state["link"] = lk
            await asyncio.sleep(1.0)
            for _ in range(40):
                try:
                    await pg.goto("http://127.0.0.1:%d/" % PORT); break
                except Exception:
                    await asyncio.sleep(0.25)
            txt = ""
            for _ in range(24):                  # the dashboard refreshes on its own cycle: wait for this case
                await pg.wait_for_timeout(500)
                txt = await pg.inner_text("#p-server")
                if all(w in txt for w in want) and not any(n in txt for n in nope):
                    break
            ok = all(w in txt for w in want) and not any(n in txt for n in nope)
            if hdr:
                h = await pg.inner_text("#s-state")
                ok = ok and (h in hdr.split("|"))
            check("%s: panel shows the right rows" % name, ok, txt.replace("\n", " | "))
            if name == "silent dropout":
                cls = await pg.get_attribute("#p-server", "class")
                check("silent dropout: panel turns amber even though TLS says ESTABLISHED", "lv-warn" in cls, cls)
        check("no page errors", not errs, errs)
        await b.close()
try:
    asyncio.run(main())
finally:
    srv.terminate()
print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
