import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
"""iTAK sends direct messages by UDP to the endpoint in a contact's position report, even when it says tcp,
and sends each one twice. Checks that the bridge's DM relay and Deck Chat take UDP DMs (XML and TAK protobuf),
deliver each once, keep the TCP path working, and survive junk.
Usage: python3 -S itak_udp_dm_test.py"""
import asyncio, configparser, json, os, socket, subprocess, sys, time, urllib.request
import warnings; warnings.filterwarnings("ignore")
import tak_mesh_bridge as t
import deck_chat as dc
import takproto

res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:400]), flush=True)

def free_port(kind):
    s = socket.socket(socket.AF_INET, kind); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def to_proto(xml):
    return bytes(takproto.xml2proto(xml.decode(), takproto.TAKProtoVer.MESH))

def udp(port, data):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(data, ("127.0.0.1", port)); s.close()

# ---------------- the bridge's DM relay (mesh phone -> server person)
async def relay_part():
    port = free_port(socket.SOCK_STREAM)
    cfg = configparser.ConfigParser(); cfg.read_dict({"bridge": {"PYTAK_MULTICAST_LOCAL_ADDR": "127.0.0.1", "DM_RELAY_PORT": str(port)}})
    t.Outbox.path = None
    t.Peers.saw_server(b'<event uid="F28"><detail><contact callsign="HQ 28" endpoint="*:-1:stcp"/></detail></event>')
    q = asyncio.Queue(); r = t.DMRelay(cfg["bridge"], q); task = asyncio.create_task(r.run())
    await asyncio.sleep(0.5)
    async def drain(wait=0.6):
        await asyncio.sleep(wait); out = []
        while not q.empty(): out.append(q.get_nowait())
        return out

    _, x = dc.build_geochat("IPHONE", "IP1", "udp dm to server", to_uid="F28", to_callsign="HQ 28")
    udp(port, x); await asyncio.sleep(2.0); udp(port, x)          # iTAK: same DM twice, 2 s apart
    got = await drain()
    check("UDP DM (iTAK) to a server person is relayed", any(b"udp dm to server" in g for g in got), got)
    check("...exactly once although iTAK sent it twice", sum(b"udp dm to server" in g for g in got) == 1, len(got))
    check("...addressed to HQ 28 only (<dest callsign>)", got and b'<dest callsign="HQ 28"/>' in got[0], got[:1])

    _, x = dc.build_geochat("IPHONE", "IP1", "proto dm to server", to_uid="F28", to_callsign="HQ 28")
    udp(port, to_proto(x)); got = await drain()
    check("UDP DM in TAK protobuf is converted and relayed once", sum(b"proto dm to server" in g for g in got) == 1, got)

    _, x = dc.build_geochat("ATAKPHONE", "AP1", "tcp dm still works", to_uid="F28", to_callsign="HQ 28")
    s = socket.create_connection(("127.0.0.1", port), timeout=3); s.sendall(x); s.close(); got = await drain()
    check("TCP DM (ATAK) still relayed once", sum(b"tcp dm still works" in g for g in got) == 1, got)
    s = socket.create_connection(("127.0.0.1", port), timeout=3); s.sendall(x); s.close(); got = await drain()
    check("...the same message again over TCP is not relayed twice", not got, got)

    _, x = dc.build_geochat("IPHONE", "IP1", "nobody", to_uid="NOPE", to_callsign="NOBODY")
    udp(port, x); got = await drain()
    check("UDP DM to an unknown person is dropped", not got, got)
    udp(port, b"\xbf\x01\xbf garbage"); udp(port, b"<event"); udp(port, b"x" * 2000)
    udp(port, b'<event uid="p" type="a-f-G-U-C"><detail/></event>'); got = await drain()
    check("junk and non-chat datagrams are ignored", not got, got)
    _, x = dc.build_geochat("IPHONE", "IP1", "still alive", to_uid="F28", to_callsign="HQ 28")
    udp(port, x); got = await drain()
    check("relay still works after junk", sum(b"still alive" in g for g in got) == 1, got)
    task.cancel()

asyncio.run(relay_part())

