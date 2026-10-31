"""Dashboard SERVER LINK panel, outbox and DM-route rows (in Chromium, REAL dashboard_server, fake status file):
"DM route" (checking / OK / BROKEN) and "chats waiting" (outbox). Also writes a screenshot per state to
<work dir>/dm_route_<state>.png (the mockup). Needs: playwright + Chromium.
Usage: python3 dash_dm_route_test.py [work dir]"""
import asyncio, json, os, socket, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); PKG = os.path.dirname(HERE)
W = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mmb_dash_dm_route"
os.makedirs(W, exist_ok=True)
from playwright.async_api import async_playwright
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:400]), flush=True)
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
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
state = {"extra": {}}
def writer():
    while True:
        now = time.time()
        st = {"schema": 1, "pid": os.getpid(), "now": now, "started": now - 600, "uptime": 600,
              "mesh": {"events": 5}, "return": {"enabled": True}, "server": {"rx_events": 9, "last_rx_ts": now - 2},
              "per_minute": {"up": {"total": 0}, "down": {"total": 0}, "total": 0},
              "link": {"state": "up", "reason": "", "answers_pings": True, "rtt_ms": 41.7, "ping_interval": 20, "timeout": 60,
                       "starts": 1, "prev_reason": "", "since": now - 8040, "last_pong_ts": now - 6}}
        for k, v in state["extra"].items():
            st[k] = {kk: (now - vv if kk.endswith("_ago") else vv) for kk, vv in v.items()}
            for a, b in (("since_ago", "since"), ("ok_ago", "last_ok_ts"), ("oldest_ago", "oldest_ts")):
                if a in st[k]:
                    st[k][b] = st[k].pop(a)
        tmp = status + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, status)
        time.sleep(0.5)
threading.Thread(target=writer, daemon=True).start()
srv = subprocess.Popen([sys.executable, PKG + "/dashboard/dashboard_server.py", "--config", ini], stdout=open(W + "/d.log", "w"), stderr=subprocess.STDOUT)
OB = {"enabled": True, "pending": 0, "replayed": 0, "expired": 0}
CASES = [
    ("checking", {"dm_route": {"state": "checking", "callsign": "DECK-selftest", "since_ago": 20, "sent": 1, "rtt_ms": None}, "outbox": OB},
     ["DM route", "checking (1 sent, none back yet)"], ["chats waiting"]),
    ("ok", {"dm_route": {"state": "ok", "callsign": "DECK-selftest", "since_ago": 3000, "ok_ago": 12, "sent": 50, "rtt_ms": 85.2}, "outbox": OB},
     ["DM route", "OK, 85.2 ms"], ["BROKEN", "chats waiting"]),
    ("broken", {"dm_route": {"state": "broken", "callsign": "DECK-selftest", "since_ago": 400, "ok_ago": 520, "sent": 60, "rtt_ms": 85.2}, "outbox": OB},
     ["BROKEN for 6m", "mesh_mailroute on the server?"], []),
    ("waiting", {"dm_route": {"state": "ok", "callsign": "DECK-selftest", "since_ago": 3000, "ok_ago": 12, "sent": 50, "rtt_ms": 85.2},
                 "outbox": dict(OB, pending=7, waiting=7, oldest_ago=1500)}, ["chats waiting", "7, oldest 25m", "sent when the link is back"], []),
    ("replayed", {"dm_route": {"state": "ok", "callsign": "DECK-selftest", "since_ago": 30, "ok_ago": 2, "sent": 1, "rtt_ms": 90.0},
                  "outbox": dict(OB, replayed=7)}, ["none (7 sent after the restart)"], []),
    ("older bridge", {}, [], ["DM route", "chats waiting"]),
    ("off", {"dm_route": {"state": "off"}, "outbox": {"enabled": False}}, [], ["DM route", "chats waiting"]),
]
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 800})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        for name, extra, want, nope in CASES:
            state["extra"] = extra
            await asyncio.sleep(1.0)
            for _ in range(40):
                try:
                    await pg.goto("http://127.0.0.1:%d/" % PORT); break
                except Exception:
                    await asyncio.sleep(0.25)
            txt = ""
            for _ in range(24):
                await pg.wait_for_timeout(500)
                txt = await pg.inner_text("#p-server")
                if all(w in txt for w in want) and not any(n in txt for n in nope):
                    break
            ok = all(w in txt for w in want) and not any(n in txt for n in nope)
            check("%s: panel shows the right rows" % name, ok, txt.replace("\n", " | "))
            if name in ("broken", "waiting"):
                cls = await pg.get_attribute("#p-server", "class")
                check("%s: panel stays CONNECTED green (a warning row, not a red panel)" % name, "lv-ok" in cls, cls)
            el = await pg.query_selector("#p-server")
            await el.screenshot(path=W + "/dm_route_%s.png" % name.replace(" ", "_"))
        check("no page errors", not errs, errs)
        await b.close()
try:
    asyncio.run(main())
finally:
    srv.terminate()
print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
