import os as _o, sys as _s  # paths relative to this package
PKG = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_s.path[:0] = [PKG + "/vendor", PKG + "/common", PKG + "/bridge", PKG + "/chat", PKG + "/dashboard"]
"""Keyboard/UI test for Deck Chat v1.1 with real bridge + fake server + fake phones; also screenshots."""
import os, socket, subprocess, sys, threading, time, json, urllib.request
sys.path.insert(0, PKG + "/chat")
import deck_chat as dc
from playwright.sync_api import sync_playwright

W = "/tmp/mmb_chat/ui"; os.makedirs(W, exist_ok=True)
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:300]))

srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 0)); srv.listen(5)
sport = srv.getsockname()[1]; conns = []; server_got = []
def serve():
    while True:
        try: c, _ = srv.accept()
        except OSError: return
        conns.append(c)
        threading.Thread(target=lambda c=c: [server_got.append(d) for d in iter(lambda: c.recv(65536), b"")], daemon=True).start()
threading.Thread(target=serve, daemon=True).start()
def mcast(data, grp):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1")); s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.sendto(data, grp); s.close()
def phone(cs, uid, text): mcast(dc.build_geochat(cs, uid, text)[1], ("224.10.10.1", 17012))
def sa(cs, uid):
    mcast(('<event version="2.0" uid="%s" type="a-f-G-U-C" how="m-g" time="2026-10-19T03:00:00Z" start="2026-10-19T03:00:00Z" stale="2026-10-19T03:05:00Z"><point lat="1" lon="2" hae="0" ce="9" le="9"/><detail><contact callsign="%s"/></detail></event>' % (uid, cs)).encode(), ("239.2.3.1", 6969))

status = W + "/st.json"; hist = W + "/hist.jsonl"
for f in (status, hist):
    if os.path.exists(f): os.remove(f)
