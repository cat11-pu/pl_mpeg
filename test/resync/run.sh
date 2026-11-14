#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")"

CC=${CC:-gcc}
SAN=${SAN:--fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer}

if [ ! -f raw_gop2.m1v ]; then
	ffmpeg -y -v error -f lavfi \
		-i testsrc=size=176x144:rate=25:duration=2 \
		-c:v mpeg1video -q:v 5 -g 2 -bf 0 -an -f mpeg1video raw_gop2.m1v
fi
if [ ! -f ps_gop2.mpg ]; then
	ffmpeg -y -v error -f lavfi \
		-i testsrc=size=176x144:rate=25:duration=2 \
		-c:v mpeg1video -q:v 5 -g 2 -bf 0 -an -f mpeg ps_gop2.mpg
fi
if [ ! -f raw_bf.m1v ]; then
	ffmpeg -y -v error -f lavfi \
		-i testsrc=size=176x144:rate=25:duration=2 \
		-c:v mpeg1video -q:v 5 -g 12 -bf 2 -an -f mpeg1video raw_bf.m1v
fi
if [ ! -f raw_gop1.m1v ]; then
	ffmpeg -y -v error -f lavfi \
		-i testsrc=size=176x144:rate=25:duration=2 \
		-c:v mpeg1video -q:v 5 -g 1 -bf 0 -an -f mpeg1video raw_gop1.m1v
fi

$CC -std=gnu99 -O1 -g $SAN resync_test.c -o resync_test

ASAN_OPTIONS=detect_leaks=1 \
	./resync_test raw_gop2.m1v ps_gop2.mpg raw_bf.m1v raw_gop1.m1v
