"""Worst-case timing for every compiled regex in the suite: for each pattern, take a typical matching string, cut it at
every point, repeat that fragment to ~63 KB (the largest mesh packet) and time one search. Usage: regex_worst.py [package dir]"""
import os, re, sys, time, importlib
P = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path[:0] = [P + "/vendor", P + "/common", P + "/bridge", P + "/chat", P + "/dashboard"]
import warnings; warnings.filterwarnings("ignore")
mods = [importlib.import_module(m) for m in ("tak_mesh_bridge", "deck_chat", "deckcommon", "dashboard_server")]
SAMPLE = {b"event": b'<event version="2.0" uid="ANDROID-1" type="a-f-G" how="m-g">',
          b"contact": b'<contact callsign="A-1" endpoint="10.41.0.5:4242:tcp"/>', b"dest": b'<dest callsign="A-1" uid="U1"/>',
          b"__chat": b'<__chat chatroom="All Chat Rooms" id="x"/>', b"chatgrp": b'<chatgrp uid0="a" uid1="b"/>'}
worst = []
for m in mods:
    for name, r in vars(m).items():
        if not isinstance(r, re.Pattern) or not isinstance(r.pattern, bytes):
            continue
        tag = next((t for t in SAMPLE if b"<" + t in r.pattern.replace(b"\\b", b"")), None)
        base = SAMPLE[tag] if tag else r.pattern.replace(b"\\", b"")[:40]
        frags = {base[:i] for i in range(1, len(base) + 1)} | {base[:i] + b" " for i in range(1, len(base))} | {b"<", b" ", b'"', b"a"}
        top = (0, b"")
        for f in frags:
            d = (f * (63000 // len(f) + 1))[:63000]
            t = time.perf_counter(); r.search(d); dt = time.perf_counter() - t
            if dt > top[0]:
                top = (dt, f)
        worst.append((top[0], m.__name__ + "." + name, top[1][:30]))
worst.sort(reverse=True)
for dt, n, f in worst[:8]:
    print("%8.1f ms  %-38s worst fragment %r" % (dt * 1000, n, f))
print("WORST %.1f ms over %d patterns" % (worst[0][0] * 1000, len(worst)))
ok = worst[0][0] < 0.05
print("%d/1 passed" % ok)
sys.exit(0 if ok == 1 else 1)
