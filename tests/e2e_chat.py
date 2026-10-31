import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
"""End-to-end: fake ATAK phone (multicast) + real bridge + fake TAK server + deck_chat."""
import json, os, socket, struct, subprocess, sys, threading, time, urllib.request, uuid
sys.path.insert(0, PKG + "/chat")
import deck_chat as dc
import takproto

W = "/tmp/mmb_chat"; _o.makedirs(W, exist_ok=True)
results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + str(extra)) if extra and not cond else ""))

# ---- fake TAK server
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 0)); srv.listen(5)
sport = srv.getsockname()[1]
server_got = []; conns = []
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
                server_got.append(d)
        threading.Thread(target=rd, daemon=True).start()
threading.Thread(target=serve, daemon=True).start()

# ---- fake phone listener on the chat group
ph = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
ph.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); ph.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
ph.bind(("224.10.10.1", 17012))
ph.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton("224.10.10.1") + socket.inet_aton("127.0.0.1"))
ph.settimeout(0.3)
phone_got = []
def phone_rx():
    while True:
        try: phone_got.append(ph.recvfrom(65535)[0])
        except socket.timeout: continue
        except OSError: return
threading.Thread(target=phone_rx, daemon=True).start()
def phone_send(data):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.sendto(data, ("224.10.10.1", 17012)); s.close()

def atak_chat(callsign, uid, text):
    _, xml = dc.build_geochat(callsign, uid, text)
    return xml

# ---- configs
status = W + "/bridge_status.json"
open(W + "/bridge.ini", "w").write(f"""[bridge]
COT_URL = tcp://127.0.0.1:{sport}
PYTAK_MULTICAST_LOCAL_ADDR = 127.0.0.1
STATUS_FILE = {status}
RETURN_PATH = 1
LOCAL_CHAT_COPY = udp://127.0.0.1:17013
""")
hist = W + "/hist.jsonl"
for f in (hist, status):
    if os.path.exists(f): os.remove(f)
open(W + "/chat.ini", "w").write(f"""[chat]
CALLSIGN = DECK
UID = DECK-test
MESH_LOCAL_ADDR = 127.0.0.1
LISTEN = 127.0.0.1:8090
HISTORY_FILE = {hist}
BRIDGE_STATUS_FILE = {status}
MAX_SENDS_PER_10S = 6
""")

env = dict(os.environ, PYTHONWARNINGS="ignore")
bridge = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", W + "/bridge.ini"],
                          stdout=open(W + "/bridge.log", "w"), stderr=subprocess.STDOUT, env=env)
chat = subprocess.Popen([sys.executable, PKG + "/chat/deck_chat.py", "--config", W + "/chat.ini"],
                        stdout=open(W + "/chat.log", "w"), stderr=subprocess.STDOUT, env=env)
time.sleep(4)

def get(path, host="127.0.0.1:8090"):
    r = urllib.request.Request("http://127.0.0.1:8090" + path, headers={"Host": host})
    try:
        with urllib.request.urlopen(r, timeout=3) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, None
def post(text, xdeck=True, host="127.0.0.1:8090"):
    h = {"Content-Type": "application/json", "Host": host}
    if xdeck: h["X-Deck"] = "1"
    r = urllib.request.Request("http://127.0.0.1:8090/api/send", data=json.dumps({"text": text}).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=3) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, None
def msgs(): return get("/api/messages?after=0")[1]["messages"]

check("bridge and chat running", bridge.poll() is None and chat.poll() is None)
code, info = get("/api/info")
check("info: mesh listener joined directly", info and info["mesh_direct"] is True, info)
check("info: bridge copy port listening", info and info["bridge_copy"]["listening"] is True, info)
check("info: bridge seen running", info and info["bridge"]["running"] is True, info)

# 1. phone -> deck (XML)
phone_send(atak_chat("ALPHA-1", "ANDROID-alpha", "Alpha-1 radio check"))
time.sleep(1.5)
m = [x for x in msgs() if x["text"] == "Alpha-1 radio check"]
check("1 phone XML chat shows on deck once", len(m) == 1, m)
check("1 tagged MESH, from ALPHA-1", m and m[0]["src"] == "mesh" and m[0]["from"] == "ALPHA-1", m)
check("1 heard directly AND via bridge copy (2 paths, 1 entry)", m and set(m[0]["via"]) == {"mesh", "mesh-bridge"}, m)
check("1 bridge forwarded phone chat to server", any(b"Alpha-1 radio check" in d for d in server_got))

# 1b. protobuf chat from phone
_, x = dc.build_geochat("BRAVO-3", "ANDROID-bravo", "Bravo proto message")
pb = takproto.xml2proto(x.decode(), takproto.TAKProtoVer.MESH)
check("1b test packet really is protobuf", bytes(pb)[:3] == b"\xbf\x01\xbf")
phone_send(bytes(pb)); time.sleep(1.5)
m = [x for x in msgs() if x["text"] == "Bravo proto message"]
check("1b protobuf chat decoded and shown once", len(m) == 1 and m[0]["from"] == "BRAVO-3", m)

# 2. deck -> mesh + server
n_phone = len(phone_got)
code, res = post("Deck to all: test 1")
check("2 send accepted and sent", code == 200 and res["ok"] and res["status"] == "sent", res)
time.sleep(1.5)
pg = [d for d in phone_got[n_phone:] if b"Deck to all: test 1" in d]
check("2 phone on mesh received deck message", len(pg) == 1, len(pg))
check("2 server received deck message (via bridge)", any(b"Deck to all: test 1" in d for d in server_got))
mine = [x for x in msgs() if x["text"] == "Deck to all: test 1"]
check("2 shown once as mine, relay confirmed", len(mine) == 1 and mine[0]["mine"] and mine[0]["relayed"], mine)
ev = dc.parse_chat(pg[0]) if pg else None
check("2 phone can parse it as GeoChat from DECK in All Chat Rooms", ev and ev["from"] == "DECK" and ev["room"] == "All Chat Rooms" and ev["from_uid"] == "DECK-test", ev)

