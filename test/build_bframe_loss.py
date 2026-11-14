#!/usr/bin/env python3
"""Drop the leading I-frame of a B-frame-bearing stream and re-mux it."""

import mpeg_es
import ps_mux


def main():
    es = open("bf.m1v", "rb").read()
    prefix, pictures, suffix = mpeg_es.frames(es)
    out = bytearray(prefix)
    for idx, (ptype, unit) in enumerate(pictures):
        if idx != 0:
            out += unit
    out += suffix
    with open("bf_loss.mpg", "wb") as fh:
        fh.write(ps_mux.mux(bytes(out)))
    print("bf_loss: dropped leading I-frame, types =",
          [mpeg_es.PICTURE_TYPE.get(t) for t, _ in pictures[:8]])


if __name__ == "__main__":
    main()
