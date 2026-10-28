/*
 * Drawing on the receiver's 128x64, 4-level vacuum fluorescent display.
 *
 * The display driver exposes a 4096-byte frame buffer: 64 bytes per row,
 * one pixel per nibble (low nibble = even x), values 0 (off) to 3 (bright).
 */
#ifndef GFX_H_INCLUDED
#define GFX_H_INCLUDED

#include <stdint.h>

#define GFX_W 128
#define GFX_H 64
#define GFX_BYTES 4096

struct glyph {
    uint8_t advance; /* pen advance */
    uint8_t width;   /* bitmap width (may exceed advance for overhangs) */
    uint32_t offset; /* into font data; rows of ceil(width/4) bytes, 2bpp */
};

struct font {
    uint8_t height;
    uint8_t first, last;
    const struct glyph *glyphs;
    const uint8_t *data;
};

extern const struct font font_small, font_medium, font_bold, font_large;
extern uint8_t gfx_fb[GFX_BYTES];

/* drawing modes for text */
#define INK_NORMAL 0 /* brighten: pixel = max(pixel, glyph * shade / 3) */
#define INK_ERASE 1  /* carve out of a lit background: pixel = shade - glyph */

void gfx_clip(int x0, int y0, int x1, int y1); /* exclusive x1,y1 */
void gfx_noclip(void);
void gfx_clear(void);
void gfx_pixel(int x, int y, int v);
void gfx_fill(int x, int y, int w, int h, int v);
void gfx_hline(int x, int y, int w, int v);
void gfx_vline(int x, int y, int h, int v);
void gfx_rect(int x, int y, int w, int h, int v);
int gfx_text_width(const struct font *f, const char *s);
/* Draw text; returns the pen x after the last glyph. */
int gfx_text(const struct font *f, int x, int y, const char *s, int shade, int ink);
/* Draw text clipped to maxw, ending in "..." when it doesn't fit. */
void gfx_text_fit(const struct font *f, int x, int y, int maxw, const char *s, int shade, int ink);
void gfx_text_center(const struct font *f, int y, const char *s, int shade);
void gfx_text_right(const struct font *f, int xr, int y, const char *s, int shade, int ink);

/*
 * Draw a 4-level bitmap (rows of ceil(w/4) bytes, 2 bits per pixel, leftmost
 * pixel in the low bits). `glint` animates a power-up sweep: pixels to the
 * right of the slanted line x + y/4 = glint glow dimly, a band just behind it
 * is drawn at full brightness, and the rest normally. Pass GLINT_NONE for a
 * plain draw.
 */
#define GLINT_NONE (-1000)
void gfx_bitmap(const uint8_t *data, int w, int h, int x, int y, int glint);

/* small icons, drawn in a 7x7 box */
#define ICON_PLAY 0
#define ICON_PAUSE 1
#define ICON_STOP 2
#define ICON_CHEVRON 3
#define ICON_SHUFFLE 4
#define ICON_REPEAT 5
#define ICON_REPEAT1 6
#define ICON_SPEAKER 7
void gfx_icon(int icon, int x, int y, int v);

#endif
