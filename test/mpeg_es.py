#!/usr/bin/env python3
"""Helpers for inspecting and splicing raw MPEG-1 video elementary streams.

A "unit" is everything from one video start code (00 00 01 xx) up to the
next start code. Picture units start with code 0x00, sequence headers with
0xB3, GOP headers with 0xB8 and sequence end with 0xB9.
"""

import sys

START_PREFIX = b"\x00\x00\x01"
SEQUENCE_HEADER = 0xB3
GOP_HEADER = 0xB8
PICTURE = 0x00
SEQUENCE_END = 0xB9

PICTURE_TYPE = {1: "I", 2: "P", 3: "B", 4: "D"}


def iter_units(data):
    offsets = []
    i = 0
    while True:
        j = data.find(START_PREFIX, i)
        if j < 0:
            break
        offsets.append((j, data[j + 3]))
        i = j + 4
    for idx, (off, code) in enumerate(offsets):
        end = offsets[idx + 1][0] if idx + 1 < len(offsets) else len(data)
        yield off, end, code, data[off:end]


def picture_type(unit):
    # unit: 00 00 01 00, then 10 bit temporal_reference, 3 bit picture_type
    b0 = unit[4]
    b1 = unit[5]
    return (b1 >> 3) & 0x07


def list_units(path):
    data = open(path, "rb").read()
    frames = 0
    for off, end, code, unit in iter_units(data):
        if code == PICTURE:
            frames += 1
            ptype = PICTURE_TYPE.get(picture_type(unit), "?")
            print(f"frame {frames:3d} type={ptype} off={off} size={end - off}")
        elif code in (SEQUENCE_HEADER, GOP_HEADER, SEQUENCE_END):
            name = {
                SEQUENCE_HEADER: "sequence",
                GOP_HEADER: "gop",
                SEQUENCE_END: "end",
            }[code]
            print(f"--- {name} off={off} size={end - off}")


def frames(data):
    """Return (prefix_bytes, [(type, unit_bytes), ...], suffix_bytes).

    A picture unit spans from its picture start code up to the next start
    code of any kind, so it includes all slices and any trailing extension /
    sequence / GOP headers that precede the next picture.
    """
    units = list(iter_units(data))
    pictures = []
    prefix = bytearray()
    suffix = bytearray()
    pic_positions = [i for i, (_, _, code, _) in enumerate(units) if code == PICTURE]
    first_pic = pic_positions[0] if pic_positions else len(units)
    last_pic = pic_positions[-1] if pic_positions else -1

    for i, (off, end, code, unit) in enumerate(units):
        if i < first_pic:
            prefix += unit
        elif i > last_pic:
            suffix += unit

    for n, pos in enumerate(pic_positions):
        end_pos = pic_positions[n + 1] if n + 1 < len(pic_positions) else last_pic + 1
        chunk = b"".join(units[k][3] for k in range(pos, end_pos))
        pictures.append((picture_type(units[pos][3]), chunk))
    return bytes(prefix), pictures, bytes(suffix)


def drop_frames(in_path, out_path, drop_predicate):
    """Rebuild an ES, dropping pictures where drop_predicate(index, type) is True."""
    data = open(in_path, "rb").read()
    prefix, pictures, suffix = frames(data)
    out = bytearray(prefix)
    dropped = []
    for idx, (ptype, unit) in enumerate(pictures):
        if drop_predicate(idx, ptype):
            dropped.append((idx, PICTURE_TYPE.get(ptype, "?")))
        else:
            out += unit
    out += suffix
    open(out_path, "wb").write(out)
    return dropped


if __name__ == "__main__":
    list_units(sys.argv[1])
