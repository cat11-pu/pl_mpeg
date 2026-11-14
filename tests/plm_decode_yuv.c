// Decode either an MPEG-PS file or a raw MPEG-1 elementary stream to planar
// yuv420p. Intentionally uses only APIs present in the upstream baseline, so
// the same source can build both the candidate and the reference decoder.

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef PLM_HEADER
#define PLM_HEADER "../pl_mpeg.h"
#endif

#define PL_MPEG_IMPLEMENTATION
#include PLM_HEADER

static int write_plane(FILE *fh, const plm_plane_t *plane, int width, int height) {
	for (int y = 0; y < height; y++) {
		if (fwrite(plane->data + (size_t)y * plane->width, 1, (size_t)width, fh) != (size_t)width) {
			return 0;
		}
	}
	return 1;
}

static int write_yuv_frame(FILE *fh, const plm_frame_t *frame) {
	if (!fh) {
		return 1;
	}
	return write_plane(fh, &frame->y, (int)frame->width, (int)frame->height)
		&& write_plane(fh, &frame->cb, (int)frame->width / 2, (int)frame->height / 2)
		&& write_plane(fh, &frame->cr, (int)frame->width / 2, (int)frame->height / 2);
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

static int decode_raw(const char *path, FILE *out, int *width, int *height) {
	size_t length = 0;
	uint8_t *data = read_file(path, &length);
	if (!data) {
		return -1;
	}
	plm_buffer_t *buffer = plm_buffer_create_with_memory(data, length, FALSE);
	plm_video_t *video = plm_video_create_with_buffer(buffer, TRUE);
	if (!video) {
		free(data);
		return -1;
	}

	int frames = 0;
	plm_frame_t *frame;
	while ((frame = plm_video_decode(video))) {
		if (!write_yuv_frame(out, frame)) {
			plm_video_destroy(video);
			free(data);
			return -1;
		}
		*width = (int)frame->width;
		*height = (int)frame->height;
		frames++;
	}

	plm_video_destroy(video);
	free(data);
	return frames;
}

static int decode_ps(const char *path, FILE *out, int *width, int *height) {
	plm_t *plm = plm_create_with_filename(path);
	if (!plm) {
		return -1;
	}
	plm_set_audio_enabled(plm, FALSE);

	int frames = 0;
	plm_frame_t *frame;
	while ((frame = plm_decode_video(plm))) {
		if (!write_yuv_frame(out, frame)) {
			plm_destroy(plm);
			return -1;
		}
		*width = (int)frame->width;
		*height = (int)frame->height;
		frames++;
	}

	plm_destroy(plm);
	return frames;
}

int main(int argc, char **argv) {
	const char *input = NULL;
	const char *output = NULL;
	int raw = 0;

	for (int i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--yuv") == 0 && i + 1 < argc) {
			output = argv[++i];
		}
		else if (strcmp(argv[i], "--raw") == 0) {
			raw = 1;
		}
		else {
			input = argv[i];
		}
	}
	if (!input) {
		fprintf(stderr, "usage: %s [--raw] [--yuv OUTPUT] INPUT\n", argv[0]);
		return 2;
	}

	FILE *out = output ? fopen(output, "wb") : NULL;
	if (output && !out) {
		fprintf(stderr, "unable to open %s\n", output);
		return 2;
	}

	int width = 0;
	int height = 0;
	int frames = raw
		? decode_raw(input, out, &width, &height)
		: decode_ps(input, out, &width, &height);

	if (out) {
		fclose(out);
	}
	if (frames < 0) {
		printf("{\"status\":\"error\"}\n");
		return 1;
	}
	printf(
		"{\"status\":\"ok\",\"mode\":\"%s\",\"frames\":%d,\"width\":%d,\"height\":%d}\n",
		raw ? "raw" : "ps", frames, width, height
	);
	return 0;
}
