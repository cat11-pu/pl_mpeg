#!/usr/bin/env python3
"""Run the 5,000-case malformed MPEG-1 video campaign.

Categories (1,250 cases each):
  truncated        Prefix truncation at structural and random byte offsets.
  misaligned       Inserted, deleted, swapped, or bit-flipped bytes.
  invalid_quant    Custom intra/non-intra quant matrices containing zero.
  oversized        12-bit dimension fields set to extreme values.

Every case is decoded in its own ASan/UBSan process. Normal rejection is
expected; a sanitizer report, non-standard exit, or timeout is a failure.
"""

import argparse
import json
import os
import pathlib
import random
import shutil
import subprocess
import sys


CATEGORIES = ("truncated", "misaligned", "invalid_quant", "oversized")
PER_CATEGORY = 1250
SANITIZER_MARKERS = (
    "AddressSanitizer", "UndefinedBehaviorSanitizer", "LeakSanitizer",
    "runtime error:", "SEGV", "heap-buffer-overflow", "stack-buffer-overflow",
)


def ffmpeg_seed(ffmpeg: str, path: pathlib.Path, size: str, frames: int,
                qscale: int, gop: int, bframes: int) -> None:
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25",
        "-frames:v", str(frames),
        "-c:v", "mpeg1video", "-pix_fmt", "yuv420p",
        "-q:v", str(qscale), "-g", str(gop), "-bf", str(bframes),
        "-threads", "1", "-an", "-f", "mpeg1video", str(path),
    ]
    subprocess.run(cmd, check=True)


def ensure_seeds(ffmpeg: str, out: pathlib.Path) -> list[pathlib.Path]:
    out.mkdir(parents=True, exist_ok=True)
    specs = [
        ("seed_small.m1v", "32x24", 1, 5, 1, 0),
        ("seed_motion.m1v", "64x48", 8, 3, 12, 2),
        ("seed_detail.m1v", "96x64", 5, 2, 5, 0),
    ]
    paths = []
    for name, size, frames, qscale, gop, bframes in specs:
        path = out / name
        if not path.exists():
            ffmpeg_seed(ffmpeg, path, size, frames, qscale, gop, bframes)
        paths.append(path)
    return paths


def sequence_header_offset(data: bytes) -> int:
    return data.find(b"\x00\x00\x01\xb3")


def read_header_fields(data: bytes):
    offset = sequence_header_offset(data)
    if offset < 0 or offset + 12 > len(data):
        raise ValueError("seed has no complete sequence header")
    value = int.from_bytes(data[offset + 4:offset + 12], "big")
    fields = {
        "width": (value >> 52) & 0xfff,
        "height": (value >> 40) & 0xfff,
        "aspect": (value >> 36) & 0xf,
        "rate": (value >> 32) & 0xf,
        "bitrate": (value >> 32) & 0x3ffff,
        "marker": (value >> 13) & 1,
        "vbv": (value >> 3) & 0x3ff,
        "constrained": (value >> 2) & 1,
    }
    flags = value & 0x3
    if flags != 0:
        raise ValueError("seed unexpectedly uses custom quant matrices")
    return offset, fields


def bits_to_bytes(bits: list[int]) -> bytes:
    padded = bits + [0] * ((8 - len(bits) % 8) % 8)
    out = bytearray()
    for i in range(0, len(padded), 8):
        value = 0
        for bit in padded[i:i + 8]:
            value = (value << 1) | bit
        out.append(value)
    return bytes(out)


def int_bits(value: int, count: int) -> list[int]:
    return [(value >> (count - 1 - i)) & 1 for i in range(count)]


def rewrite_with_quant_matrix(data: bytes, intra: list[int] | None,
                              non_intra: list[int] | None) -> bytes:
    offset, fields = read_header_fields(data)
    bits = []
    bits += int_bits(fields["width"], 12)
    bits += int_bits(fields["height"], 12)
    bits += int_bits(fields["aspect"], 4)
    bits += int_bits(fields["rate"], 4)
    bits += int_bits(fields["bitrate"], 18)
    bits += int_bits(fields["marker"], 1)
    bits += int_bits(fields["vbv"], 10)
    bits += int_bits(fields["constrained"], 1)

    bits += [1 if intra is not None else 0]
    if intra is not None:
        for value in intra:
            bits += int_bits(value, 8)
    bits += [1 if non_intra is not None else 0]
    if non_intra is not None:
        for value in non_intra:
            bits += int_bits(value, 8)

    header = bits_to_bytes(bits)
    return data[:offset + 4] + header + data[offset + 12:]


def patch_dimensions(data: bytes, width: int, height: int) -> bytes:
    offset = sequence_header_offset(data)
    if offset < 0 or offset + 8 > len(data):
        return data
    out = bytearray(data)
    value = int.from_bytes(out[offset + 4:offset + 8], "big")
    value = (
        (value & 0xff) |
        ((width & 0xfff) << 20) |
        ((height & 0xfff) << 8)
    )
    out[offset + 4:offset + 8] = value.to_bytes(4, "big")
    return bytes(out)


