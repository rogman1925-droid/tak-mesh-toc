"""opuscodec.py - the few libopus calls the voice gateway needs, through ctypes (no pip packages).

libopus is part of every Raspberry Pi OS / Debian install that has any audio (package libopus0).
48 kHz mono, 20 ms frames (960 samples) to match OpenMANET comms.
"""
import ctypes
import ctypes.util

RATE = 48000
FRAME = 960                      # 20 ms at 48 kHz
APPLICATION_VOIP = 2048
SET_BITRATE = 4002
SET_INBAND_FEC = 4012
SET_PACKET_LOSS_PERC = 4014
SET_COMPLEXITY = 4010

_name = ctypes.util.find_library("opus") or "libopus.so.0"
try:
    _lib = ctypes.CDLL(_name)
except OSError as e:                                   # pragma: no cover - reported by the caller
    _lib = None
    LOAD_ERROR = "libopus not found (%s): sudo apt install libopus0" % e
else:
    LOAD_ERROR = None
    _lib.opus_encoder_create.restype = ctypes.c_void_p
    _lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    _lib.opus_encode.restype = ctypes.c_int32
    _lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_char_p, ctypes.c_int32]
    _lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
    _lib.opus_encoder_ctl.restype = ctypes.c_int
    _lib.opus_decoder_create.restype = ctypes.c_void_p
    _lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    _lib.opus_decode.restype = ctypes.c_int
    _lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_int]
    _lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    _lib.opus_strerror.restype = ctypes.c_char_p
    _lib.opus_strerror.argtypes = [ctypes.c_int]


class OpusError(Exception):
    pass


def _check(rc):
    if rc < 0:
        raise OpusError(_lib.opus_strerror(rc).decode())
    return rc


class Encoder:
    def __init__(self, bitrate=32000, fec=True, loss=20):
        if _lib is None:
            raise OpusError(LOAD_ERROR)
        err = ctypes.c_int()
        self._st = _lib.opus_encoder_create(RATE, 1, APPLICATION_VOIP, ctypes.byref(err))
        _check(err.value)
        _lib.opus_encoder_ctl(ctypes.c_void_p(self._st), SET_BITRATE, ctypes.c_int32(bitrate))
        _lib.opus_encoder_ctl(ctypes.c_void_p(self._st), SET_INBAND_FEC, ctypes.c_int32(1 if fec else 0))
        _lib.opus_encoder_ctl(ctypes.c_void_p(self._st), SET_PACKET_LOSS_PERC, ctypes.c_int32(loss))
        _lib.opus_encoder_ctl(ctypes.c_void_p(self._st), SET_COMPLEXITY, ctypes.c_int32(5))   # as OpenMANET: light on a Pi
        self._out = ctypes.create_string_buffer(4000)

    def encode(self, pcm):
        """pcm: bytes, 960 little-endian int16 samples (1920 bytes) -> one Opus packet."""
        if len(pcm) != FRAME * 2:
            raise OpusError("need exactly %d samples" % FRAME)
        buf = (ctypes.c_int16 * FRAME).from_buffer_copy(pcm)
        n = _check(_lib.opus_encode(self._st, buf, FRAME, self._out, len(self._out)))
        return self._out.raw[:n]

    def __del__(self):
        if getattr(self, "_st", None) and _lib is not None:
            _lib.opus_encoder_destroy(self._st)
            self._st = None


class Decoder:
    MAX = 5760                                       # 120 ms, the longest Opus packet

    def __init__(self):
        if _lib is None:
            raise OpusError(LOAD_ERROR)
        err = ctypes.c_int()
        self._st = _lib.opus_decoder_create(RATE, 1, ctypes.byref(err))
        _check(err.value)
        self._pcm = (ctypes.c_int16 * self.MAX)()

    def decode(self, packet, fec=False):
        """One Opus packet -> PCM bytes (int16 mono 48 kHz). packet=None = conceal one 20 ms gap."""
        if packet is None:
            n = _check(_lib.opus_decode(self._st, None, 0, self._pcm, FRAME, 0))
        else:
            n = _check(_lib.opus_decode(self._st, packet, len(packet), self._pcm, self.MAX, 1 if fec else 0))
        return bytes(memoryview(self._pcm).cast("B")[:n * 2])

    def __del__(self):
        if getattr(self, "_st", None) and _lib is not None:
            _lib.opus_decoder_destroy(self._st)
            self._st = None


def valid_packet(packet):
    """True if bytes look like a real Opus packet (libopus can tell how many samples it holds)."""
    if _lib is None or not packet:
        return False
    return 0 < _lib.opus_packet_get_nb_samples(packet, len(packet), RATE) <= 5760


def packet_samples(packet):
    """Samples (at 48 kHz) in one Opus packet, or 960 if it can't be read."""
    if _lib is None or not packet:
        return FRAME
    n = _lib.opus_packet_get_nb_samples(packet, len(packet), RATE)
    return n if n > 0 else FRAME


if _lib is not None:
    _lib.opus_packet_get_nb_samples.restype = ctypes.c_int
    _lib.opus_packet_get_nb_samples.argtypes = [ctypes.c_char_p, ctypes.c_int32, ctypes.c_int32]
