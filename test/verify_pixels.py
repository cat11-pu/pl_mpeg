#!/usr/bin/env python3
"""Assert recovered frames are pixel-identical to the reference decoder.

Every frame the lossy stream outputs must match the corresponding frame from
the clean stream. A mismatch (e.g. a frame decoded against a wrong/zero
reference) means a mosaic/garbage frame leaked through.
"""

import subprocess
import sys


def output_hashes(path):
    proc = subprocess.run(
        ["./harness_asan", path], capture_output=True, text=True, timeout=60
    )
    if proc.returncode not in (0, 3):
        print("harness crashed on", path)
        print(proc.stderr[:800])
        sys.exit(1)
    return [
        line.split("hash=")[1].strip()
        for line in proc.stdout.splitlines()
        if "OUT" in line
    ]


def main():
    reference = output_hashes("muxed_full.mpg")

    # loss_i drops frame index 0; output begins at reference frame index 12.
    loss_i = output_hashes("loss_i.mpg")
    expected_i = reference[12:]
    if loss_i != expected_i:
        print("FAIL: loss_i recovered frames differ from reference")
        for a, b in zip(loss_i, expected_i):
            if a != b:
                print("  first mismatch:", a, b)
                break
        return 1
    print(f"PASS: loss_i {len(loss_i)} recovered frames match reference exactly")

    # loss_10 drops indices 0..9; only frames 10,11 are orphaned and skipped,
    # so output also begins at reference frame index 12.
    loss_10 = output_hashes("loss_10.mpg")
    if loss_10 != expected_i:
        print("FAIL: loss_10 recovered frames differ from reference")
        return 1
    print(f"PASS: loss_10 {len(loss_10)} recovered frames match reference exactly")

    return 0


if __name__ == "__main__":
    sys.exit(main())
