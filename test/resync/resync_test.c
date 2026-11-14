/*
 * Regression harness for byte-level resynchronization.
 *
 * Usage:
 *   resync_test raw_gop2.m1v ps_gop2.mpg raw_bf.m1v raw_gop1.m1v
 *
 * The harness cuts streams at many byte offsets, resynchronizes to the next
 * complete I-picture, decodes to EOF, and compares every recovered frame
 * checksum against a linear decode from the beginning. It also exercises
 * boundary conditions and chunked/streamed continuation.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PL_MPEG_IMPLEMENTATION
#include "../../pl_mpeg.h"

#define MAX_FRAMES 4096

typedef struct {
	int count;
	unsigned long long hash[MAX_FRAMES];
	double time[MAX_FRAMES];
} baseline_t;

typedef struct {
	int count;
	size_t offset[MAX_FRAMES];
	int type[MAX_FRAMES];
} pictures_t;

static int failures = 0;

static void fail(const char *what, const char *detail) {
	fprintf(stderr, "FAIL: %s%s%s\n", what, detail ? ": " : "", detail ? detail : "");
	failures++;
}

static unsigned long long hash_frame(const plm_frame_t *frame) {
	unsigned long long hash = 1469598103934665603ULL;
	const plm_plane_t *planes[3] = {&frame->y, &frame->cr, &frame->cb};
	for (int p = 0; p < 3; p++) {
		size_t count = (size_t)planes[p]->width * planes[p]->height;
		for (size_t i = 0; i < count; i++) {
			hash ^= planes[p]->data[i];
			hash *= 1099511628211ULL;
		}
	}
	return hash;
}

static uint8_t *read_file(const char *path, size_t *size) {
	FILE *f = fopen(path, "rb");
	if (!f) {
		perror(path);
		exit(2);
	}
	if (fseek(f, 0, SEEK_END) != 0) {
		perror("fseek");
		exit(2);
	}
	long length = ftell(f);
	if (length < 0) {
		perror("ftell");
		exit(2);
	}
	rewind(f);
	uint8_t *bytes = (uint8_t *)malloc((size_t)length);
	if (!bytes || fread(bytes, 1, (size_t)length, f) != (size_t)length) {
		perror("fread");
		exit(2);
	}
	fclose(f);
	*size = (size_t)length;
	return bytes;
}

static uint8_t *copy_bytes(const uint8_t *bytes, size_t size) {
	uint8_t *copy = (uint8_t *)malloc(size);
	if (!copy) {
		fprintf(stderr, "out of memory\n");
		exit(2);
	}
	memcpy(copy, bytes, size);
	return copy;
}

static const char *status_name(int status) {
	switch (status) {
		case PLM_RESYNC_OK: return "OK";
		case PLM_RESYNC_NEED_MORE_DATA: return "NEED_MORE_DATA";
		case PLM_RESYNC_END_OF_STREAM: return "END_OF_STREAM";
		case PLM_RESYNC_BAD_OFFSET: return "BAD_OFFSET";
		default: return "UNKNOWN";
	}
}

static int find_hash(const baseline_t *base, unsigned long long hash) {
	for (int i = 0; i < base->count; i++) {
		if (base->hash[i] == hash) {
			return i;
		}
	}
	return -1;
}

static int verify_suffix(
	const baseline_t *base, int first, const unsigned long long *hashes, int count
) {
	for (int i = 0; i < count; i++) {
		if (first + i >= base->count || base->hash[first + i] != hashes[i]) {
			return FALSE;
		}
	}
	return TRUE;
}

static void collect_pictures(const uint8_t *data, size_t size, pictures_t *pictures) {
	pictures->count = 0;
	for (size_t i = 0; i + 6 <= size; i++) {
		if (
			data[i] == 0x00 && data[i + 1] == 0x00 &&
			data[i + 2] == 0x01 && data[i + 3] == 0x00
		) {
			int type = (data[i + 5] >> 3) & 0x07;
			if (
				pictures->count < MAX_FRAMES &&
				type >= PLM_VIDEO_PICTURE_TYPE_INTRA &&
				type <= PLM_VIDEO_PICTURE_TYPE_B
			) {
				pictures->offset[pictures->count] = i;
				pictures->type[pictures->count] = type;
				pictures->count++;
			}
		}
	}
}

static void raw_baseline(const uint8_t *data, size_t size, baseline_t *base) {
	uint8_t *copy = copy_bytes(data, size);
	plm_buffer_t *buffer = plm_buffer_create_with_memory(copy, size, TRUE);
	plm_video_t *video = plm_video_create_with_buffer(buffer, TRUE);
	plm_frame_t *frame;
	base->count = 0;
	while (base->count < MAX_FRAMES && (frame = plm_video_decode(video))) {
		base->hash[base->count] = hash_frame(frame);
		base->time[base->count] = frame->time;
		base->count++;
	}
	plm_video_destroy(video);
}

static void ps_baseline(const uint8_t *data, size_t size, baseline_t *base) {
	uint8_t *copy = copy_bytes(data, size);
	plm_t *plm = plm_create_with_memory(copy, size, TRUE);
	plm_set_audio_enabled(plm, FALSE);
	plm_frame_t *frame;
	base->count = 0;
	while (base->count < MAX_FRAMES && (frame = plm_decode_video(plm))) {
		base->hash[base->count] = hash_frame(frame);
		base->time[base->count] = frame->time;
		base->count++;
	}
	plm_destroy(plm);
}

static int decode_raw_after_resync(
	const uint8_t *data, size_t size, size_t offset,
	plm_resync_info_t *info, unsigned long long *hashes, int *count
) {
	uint8_t *copy = copy_bytes(data, size);
	plm_buffer_t *buffer = plm_buffer_create_with_memory(copy, size, TRUE);
	plm_video_t *video = plm_video_create_with_buffer(buffer, TRUE);
	plm_video_has_header(video);

	int status = plm_video_resync(video, offset, info);
	*count = 0;
	if (status == PLM_RESYNC_OK) {
		plm_frame_t *frame;
		while (*count < MAX_FRAMES && (frame = plm_video_decode(video))) {
			hashes[*count] = hash_frame(frame);
			(*count)++;
		}
	}
	plm_video_destroy(video);
	return status;
}

static int decode_ps_after_resync(
	const uint8_t *data, size_t size, size_t offset,
	plm_resync_info_t *info, unsigned long long *hashes, int *count
) {
	uint8_t *copy = copy_bytes(data, size);
	plm_t *plm = plm_create_with_memory(copy, size, TRUE);
	plm_set_audio_enabled(plm, FALSE);
	if (!plm_has_headers(plm)) {
		plm_destroy(plm);
		return PLM_RESYNC_NEED_MORE_DATA;
	}

	int status = plm_resync(plm, offset, info);
	*count = 0;
	if (status == PLM_RESYNC_OK) {
		plm_frame_t *frame;
		while (*count < MAX_FRAMES && (frame = plm_decode_video(plm))) {
			hashes[*count] = hash_frame(frame);
			(*count)++;
		}
	}
	plm_destroy(plm);
	return status;
}

static void test_raw_cuts(
	const char *name, const uint8_t *data, size_t size,
	const baseline_t *base, const pictures_t *pictures, int max_lost
) {
	unsigned long long hashes[MAX_FRAMES];
	size_t cut_step = 0;
	int cuts = 0;
	int ok = 0;
	int mismatch = 0;
	int max_frames_lost = 0;
	size_t max_bytes_discarded = 0;

	for (int p = 0; p < pictures->count && p < 45; p++) {
		size_t start = pictures->offset[p];
		size_t end = (p + 1 < pictures->count)
			? pictures->offset[p + 1]
			: size;
		size_t offsets[4];
		offsets[0] = start;
		offsets[1] = start + 1;
		offsets[2] = start + (end - start) / 2;
		offsets[3] = end - 1;
		cut_step = 4;

		for (size_t i = 0; i < cut_step; i++) {
			size_t cut = offsets[i];
			if (cut >= size) {
				continue;
			}
			cuts++;

			plm_resync_info_t info;
			int output = 0;
			int status = decode_raw_after_resync(
				data, size, cut, &info, hashes, &output
			);
			if (status != PLM_RESYNC_OK) {
				char detail[128];
				snprintf(
					detail, sizeof(detail),
					"%s cut=%zu status=%s", name, cut,
					status_name(status)
				);
				fail("raw resync did not recover", detail);
				continue;
			}
			ok++;
			if (info.picture_type != PLM_VIDEO_PICTURE_TYPE_INTRA) {
				fail("raw resync did not land on an I-picture", name);
			}
			if (info.frames_lost > max_lost) {
				char detail[128];
				snprintf(
					detail, sizeof(detail),
					"%s cut=%zu frames_lost=%d max=%d",
					name, cut, info.frames_lost, max_lost
				);
				fail("raw resync latency exceeded", detail);
			}
			if (info.frames_lost > max_frames_lost) {
				max_frames_lost = info.frames_lost;
			}
			if (info.bytes_discarded > max_bytes_discarded) {
				max_bytes_discarded = info.bytes_discarded;
			}

			int first = -1;
			if (output > 0) {
				first = find_hash(base, hashes[0]);
			}
			if (first < 0 || !verify_suffix(base, first, hashes, output)) {
				char detail[160];
				snprintf(
					detail, sizeof(detail),
					"%s cut=%zu output=%d first=%d frames_lost=%d",
					name, cut, output, first, info.frames_lost
				);
				fail("raw recovered pixels differ", detail);
				mismatch++;
			}
		}
	}

	printf(
		"RAW   %-12s cuts=%d ok=%d mismatch=%d max_lost=%d max_bytes=%zu\n",
		name, cuts, ok, mismatch, max_frames_lost, max_bytes_discarded
	);
}

static void test_ps_cuts(
	const char *name, const uint8_t *data, size_t size,
	const baseline_t *base
) {
	unsigned long long hashes[MAX_FRAMES];
	int cuts = 0;
	int ok = 0;
	int mismatch = 0;
	int max_frames_lost = 0;
	int time_mismatch = 0;

	for (int i = 0; i <= 16; i++) {
		size_t cut = size * (size_t)i / 20;
		plm_resync_info_t info;
		int output = 0;
		int status = decode_ps_after_resync(
			data, size, cut, &info, hashes, &output
		);
		cuts++;
		if (status != PLM_RESYNC_OK) {
			continue;
		}
		ok++;
		if (info.frames_lost > 2) {
			char detail[128];
			snprintf(
				detail, sizeof(detail),
				"%s cut=%zu frames_lost=%d", name, cut, info.frames_lost
			);
			fail("PS resync latency exceeded", detail);
		}
		if (info.frames_lost > max_frames_lost) {
			max_frames_lost = info.frames_lost;
		}

		int first = output > 0 ? find_hash(base, hashes[0]) : -1;
		if (first < 0 || !verify_suffix(base, first, hashes, output)) {
			char detail[160];
			snprintf(
				detail, sizeof(detail),
				"%s cut=%zu output=%d first=%d frames_lost=%d",
				name, cut, output, first, info.frames_lost
			);
			fail("PS recovered pixels differ", detail);
			mismatch++;
		}
		else if (info.time + 1e-6 < base->time[first] ||
			info.time > base->time[first] + 1e-6) {
			char detail[160];
			snprintf(
				detail, sizeof(detail),
				"%s cut=%zu time=%.6f expected=%.6f",
				name, cut, info.time, base->time[first]
			);
			fail("PS recovered PTS time differs", detail);
			time_mismatch++;
		}
	}

	printf(
		"PS    %-12s cuts=%d ok=%d mismatch=%d time_mismatch=%d max_lost=%d\n",
		name, cuts, ok, mismatch, time_mismatch, max_frames_lost
	);
}

static void test_bframes(
	const char *name, const uint8_t *data, size_t size,
	const baseline_t *base, const pictures_t *pictures
) {
	unsigned long long hashes[MAX_FRAMES];
	int cuts = 0;
	int ok = 0;
	int mismatch = 0;

	for (int p = 0; p < pictures->count && p < 30; p += 3) {
		size_t start = pictures->offset[p];
		size_t end = (p + 1 < pictures->count)
			? pictures->offset[p + 1]
			: size;
		size_t offsets[3] = {
			start,
			start + 1,
			start + (end - start) / 2
		};
		for (int i = 0; i < 3; i++) {
			size_t cut = offsets[i];
			if (cut >= size) {
				continue;
			}
			plm_resync_info_t info;
			int output = 0;
			int status = decode_raw_after_resync(
				data, size, cut, &info, hashes, &output
			);
			cuts++;
			if (status != PLM_RESYNC_OK) {
				continue;
			}
			ok++;
			int first = output > 0 ? find_hash(base, hashes[0]) : -1;
			if (first < 0 || !verify_suffix(base, first, hashes, output)) {
				char detail[160];
				snprintf(
					detail, sizeof(detail),
					"%s cut=%zu output=%d first=%d",
					name, cut, output, first
				);
				fail("B-frame recovered pixels differ", detail);
				mismatch++;
			}
		}
	}

	printf(
		"BFRM  %-12s cuts=%d ok=%d mismatch=%d\n",
		name, cuts, ok, mismatch
	);
}

static void test_raw_streaming(
	const char *name, const uint8_t *data, size_t size,
	const baseline_t *base, const pictures_t *pictures
) {
	unsigned long long hashes[MAX_FRAMES];
	size_t header_size = pictures->count > 0 ? pictures->offset[0] : 0;
	int cuts = 0;
	int ok = 0;
	int mismatch = 0;
	int max_lost = 0;
	size_t max_discarded = 0;

	for (int p = 1; p < pictures->count && p < 24; p += 2) {
		size_t start = pictures->offset[p];
		size_t end = (p + 1 < pictures->count)
			? pictures->offset[p + 1]
			: size;
		size_t cut = start + (end - start) / 2;
		if (cut >= size) {
			continue;
		}
		cuts++;

		plm_buffer_t *buffer = plm_buffer_create_with_capacity(64);
		plm_buffer_write(buffer, (uint8_t *)data, header_size);
		plm_video_t *video = plm_video_create_with_buffer(buffer, TRUE);
		plm_video_has_header(video);

		size_t pos = cut;
		int steps = 0;
		int status = PLM_RESYNC_NEED_MORE_DATA;
		plm_resync_info_t info;
		const size_t chunks[] = {1, 2, 3, 5, 7, 11, 31};
		size_t chunk_count = sizeof(chunks) / sizeof(chunks[0]);

		while (status == PLM_RESYNC_NEED_MORE_DATA && pos < size) {
			size_t take = chunks[steps % chunk_count];
			if (take > size - pos) {
				take = size - pos;
			}
			plm_buffer_write(buffer, (uint8_t *)data + pos, take);
			pos += take;
			status = plm_video_resync(
				video,
				steps == 0 ? 0 : PLM_RESYNC_CONTINUE,
				&info
			);
			steps++;
		}
		if (status == PLM_RESYNC_NEED_MORE_DATA) {
			plm_buffer_signal_end(buffer);
			status = plm_video_resync(video, PLM_RESYNC_CONTINUE, &info);
		}

		int output = 0;
		if (status == PLM_RESYNC_OK) {
			ok++;
			if (pos < size) {
				plm_buffer_write(buffer, (uint8_t *)data + pos, size - pos);
			}
			plm_buffer_signal_end(buffer);
			plm_frame_t *frame;
			while (output < MAX_FRAMES && (frame = plm_video_decode(video))) {
				hashes[output++] = hash_frame(frame);
			}
			if (info.frames_lost > max_lost) {
				max_lost = info.frames_lost;
			}
			if (info.bytes_discarded > max_discarded) {
				max_discarded = info.bytes_discarded;
			}
			int first = output > 0 ? find_hash(base, hashes[0]) : -1;
			if (first < 0 || !verify_suffix(base, first, hashes, output)) {
				char detail[160];
				snprintf(
					detail, sizeof(detail),
					"%s cut=%zu output=%d first=%d",
					name, cut, output, first
				);
				fail("streamed resync pixels differ", detail);
				mismatch++;
			}
		}
		else {
			plm_video_destroy(video);
			fail("streamed resync did not recover", name);
			continue;
		}
		plm_video_destroy(video);
	}

	printf(
		"STRM  %-12s cuts=%d ok=%d mismatch=%d max_lost=%d max_bytes=%zu\n",
		name, cuts, ok, mismatch, max_lost, max_discarded
	);
}

static void test_boundaries(
	const uint8_t *raw, size_t raw_size, const uint8_t *ps, size_t ps_size
) {
	plm_resync_info_t info;
	unsigned long long hashes[MAX_FRAMES];
	int output = 0;
	int status;

	status = decode_raw_after_resync(
		raw, raw_size, raw_size + 1, &info, hashes, &output
	);
	if (status != PLM_RESYNC_BAD_OFFSET) {
		fail("raw offset past EOF was not rejected", status_name(status));
	}
	status = decode_raw_after_resync(
		raw, raw_size, raw_size, &info, hashes, &output
	);
	if (status != PLM_RESYNC_END_OF_STREAM) {
		fail("raw offset at EOF was not END", status_name(status));
	}

	size_t last_sequence = 0;
	for (size_t i = 0; i + 4 <= raw_size; i++) {
		if (
			raw[i] == 0x00 && raw[i + 1] == 0x00 &&
			raw[i + 2] == 0x01 && raw[i + 3] == 0xB3
		) {
			last_sequence = i;
		}
	}
	size_t midstream_offset = last_sequence + 4;
	uint8_t *midstream = copy_bytes(
		raw + midstream_offset, raw_size - midstream_offset
	);
	size_t midstream_size = raw_size - midstream_offset;
	plm_buffer_t *buffer = plm_buffer_create_with_memory(
		midstream, midstream_size, TRUE
	);
	plm_video_t *video = plm_video_create_with_buffer(buffer, TRUE);
	status = plm_video_resync(video, 0, &info);
	if (status != PLM_RESYNC_END_OF_STREAM) {
		fail("headerless mid-stream did not END", status_name(status));
	}
	plm_video_destroy(video);

	plm_buffer_t *partial = plm_buffer_create_with_capacity(256);
	plm_buffer_write(partial, (uint8_t *)raw, 100);
	plm_video_t *partial_video = plm_video_create_with_buffer(partial, TRUE);
	status = plm_video_resync(partial_video, 0, &info);
	if (status != PLM_RESYNC_NEED_MORE_DATA) {
		fail("truncated chunk did not request more data", status_name(status));
	}
	if (raw_size > 14000) {
		plm_buffer_write(partial, (uint8_t *)raw + 100, 14000 - 100);
		status = plm_video_resync(
			partial_video, PLM_RESYNC_CONTINUE, &info
		);
		if (status != PLM_RESYNC_OK) {
			fail("truncated chunk did not recover after append", status_name(status));
		}
	}
	plm_video_destroy(partial_video);

	status = decode_ps_after_resync(
		ps, ps_size, ps_size + 1, &info, hashes, &output
	);
	if (status != PLM_RESYNC_BAD_OFFSET) {
		fail("PS offset past EOF was not rejected", status_name(status));
	}
	status = decode_ps_after_resync(
		ps, ps_size, ps_size, &info, hashes, &output
	);
	if (status != PLM_RESYNC_END_OF_STREAM) {
		fail("PS offset at EOF was not END", status_name(status));
	}
	status = decode_ps_after_resync(
		ps, ps_size, ps_size - 8, &info, hashes, &output
	);
	if (status != PLM_RESYNC_END_OF_STREAM) {
		fail("PS padding did not END", status_name(status));
	}

	printf("EDGE  boundaries=%s\n", failures ? "FAILED" : "PASS");
}

int main(int argc, char **argv) {
	if (argc < 4) {
		fprintf(
			stderr,
			"usage: %s raw_gop2.m1v ps_gop2.mpg raw_bf.m1v [raw_gop1.m1v]\n",
			argv[0]
		);
		return 2;
	}

	size_t raw_size;
	size_t ps_size;
	size_t bf_size;
	uint8_t *raw = read_file(argv[1], &raw_size);
	uint8_t *ps = read_file(argv[2], &ps_size);
	uint8_t *bf = read_file(argv[3], &bf_size);

	pictures_t raw_pictures;
	pictures_t bf_pictures;
	collect_pictures(raw, raw_size, &raw_pictures);
	collect_pictures(bf, bf_size, &bf_pictures);

	baseline_t raw_base;
	baseline_t ps_base;
	baseline_t bf_base;
	raw_baseline(raw, raw_size, &raw_base);
	ps_baseline(ps, ps_size, &ps_base);
	raw_baseline(bf, bf_size, &bf_base);

	printf(
		"BASELINE raw_frames=%d ps_frames=%d bf_frames=%d raw_pictures=%d\n",
		raw_base.count, ps_base.count, bf_base.count, raw_pictures.count
	);

	test_raw_cuts("gop2", raw, raw_size, &raw_base, &raw_pictures, 2);
	test_ps_cuts("ps-gop2", ps, ps_size, &ps_base);
	test_bframes("bf-gop12", bf, bf_size, &bf_base, &bf_pictures);
	test_raw_streaming("stream", raw, raw_size, &raw_base, &raw_pictures);
	test_boundaries(raw, raw_size, ps, ps_size);

	if (argc > 4) {
		size_t g1_size;
		uint8_t *g1 = read_file(argv[4], &g1_size);
		pictures_t g1_pictures;
		baseline_t g1_base;
		collect_pictures(g1, g1_size, &g1_pictures);
		raw_baseline(g1, g1_size, &g1_base);
		test_raw_cuts("gop1", g1, g1_size, &g1_base, &g1_pictures, 1);
		free(g1);
	}

	free(raw);
	free(ps);
	free(bf);

	if (failures) {
		printf("TESTS FAILED failures=%d\n", failures);
		return 1;
	}
	printf("ALL TESTS PASSED\n");
	return 0;
}
