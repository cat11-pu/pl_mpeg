#!/usr/bin/env python3
"""Replay a malformed corpus against the unmodified baseline decoder.

The baseline can only be classified by process status/sanitizer output; it
does not have the diagnostic API added by the hardening patch.
"""

import argparse
import collections
import json
import os
import pathlib
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

CATEGORIES = ("truncated", "misaligned", "invalid_quant", "oversized")


def classify(binary: str, path: pathlib.Path, timeout: float) -> dict:
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = (
        "detect_leaks=1:abort_on_error=1:allocator_may_return_null=1:"
        "max_allocation_size_mb=256"
    )
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=0"
    try:
        proc = subprocess.run(
            [binary, "--raw", str(path)],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"file": str(path), "category": path.parent.name,
                "kind": "timeout", "detail": ""}

    output = (proc.stdout or "") + (proc.stderr or "")
    if "AddressSanitizer" in output:
        kind = "heap_oob"
        detail = next(
            (line for line in output.splitlines() if "ERROR: AddressSanitizer" in line),
            "",
        )
    elif "runtime error:" in output:
        kind = "undefined_behavior"
        detail = next(
            (line for line in output.splitlines() if "runtime error:" in line),
            "",
        )
    elif "LeakSanitizer" in output:
        kind = "leak"
        detail = next(
            (line for line in output.splitlines() if "LeakSanitizer" in line),
            "",
        )
    elif proc.returncode != 0:
        kind = "abnormal_exit"
        detail = f"returncode={proc.returncode}"
    else:
        kind = "ok"
        detail = ""
    return {"file": str(path), "category": path.parent.name,
            "kind": kind, "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", default="validation/build")
    parser.add_argument("--dir", default="validation/malformed")
    parser.add_argument("--out", default="validation/results/baseline.json")
    parser.add_argument("--timeout", type=float, default=1.5)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    files = sorted(
        path
        for path in pathlib.Path(args.dir).glob("*/*.m1v")
        if path.parent.name in CATEGORIES
    )
    if not files:
        raise SystemExit(f"no malformed cases in {args.dir}")
    binary = str(pathlib.Path(args.build) / "decode_reference_asan")
    if not pathlib.Path(binary).exists():
        raise SystemExit(f"missing {binary}; run tests/build.sh")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(classify, binary, path, args.timeout) for path in files]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["file"])

    outcomes = collections.Counter(item["kind"] for item in results)
    by_category = {}
    for category in CATEGORIES:
        subset = [item for item in results if item["category"] == category]
        by_category[category] = {
            "cases": len(subset),
            **collections.Counter(item["kind"] for item in subset),
        }
    summary = {
        "cases": len(results),
        "outcomes": dict(outcomes),
        "by_category": by_category,
        "failures": [item for item in results if item["kind"] != "ok"],
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"baseline: {len(results)} cases; outcomes: {dict(outcomes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
