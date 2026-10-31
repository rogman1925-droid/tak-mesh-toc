import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/dashboard", PKG + "/tools"]
"""fresh_setup_test.py: a deck whose TAK server isn't set up yet shows ONE thing to do (Ops SETUP page and
`meshtoc check` use the same rule), and a set-up deck shows the normal checks."""
import configparser, importlib.util, shutil, sys, tempfile
import dashboard_server as ds

res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:300]))

W = tempfile.mkdtemp(prefix="mmb_fresh_")
def ini(bridge):
    p = W + "/deck%d.ini" % len(_o.listdir(W))
    open(p, "w").write("[bridge]\n" + "".join("%s = %s\n" % kv for kv in bridge.items()))
    return p
example = ini({"COT_URL": "tls://tak.example.net:8089"})
nocert = ini({"COT_URL": "tls://10.0.0.5:8089"})
ready = ini({"COT_URL": "tls://10.0.0.5:8089", "PYTAK_TLS_CLIENT_CERT": "~/certs/deck.pem"})
plain = ini({"COT_URL": "tcp://10.0.0.5:8087"})

spec = importlib.util.spec_from_file_location("meshtoc", PKG + "/tools/manet_mesh_bridge.py")
mt = importlib.util.module_from_spec(spec); spec.loader.exec_module(mt)
def cp_of(p):
    c = configparser.ConfigParser(interpolation=None, strict=False); c.read(p); return c
for name, p, want in (("example COT_URL", example, "COT_URL is still the example"), ("tls:// without a certificate", nocert, "no certificate yet"),
                      ("tls:// with a certificate", ready, None), ("plain tcp://", plain, None)):
    a = ds.server_unset(dict(ds.DEFAULTS, BRIDGE_INI=p)); b = mt.server_unset(cp_of(p))
    check("%s: Ops and meshtoc agree (%r)" % (name, want), a == want and b == want, (a, b))

class Stub:
    errors = []
    def can_restart(self): return True
snap = ds.mock_status("down", ds.Events(W + "/ev.jsonl"))
items = ds.setup_checks(dict(ds.DEFAULTS, BRIDGE_UNIT="manet-bridge", BRIDGE_INI=example), snap, Stub(), Stub())
titles = [i["title"] for i in items]
todo = [i for i in items if i["title"] == "TAK server not set up yet"]
check("fresh deck, Ops SETUP: one 'TAK server not set up yet' item, amber, with the fix", len(todo) == 1 and todo[0]["level"] == "warn" and "COT_URL" in todo[0]["fix"], todo)
check("fresh deck, Ops SETUP: no red bridge/TLS items on top of it",
      not any(t in titles for t in ("Bridge service running", "Bridge status file", "Server TLS link")), titles)
check("fresh deck, Ops SETUP: the bridge shows as waiting (grey), not OK", any(i["title"] == "Bridge" and i["level"] == "info" and not i["ok"] for i in items))
items = ds.setup_checks(dict(ds.DEFAULTS, BRIDGE_UNIT="manet-bridge", BRIDGE_INI=ready), snap, Stub(), Stub())
titles = [i["title"] for i in items]
check("set-up deck, Ops SETUP: the normal bridge and TLS checks are back", all(t in titles for t in ("Bridge service running", "Server TLS link")) and "TAK server not set up yet" not in titles, titles)

shutil.rmtree(W, ignore_errors=True)
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
