import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
import asyncio, socket, sys, configparser
sys.path.insert(0, PKG + "/dashboard")
import warnings; warnings.filterwarnings("ignore")
import tak_mesh_bridge as t
sys.path.insert(0, PKG + "/chat"); import deck_chat as dc
res=[]
def check(n,c,x=""): res.append(bool(c)); print(("PASS  " if c else "FAIL  ")+n+("" if c else "  "+str(x)))
def dm_to_deck():
    _, x = dc.build_geochat("FOXTROT", "SRV1", "hi deck, just you")
    return x.replace(b'chatroom="All Chat Rooms" id="All Chat Rooms"', b'chatroom="DECK" id="DECK-test"').replace(b'uid1="All Chat Rooms"', b'uid1="DECK-test"').replace(b"</detail>", b'<marti><dest callsign="DECK"/></marti></detail>')
async def main():
    mesh = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); mesh.bind(("127.0.0.1", 0)); mesh.settimeout(0.5)
    copy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); copy.bind(("127.0.0.1", 0)); copy.settimeout(0.5)
    cfg = configparser.ConfigParser(); cfg.read_dict({"bridge": {"RETURN_PATH": "1", "BRIDGE_UID": "DECK-test",
        "RETURN_CHAT_URL": "udp://127.0.0.1:%d" % mesh.getsockname()[1], "LOCAL_CHAT_COPY": "udp://127.0.0.1:%d" % copy.getsockname()[1]}})
    t.LocalCopy.setup(cfg["bridge"])
    q = asyncio.Queue(); w = t.ServerToMesh(cfg["bridge"], q); task = asyncio.create_task(w.run())
    await q.put(dm_to_deck())
    await q.put(dc.build_geochat("FOXTROT", "SRV1", "hello everyone")[1])
    await asyncio.sleep(0.3)
    def drain(s):
        out = []
        try:
            while True: out.append(s.recvfrom(65535)[0])
        except socket.timeout: return out
    m, c = drain(mesh), drain(copy)
    check("DM to the deck is NOT put on the mesh", not any(b"hi deck, just you" in x for x in m), m)
    check("DM to the deck still reaches Deck Chat (local copy)", any(b"hi deck, just you" in x for x in c))
    check("All Chat Rooms message still goes to the mesh", any(b"hello everyone" in x for x in m))
    task.cancel()
asyncio.run(main())
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
