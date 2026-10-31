import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge"]
"""pytak_patch_test.py: the bridge replaces two PyTAK methods (RXWorker.readcot, TXWorker.send_data) because PyTAK
has no hook for what they add: big server events, a closed socket, socket tuning and the outbox. This checks every
upstream detail those replacements rely on, so an upgraded PyTAK fails here, loudly, instead of in the field.
Run with python3 -S (as run_all.sh does): only the bundled PyTAK is used."""
import asyncio, inspect, sys, warnings
warnings.filterwarnings("ignore")
import pytak
import tak_mesh_bridge as B

res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:300]))

# 1. the bundled PyTAK, the version the patches were written against
check("PyTAK comes from vendor/", _o.path.join(PKG, "vendor") in _o.path.abspath(pytak.__file__), pytak.__file__)
ver = B.pytak_version()
check("bundled PyTAK is 7.6.1 (re-check this file's assumptions before changing it)", str(ver).startswith("7.6.1"), ver)

# 2. the patches are installed, and they wrap the originals
check("RXWorker.readcot is the bridge's version", pytak.RXWorker.readcot is B._readcot_big)
check("TXWorker.send_data is the bridge's version", pytak.TXWorker.send_data is B._send_tuned)
orig_read, orig_send = B._pytak_readcot, B._pytak_send
check("the originals were kept to call through to", orig_read is not B._readcot_big and orig_send is not B._send_tuned)

# 3. what the replacements assume about the originals
check("original readcot and send_data are coroutines", inspect.iscoroutinefunction(orig_read) and inspect.iscoroutinefunction(orig_send))
check("original signatures: readcot(self), send_data(self, data)",
      list(inspect.signature(orig_read).parameters) == ["self"] and list(inspect.signature(orig_send).parameters) == ["self", "data"])
src = inspect.getsource(orig_read)
check("original readcot reads with readuntil(b'</event>') (where the 64 KB limit bites)", "readuntil" in src and "</event>" in src)
check("original readcot returns None on a closed stream (IncompleteReadError)", "IncompleteReadError" in src and "return None" in src)
check("asyncio.StreamReader still has the private _limit the bridge raises", hasattr(asyncio.StreamReader(limit=10), "_limit"))
check("TXWorker keeps its writer as self.writer", "self.writer" in inspect.getsource(pytak.TXWorker.__init__))

# 4. behaviour, with a real asyncio.StreamReader
class FakeRX:
    use_protobuf = False
    def __init__(self, reader): self.reader = reader

async def read_all(chunks, eof=True, n=3):
    r = asyncio.StreamReader()                          # asyncio's default 64 KB limit, as PyTAK gets it
    for c in chunks:
        r.feed_data(c)
    if eof:
        r.feed_eof()
    w, out = FakeRX(r), []
    for _ in range(n):
        try:
            out.append(await B._readcot_big(w))
        except ConnectionResetError as exc:
            out.append(("closed", str(exc)))
            break
    return out

big = b"<event uid='big'>" + b"x" * 200_000 + b"</event>"
small = b"<event uid='small'/></event>"
out = asyncio.run(read_all([big, small], eof=False, n=2))
check("a 200 KB server event (over asyncio's 64 KB) is read whole, not fatal", out and out[0] == big, [len(x or b"") for x in out])
check("...and the next event still arrives", len(out) > 1 and out[1] == small)

huge = b"<event uid='huge'>" + b"y" * (B.SERVER_READ_LIMIT + 10) + b"</event>"
out = asyncio.run(read_all([huge, small], eof=False, n=2))
check("an event over the 4 MB cap is skipped (None), the connection kept", out[:1] == [None], [type(x) for x in out])
check("...and the next event still arrives", len(out) > 1 and out[1] == small, out[1:2])

B.Link.status_writer = None
out = asyncio.run(read_all([small], eof=True, n=3))
check("a closed server connection ends the bridge (systemd reconnects) instead of spinning", out[-1][0] == "closed" if out else False, out)

class FakeTX:
    def __init__(self): self.sent = []
    async def send_data(self, data): self.sent.append(data)
sent_seen = []
real_sent, real_tune, real_orig = B.Outbox.sent, B.tune_server_socket, B._pytak_send
B.Outbox.sent = staticmethod(lambda d: sent_seen.append(d))
B.tune_server_socket = lambda w: sent_seen.append("tuned")
w = FakeTX(); w.writer = None
B._pytak_send = FakeTX.send_data
asyncio.run(B._send_tuned(w, b"a")); asyncio.run(B._send_tuned(w, b"b"))
B.Outbox.sent, B.tune_server_socket, B._pytak_send = real_sent, real_tune, real_orig
check("send: the socket is tuned once, every write reaches PyTAK and is reported to the outbox",
      sent_seen == ["tuned", b"a", b"b"] and w.sent == [b"a", b"b"], (sent_seen, w.sent))

print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
