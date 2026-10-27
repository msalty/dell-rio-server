#include "gfx.h"
#include "sys.h"
#include "fonts.h"

uint8_t gfx_fb[GFX_BYTES];
static int cx0 = 0, cy0 = 0, cx1 = GFX_W, cy1 = GFX_H;

void gfx_clip(int x0, int y0, int x1, int y1)
{
    cx0 = x0 < 0 ? 0 : x0;
    cy0 = y0 < 0 ? 0 : y0;
    cx1 = x1 > GFX_W ? GFX_W : x1;
    cy1 = y1 > GFX_H ? GFX_H : y1;
}

void gfx_noclip(void) { gfx_clip(0, 0, GFX_W, GFX_H); }

void gfx_clear(void) { memset(gfx_fb, 0, sizeof(gfx_fb)); }

static inline int get_px(int x, int y)
{
    uint8_t b = gfx_fb[(y << 6) + (x >> 1)];
    return (x & 1) ? (b >> 4) : (b & 15);
}

static inline void put_px(int x, int y, int v)
{
    uint8_t *b = &gfx_fb[(y << 6) + (x >> 1)];
    if (x & 1)
        *b = (uint8_t)((*b & 0x0f) | (v << 4));
    else
        *b = (uint8_t)((*b & 0xf0) | v);
}

void gfx_pixel(int x, int y, int v)
{
    if (x >= cx0 && x < cx1 && y >= cy0 && y < cy1)
        put_px(x, y, v & 3);
}

void gfx_fill(int x, int y, int w, int h, int v)
{
    for (int j = y; j < y + h; j++)
        for (int i = x; i < x + w; i++)
            gfx_pixel(i, j, v);
}

void gfx_hline(int x, int y, int w, int v) { gfx_fill(x, y, w, 1, v); }
void gfx_vline(int x, int y, int h, int v) { gfx_fill(x, y, 1, h, v); }

void gfx_rect(int x, int y, int w, int h, int v)
{
    gfx_hline(x, y, w, v);
    gfx_hline(x, y + h - 1, w, v);
    gfx_vline(x, y, h, v);
    gfx_vline(x + w - 1, y, h, v);
}

static const struct glyph *glyph_for(const struct font *f, uint8_t c)
{
    if (c < f->first || c > f->last)
        c = '?';
    return &f->glyphs[c - f->first];
}

int gfx_text_width(const struct font *f, const char *s)
{
    int w = 0;
    for (; *s; s++)
        w += glyph_for(f, (uint8_t)*s)->advance;
    return w;
}

static void draw_glyph(const struct font *f, const struct glyph *g, int x, int y, int shade, int ink)
{
    int stride = (g->width + 3) / 4;
    const uint8_t *p = f->data + g->offset;
    for (int row = 0; row < f->height; row++, p += stride) {
        int py = y + row;
        if (py < cy0 || py >= cy1)
            continue;
        for (int col = 0; col < g->width; col++) {
            int px = x + col;
            if (px < cx0 || px >= cx1)
                continue;
            int v = (p[col >> 2] >> ((col & 3) * 2)) & 3;
            if (!v)
                continue;
            if (ink == INK_ERASE) {
                int cur = get_px(px, py);
                int nv = cur - v;
                put_px(px, py, nv < 0 ? 0 : nv);
            } else {
                int nv = (v * shade + 2) / 3;
                if (nv > get_px(px, py))
                    put_px(px, py, nv);
            }
        }
    }
}

int gfx_text(const struct font *f, int x, int y, const char *s, int shade, int ink)
{
    for (; *s; s++) {
        const struct glyph *g = glyph_for(f, (uint8_t)*s);
        if (x >= cx1)
            break;
        if (x + g->width > cx0)
            draw_glyph(f, g, x, y, shade, ink);
        x += g->advance;
    }
    return x;
}

void gfx_text_fit(const struct font *f, int x, int y, int maxw, const char *s, int shade, int ink)
{
    if (gfx_text_width(f, s) <= maxw) {
        gfx_text(f, x, y, s, shade, ink);
        return;
    }
    int dots = gfx_text_width(f, "...");
    int sx0 = cx0, sy0 = cy0, sx1 = cx1, sy1 = cy1;
    int w = 0;
    const char *p = s;
    for (; *p; p++) {
        int a = glyph_for(f, (uint8_t)*p)->advance;
        if (w + a > maxw - dots)
            break;
        w += a;
    }
    gfx_clip(x > sx0 ? x : sx0, sy0, (x + w) < sx1 ? (x + w) : sx1, sy1);
    gfx_text(f, x, y, s, shade, ink);
    gfx_clip(sx0, sy0, sx1, sy1);
    gfx_text(f, x + w, y, "...", shade, ink);
}

void gfx_text_center(const struct font *f, int y, const char *s, int shade)
{
    int w = gfx_text_width(f, s);
    if (w > GFX_W - 2) {
        gfx_text_fit(f, 1, y, GFX_W - 2, s, shade, INK_NORMAL);
        return;
    }
    gfx_text(f, (GFX_W - w) / 2, y, s, shade, INK_NORMAL);
}

void gfx_text_right(const struct font *f, int xr, int y, const char *s, int shade, int ink)
{
    gfx_text(f, xr - gfx_text_width(f, s), y, s, shade, ink);
}

/* 7x7 icons, one string per row, '#' = lit */
static const char *const icons[][7] = {
    [ICON_PLAY] = {"#      ", "###    ", "#####  ", "#######", "#####  ", "###    ", "#      "},
    [ICON_PAUSE] = {"## ##  ", "## ##  ", "## ##  ", "## ##  ", "## ##  ", "## ##  ", "## ##  "},
    [ICON_STOP] = {"       ", "#####  ", "#####  ", "#####  ", "#####  ", "#####  ", "       "},
    [ICON_CHEVRON] = {"       ", " #     ", "  #    ", "   #   ", "  #    ", " #     ", "       "},
    [ICON_SHUFFLE] = {"     # ", "##  ###", "  ##  #", "  ##   ", "  ##  #", "##  ###", "     # "},
    [ICON_REPEAT] = {"  ###  ", " #   # ", "#     #", "#      ", "#    # ", " #  ###", "  ##  #"},
    [ICON_REPEAT1] = {"  ###  ", " #   # ", "#  #  #", "# ##   ", "#  # # ", " # ####", "  ##  #"},
    [ICON_SPEAKER] = {"   #   ", "  ##   ", "####  #", "####   ", "####  #", "  ##   ", "   #   "},
};

void gfx_icon(int icon, int x, int y, int v)
{
    for (int r = 0; r < 7; r++)
        for (int c = 0; c < 7; c++)
            if (icons[icon][r][c] == '#')
                gfx_pixel(x + c, y + r, v);
}
