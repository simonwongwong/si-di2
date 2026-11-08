"""Serial framing used by Shimano's PC interfaces (SM-BCR2, SM-PCE1).

A frame on the wire is::

    BB  escape(ctrl, data..., fcs)  BB

* ``ctrl`` is the command byte; replies come back as ``ctrl | 0x20``.
* ``fcs`` is the two's complement of the byte sum of ``ctrl`` and ``data``.
* ``BB`` and ``BD`` inside the frame are sent as ``BD, byte ^ 0x20``.

Source: ``Shimano.SMPCE1COM.Common`` (``CreateFrame``, ``CreateFCS``,
``SetEscape``/``RemoveEscape``) in E-Tube Project 4.0.4.
"""
from typing import Iterator, NamedTuple

FLAG = 0xBB
ESC = 0xBD


class Frame(NamedTuple):
    ctrl: int
    data: bytes
    fcs_ok: bool


def fcs(body: bytes) -> int:
    return (-sum(body)) & 0xFF


def _escape(body: bytes) -> bytes:
    out = bytearray()
    for b in body:
        if b in (FLAG, ESC):
            out += bytes([ESC, b ^ 0x20])
        else:
            out.append(b)
    return bytes(out)


def _unescape(chunk: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(chunk):
        if chunk[i] == ESC and i + 1 < len(chunk):
            out.append(chunk[i + 1] ^ 0x20)
            i += 2
        else:
            out.append(chunk[i])
            i += 1
    return bytes(out)


def encode(ctrl: int, data: bytes = b'') -> bytes:
    body = bytes([ctrl]) + bytes(data)
    return bytes([FLAG]) + _escape(body + bytes([fcs(body)])) + bytes([FLAG])


class Decoder:
    """Incremental decoder: feed raw serial bytes, get complete frames back."""

    def __init__(self) -> None:
        self._buf = b''

    def feed(self, raw: bytes) -> Iterator[Frame]:
        self._buf += raw
        *complete, self._buf = self._buf.split(bytes([FLAG]))
        for chunk in complete:
            if not chunk:
                continue
            body = _unescape(chunk)
            if len(body) < 2:
                continue
            yield Frame(body[0], body[1:-1], fcs(body[:-1]) == body[-1])