open(W + "/b.ini", "w").write("[bridge]\nCOT_URL = tcp://127.0.0.1:%d\nPYTAK_MULTICAST_LOCAL_ADDR = 127.0.0.1\nSTATUS_FILE = %s\nRETURN_PATH = 1\nLOCAL_CHAT_COPY = udp://127.0.0.1:17013\n" % (sport, status))
open(W + "/c.ini", "w").write("[chat]\nUID = DECK-ui\nMESH_LOCAL_ADDR = 127.0.0.1\nHISTORY_FILE = %s\nBRIDGE_STATUS_FILE = %s\nMAX_SENDS_PER_10S = 20\n" % (hist, status))
env = dict(os.environ, PYTHONWARNINGS="ignore")
bp = subprocess.Popen([sys.executable, "-S", PKG + "/bridge/tak_mesh_bridge.py", W + "/b.ini"], stdout=open(W + "/b.log", "w"), stderr=subprocess.STDOUT, env=env)
cp = subprocess.Popen([sys.executable, PKG + "/chat/deck_chat.py", "--config", W + "/c.ini"], stdout=open(W + "/c.log", "w"), stderr=subprocess.STDOUT, env=env)
time.sleep(4)
for cs, uid in (("ALPHA-1", "A1"), ("BRAVO-3", "B3"), ("MEDIC-1", "M1"), ("CHARLIE-2", "C2")): sa(cs, uid)
phone("ALPHA-1", "A1", "Alpha-1 radio check, how copy")
time.sleep(0.4)
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 720})
        pg.goto("http://127.0.0.1:8090/"); pg.wait_for_timeout(2000)
        check("input focused on load", pg.evaluate("document.activeElement.id") == "text")
        # type + Enter
        pg.keyboard.type("Loud and clear, Alpha-1"); pg.keyboard.press("Enter"); pg.wait_for_timeout(2500)
        last = pg.locator(".m.src-me").last
        check("Enter sends; shows as mine", "Loud and clear, Alpha-1" in last.inner_text())
        check("double tick once relayed through the bridge", "✓✓" in last.locator(".tick").inner_text(), last.inner_html())
        check("server got the deck message", any(b"Loud and clear, Alpha-1" in d for d in server_got))
        # F-key quick phrase
        pg.keyboard.press("F3");
        check("F3 puts quick phrase 3 in the box", pg.input_value("#text") == "Roger", pg.input_value("#text"))
        pg.keyboard.press("Escape")
        check("Esc clears the box", pg.input_value("#text") == "")
        # Up recall
        pg.keyboard.press("ArrowUp")
        check("Up recalls last sent message", pg.input_value("#text") == "Loud and clear, Alpha-1", pg.input_value("#text"))
        pg.keyboard.press("ArrowDown")
        check("Down past newest clears", pg.input_value("#text") == "")
        # /flash
        pg.keyboard.type("/flash contact north ridge"); pg.keyboard.press("Enter"); pg.wait_for_timeout(1500)
        fl = pg.locator(".m.p-flash").last
        check("/flash sends with FLASH precedence and red row", "FLASH" in fl.inner_text() and "contact north ridge" in fl.inner_text())
        # incoming server message + precedence from phone
        for c in conns: c.sendall(dc.build_geochat("FOXTROT", "SRV1", "Got your stream, looks good from home")[1])
        phone("MEDIC-1", "M1", "IMMEDIATE: 1 urgent at the trailhead")
        pg.wait_for_timeout(2500)
        st = json.load(open(status)); check("server message shows with server bar", pg.locator(".m.src-server").count() == 1, ("conns", len(conns), "bridge server rx", st["server"]))
        check("phone IMMEDIATE highlighted", pg.locator(".m.p-immediate").count() == 1)
        # tab completion + /z
        pg.keyboard.type("/sa"); pg.keyboard.press("Tab")
        check("Tab completes /sa -> /salute", pg.input_value("#text") == "/salute ", pg.input_value("#text"))
        pg.keyboard.press("Escape")
        t_before = pg.locator(".m .t").first.inner_text()
        pg.keyboard.type("/z"); pg.keyboard.press("Enter"); pg.wait_for_timeout(300)
        t_after = pg.locator(".m .t").first.inner_text()
        check("/z switches to Zulu times", t_after.endswith("Z") and not t_before.endswith("Z"), (t_before, t_after))
        pg.keyboard.type("/z"); pg.keyboard.press("Enter")
        # roster
        pg.wait_for_timeout(5500)
        names = pg.locator("#rlist .name").all_inner_texts()
        check("roster shows SA + chat senders", {"ALPHA-1", "BRAVO-3", "MEDIC-1", "CHARLIE-2", "FOXTROT"} <= set(names), names)
        pg.screenshot(path=W + "/land.png")
        # 9-line form
        pg.keyboard.type("/9line"); pg.keyboard.press("Enter"); pg.wait_for_timeout(300)
        check("/9line opens the form, first field focused", pg.is_visible("#modal") and pg.evaluate("document.activeElement.id") == "ff0")
        for v in ["13S DD 12345 67890", "MEDIC-1 / 146.520", "1A", "A", "1L", "N", "C", "B", "flat open field"]:
            pg.keyboard.type(v); pg.keyboard.press("Enter")
        check("Enter walks through all 9 lines to SEND", pg.evaluate("document.activeElement.id") == "fsend")
        pg.select_option("#fprec", "IMMEDIATE")
        pg.screenshot(path=W + "/form.png")
        pg.keyboard.press("Control+Enter"); pg.wait_for_timeout(1500)
        msg = pg.locator(".m.src-me").last.inner_text()
        check("Ctrl+Enter sends the 9-line as one formatted message", "9-LINE MEDEVAC" in msg and "1: 13S DD 12345 67890" in msg and "9: flat open field" in msg and pg.is_hidden("#modal"), msg)
        check("9-line reached the server", any(b"9-LINE MEDEVAC" in d for d in server_got))
        # Esc closes an empty form; empty form refuses to send
        pg.keyboard.type("/lace"); pg.keyboard.press("Enter"); pg.wait_for_timeout(200)
        pg.keyboard.press("Control+Enter"); pg.wait_for_timeout(300)
        check("empty form refuses to send", pg.is_visible("#modal") and "at least one" in pg.inner_text("#ferr"))
        pg.keyboard.press("Escape")
        check("Esc closes the form", pg.is_hidden("#modal"))
        # unknown command + help
        pg.keyboard.type("/bogus"); pg.keyboard.press("Enter")
        check("unknown command explained", "Unknown command" in pg.inner_text("#hint"))
        pg.keyboard.press("Escape"); pg.keyboard.type("/help"); pg.keyboard.press("Enter")
        check("/help lists keys and commands", "/9line" in pg.inner_text("#hint") and "F1-F9" in pg.inner_text("#hint"))
        pg.keyboard.press("Escape")
        # scrolled up -> new message bar
        for i in range(12): phone("BRAVO-3", "B3", "filler %d" % i); time.sleep(0.05)
        pg.wait_for_timeout(2000)
        pg.evaluate("document.getElementById('log').scrollTop = 0"); pg.wait_for_timeout(300)
        phone("ALPHA-1", "A1", "Set at the ridge")
        pg.wait_for_timeout(2000)
        check("scrolled up: NEW bar appears", pg.is_visible("#newbar") and "NEW" in pg.inner_text("#newbar"))
        pg.keyboard.press("End"); pg.wait_for_timeout(300)
        check("End jumps to newest and hides the bar", pg.is_hidden("#newbar"))
        # no sound anywhere
        html = open(PKG + "/chat/index.html").read()
        check("no sound code or button", "AudioContext" not in html and "SOUND" not in html)
        # portrait
        pp = b.new_page(viewport={"width": 720, "height": 1280}); pp.goto("http://127.0.0.1:8090/"); pp.wait_for_timeout(2500)
        check("portrait hides roster panel by default", pp.is_hidden("#roster"))
        pp.keyboard.type("/roster"); pp.keyboard.press("Enter"); pp.wait_for_timeout(300)
        check("/roster shows it in portrait", pp.is_visible("#roster"))
        pp.screenshot(path=W + "/port.png")
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.reload(); pg.wait_for_timeout(1500)
        check("no JS errors on reload", not errs, errs)
        b.close()
finally:
    cp.terminate(); bp.terminate()
print("%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