# ---------------- server -> mesh: iTAK drops a direct DM that still carries the server's <marti><dest>
async def down_part():
    _, x = dc.build_geochat("HQ 28", "F28", "server dm to itak", to_uid="IP1", to_callsign="IPHONE", via_server=True)
    x = x.replace(b"</detail>", b'<__serverdestination destinations="1.2.3.4:4242:tcp:F28"/><marti><dest callsign="IPHONE"/></marti></detail>')
    check("(test setup) the server DM carries <marti><dest>", b"<marti><dest" in x, x[-200:])
    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); r.bind(("127.0.0.1", 0)); r.settimeout(2)
    err = await t.deliver_to_mesh({"ip": "127.0.0.1", "port": r.getsockname()[1], "proto": "udp"}, x)
    got = r.recvfrom(65536)[0] if not err else b""
    check("server DM handed to a mesh phone arrives", b"server dm to itak" in got, err or got[-200:])
    check("...without <marti> (iTAK drops DMs that keep it)", got and b"<marti" not in got, got[-300:])
    check("...everything else unchanged", got == x.replace(b'<marti><dest callsign="IPHONE"/></marti>', b""), got[-300:])
    f = t.strip_marti
    check("strip_marti: self-closing, spaced, and several blocks; <martian> kept",
          f(b'<d><marti/><martian/><marti >y</marti><marti><dest uid="a"/></marti></d>') == b"<d><martian/></d>")
    check("strip_marti: unclosed <marti> left as is", f(b"<d><marti><dest/>") == b"<d><marti><dest/>")
    t0 = time.time(); f(b"<marti>" * 50000); f(b"<marti" * 50000); f(b"<marti/" * 50000)
    check("strip_marti: worst-case input stays fast", time.time() - t0 < 1.0, time.time() - t0)

asyncio.run(down_part())

# ---------------- Deck Chat (mesh phone -> the deck itself)
W = "/tmp/mmb_itak"; os.makedirs(W, exist_ok=True)
dm_port, web = free_port(socket.SOCK_STREAM), free_port(socket.SOCK_STREAM)
hist = W + "/hist.jsonl"
if os.path.exists(hist): os.remove(hist)
open(W + "/c.ini", "w").write("[chat]\nUID = DECK-test\nMESH_LOCAL_ADDR = 127.0.0.1\nHISTORY_FILE = %s\nDM_PORT = %d\nLISTEN = 127.0.0.1:%d\n"
                              "BRIDGE_COPY_LISTEN = 127.0.0.1:%d\nSA_SEND = 0\nSA_GROUP = off\n" % (hist, dm_port, web, free_port(socket.SOCK_DGRAM)))
p = subprocess.Popen([sys.executable, PKG + "/chat/deck_chat.py", "--config", W + "/c.ini"], stdout=open(W + "/c.log", "w"),
                     stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONWARNINGS="ignore"))
def msgs():
    rq = urllib.request.Request("http://127.0.0.1:%d/api/messages?after=0" % web, headers={"Host": "127.0.0.1:%d" % web})
    return [m["text"] for m in json.load(urllib.request.urlopen(rq, timeout=3))["messages"]]
def wait(cond, t=6.0):
    end = time.time() + t
    while time.time() < end:
        try:
            if cond(): return True
        except Exception: pass
        time.sleep(0.2)
    return False
def dm_to_deck(text):
    _, x = dc.build_geochat("IPHONE", "IP1", text)
    return x.replace(b'chatroom="All Chat Rooms" id="All Chat Rooms"', b'chatroom="DECK" id="DECK-test"').replace(
        b'uid1="All Chat Rooms"', b'uid1="DECK-test"').replace(b"</detail>", b'<marti><dest callsign="DECK"/></marti></detail>')
try:
    check("Deck Chat started", wait(lambda: msgs() is not None, 10), open(W + "/c.log").read()[-400:])
    time.sleep(1)
    x = dm_to_deck("udp dm to deck"); udp(dm_port, x); time.sleep(1.0); udp(dm_port, x)
    check("UDP DM (iTAK) to the deck shows in Deck Chat", wait(lambda: "udp dm to deck" in msgs()), msgs())
    time.sleep(1)
    check("...once although sent twice", msgs().count("udp dm to deck") == 1, msgs())
    udp(dm_port, to_proto(dm_to_deck("proto dm to deck")))
    check("UDP DM in TAK protobuf shows in Deck Chat", wait(lambda: "proto dm to deck" in msgs()), msgs())
    s = socket.create_connection(("127.0.0.1", dm_port), timeout=3); s.sendall(dm_to_deck("tcp dm to deck")); s.close()
    check("TCP DM (ATAK) to the deck still works", wait(lambda: "tcp dm to deck" in msgs()), msgs())
    udp(dm_port, b"\xbf\x01\xbf junk"); udp(dm_port, b"<event no end"); udp(dm_port, dm_to_deck("after junk"))
    check("Deck Chat ignores junk datagrams and keeps working", wait(lambda: "after junk" in msgs()), msgs())
    log = open(W + "/c.log").read()
    check("log says the UDP DM port is listening", "(udp, for iTAK)" in log, log[-400:])
    check("no tracebacks in Deck Chat's log", "Traceback" not in log, log[-600:])
finally:
    p.terminate(); p.wait(5)
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
