#!/usr/bin/env python3
"""Generate all corrupt / reference streams used by recover_test.sh."""

import mpeg_es
import ps_mux


def write_ps(path, es, drop=()):
    prefix, pictures, suffix = mpeg_es.frames(es)
    out = bytearray(prefix)
    dropped = []
    for idx, (ptype, unit) in enumerate(pictures):
        if idx in drop:
            dropped.append((idx, mpeg_es.PICTURE_TYPE.get(ptype, "?")))
        else:
            out += unit
    out += suffix
    with open(path, "wb") as fh:
        fh.write(ps_mux.mux(bytes(out)))
    return dropped, len(pictures)


def main():
    es = open("es.m1v", "rb").read()

    # Reference: our own muxer over the intact ES.
    with open("muxed_full.mpg", "wb") as fh:
        fh.write(ps_mux.mux(es))

    # Drop the leading I-frame (index 0). The next 11 P frames have no
    # reference and must be skipped until the I-frame at index 12.
    d, n = write_ps("loss_i.mpg", es, drop=set(range(0, 1)))
    print(f"loss_i: pictures={n} dropped={d}")

    # Ten consecutive frames lost (I + 9 P). Two orphan P frames (10, 11)
    # remain before the next key frame.
    d, n = write_ps("loss_10.mpg", es, drop=set(range(0, 10)))
    print(f"loss_10: pictures={n} dropped={d}")

    # Ten P frames lost in the middle of a GOP (13..22). Key frame at 24
    # re-establishes the reference chain.
    d, n = write_ps("loss_mid10.mpg", es, drop=set(range(13, 23)))
    print(f"loss_mid10: pictures={n} dropped={d}")


if __name__ == "__main__":
    main()
