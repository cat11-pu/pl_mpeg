#!/usr/bin/env python3
"""Randomized corruption fuzzer: the decoder must never crash or report
AddressSanitizer/UBSan violations. Corrupt frames are allowed to be skipped
with PLM_ERROR_* codes."""

import random
import subprocess
import sys


def main(iterations):
    data = open("base.mpg", "rb").read()
    positions = []
    i = 0
    while True:
        j = data.find(b"\x00\x00\x01\x00", i)
        if j < 0:
            break
        ptype = (data[j + 5] >> 3) & 7
        if ptype in (1, 2, 3):
            positions.append(j)
        i = j + 4

    crashes = 0
    for seed in range(iterations):
        rnd = random.Random(seed)
        mode = rnd.randrange(3)
        if mode == 0:  # truncate
            mutated = data[: rnd.randrange(len(data) // 4, len(data))]
        elif mode == 1:  # overwrite region with 0xff
            mutated = bytearray(data)
            a = rnd.randrange(0, len(data) - 200)
            for k in range(a, min(a + rnd.randrange(20, 2000), len(mutated))):
                mutated[k] = 0xFF
        else:  # flip random bytes inside one picture
            mutated = bytearray(data)
            idx = rnd.randrange(1, len(positions) - 1)
            end = positions[idx + 1]
            for _ in range(rnd.randint(1, 12)):
                k = rnd.randrange(positions[idx] + 6, end)
                mutated[k] ^= rnd.randrange(1, 256)

        with open("fuzz_case.mpg", "wb") as fh:
            fh.write(mutated)
        proc = subprocess.run(
            ["./harness_asan", "fuzz_case.mpg"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode not in (0, 3) or "runtime error" in proc.stderr \
                or "AddressSanitizer" in proc.stderr:
            crashes += 1
            print(f"FAIL: seed {seed} rc={proc.returncode}")
            print(proc.stderr[:800])
            break

    if crashes == 0:
        print(f"PASS: fuzz {iterations} iterations, zero crashes / sanitizer errors")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 200))
