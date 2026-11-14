#!/usr/bin/env bash
# Recovery / robustness regression test for lost reference frames.
# Builds the ASan+UBSan harness, generates corrupt streams and asserts:
#   1. no process crash / no sanitizer report (no OOB, no UB)
#   2. P/B frames with a missing reference -> PLM_ERROR_MISSING_REFERENCE,
#      never output (no mosaic frames)
#   3. decoding resumes at the next I-frame, pixel-identical to the reference
#   4. structural corruption -> PLM_ERROR_CORRUPT instead of a crash
set -u
cd "$(dirname "$0")"

CC=${CC:-gcc}
SAN="-fsanitize=address,undefined -fno-omit-frame-pointer"
$CC -std=gnu99 -g -O1 $SAN decode_harness.c -o harness_asan || exit 1

fail=0
check() { # desc expected_substring file
  local desc="$1" want="$2" file="$3"
  local out
  out=$(ASAN_OPTIONS=detect_leaks=1 ./harness_asan "$file" 2>&1)
  if echo "$out" | grep -q "$want"; then
    echo "PASS: $desc"
  else
    echo "FAIL: $desc (wanted '$want')"; echo "$out" | tail -5; fail=1
  fi
  if echo "$out" | grep -qiE "runtime error|AddressSanitizer|ERROR: "; then
    echo "FAIL: sanitizer report in $desc"; fail=1
  fi
}

# --- reference streams (generated if absent) --------------------------------
if [ ! -f base.mpg ]; then
  ffmpeg -y -v error -f lavfi -i testsrc=size=176x144:rate=25:duration=4 \
    -c:v mpeg1video -q:v 5 -g 12 -bf 0 -an -f mpeg base.mpg
fi
ffmpeg -y -v error -i base.mpg -an -c:v copy -f mpeg1video es.m1v
python3 build_streams.py

# --- 1. clean stream decodes fully ------------------------------------------
check "clean stream: 100 frames, zero skips" "output=100 skipped=0 errors=0" muxed_full.mpg

# --- 2. lost first I-frame: 11 P frames skipped, recovery at next I ---------
check "lost I-frame: missing-reference skips" "PLM_ERROR_MISSING_REFERENCE" loss_i.mpg
check "lost I-frame: recovery at I-frame" "output=88 skipped=11 errors=11" loss_i.mpg

# --- 3. ten consecutive lost frames recover at the next key frame -----------
check "10 lost frames: recovery" "output=88 skipped=2 errors=2" loss_10.mpg
check "mid-GOP 10 P-frame loss: key-frame recovery" "output=90 skipped=0 errors=0" loss_mid10.mpg

# --- 4. truncated stream reports CORRUPT, no crash --------------------------
python3 -c "d=open('base.mpg','rb').read(); open('trunc.mpg','wb').write(d[:5923])"
check "truncated stream: corrupt error" "PLM_ERROR_CORRUPT" trunc.mpg

# --- 5. recovered pixels are identical to the reference decoder -------------
python3 verify_pixels.py || fail=1

# --- 6. fuzz: randomized corruption must never crash -------------------------
python3 fuzz_run.py 300 || fail=1

# --- 7. B-frame stream: no garbage frames after reference loss --------------
ffmpeg -y -v error -f lavfi -i testsrc=size=176x144:rate=25:duration=2 \
  -c:v mpeg1video -q:v 6 -g 9 -bf 2 -an -f mpeg bframes.mpg
ffmpeg -y -v error -i bframes.mpg -an -c:v copy -f mpeg1video bf.m1v
python3 build_bframe_loss.py
check "B-frame loss: missing-reference skips" "PLM_ERROR_MISSING_REFERENCE" bf_loss.mpg
python3 verify_bframes.py || fail=1

if [ "$fail" -eq 0 ]; then echo "ALL TESTS PASSED"; else echo "TESTS FAILED"; fi
exit $fail