def make_case(category: str, index: int, rng: random.Random,
              seeds: list[bytes]) -> tuple[bytes, dict]:
    seed_index = index % len(seeds)
    seed = seeds[seed_index]
    info = {"seed_index": seed_index, "mutation": ""}

    if category == "truncated":
        offset = sequence_header_offset(seed)
        structural = [0, 1, 2, 3, 4, 8, 12, offset, offset + 1,
                      offset + 4, offset + 8, offset + 12, len(seed) - 1]
        if index < len(structural):
            length = max(0, min(len(seed), structural[index]))
            info["mutation"] = f"prefix length {length}"
        else:
            length = rng.randrange(0, len(seed))
            info["mutation"] = f"prefix length {length}"
        return seed[:length], info

    if category == "misaligned":
        data = bytearray(seed)
        op = index % 6
        position = rng.randrange(0, len(data))
        if op == 0:
            count = rng.randrange(1, 17)
            data[position:position] = bytes(rng.randrange(256) for _ in range(count))
            info["mutation"] = f"insert {count} bytes at {position}"
        elif op == 1:
            count = rng.randrange(1, min(17, len(data) - position + 1))
            del data[position:position + count]
            info["mutation"] = f"delete {count} bytes at {position}"
        elif op == 2:
            bit = rng.randrange(8)
            data[position] ^= 1 << bit
            info["mutation"] = f"flip bit {bit} at {position}"
        elif op == 3:
            if position + 1 < len(data):
                data[position], data[position + 1] = data[position + 1], data[position]
            info["mutation"] = f"swap bytes at {position}"
        elif op == 4:
            code = rng.choice((b"\x00\x00\x01\xb3", b"\x00\x00\x01\x00",
                               b"\x00\x00\x01\x01", b"\x00\x00\x01\xb7"))
            data[position:position] = code
            info["mutation"] = f"insert start code {code.hex()} at {position}"
        else:
            count = rng.randrange(1, 9)
            data[position:position + count] = b"\x00" * count
            info["mutation"] = f"zero {count} bytes at {position}"
        return bytes(data), info

    if category == "invalid_quant":
        mode = index % 5
        intra = None
        non_intra = None
        zeros = sorted(rng.sample(range(64), 1 if mode < 3 else 64))
        if mode in (0, 2, 4):
            intra = [1] * 64
            for pos in zeros:
                intra[pos] = 0
        if mode in (1, 2, 3, 4):
            non_intra = [1] * 64
            for pos in zeros:
                non_intra[pos] = 0
        data = rewrite_with_quant_matrix(seed, intra, non_intra)
        info["mutation"] = (
            f"custom matrices intra={intra is not None} "
            f"non_intra={non_intra is not None} zero_positions={zeros[:8]}"
        )
        return data, info

    if category == "oversized":
        choices = [
            (4095, 4095), (4095, 1), (1, 4095), (4095, 2048),
            (2048, 4095), (2048, 2048), (1024, 1024), (0, 4095),
            (4095, 0), (rng.randrange(4096), rng.randrange(4096)),
        ]
        width, height = choices[index % len(choices)]
        data = patch_dimensions(seed, width, height)
        if index % 5 != 0:
            offset = sequence_header_offset(data)
            data = data[:offset + 12]
        info["mutation"] = f"dimensions {width}x{height}"
        return data, info

    raise ValueError(category)


def classify_run(proc: subprocess.CompletedProcess) -> dict:
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if any(marker in combined for marker in SANITIZER_MARKERS):
        if "LeakSanitizer" in combined:
            kind = "leak"
        elif "runtime error:" in combined:
            kind = "undefined_behavior"
        else:
            kind = "memory_error"
        return {"outcome": kind, "detail": combined[-4000:]}

    if proc.returncode not in (0, 1):
        return {
            "outcome": "abnormal_exit",
            "detail": f"returncode={proc.returncode}\n{combined[-4000:]}",
        }

    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        return {"outcome": "bad_output", "detail": combined[-4000:]}
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"outcome": "bad_output", "detail": combined[-4000:]}

    if data.get("status") == "ok":
        data["outcome"] = "accepted"
    elif data.get("status") == "rejected":
        data["outcome"] = "rejected"
        if not data.get("reason"):
            data["outcome"] = "missing_reason"
        elif not isinstance(data.get("offset"), int) or data["offset"] < 0:
            data["outcome"] = "missing_offset"
    else:
        data["outcome"] = "bad_output"
    return data


def run_one(binary: str, path: pathlib.Path, timeout: float) -> dict:
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = (
        "detect_leaks=1:abort_on_error=1:allocator_may_return_null=1:"
        "max_allocation_size_mb=256"
    )
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    try:
        proc = subprocess.run(
            [binary, "--max-frames", "6", str(path)],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"outcome": "timeout", "detail": f"exceeded {timeout}s"}
    return classify_run(proc)


