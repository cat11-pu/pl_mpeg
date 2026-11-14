#!/usr/bin/env python3
"""Every frame output by the B-frame loss stream must also appear in the clean
reference decode. A hash that exists only in the lossy stream is a mosaic /
garbage frame and must fail the test."""

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
    reference = set(output_hashes("bframes.mpg"))
    lossy = output_hashes("bf_loss.mpg")
    garbage = [h for h in lossy if h not in reference]
    if garbage:
        print(f"FAIL: {len(garbage)} garbage frame(s) leaked in B-frame stream")
        return 1
    print(f"PASS: B-frame loss produced {len(lossy)} clean frames, zero garbage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
