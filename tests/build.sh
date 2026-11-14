#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/validation/build"
BASE_REV="${PLM_BASE_REV:-c871f2b}"

mkdir -p "$BUILD"

git -C "$ROOT" show "$BASE_REV:pl_mpeg.h" > "$BUILD/pl_mpeg_reference.h"

CFLAGS=(
	-std=gnu99 -O1 -g -Wall -Wextra
	-fsanitize=address,undefined -fno-sanitize-recover=all
)

gcc "${CFLAGS[@]}" "$ROOT/tests/plm_guard.c" -o "$BUILD/plm_guard"
gcc -std=gnu99 -O2 -Wall -Wextra "$ROOT/tests/plm_decode_yuv.c" \
	-o "$BUILD/decode_candidate"
gcc -std=gnu99 -O2 -Wall -Wextra "$ROOT/tests/plm_decode_yuv.c" \
	-DPLM_HEADER="\"$BUILD/pl_mpeg_reference.h\"" \
	-o "$BUILD/decode_reference"
gcc "${CFLAGS[@]}" "$ROOT/tests/plm_decode_yuv.c" \
	-DPLM_HEADER="\"$BUILD/pl_mpeg_reference.h\"" \
	-o "$BUILD/decode_reference_asan"

printf 'built %s\n' "$BUILD"