# 3. server user -> deck + mesh
fam = atak_chat("FOXTROT", "ANDROID-srv1", "HQ here, copy you")
n_phone = len(phone_got)
for c in conns: c.sendall(fam)
time.sleep(1.5)
m = [x for x in msgs() if x["text"] == "HQ here, copy you"]
check("3 server message shows on deck, tagged SERVER", len(m) == 1 and m[0]["src"] == "server" and m[0]["from"] == "FOXTROT", m)
check("3 return path also put it on the mesh for phones", any(b"HQ here, copy you" in d for d in phone_got[n_phone:]))
time.sleep(1)
m = [x for x in msgs() if x["text"] == "HQ here, copy you"]
check("3 not duplicated by the mesh echo", len(m) == 1, m)

# 4. security
check("4 GET with foreign Host refused", get("/api/info", host="evil.example:8090")[0] == 403)
check("4 POST without X-Deck refused", post("x", xdeck=False)[0] == 403)
check("4 POST with foreign Host refused", post("x", host="evil.example")[0] == 403)
check("4 empty message refused", post("   ")[0] == 400)
check("4 too long refused (1000 max)", post("a" * 1001)[0] == 400)
lt = '<b>&"x</b>'
code, res = post(lt); time.sleep(1)
pg = [d for d in phone_got if b"&lt;b&gt;" in d]
check("4 markup characters escaped in CoT and parsed back intact", code == 200 and pg and dc.parse_chat(pg[-1])["text"] == lt, (code, res))

# 5. rate limit (6 per 10 s; 2 used above)
codes = [post("burst %d" % i)[0] for i in range(6)]
check("5 rate limit kicks in (6 per 10 s)", codes.count(200) == 4 and codes.count(400) == 2, codes)

# 6. CLI send
r = subprocess.run([sys.executable, PKG + "/chat/deck_chat.py", "--config", W + "/chat.ini", "--send", "from the command line"],
                   capture_output=True, text=True, env=env)
time.sleep(1)
check("6 --send works", r.returncode == 0 and any(b"from the command line" in d for d in phone_got), r.stdout + r.stderr)


# 9. roster from position reports + chat senders
sa = b'<event version="2.0" uid="ANDROID-charlie" type="a-f-G-U-C" how="m-g" time="2026-10-19T03:00:00Z" start="2026-10-19T03:00:00Z" stale="2026-10-19T03:05:00Z"><point lat="1" lon="2" hae="0" ce="9" le="9"/><detail><contact callsign="CHARLIE-2" endpoint="*:-1:stcp"/></detail></event>'
s9 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s9.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1")); s9.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
s9.sendto(sa, ("239.2.3.1", 6969))
pbsa = takproto.xml2proto(sa.decode().replace("ANDROID-charlie","ANDROID-delta").replace("CHARLIE-2","DELTA-6"), takproto.TAKProtoVer.MESH)
s9.sendto(bytes(pbsa), ("239.2.3.1", 6969))
time.sleep(1.5)
code, info = get("/api/info")
names = [r["callsign"] for r in info["roster"]]
check("9 roster has SA callsign (XML)", "CHARLIE-2" in names, names)
check("9 roster has SA callsign (protobuf)", "DELTA-6" in names, names)
check("9 roster has chat senders incl. a server user", "ALPHA-1" in names and "FOXTROT" in names, names)
check("9 roster does not list the deck itself", "DECK" not in names, names)
check("9 SA listener joined", info["sa_listening"] is True, info["sa_listening"])

# 10. export
r = urllib.request.Request("http://127.0.0.1:8090/api/export", headers={"Host": "127.0.0.1:8090"})
with urllib.request.urlopen(r, timeout=3) as x:
    body = x.read().decode(); disp = x.headers.get("Content-Disposition", "")
check("10 export is a text attachment", "attachment" in disp and body.startswith("TAK MESH TOC CHAT LOG"), disp)
check("10 export has Zulu DTGs and messages", "Z " in body and "Alpha-1 radio check" in body and "FOXTROT: HQ here" in body, body[:300])

# 7. bridge down: mesh chat still arrives directly
bridge.terminate(); bridge.wait(5)
time.sleep(11)
code, info = get("/api/info")
check("7 info shows bridge down", info and info["bridge"]["running"] is False, info)
phone_send(atak_chat("ALPHA-1", "ANDROID-alpha", "bridge is down test"))
time.sleep(1.5)
m = [x for x in msgs() if x["text"] == "bridge is down test"]
check("7 mesh chat still received with bridge down", len(m) == 1 and m[0]["via"] == ["mesh"], m)

# 8. restart keeps history, no duplicates
before = len(msgs())
chat.terminate(); chat.wait(5)
chat = subprocess.Popen([sys.executable, PKG + "/chat/deck_chat.py", "--config", W + "/chat.ini"],
                        stdout=open(W + "/chat2.log", "w"), stderr=subprocess.STDOUT, env=env)
time.sleep(3)
after = msgs()
check("8 history reloaded after restart", len(after) >= before - 1 and any(x["text"] == "Alpha-1 radio check" for x in after), (before, len(after)))
ids = [x["id"] for x in after]
check("8 no duplicate ids after reload", len(ids) == len(set(ids)))

chat.terminate()
print("\n%d/%d passed" % (sum(r for _, r in results), len(results)))
sys.exit(0 if all(r for _, r in results) else 1)