def minimize(binary: str, data: bytes, category: str, outcome: dict,
             timeout: float, max_runs: int = 400) -> bytes:
    """Greedy byte-chunk deletion while preserving the same failure class."""
    target_outcome = outcome.get("outcome")
    target_error = outcome.get("error")
    runs = 0

    def still_fails(blob: bytes) -> bool:
        nonlocal runs
        if runs >= max_runs:
            return False
        runs += 1
        candidate = pathlib.Path("/tmp") / f"plm-min-{os.getpid()}.m1v"
        candidate.write_bytes(blob)
        result = run_one(binary, candidate, timeout)
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        if target_outcome in ("accepted", "rejected"):
            return (
                result.get("outcome") == target_outcome
                and result.get("error") == target_error
            )
        return result.get("outcome") == target_outcome

    current = data
    chunk = max(1, len(current) // 2)
    while chunk >= 1 and runs < max_runs:
        index = 0
        while index < len(current) and runs < max_runs:
            candidate = current[:index] + current[index + chunk:]
            if candidate and still_fails(candidate):
                current = candidate
            else:
                index += chunk
        if chunk == 1:
            break
        chunk = max(1, chunk // 2)
    return current


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", default="validation/build")
    parser.add_argument("--out", default="validation/malformed")
    parser.add_argument("--results", default="validation/results/malformed.json")
    parser.add_argument("--counterexamples", default="validation/counterexamples")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--no-minimize", action="store_true")
    args = parser.parse_args()

    build = pathlib.Path(args.build)
    output = pathlib.Path(args.out)
    counterexamples = pathlib.Path(args.counterexamples)
    for category in CATEGORIES:
        (output / category).mkdir(parents=True, exist_ok=True)
    counterexamples.mkdir(parents=True, exist_ok=True)

    seed_paths = ensure_seeds(args.ffmpeg, output / "seeds")
    seeds = [path.read_bytes() for path in seed_paths]
    rng = random.Random(0x5EEDC0DE)
    binary = str(build / "plm_guard")
    if not pathlib.Path(binary).exists():
        raise SystemExit(f"missing {binary}; run tests/build.sh")

    records = []
    failures = []
    for category in CATEGORIES:
        counts = {}
        for index in range(PER_CATEGORY):
            data, info = make_case(category, index, rng, seeds)
            path = output / category / f"{index:04d}.m1v"
            path.write_bytes(data)
            result = run_one(binary, path, args.timeout)
            outcome = result.get("outcome")
            counts[outcome] = counts.get(outcome, 0) + 1
            record = {
                "file": str(path),
                "category": category,
                "size": len(data),
                **info,
                **result,
            }
            records.append(record)
            if outcome not in ("accepted", "rejected"):
                failures.append(record)

        print(f"{category}: {counts}", flush=True)

    minimized = []
    if failures and not args.no_minimize:
        for index, failure in enumerate(failures[:20]):
            data = pathlib.Path(failure["file"]).read_bytes()
            small = minimize(binary, data, failure["category"], failure, args.timeout)
            path = counterexamples / f"failure_{index:02d}_{failure['category']}.m1v"
            path.write_bytes(small)
            minimized.append({
                "source": failure["file"],
                "category": failure["category"],
                "outcome": failure["outcome"],
                "minimized": str(path),
                "original_size": len(data),
                "minimized_size": len(small),
            })

    # Even with no safety failure, preserve one minimized sample per rejection
    # class so the report has reproducible counterexamples.
    representatives = {}
    for record in records:
        if record.get("outcome") != "rejected":
            continue
        key = record.get("error", "unknown")
        if key in representatives:
            continue
        data = pathlib.Path(record["file"]).read_bytes()
        small = data if args.no_minimize else minimize(
            binary, data, record["category"], record, args.timeout
        )
        path = counterexamples / f"min_{key}.m1v"
        path.write_bytes(small)
        representatives[key] = {
            "source": record["file"],
            "category": record["category"],
            "error": key,
            "offset": record.get("offset"),
            "reason": record.get("reason"),
            "minimized": str(path),
            "original_size": len(data),
            "minimized_size": len(small),
        }

    summary = {
        "cases": len(records),
        "categories": {
            category: {
                "cases": sum(1 for r in records if r["category"] == category),
                "accepted": sum(
                    1 for r in records
                    if r["category"] == category and r.get("outcome") == "accepted"
                ),
                "rejected": sum(
                    1 for r in records
                    if r["category"] == category and r.get("outcome") == "rejected"
                ),
            }
            for category in CATEGORIES
        },
        "safety_failures": len(failures),
        "failure_outcomes": {
            outcome: sum(1 for r in records if r.get("outcome") == outcome)
            for outcome in sorted({r.get("outcome") for r in records})
        },
        "rejection_reasons": {
            error: sum(1 for r in records if r.get("error") == error)
            for error in sorted({r.get("error") for r in records if r.get("error")})
        },
        "minimized_failures": minimized,
        "representatives": list(representatives.values()),
        "records": records,
    }
    out = pathlib.Path(args.results)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(
        f"total: {len(records)}; safety failures: {len(failures)}; "
        f"rejected: {summary['failure_outcomes'].get('rejected', 0)}; "
        f"accepted: {summary['failure_outcomes'].get('accepted', 0)}"
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
