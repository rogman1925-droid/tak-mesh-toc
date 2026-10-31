# Test helper for robust_test.py: the stream-parsing loop of OpenTAKServer 1.7.13 EudHandler.handle(), copied as-is.
"""OTS 1.7.13 EudHandler.handle() parsing loop, copied verbatim (minus logging/RabbitMQ), as a TCP server.
Writes every event it would accept to a file, one per line."""
import re, socket, sys
from xml.etree.ElementTree import fromstring, ParseError
port, out = int(sys.argv[1]), open(sys.argv[2], "w")
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", port)); s.listen(5)
while True:
    c, _ = s.accept()
    cot = ""
    while True:
        data = c.recv(65536)
        if not data:
            break
        cot += data.decode("utf-8")
        cot_list = re.split("</event>|</auth>", cot)
        if len(cot_list) < 2:
            continue
        for x in cot_list:
            try:
                if "<event" in x:
                    fromstring(x + "</event>")
                    m = re.search(r'uid="([^"]+)"', x); out.write((m.group(1) if m else "?") + "\n"); out.flush()
                elif "<auth>" in x:
                    fromstring(x + "</auth>")
            except ParseError as e:
                cot = x
                break
        cot = ""
