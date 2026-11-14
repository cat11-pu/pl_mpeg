#!/usr/bin/env python3
"""Minimal MPEG-1 Program Stream muxer for a raw MPEG-1 video ES.

The output only needs to be valid enough for pl_mpeg's demuxer: pack headers
plus video PES packets (stream id 0xE0). PES packets are written without PTS
(flags 0x00) which pl_mpeg accepts. This keeps the muxer independent of
ffmpeg, which refuses to start a PS stream on a non-I-frame.
"""

PACK_PAYLOAD = 2048
PES_HEADER = bytes([0x00, 0x00, 0x01, 0xE0])


def _pack_header():
    # 12 byte MPEG-1 pack header; SCR/mux-rate fields are not used by pl_mpeg
    return bytes([
        0x00, 0x00, 0x01, 0xBA,
        0x21, 0x00, 0x01, 0x00,
        0x01, 0xC3, 0x33, 0x67,
    ])


def _pes_packet(payload):
    # PES_packet_length covers the one flags byte + payload
    length = 1 + len(payload)
    header = PES_HEADER + bytes([(length >> 8) & 0xFF, length & 0xFF, 0x00])
    return header + payload


SYSTEM_HEADER = bytes([
    0x00, 0x00, 0x01, 0xBB, 0x00, 0x09,
    0xC3, 0x33, 0x67, 0x00, 0x21, 0xFF, 0xE0, 0xE0, 0xE6,
])


def mux(es, chunk_size=PACK_PAYLOAD):
    out = bytearray()
    header_emitted = False
    for off in range(0, len(es), chunk_size):
        chunk = es[off:off + chunk_size]
        packet = _pes_packet(chunk)
        out += _pack_header()
        if not header_emitted:
            out += SYSTEM_HEADER
            header_emitted = True
        out += packet
    return bytes(out)
