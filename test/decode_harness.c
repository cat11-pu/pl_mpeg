/*
 * Reproduction / regression harness for lost reference frames.
 *
 * Feeds an MPEG-PS or raw MPEG-1 video file to the decoder and reports,
 * per picture: whether a frame was produced, the decoder reported error
 * code and the picture type. A decoded frame is checksummed so that
 * garbage / "mosaic" output is easy to spot.
 *
 * Usage: decode_harness <file.mpg|file.m1v>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PL_MPEG_IMPLEMENTATION
#include "../pl_mpeg.h"

static unsigned long frame_hash(const plm_frame_t *frame) {
	unsigned long hash = 1469598103934665603UL;
	const plm_plane_t planes[3] = { frame->y, frame->cr, frame->cb };
	for (int p = 0; p < 3; p++) {
		size_t n = (size_t)planes[p].width * planes[p].height;
		const uint8_t *d = planes[p].data;
		for (size_t i = 0; i < n; i++) {
			hash ^= d[i];
			hash *= 1099511628211UL;
		}
	}
	return hash;
}

int main(int argc, char *argv[]) {
	if (argc < 2) {
		fprintf(stderr, "usage: %s <file>\n", argv[0]);
		return 2;
	}

	FILE *fh = fopen(argv[1], "rb");
	if (!fh) {
		perror("fopen");
		return 2;
	}
	fseek(fh, 0, SEEK_END);
	long size = ftell(fh);
	fseek(fh, 0, SEEK_SET);
	uint8_t *bytes = malloc((size_t)size);
	if (fread(bytes, 1, (size_t)size, fh) != (size_t)size) {
		perror("fread");
		return 2;
	}
	fclose(fh);

	plm_t *plm = plm_create_with_memory(bytes, (size_t)size, TRUE);
	if (!plm) {
		fprintf(stderr, "plm_create failed\n");
		return 2;
	}
	plm_set_audio_enabled(plm, FALSE);

	int produced = 0;
	int skipped = 0;
	int errors = 0;
	int picture = 0;

	for (;;) {
		int code = PLM_OK;
		plm_frame_t *frame = plm_decode_video_ex(plm, &code);
		if (!frame) {
			if (code == PLM_ERROR_END || code == PLM_ERROR_TRY_AGAIN) {
				break; // clean end of stream, or need more buffered bytes
			}
			if (code == PLM_OK) {
				break;
			}
		}
		picture++;
		if (code != PLM_OK) {
			errors++;
			if (code == PLM_ERROR_MISSING_REFERENCE) {
				skipped++;
			}
			printf("picture %3d: SKIP code=%d (%s)\n", picture, code,
				plm_error_name(code));
			continue;
		}
		produced++;
		printf("picture %3d: OUT  type=%d hash=%016lx\n",
			picture, plm_get_last_picture_type(plm), frame_hash(frame));
	}

	printf("summary: pictures=%d output=%d skipped=%d errors=%d ended=%d\n",
		picture, produced, skipped, errors, plm_has_ended(plm));

	plm_destroy(plm);
	return errors ? 3 : 0;
}
