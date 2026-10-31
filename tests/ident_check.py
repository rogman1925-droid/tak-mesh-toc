import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
import sys, configparser, time
sys.path.insert(0, PKG + "/dashboard")
import warnings; warnings.filterwarnings("ignore")
import tak_mesh_bridge as b
import xml.etree.ElementTree as ET
res=[]
def check(n,c,x=""): res.append(bool(c)); print(("PASS  " if c else "FAIL  ")+n+("" if c else "  "+str(x)))
cfg = configparser.ConfigParser(); cfg.read_dict({"bridge": {}}); c = cfg["bridge"]
xml = b.bridge_identity(c)
print(xml.decode())
# --- the exact checks OpenTAKServer 1.7.13 EudHandler.handle_cot / parse_device_info applies
event = ET.fromstring(xml.decode())
check("OTS parses it as an <event>", event is not None and event.tag == "event")
check("not treated as a ping (pong() only answers t-x-c-t)", event.get("type") != "t-x-c-t")
contact = event.find(".//contact"); takv = event.find(".//takv")
uid = event.get("uid")
check("has <contact> -> OTS treats it as an EUD", contact is not None)
check("uid doesn't end with 'ping'", uid and not uid.endswith("ping"))
check("has callsign -> OTS declares the delivery queue", "callsign" in contact.attrib)
platform = takv.get("platform")
check("platform isn't one OTS skips (OpenTAK ICU / Meshtastic / DMRCOT)", platform not in ("OpenTAK ICU", "Meshtastic", "DMRCOT"), platform)
check("default uid DECK-<hostname>, callsign DECK", uid.startswith("DECK-") and contact.get("callsign") == "DECK", uid)
check("sent already stale by default (no DECK marker on maps)", event.get("stale") == event.get("start"))
cfg2 = configparser.ConfigParser(); cfg2.read_dict({"bridge": {"BRIDGE_UID": "DECK-test", "BRIDGE_CALLSIGN": "RAVEN", "BRIDGE_VISIBLE": "1", "IDENTIFY_INTERVAL": "60", "BRIDGE_LAT": "38.8", "BRIDGE_LON": "-104.8"}})
e2 = ET.fromstring(b.bridge_identity(cfg2["bridge"], now=1000.0).decode())
check("custom uid/callsign/position honored", e2.get("uid") == "DECK-test" and e2.find(".//contact").get("callsign") == "RAVEN" and e2.find("point").get("lat") == "38.8")
check("visible mode: stale = 2 x interval later", e2.get("stale").startswith("1970-01-01T00:18:40") and e2.get("start").startswith("1970-01-01T00:16:40"), (e2.get("start"), e2.get("stale")))
check("bridge's own UID regex reads it (for logging)", b.UID_RE.search(xml).group(1).decode() == uid)
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
