// Decode a raw MPEG-1 video elementary stream and report diagnostics.
// Used by the robustness and reference-conformance test drivers.
//
// Build:
//   cc -std=c99 -O1 -g -fsanitize=address,undefined tests/plm_guard.c -o plm_guard
//
// Usage:
//   plm_guard [--yuv OUTPUT] [--max-frames N] INPUT
//
// The YUV output is planar yuv420p, cropped to the display dimensions, one
// frame after another in decoder output order.

#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define PL_MPEG_IMPLEMENTATION
#include "../pl_mpeg.h"

static volatile int timed_out;

static void on_alarm(int sig) {
	(void)sig;
	timed_out = 1;
	_exit(124);
}

static uint8_t *read_file(const char *path, size_t *size) {
	FILE *fh = fopen(path, "rb");
	if (!fh) {
		return NULL;
	}
	if (fseek(fh, 0, SEEK_END) != 0) {
		fclose(fh);
		return NULL;
	}
	long end = ftell(fh);
	if (end < 0 || fseek(fh, 0, SEEK_SET) != 0) {
		fclose(fh);
		return NULL;
	}
	size_t length = (size_t)end;
	uint8_t *data = (uint8_t *)malloc(length ? length : 1);
	if (!data) {
		fclose(fh);
		return NULL;
	}
	if (length && fread(data, 1, length, fh) != length) {
		free(data);
		fclose(fh);
		return NULL;
	}
	fclose(fh);
	*size = length;
	return data;
}

static int write_plane(FILE *fh, const plm_plane_t *plane, int width, int height) {
	for (int y = 0; y < height; y++) {
		if (fwrite(plane->data + (size_t)y * plane->width, 1, (size_t)width, fh) != (size_t)width) {
			return 0;
		}
	}
	return 1;
}

static int write_yuv_frame(FILE *fh, const plm_frame_t *frame) {
	int width = (int)frame->width;
	int height = (int)frame->height;
	int chroma_width = width / 2;
	int chroma_height = height / 2;
	return write_plane(fh, &frame->y, width, height)
		&& write_plane(fh, &frame->cb, chroma_width, chroma_height)
		&& write_plane(fh, &frame->cr, chroma_width, chroma_height);
}

static const char *error_name(plm_error_t error) {
	switch (error) {
		case PLM_ERROR_NONE: return "none";
		case PLM_ERROR_TRUNCATED: return "truncated";
		case PLM_ERROR_INVALID_SEQUENCE: return "invalid_sequence";
		case PLM_ERROR_INVALID_DIMENSIONS: return "invalid_dimensions";
		case PLM_ERROR_OVERSIZED_FRAME: return "oversized_frame";
		case PLM_ERROR_INVALID_QUANT_MATRIX: return "invalid_quant_matrix";
		case PLM_ERROR_INVALID_PICTURE: return "invalid_picture";
		case PLM_ERROR_INVALID_SLICE: return "invalid_slice";
		case PLM_ERROR_INVALID_MACROBLOCK: return "invalid_macroblock";
		case PLM_ERROR_INVALID_BLOCK: return "invalid_block";
		case PLM_ERROR_INVALID_PACKET: return "invalid_packet";
		case PLM_ERROR_ALLOCATION: return "allocation";
		default: return "unknown";
	}
}

int main(int argc, char **argv) {
	const char *input_path = NULL;
	const char *yuv_path = NULL;
	int max_frames = 100000;

	for (int i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--yuv") == 0 && i + 1 < argc) {
			yuv_path = argv[++i];
		}
		else if (strcmp(argv[i], "--max-frames") == 0 && i + 1 < argc) {
			max_frames = atoi(argv[++i]);
		}
		else {
			input_path = argv[i];
		}
	}
	if (!input_path) {
		fprintf(stderr, "usage: %s [--yuv OUTPUT] [--max-frames N] INPUT\n", argv[0]);
		return 2;
	}

	signal(SIGALRM, on_alarm);
	alarm(5);

	size_t length = 0;
	uint8_t *data = read_file(input_path, &length);
	if (!data) {
		printf("{\"status\":\"io_error\",\"input\":\"%s\"}\n", input_path);
		return 2;
	}

	plm_buffer_t *buffer = plm_buffer_create_with_memory(data, length, FALSE);
	if (!buffer) {
		free(data);
		printf("{\"status\":\"error\",\"error\":\"allocation\"}\n");
		return 2;
	}
	plm_video_t *video = plm_video_create_with_buffer(buffer, TRUE);
	if (!video) {
		free(data);
		printf("{\"status\":\"error\",\"error\":\"allocation\"}\n");
		return 2;
	}

	FILE *yuv = NULL;
	if (yuv_path) {
		yuv = fopen(yuv_path, "wb");
		if (!yuv) {
			free(data);
			plm_video_destroy(video);
			printf("{\"status\":\"io_error\",\"input\":\"%s\"}\n", input_path);
			return 2;
		}
	}

	int frames = 0;
	int output_ok = 1;
	plm_frame_t *frame;
	while (frames < max_frames && (frame = plm_video_decode(video))) {
		if (yuv && !write_yuv_frame(yuv, frame)) {
			output_ok = 0;
			break;
		}
		frames++;
	}

	if (yuv) {
		fclose(yuv);
	}

	plm_error_t error = plm_video_get_error(video);
	size_t offset = plm_video_get_error_offset(video);
	size_t bit_offset = plm_video_get_error_bit_offset(video);
	const char *reason = plm_video_get_error_reason(video);
	int width = plm_video_get_width(video);
	int height = plm_video_get_height(video);

	printf(
		"{\"status\":\"%s\",\"frames\":%d,\"width\":%d,\"height\":%d,"
		"\"error\":\"%s\",\"offset\":%zu,\"bit_offset\":%zu,\"reason\":\"%s\","
		"\"yuv_ok\":%s}\n",
		error == PLM_ERROR_NONE ? "ok" : "rejected",
		frames, width, height, error_name(error), offset, bit_offset,
		reason ? reason : "", output_ok ? "true" : "false"
	);

	fflush(stdout);
	plm_video_destroy(video);
	free(data);
	alarm(0);
	return error == PLM_ERROR_NONE ? 0 : 1;
}
