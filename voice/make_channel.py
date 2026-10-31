#!/usr/bin/env python3
"""make_channel.py - create a permanent channel on a Mumble server, logged in as SuperUser.
Standard library only.   python3 make_channel.py TG1 [host] [port]   (asks for the SuperUser password; SuperUser skips the server password)"""
import getpass, os, socket, ssl, struct, sys, time


def vint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field(num, v):
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int):
        return vint(num << 3) + vint(v)
    v = v.encode()
    return vint((num << 3) | 2) + vint(len(v)) + v


def parse(d):
    out, i = {}, 0
    while i < len(d):
        k = s = 0
        while True:
            b = d[i]; i += 1; k |= (b & 0x7F) << s; s += 7
            if not b & 0x80:
                break
        num, wt = k >> 3, k & 7
        if wt == 0:
            v = s = 0
            while True:
                b = d[i]; i += 1; v |= (b & 0x7F) << s; s += 7
                if not b & 0x80:
                    break
        elif wt == 2:
            ln = s = 0
            while True:
                b = d[i]; i += 1; ln |= (b & 0x7F) << s; s += 7
                if not b & 0x80:
                    break
            v, i = d[i:i + ln], i + ln
        else:
            i += 4 if wt == 5 else 8
            continue
        out.setdefault(num, []).append(v)
    return out


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "TG1"
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 64738
    supw = os.environ.get("MUMBLE_SUPERUSER_PASSWORD") or getpass.getpass("SuperUser password: ")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    s = ctx.wrap_socket(socket.create_connection((host, port), timeout=10))

    def send(t, p):
        s.sendall(struct.pack(">HI", t, len(p)) + p)

    def recv():
        h = b""
        while len(h) < 6:
            h += s.recv(6 - len(h))
        t, ln = struct.unpack(">HI", h)
        p = b""
        while len(p) < ln:
            p += s.recv(ln - len(p))
        return t, p

    send(0, field(1, (1 << 16) | (4 << 8)) + field(2, "make_channel"))
    send(2, field(1, "SuperUser") + field(2, supw) + field(5, True))
    channels, synced, deadline = {}, False, time.time() + 10
    while time.time() < deadline and not synced:
        t, p = recv()
        if t == 4:
            sys.exit("Refused: %s" % parse(p).get(2, [b"?"])[0].decode(errors="replace"))
        if t == 7:
            f = parse(p)
            if 3 in f:
                channels[f[1][0] if 1 in f else 0] = f[3][0].decode(errors="replace")
        if t == 5:
            synced = True
    if not synced:
        sys.exit("No answer from the server.")
    if name in channels.values():
        print("Channel %r already exists. Channels: %s" % (name, ", ".join(sorted(channels.values()))))
        return
    send(7, field(2, 0) + field(3, name) + field(8, False))
    deadline = time.time() + 5
    while time.time() < deadline:
        s.settimeout(max(0.1, deadline - time.time()))
        try:
            t, p = recv()
        except (socket.timeout, OSError):
            break
        if t == 12:
            sys.exit("Server refused to create it: %r" % parse(p))
        if t == 7:
            f = parse(p)
            if 3 in f and f[3][0].decode(errors="replace") == name:
                print("Created channel %r (permanent)." % name)
                return
    print("No confirmation from the server; open the Mumble app and look for %r." % name)


if __name__ == "__main__":
    main()
