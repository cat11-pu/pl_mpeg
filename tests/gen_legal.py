#!/usr/bin/env python3
"""Generate a deterministic corpus of 100 legal MPEG-1 program streams."""

import argparse
import json
import pathlib
import subprocess


SIZES = [
    (16, 16), (32, 24), (48, 32), (64, 48), (96, 64), (128, 96),
    (176, 144), (320, 240), (352, 288), (640, 480), (720, 480), (720, 576),
]
RATES = [24, 25, 30, 50]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="validation/legal")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []

    for i in range(args.count):
        width, height = SIZES[i % len(SIZES)]
        rate = RATES[(i // len(SIZES)) % len(RATES)]
        # Keep well above the baseline's historical 136-byte sequence-header
        # precheck so the 100-stream pixel comparison isolates the decoder
        # itself rather than that unrelated baseline limitation.
        frames = 4 + (i * 7) % 9
        qscale = 1 + (i * 3) % 20
        gop = 1 + (i * 5) % 15
        bframes = (0, 0, 1, 2)[i % 4] if gop > 1 else 0

        name = f"{i:03d}_{width}x{height}_{rate}fps_q{qscale}_g{gop}_b{bframes}.mpg"
        path = out / name
        cmd = [
            args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={rate}",
            "-frames:v", str(frames),
            "-c:v", "mpeg1video", "-pix_fmt", "yuv420p",
            "-q:v", str(qscale), "-g", str(gop), "-bf", str(bframes),
            "-threads", "1", "-an", "-f", "mpeg", str(path),
        ]
        subprocess.run(cmd, check=True)
        manifest.append({
            "index": i,
            "file": name,
            "width": width,
            "height": height,
            "rate": rate,
            "requested_frames": frames,
            "qscale": qscale,
            "gop": gop,
            "bframes": bframes,
            "command": cmd,
        })

    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated {len(manifest)} streams in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
