"""mumbleclient.py - a minimal Mumble client: log in, join a channel, send and receive Opus voice.

Standard library only (no protobuf package, no pymumble): the handful of Mumble control messages it
needs are encoded by hand. Voice goes through the TCP control connection ("UDP tunnel"), which every
Mumble server accepts; that keeps NAT and firewalls simple (one TCP port, 64738) at the cost of
TCP's head-of-line delay on a lossy link.

The client announces protocol 1.4.0 so the server uses the classic voice packet format.
"""
import socket
import ssl
import struct
import threading
import time

# control message types
VERSION, UDPTUNNEL, AUTHENTICATE, PING, REJECT, SERVERSYNC = 0, 1, 2, 3, 4, 5
CHANNELREMOVE, CHANNELSTATE, USERREMOVE, USERSTATE, TEXTMESSAGE = 6, 7, 8, 9, 11
CODEC_OPUS = 4
TERMINATOR = 0x2000


# ---- protobuf, just enough ------------------------------------------------------------------
def _pb_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pb_field(num, value):
    """One protobuf field: int/bool -> varint, str/bytes -> length-delimited."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        return _pb_varint(num << 3) + _pb_varint(value)
    if isinstance(value, str):
        value = value.encode()
    return _pb_varint((num << 3) | 2) + _pb_varint(len(value)) + value


def pb_parse(data):
    """protobuf bytes -> {field number: [values]} (varints as int, length-delimited as bytes)."""
    out, i, n = {}, 0, len(data)
    while i < n:
        key, i = _pb_read_varint(data, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _pb_read_varint(data, i)
        elif wt == 2:
            ln, i = _pb_read_varint(data, i)
            v, i = data[i:i + ln], i + ln
        elif wt == 5:
            v, i = data[i:i + 4], i + 4
        elif wt == 1:
            v, i = data[i:i + 8], i + 8
        else:
            raise ValueError("unsupported protobuf wire type %d" % wt)
        out.setdefault(num, []).append(v)
    return out


def _pb_read_varint(data, i):
    shift = result = 0
    while True:
        if i >= len(data):
            raise ValueError("truncated varint")
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


# ---- Mumble's own varint (voice packets) ------------------------------------------------------
def mv_encode(v):
    if v < 0x80:
        return bytes([v])
    if v < 0x4000:
        return bytes([0x80 | (v >> 8), v & 0xFF])
    if v < 0x200000:
        return bytes([0xC0 | (v >> 16), (v >> 8) & 0xFF, v & 0xFF])
    if v < 0x10000000:
        return bytes([0xE0 | (v >> 24), (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])
    if v < 0x100000000:
        return b"\xF0" + struct.pack(">I", v)
    return b"\xF4" + struct.pack(">Q", v)


def mv_decode(data, i):
    b = data[i]
    if b & 0x80 == 0:
        return b, i + 1
    if b & 0xC0 == 0x80:
        return ((b & 0x3F) << 8) | data[i + 1], i + 2
    if b & 0xE0 == 0xC0:
        return ((b & 0x1F) << 16) | (data[i + 1] << 8) | data[i + 2], i + 3
    if b & 0xF0 == 0xE0:
        return ((b & 0x0F) << 24) | (data[i + 1] << 16) | (data[i + 2] << 8) | data[i + 3], i + 4
    if b & 0xFC == 0xF0:
        return struct.unpack(">I", data[i + 1:i + 5])[0], i + 5
    if b & 0xFC == 0xF4:
        return struct.unpack(">Q", data[i + 1:i + 9])[0], i + 9
    if b & 0xFC == 0xF8:                                 # negative (recursive); not used for voice
        v, j = mv_decode(data, i + 1)
        return -v, j
    if b & 0xFC == 0xFC:
        return ~(b & 0x03), i + 1
    raise ValueError("bad Mumble varint")


def voice_packet(seq, opus, last=False):
    """Client -> server voice packet (classic format): Opus, normal talking target."""
    size = len(opus) | (TERMINATOR if last else 0)
    return bytes([(CODEC_OPUS << 5) | 0]) + mv_encode(seq) + mv_encode(size) + opus


def parse_voice(data):
    """Server -> client voice packet -> (session, seq, opus bytes, last) or None (not Opus)."""
    if not data or (data[0] >> 5) != CODEC_OPUS:
        return None
    i = 1
    session, i = mv_decode(data, i)
    seq, i = mv_decode(data, i)
    size, i = mv_decode(data, i)
    last = bool(size & TERMINATOR)
    size &= 0x1FFF
    return session, seq, bytes(data[i:i + size]), last


# ---- the client ---------------------------------------------------------------------------------
class MumbleClient:
    """Connect, authenticate, join a channel; call send_voice(); on_voice(session, name, opus, last) fires
    for other users' audio in our channel. Reconnects are the caller's job (connect() again)."""

    def __init__(self, host, port=64738, username="DECK-voice", password="", channel="", certfile=None,
                 keyfile=None, pin_sha256=None, on_voice=None, on_text=None, log=None):
        self.host, self.port = host, int(port)
        self.username, self.password, self.channel_name = username, password, channel
        self.certfile, self.keyfile, self.pin = certfile, keyfile, (pin_sha256 or "").replace(":", "").lower()
        self.on_voice, self.on_text = on_voice, on_text
        self.log = log or (lambda *a: None)
        self.sock = None
        self.session = None
        self.channels = {}            # id -> name
        self.users = {}               # session -> {"name", "channel"}
        self.my_channel = 0
        self.synced = threading.Event()
        self.closed = threading.Event()
        self.reject = None
        self._wlock = threading.Lock()
        self._seq = 0
        self.rx_voice = self.tx_voice = 0
        self.rtt_ms = None

    # -- transport
    def connect(self, timeout=10):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE           # Mumble servers are usually self-signed: pin instead (MUMBLE_CERT_SHA256)
        if self.certfile:
            ctx.load_cert_chain(self.certfile, self.keyfile)
        raw = socket.create_connection((self.host, self.port), timeout=timeout)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # voice: never wait to fill a packet
        self.sock = ctx.wrap_socket(raw)
        if self.pin:
            import hashlib
            got = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest()
            if got != self.pin:
                self.sock.close()
                raise ssl.SSLError("server certificate %s does not match MUMBLE_CERT_SHA256" % got)
        self.sock.settimeout(None)
        self.closed.clear()
        self.synced.clear()
        self._send(VERSION, pb_field(1, (1 << 16) | (4 << 8)) + pb_field(2, "TAK MESH TOC voice")
                   + pb_field(3, "Linux") + pb_field(4, ""))
        auth = pb_field(1, self.username) + pb_field(5, True)
        if self.password:
            auth += pb_field(2, self.password)
        self._send(AUTHENTICATE, auth)
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._pinger, daemon=True).start()
        if not self.synced.wait(timeout):
            self.close()
            raise ConnectionError(self.reject or "no answer from the Mumble server")
        if self.channel_name:
            self.join(self.channel_name)
        return self

    def close(self):
        self.closed.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)       # wakes our reader; close() alone leaves the link up on Linux
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    def _send(self, mtype, payload):
        with self._wlock:
            self.sock.sendall(struct.pack(">HI", mtype, len(payload)) + payload)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            d = self.sock.recv(n - len(buf))
            if not d:
                raise ConnectionError("Mumble server closed the connection")
            buf += d
        return buf

    def _pinger(self):
        while not self.closed.wait(10):
            try:
                self._send(PING, pb_field(1, int(time.time() * 1000)))
            except Exception:
                self.closed.set()
                return

    def _reader(self):
        try:
            while not self.closed.is_set():
                mtype, ln = struct.unpack(">HI", self._recv_exact(6))
                if ln > 8 * 1024 * 1024:
                    raise ConnectionError("oversized Mumble message")
                self._handle(mtype, self._recv_exact(ln) if ln else b"")
        except Exception as e:
            if not self.closed.is_set():
                self.log("mumble: connection lost (%s)" % e)
        finally:
            self.closed.set()

    def _handle(self, mtype, data):
        if mtype == UDPTUNNEL:
            v = parse_voice(data)
            if v and self.on_voice:
                session, seq, opus, last = v
                u = self.users.get(session, {})
                if u.get("channel", self.my_channel) == self.my_channel:
                    self.rx_voice += 1
                    self.on_voice(session, u.get("name", "?"), opus, last)
            return
        if mtype == PING:
            f = pb_parse(data) if data else {}
            ts = f.get(1, [None])[0]
            if ts:
                self.rtt_ms = max(0.0, time.time() * 1000 - ts)
            return
        if mtype == VERSION:
            return
        f = pb_parse(data)
        if mtype == SERVERSYNC:
            self.session = f.get(1, [None])[0]
            self.synced.set()
        elif mtype == REJECT:
            self.reject = "Mumble server refused the login: %s" % (f.get(2, [b"?"])[0].decode(errors="replace"))
            self.log(self.reject)
        elif mtype == CHANNELSTATE:
            cid = f.get(1, [0])[0]
            if 3 in f:
                self.channels[cid] = f[3][0].decode(errors="replace")
        elif mtype == CHANNELREMOVE:
            self.channels.pop(f.get(1, [None])[0], None)
        elif mtype == USERSTATE:
            s = f.get(1, [None])[0]
            u = self.users.setdefault(s, {"name": "?", "channel": 0})
            if 3 in f:
                u["name"] = f[3][0].decode(errors="replace")
            if 5 in f:
                u["channel"] = f[5][0]
                if s == self.session:
                    self.my_channel = f[5][0]
        elif mtype == USERREMOVE:
            self.users.pop(f.get(1, [None])[0], None)
        elif mtype == TEXTMESSAGE and self.on_text:
            self.on_text(f.get(1, [None])[0], f.get(5, [b""])[0].decode(errors="replace"))

    # -- actions
    def join(self, name):
        cid = next((c for c, n in self.channels.items() if n.lower() == name.lower()), None)
        if cid is None:
            self.log("mumble: no channel named %r; staying in %r" % (name, self.channels.get(self.my_channel, "Root")))
            return False
        self._send(USERSTATE, pb_field(1, self.session) + pb_field(5, cid))
        self.my_channel = cid
        return True

    def send_voice(self, opus, last=False, frames=2):
        """One Opus packet into our channel. frames = its length in 10 ms units (2 for 20 ms)."""
        self._send(UDPTUNNEL, voice_packet(self._seq, opus, last))
        self._seq += frames
        self.tx_voice += 1

    def end_voice(self):
        """Tell listeners this talker is done (empty terminator packet)."""
        self._send(UDPTUNNEL, voice_packet(self._seq, b"", True))
        self._seq += 1

    def text(self, message, session=None):
        p = pb_field(5, message)
        p += pb_field(2, session) if session is not None else pb_field(3, self.my_channel)
        self._send(TEXTMESSAGE, p)

    def channel_users(self):
        return [u["name"] for s, u in self.users.items() if u.get("channel") == self.my_channel and s != self.session]
