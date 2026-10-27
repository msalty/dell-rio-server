/*
 * CPU benchmark for the receiver's playback path, run under an ARM emulator
 * by tools/cpubench.py, which counts the instructions each part executes.
 *
 * `bench_mode` (patched in by the harness) selects what runs per MP3 frame:
 *   0  decode + synthesise + convert to 16-bit PCM (what playback must do)
 *   2  render the Now Playing screen once (UI cost, measured separately)
 */
#include "gfx.h"
#include "mad.h"
#include "sys.h"

extern const unsigned char bench_mp3[];
extern const unsigned int bench_mp3_len;

volatile int bench_mode = 0;
volatile unsigned bench_frames;
static int16_t pcm[1152 * 2];
static uint8_t inbuf[96 * 1024 + MAD_BUFFER_GUARD];

static inline int16_t scale(mad_fixed_t s)
{
    s += 1L << (MAD_F_FRACBITS - 16);
    if (s >= MAD_F_ONE)
        s = MAD_F_ONE - 1;
    else if (s < -MAD_F_ONE)
        s = -MAD_F_ONE;
    return (int16_t)(s >> (MAD_F_FRACBITS + 1 - 16));
}

static void render(void)
{
    gfx_clear();
    gfx_text_center(&font_small, 0, "3 of 12", 3);
    gfx_icon(ICON_PLAY, 2, 2, 3);
    gfx_hline(0, 11, GFX_W, 1);
    gfx_clip(2, 12, GFX_W - 2, 29);
    gfx_text(&font_large, -20, 12, "Maxwell's Silver Hammer", 3, INK_NORMAL);
    gfx_noclip();
    gfx_text_center(&font_medium, 29, "The Beatles", 3);
    gfx_text_center(&font_medium, 41, "Abbey Road", 2);
    gfx_text(&font_small, 1, 54, "1:23", 3, INK_NORMAL);
    gfx_text_right(&font_small, GFX_W - 1, 54, "-2:10", 3, INK_NORMAL);
    gfx_rect(30, 57, 68, 5, 1);
    gfx_fill(31, 58, 30, 3, 3);
}

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    if (bench_mode == 2) {
        render();
        return 0;
    }
    struct mad_stream st;
    struct mad_frame fr;
    struct mad_synth sy;
    mad_stream_init(&st);
    mad_frame_init(&fr);
    mad_synth_init(&sy);
    memcpy(inbuf, bench_mp3, bench_mp3_len);
    memset(inbuf + bench_mp3_len, 0, MAD_BUFFER_GUARD);
    mad_stream_buffer(&st, inbuf, bench_mp3_len + MAD_BUFFER_GUARD);
    for (;;) {
        if (mad_frame_decode(&fr, &st)) {
            if (st.error == MAD_ERROR_BUFLEN)
                break;
            if (MAD_RECOVERABLE(st.error))
                continue;
            break;
        }
        mad_synth_frame(&sy, &fr);
        for (int i = 0; i < sy.pcm.length; i++) {
            pcm[2 * i] = scale(sy.pcm.samples[0][i]);
            pcm[2 * i + 1] = scale(sy.pcm.samples[sy.pcm.channels > 1][i]);
        }
        bench_frames++;
    }
    return 0;
}
