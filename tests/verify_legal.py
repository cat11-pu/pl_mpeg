#!/usr/bin/env python3
"""Decode 100 legal streams with the candidate and baseline decoders and
compare every cropped Y/Cb/Cr plane byte-for-byte."""

import argparse
import hashlib
import json
import pathlib
import subprocess
import tempfile


def run_decoder(binary: str, source: pathlib.Path, yuv: pathlib.Path) -> dict:
    proc = subprocess.run(
        [binary, "--yuv", str(yuv), str(source)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{binary} failed on {source} (rc={proc.returncode}): "
            f"{proc.stdout.strip()} {proc.stderr.strip()}"
        )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default="validation/legal")
    parser.add_argument("--build", default="validation/build")
    parser.add_argument("--out", default="validation/results/legal.json")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    legal = pathlib.Path(args.dir)
    build = pathlib.Path(args.build)
    files = sorted(legal.glob("*.mpg"))
    if not files:
        raise SystemExit(f"no legal streams in {legal}; run tests/gen_legal.py")
    if args.limit:
        files = files[:args.limit]

    results = []
    mismatches = 0
    with tempfile.TemporaryDirectory(prefix="plm-legal-") as tmp:
        tmpdir = pathlib.Path(tmp)
        for index, source in enumerate(files):
            cand_yuv = tmpdir / f"{index}.candidate.yuv"
            ref_yuv = tmpdir / f"{index}.reference.yuv"
            cand = run_decoder(
                str(build / "decode_candidate"), source, cand_yuv
            )
            ref = run_decoder(
                str(build / "decode_reference"), source, ref_yuv
            )
            cand_hash = sha256(cand_yuv)
            ref_hash = sha256(ref_yuv)
            match = (
                cand["frames"] == ref["frames"]
                and cand["width"] == ref["width"]
                and cand["height"] == ref["height"]
                and cand_hash == ref_hash
            )
            if not match:
                mismatches += 1
            results.append({
                "file": source.name,
                "match": match,
                "candidate": cand,
                "reference": ref,
                "candidate_yuv_sha256": cand_hash,
                "reference_yuv_sha256": ref_hash,
            })

    report = {
        "streams": len(results),
        "matching": len(results) - mismatches,
        "mismatching": mismatches,
        "results": results,
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"legal streams: {len(results)}; "
        f"pixel-identical: {len(results) - mismatches}; mismatches: {mismatches}"
    )
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
