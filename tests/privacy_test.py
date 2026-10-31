"""Release hygiene: the package must not carry anyone's personal details.
  - no address from the Tailscale / CGNAT range (100.64.0.0/10) except the documentation examples 100.64.0.9 and
    100.64.0.10, no e-mail address, no /home/<name>/ path
  - none of a list of the original author's personal identifiers (names, callsigns, addresses). The list is stored
    as salted SHA-256 hashes, so this file itself reveals nothing; every word and IP in the package is hashed the
    same way and compared.
  - no private keys or certificates
Scans every text file in the package except vendor/ (third-party code, checked separately for keys only).
Usage: python3 privacy_test.py [package dir]"""
import hashlib, ipaddress, os, re, sys
PKG = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALT = b"mmb-privacy:"
BLOCKED = {'eb3820897fb755f892c6b9ac', 'eeccc095520e82c5d59d0ab1', 'ac4286d59bb9a805a157ad5f', 'b28c06b6c9774a0ce8ef6b7f',
           '5cac19a77a198dc474e75de0', '8040932acf7eb52a94dc8f72', '62286179d5f2e9e16a08c3e8', 'd3d70305f3f1e467541d6694',
           '6732b4426a59addbbb83c5a6', 'ae70cc26c6eacd15a6f71f11', '54a57aa946126c3f7603505b'}
CGNAT = ipaddress.ip_network("100.64.0.0/10")
EXAMPLES = {"100.64.0.9", "100.64.0.10"}
SELF = os.path.abspath(__file__)
res = []
def check(n, c, x=""):
    res.append(bool(c)); print(("PASS  " if c else "FAIL  ") + n + ("" if c else "  " + str(x)[:600]), flush=True)
def h(tok):
    return hashlib.sha256(SALT + tok.encode()).hexdigest()[:24]

hits = {"blocked": [], "cgnat": [], "email": [], "home": [], "key": []}
n_files = 0
for root, dirs, files in os.walk(PKG):
    dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
    for name in files:
        p = os.path.join(root, name)
        rel = os.path.relpath(p, PKG)
        try:
            text = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        n_files += 1
        if re.search(r"-----BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE)-----\s*\n[A-Za-z0-9+/=\s]{64,}", text):   # a real PEM block
            hits["key"].append(rel)
        if rel.startswith("vendor" + os.sep) or os.path.abspath(p) == SELF:
            continue
        low = text.lower()
        for tok in set(re.findall(r"[a-z0-9]+", low)) | set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", low)):
            if h(tok) in BLOCKED:
                hits["blocked"].append("%s (%s)" % (rel, h(tok)[:8]))
        for ip in set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)):
            try:
                if ipaddress.ip_address(ip) in CGNAT and ip not in EXAMPLES:
                    hits["cgnat"].append("%s: %s" % (rel, ip))
            except ValueError:
                pass
        for m in re.findall(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", text):
            if not m.endswith(("example.com", "example.net", "example.org")) and not re.match(r"^\d", m):
                hits["email"].append("%s: %s" % (rel, m))
        for m in re.findall(r"/home/([a-z_][a-z0-9_-]*)/", text):
            if m not in ("user", "you", "pi", "deck"):
                hits["home"].append("%s: /home/%s/" % (rel, m))

check("scanned the package (%d text files)" % n_files, n_files > 30)
check("none of the author's personal identifiers", not hits["blocked"], sorted(set(hits["blocked"])))
check("no Tailscale/CGNAT addresses except the documentation examples", not hits["cgnat"], sorted(set(hits["cgnat"])))
check("no e-mail addresses", not hits["email"], sorted(set(hits["email"])))
check("no /home/<name>/ paths", not hits["home"], sorted(set(hits["home"])))
check("no private keys or certificates anywhere (vendor/ included)", not hits["key"], hits["key"])
print("\n%d/%d passed" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
