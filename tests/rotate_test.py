"""Dashboard rotate + full-screen chips, in Chromium against the REAL dashboard (mock data, fake player page):
  chip shown, each tap +90 (90/180/270/0), the player frame is turned and sized to fill, remembered per stream
  after a reload, 2-up screens are independent, clicking the chip doesn't change the selected screen, no page errors.
Needs: playwright + Chromium. Usage: python3 rotate_test.py"""
import asyncio, os, socket, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
from playwright.async_api import async_playwright

res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:300]), flush=True)

s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
srv = subprocess.Popen([sys.executable, PKG + "/dashboard/dashboard_server.py", "--mock", "hop", "--port", str(PORT), "--listen", "127.0.0.1"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
PAGE = "<!doctype html><body style='margin:0;background:#000'><div style='width:100%;height:100%;background:#468'></div></body>"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=PAGE) if ":8889/" in r.request.url or ":8888/" in r.request.url else r.continue_())
        for _ in range(40):
            try:
                await pg.goto("http://127.0.0.1:%d/" % PORT); break
            except Exception:
                await asyncio.sleep(0.25)
        await pg.wait_for_timeout(2000)
        await pg.evaluate("() => { try { localStorage.clear(); } catch (e) {} }")
        await pg.reload(); await pg.wait_for_timeout(2000)
        check("no chip before a stream plays", await pg.query_selector("#v-players .rot[data-rot]") is None)
        await pg.click("#b-play"); await pg.wait_for_timeout(800)
        chip = "#v-players .player[data-i='0'] .rot[data-rot]"
        check("chip shows 0 degrees on a new stream", (await pg.inner_text(chip)).strip() == "⟳ 0°", await pg.inner_text(chip))
        async def frame():
            return await pg.evaluate("""() => { const p = document.querySelector("#v-players .player[data-i='0']"), f = p.querySelector('iframe'), r = f.getBoundingClientRect(), q = p.getBoundingClientRect();
                return {t: f.style.transform, w: f.offsetWidth, h: f.offsetHeight, pw: p.clientWidth, ph: p.clientHeight, cx: r.left + r.width / 2 - (q.left + q.width / 2), cy: r.top + r.height / 2 - (q.top + q.height / 2)}; }""")
        f0 = await frame()
        check("not turned at 0", f0["t"] == "", f0)
        for deg in (90, 180, 270, 0):
            await pg.click(chip); await pg.wait_for_timeout(200)
            f = await frame()
            txt = (await pg.inner_text(chip)).strip()
            ok = txt == "⟳ %d°" % deg and (("rotate(%ddeg)" % deg) in f["t"] if deg else f["t"] == "")
            if deg in (90, 270):
                ok = ok and f["w"] == f["ph"] and f["h"] == f["pw"]          # sides swapped: fills the player
            if deg:
                ok = ok and abs(f["cx"]) < 2 and abs(f["cy"]) < 2              # centred
            check("tap -> %d degrees (turned, sized, centred)" % deg, ok, (txt, f))
        await pg.click(chip); await pg.wait_for_timeout(200)
        await pg.reload(); await pg.wait_for_timeout(2000); await pg.click("#b-play"); await pg.wait_for_timeout(600)
        check("remembered per stream after a reload (90)", (await pg.inner_text(chip)).strip() == "⟳ 90°")
        check("chip lit when turned", await pg.get_attribute(chip, "aria-pressed") == "true")
        await pg.set_viewport_size({"width": 1000, "height": 700}); await pg.wait_for_timeout(400)
        f = await frame()
        check("still fills after the window is resized", f["w"] == f["ph"] and f["h"] == f["pw"], f)
        await pg.set_viewport_size({"width": 1280, "height": 720}); await pg.wait_for_timeout(300)
        # full screen keeps the rotation (v4.6): the whole player goes full screen, not the video inside it
        await pg.click("#v-players .player[data-i='0'] .fs"); await pg.wait_for_timeout(700)
        fe = await pg.evaluate("() => !!document.fullscreenElement && document.fullscreenElement.matches('#v-players .player')")
        check("full-screen chip puts the whole player in full screen", fe)
        f = await frame()
        check("in full screen the 90-degree stream fills the screen", "rotate(90deg)" in f["t"] and f["w"] == f["ph"] and f["h"] == f["pw"] and f["pw"] >= 1270, f)
        await pg.click("#v-players .player[data-i='0'] .fs"); await pg.wait_for_timeout(700)
        check("tapping it again leaves full screen", not await pg.evaluate("() => !!document.fullscreenElement"))
        allow = await pg.get_attribute("#v-players iframe", "allow")
        check("the player's own full screen (which loses rotation) is off", "fullscreen" not in (allow or ""), allow)
        await pg.click("#m-two"); await pg.wait_for_timeout(500)
        # make screen 2 play too (it picks the other stream) and check it is independent
        await pg.click("#v-players .player[data-i='1']"); await pg.wait_for_timeout(200)
        await pg.click("#b-play"); await pg.wait_for_timeout(600)
        c2 = "#v-players .player[data-i='1'] .rot[data-rot]"
        have2 = await pg.query_selector(c2)
        if have2:
            p0, p1 = await pg.evaluate("() => [...document.querySelectorAll('#v-players .player .cap')].map(e => e.textContent)"), None
            same = len(p0) == 2 and p0[0] == p0[1]
            want = "⟳ 90°" if same else "⟳ 0°"
            check("2-up: second screen has its own chip", (await pg.inner_text(c2)).strip() == want, (p0, await pg.inner_text(c2)))
            await pg.click(chip); await pg.wait_for_timeout(200)
            act = await pg.evaluate("() => document.querySelector(\"#v-players .player.active\").getAttribute('data-i')")
            check("tapping a chip doesn't change the selected screen", act == "1", act)
        else:
            check("2-up: second screen plays", False, "no chip on screen 2")
        check("no page errors", not errors, errors)
        await b.close()
try:
    asyncio.run(main())
finally:
    srv.terminate()
print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
