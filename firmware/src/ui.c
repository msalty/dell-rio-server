/*
 * The user interface: an iPod-style stack of menus served by the music
 * server, a Now Playing screen and a local Settings menu.
 *
 *   wheel / up / down    scroll (volume on Now Playing)
 *   wheel push / enter   select
 *   menu                 back
 *   play                 play the highlighted item, or play/pause
 *   >> / <<              next / previous track
 *   random               shuffle on/off
 */
#include "ui.h"
#include "gfx.h"
#include "hw.h"
#include "net.h"
#include "player.h"
#include "sys.h"

#define VERSION "1.0"
#define WIN 24
#define ROWS 4
#define ROW_H 13
#define LIST_Y 12
#define MAX_DEPTH 10
#define BACKLIGHT_TIMEOUT 60000

struct item {
    char kind; /* M menu, T track, S shuffle, plus local: N now playing, L settings, C setting */
    char ref[64];
    char label[96];
    char detail[40];
};

struct level {
    char node[128];
    char title[48];
    int local; /* 1 = settings menu */
    int loaded;
    int total;
    int cursor, top;
    int win_base, win_count;
    struct item win[WIN];
};

enum screen { SCR_MENU, SCR_NOW };

static struct level stack[MAX_DEPTH];
static int depth;
static enum screen screen = SCR_MENU;
static int dirty = 1;
static uint32_t last_key, marquee_start, vol_until, last_render;
static int backlight_on = 1, backlight_auto = 1, standby;
static uint32_t lib_version;
static char menu_error[48];

/* ---- helpers ----------------------------------------------------------- */

void ui_dirty(void) { dirty = 1; }

static struct level *cur(void) { return &stack[depth - 1]; }

static void fmt_time(char *buf, int cap, uint32_t ms)
{
    uint32_t s = ms / 1000;
    if (s >= 3600)
        snprintf(buf, (size_t)cap, "%u:%02u:%02u", (unsigned)(s / 3600), (unsigned)(s / 60 % 60),
                 (unsigned)(s % 60));
    else
        snprintf(buf, (size_t)cap, "%u:%02u", (unsigned)(s / 60), (unsigned)(s % 60));
}

static void url_node(char *out, int cap, const char *node)
{
    /* nodes only contain [a-z0-9:/], which are URL-safe */
    strlcpy(out, node, (size_t)cap);
}

/* ---- menu data --------------------------------------------------------- */

static int is_root(struct level *l) { return !l->local && !strcmp(l->node, "root"); }
static int root_pre(struct level *l) { return is_root(l) && P.have_track ? 1 : 0; }

static int level_total(struct level *l)
{
    if (l->local)
        return 7;
    if (is_root(l))
        return root_pre(l) + l->total + 1; /* + Settings */
    return l->total;
}

static int fetch_window(struct level *l, int center)
{
    char path[200], node[128];
    int base = center - WIN / 2;
    if (base < 0)
        base = 0;
    url_node(node, sizeof(node), l->node);
    snprintf(path, sizeof(path), "/ng/menu/%s?b=%d&n=%d", node, base, WIN);
    char *buf = malloc(16 * 1024);
    if (!buf)
        return -1;
    int n = http_get(path, buf, 16 * 1024, 4000);
    if (n <= 0) {
        free(buf);
        strlcpy(menu_error, n == -404 ? "Not found" : "Server unavailable", sizeof(menu_error));
        return -1;
    }
    menu_error[0] = 0;
    l->win_base = base;
    l->win_count = 0;
    for (char *line = buf; line && *line;) {
        char *next = strchr(line, '\n');
        if (next)
            *next++ = 0;
        char *f[5] = {0};
        int nf = 0;
        for (char *p = line; p && nf < 5;) {
            f[nf++] = p;
            p = strchr(p, '\t');
            if (p)
                *p++ = 0;
        }
        if (nf >= 2 && !strcmp(f[0], "title"))
            strlcpy(l->title, f[1], sizeof(l->title));
        else if (nf >= 2 && !strcmp(f[0], "total"))
            l->total = atoi(f[1]);
        else if (nf >= 2 && !strcmp(f[0], "begin"))
            l->win_base = atoi(f[1]);
        else if (nf >= 4 && !strcmp(f[0], "i") && l->win_count < WIN) {
            struct item *it = &l->win[l->win_count++];
            it->kind = f[1][0];
            strlcpy(it->ref, f[2], sizeof(it->ref));
            strlcpy(it->label, f[3], sizeof(it->label));
            strlcpy(it->detail, nf >= 5 ? f[4] : "", sizeof(it->detail));
        }
        line = next;
    }
    free(buf);
    l->loaded = 1;
    return 0;
}

static void settings_item(int idx, struct item *it)
{
    static const char *const repeat_names[] = {"Off", "All", "One"};
    memset(it, 0, sizeof(*it));
    it->kind = 'C';
    switch (idx) {
    case 0:
        strlcpy(it->label, "Shuffle", sizeof(it->label));
        strlcpy(it->detail, P.shuffle ? "Songs" : "Off", sizeof(it->detail));
        break;
    case 1:
        strlcpy(it->label, "Repeat", sizeof(it->label));
        strlcpy(it->detail, repeat_names[P.repeat], sizeof(it->detail));
        break;
    case 2:
        strlcpy(it->label, "Volume", sizeof(it->label));
        snprintf(it->detail, sizeof(it->detail), "%d%%", P.volume);
        break;
    case 3:
        strlcpy(it->label, "Backlight", sizeof(it->label));
        strlcpy(it->detail, backlight_auto ? "Auto" : "Always On", sizeof(it->detail));
        break;
    case 4:
        strlcpy(it->label, "Server", sizeof(it->label));
        strlcpy(it->detail, g_server.host, sizeof(it->detail));
        break;
    case 5:
        strlcpy(it->label, "About", sizeof(it->label));
        strlcpy(it->detail, "v" VERSION, sizeof(it->detail));
        break;
    default:
        strlcpy(it->label, "Restart Receiver", sizeof(it->label));
        break;
    }
}

/* Item `idx` of a level, fetching from the server as needed. */
static struct item *get_item(struct level *l, int idx)
{
    static struct item tmp;
    if (l->local) {
        settings_item(idx, &tmp);
        return &tmp;
    }
    if (is_root(l)) {
        int pre = root_pre(l);
        if (idx < pre) {
            memset(&tmp, 0, sizeof(tmp));
            tmp.kind = 'N';
            strlcpy(tmp.label, "Now Playing", sizeof(tmp.label));
            return &tmp;
        }
        if (idx - pre >= l->total) {
            memset(&tmp, 0, sizeof(tmp));
            tmp.kind = 'L';
            strlcpy(tmp.label, "Settings", sizeof(tmp.label));
            return &tmp;
        }
        idx -= pre;
    }
    if (!l->loaded || idx < l->win_base || idx >= l->win_base + l->win_count) {
        if (fetch_window(l, idx) < 0)
            return 0;
    }
    if (idx < l->win_base || idx >= l->win_base + l->win_count)
        return 0;
    return &l->win[idx - l->win_base];
}

static void push_level(const char *node, const char *title, int local)
{
    if (depth >= MAX_DEPTH)
        return;
    struct level *l = &stack[depth++];
    memset(l, 0, sizeof(*l));
    strlcpy(l->node, node, sizeof(l->node));
    strlcpy(l->title, title, sizeof(l->title));
    l->local = local;
    if (!local && fetch_window(l, 0) < 0 && depth > 1 && menu_error[0]) {
        /* keep the level so the error is visible; BACK pops it */
    }
    marquee_start = time_ms();
    screen = SCR_MENU;
    dirty = 1;
}

/* ---- rendering --------------------------------------------------------- */

static int marquee_offset(const struct font *f, const char *s, int avail)
{
    int w = gfx_text_width(f, s);
    if (w <= avail)
        return 0;
    uint32_t t = time_ms() - marquee_start;
    if (t < 1500)
        return 0;
    int travel = w - avail + 12;
    int px = (int)((t - 1500) / 40); /* 25 px/s */
    int cycle = travel + 40;         /* hold at the end for a moment */
    px %= cycle;
    return px > travel ? travel : px;
}

static int needs_marquee(const struct font *f, const char *s, int avail)
{
    return gfx_text_width(f, s) > avail;
}

static void draw_title_bar(const char *title)
{
    gfx_text_center(&font_small, 0, title, 3);
    if (P.state == P_PLAYING || P.state == P_BUFFERING)
        gfx_icon(ICON_PLAY, 2, 2, 3);
    else if (P.state == P_PAUSED)
        gfx_icon(ICON_PAUSE, 2, 2, 3);
    gfx_hline(0, 11, GFX_W, 1);
}

static int animating;

static void render_menu(void)
{
    struct level *l = cur();
    int total = level_total(l);
    draw_title_bar(depth == 1 && !l->local ? "Music" : l->title);
    if (total == 0) {
        gfx_text_center(&font_medium, 30, menu_error[0] ? menu_error : "Empty", 2);
        return;
    }
    if (l->cursor >= total)
        l->cursor = total - 1;
    if (l->cursor < l->top)
        l->top = l->cursor;
    if (l->cursor >= l->top + ROWS)
        l->top = l->cursor - ROWS + 1;
    int scroll = total > ROWS;
    int right = scroll ? GFX_W - 4 : GFX_W;
    for (int r = 0; r < ROWS && l->top + r < total; r++) {
        int idx = l->top + r;
        int y = LIST_Y + r * ROW_H;
        struct item *it = get_item(l, idx);
        if (!it) {
            gfx_text(&font_medium, 3, y, menu_error[0] ? menu_error : "...", 2, INK_NORMAL);
            continue;
        }
        int sel = idx == l->cursor;
        int ink = sel ? INK_ERASE : INK_NORMAL;
        if (sel)
            gfx_fill(0, y, right, ROW_H, 3);
        int text_right = right - 3;
        if (it->kind == 'M' || it->kind == 'L' || it->kind == 'N') {
            text_right -= 8;
            gfx_icon(ICON_CHEVRON, right - 9, y + 3, sel ? 0 : 2);
        } else if (it->kind == 'S') {
            text_right -= 10;
            gfx_icon(ICON_SHUFFLE, right - 10, y + 3, sel ? 0 : 2);
        } else if (it->kind == 'T' && P.have_track && strtoul(it->ref, 0, 16) == P.cur.fid) {
            text_right -= 9;
            gfx_icon(ICON_SPEAKER, right - 10, y + 3, sel ? 0 : 3);
        }
        int avail = text_right - 3;
        if (it->detail[0]) {
            int dw = gfx_text_width(&font_small, it->detail);
            int lw = gfx_text_width(&font_medium, it->label);
            if (lw + dw + 8 <= avail || it->kind == 'C') {
                gfx_clip(0, y, right, y + ROW_H);
                gfx_text_right(&font_small, text_right, y + 2, it->detail, sel ? 3 : 1, ink);
                gfx_noclip();
                avail -= dw + 6;
            }
        }
        gfx_clip(3, y, 3 + avail, y + ROW_H);
        if (sel && needs_marquee(&font_medium, it->label, avail)) {
            animating = 1;
            gfx_text(&font_medium, 3 - marquee_offset(&font_medium, it->label, avail), y, it->label, 3, ink);
        } else {
            gfx_noclip();
            gfx_clip(0, y, right, y + ROW_H);
            gfx_text_fit(&font_medium, 3, y, avail, it->label, 3, ink);
        }
        gfx_noclip();
    }
    if (scroll) {
        int track_h = GFX_H - LIST_Y;
        int thumb = track_h * ROWS / total;
        if (thumb < 4)
            thumb = 4;
        int pos = LIST_Y + (track_h - thumb) * l->top / (total - ROWS);
        gfx_vline(GFX_W - 2, LIST_Y, track_h, 1);
        gfx_fill(GFX_W - 3, pos, 3, thumb, 3);
    }
}

static void render_now(void)
{
    char buf[48], t1[16], t2[16];
    if (P.qlen)
        snprintf(buf, sizeof(buf), "%d of %d", P.qi + 1, P.qlen);
    else
        strlcpy(buf, "Now Playing", sizeof(buf));
    draw_title_bar(buf);
    if (P.state == P_STOPPED)
        gfx_icon(ICON_STOP, 2, 2, 3);
    if (P.shuffle)
        gfx_icon(ICON_SHUFFLE, GFX_W - (P.repeat ? 18 : 9), 2, 3);
    if (P.repeat)
        gfx_icon(P.repeat == REPEAT_ONE ? ICON_REPEAT1 : ICON_REPEAT, GFX_W - 9, 2, 3);
    if (!P.have_track) {
        gfx_text_center(&font_medium, 28, "Nothing playing", 2);
        return;
    }
    const char *title = P.cur.title[0] ? P.cur.title : "Unknown";
    int off = marquee_offset(&font_large, title, GFX_W - 4);
    if (needs_marquee(&font_large, title, GFX_W - 4)) {
        animating = 1;
        gfx_clip(2, 12, GFX_W - 2, 29);
        gfx_text(&font_large, 2 - off, 12, title, 3, INK_NORMAL);
        gfx_noclip();
    } else {
        gfx_text_center(&font_large, 12, title, 3);
    }
    gfx_text_center(&font_medium, 29, P.cur.artist, 3);
    gfx_text_center(&font_medium, 41, P.cur.album, 2);

    uint32_t now = time_ms();
    if ((int32_t)(vol_until - now) > 0) {
        gfx_icon(ICON_SPEAKER, 1, 56, 3);
        gfx_rect(12, 57, 100, 5, 1);
        gfx_fill(13, 58, 98 * P.volume / 100, 3, 3);
        snprintf(buf, sizeof(buf), "%d", P.volume);
        gfx_text_right(&font_small, GFX_W - 1, 54, buf, 3, INK_NORMAL);
        return;
    }
    uint32_t pos = player_position_ms();
    uint32_t dur = P.cur.dur_ms;
    if (dur && pos > dur)
        pos = dur;
    fmt_time(t1, sizeof(t1), pos);
    buf[0] = '-';
    fmt_time(t2, sizeof(t2), dur > pos ? dur - pos : 0);
    strlcpy(buf + 1, t2, sizeof(buf) - 1);
    gfx_text(&font_small, 1, 54, t1, 3, INK_NORMAL);
    gfx_text_right(&font_small, GFX_W - 1, 54, buf, 3, INK_NORMAL);
    int bx = 30, bw = GFX_W - 60;
    gfx_rect(bx, 57, bw, 5, 1);
    if (dur)
        gfx_fill(bx + 1, 58, (int)((uint64_t)(bw - 2) * pos / dur), 3, 3);
    if (P.state == P_BUFFERING && (now / 400) % 2)
        gfx_fill(bx + 1, 58, 2, 3, 2);
}

static void render_status(void)
{
    if (!P.status[0] || (int32_t)(P.status_until - time_ms()) <= 0)
        return;
    int w = gfx_text_width(&font_medium, P.status) + 10;
    if (w > GFX_W - 4)
        w = GFX_W - 4;
    int x = (GFX_W - w) / 2;
    gfx_fill(x, 22, w, 20, 0);
    gfx_rect(x, 22, w, 20, 3);
    gfx_clip(x + 1, 22, x + w - 1, 42);
    gfx_text_center(&font_medium, 25, P.status, 3);
    gfx_noclip();
    animating = 1;
}

static void render(void)
{
    animating = 0;
    gfx_clear();
    gfx_noclip();
    if (screen == SCR_NOW)
        render_now();
    else
        render_menu();
    render_status();
    display_push();
    last_render = time_ms();
    dirty = 0;
}

void ui_boot_screen(const char *line1, const char *line2)
{
    gfx_clear();
    gfx_text_center(&font_large, 8, "Rio", 3);
    gfx_text_center(&font_small, 26, "RioServer NG " VERSION, 2);
    gfx_hline(24, 37, 80, 1);
    if (line1)
        gfx_text_center(&font_medium, 39, line1, 3);
    if (line2)
        gfx_text_center(&font_small, 53, line2, 2);
    display_push();
}

/* ---- actions ----------------------------------------------------------- */

static void notify(const char *msg)
{
    strlcpy(P.status, msg, sizeof(P.status));
    P.status_until = time_ms() + 1500;
    dirty = 1;
}

static void show_now_playing(void)
{
    screen = SCR_NOW;
    marquee_start = time_ms();
    dirty = 1;
}

static void play_from(struct level *l, struct item *it, int idx, int shuffle)
{
    int ok = -1;
    if (it->kind == 'T') {
        int first = idx - (is_root(l) ? root_pre(l) : 0);
        ok = player_play_node(l->node, first, shuffle || P.shuffle);
    } else if (it->kind == 'M') {
        ok = player_play_node(it->ref, -1, shuffle || P.shuffle);
    } else if (it->kind == 'S') {
        ok = player_play_node(it->ref, -1, 1);
    }
    if (ok == 0)
        show_now_playing();
    dirty = 1;
}

static void activate_setting(int idx)
{
    switch (idx) {
    case 0:
        player_set_shuffle(!P.shuffle);
        break;
    case 1:
        P.repeat = (enum repeat_mode)((P.repeat + 1) % 3);
        break;
    case 2:
        player_set_volume(P.volume >= 100 ? 10 : P.volume + 10);
        break;
    case 3:
        backlight_auto = !backlight_auto;
        break;
    case 4:
        notify(g_server.host);
        break;
    case 5:
        notify("RioServer NG " VERSION);
        break;
    case 6:
        ui_boot_screen("Restarting...", 0);
        player_stop();
        sys_nanosleep(500);
        sys_reboot();
        break;
    }
    dirty = 1;
}

static void select_item(int play_key)
{
    struct level *l = cur();
    if (!level_total(l))
        return;
    struct item *it = get_item(l, l->cursor);
    if (!it)
        return;
    struct item copy = *it;
    switch (copy.kind) {
    case 'M':
        if (play_key)
            play_from(l, &copy, l->cursor, 0);
        else
            push_level(copy.ref, copy.label, 0);
        break;
    case 'T':
    case 'S':
        play_from(l, &copy, l->cursor, 0);
        break;
    case 'N':
        if (play_key)
            player_toggle();
        show_now_playing();
        break;
    case 'L':
        push_level("settings", "Settings", 1);
        break;
    case 'C':
        activate_setting(l->cursor);
        break;
    }
}

static void change_volume(int delta)
{
    player_set_volume(P.volume + delta);
    vol_until = time_ms() + 1500;
    dirty = 1;
}

static void move_cursor(int delta)
{
    struct level *l = cur();
    int total = level_total(l);
    int c = l->cursor + delta;
    if (c < 0)
        c = 0;
    if (c >= total)
        c = total - 1;
    if (c != l->cursor) {
        l->cursor = c;
        marquee_start = time_ms();
        dirty = 1;
    }
}

void ui_key(int key)
{
    int wheel = key & KEY_FROM_WHEEL;
    key &= ~KEY_FROM_WHEEL;
    last_key = time_ms();
    if (!backlight_on) {
        display_backlight(1);
        backlight_on = 1;
    }
    if (standby) {
        if (key == KEY_POWER || key == KEY_PLAY) {
            standby = 0;
            display_power(1);
            dirty = 1;
        }
        return;
    }
    switch (key) {
    case KEY_POWER:
        standby = 1;
        player_stop();
        gfx_clear();
        display_push();
        display_power(0);
        return;
    case KEY_POWER_HELD:
        activate_setting(6);
        return;
    case KEY_NEXT:
        player_next();
        dirty = 1;
        return;
    case KEY_PREV:
        player_prev();
        dirty = 1;
        return;
    case KEY_STOP:
        player_stop();
        dirty = 1;
        return;
    case KEY_VOLUP:
        change_volume(5);
        return;
    case KEY_VOLDOWN:
        change_volume(-5);
        return;
    case KEY_RANDOM:
        player_set_shuffle(!P.shuffle);
        notify(P.shuffle ? "Shuffle On" : "Shuffle Off");
        return;
    case KEY_LIST:
        if (P.have_track)
            show_now_playing();
        return;
    case KEY_SEARCH:
        while (depth > 1)
            depth--;
        screen = SCR_MENU;
        dirty = 1;
        return;
    }
    if (screen == SCR_NOW) {
        switch (key) {
        case KEY_UP:
            change_volume(wheel ? -2 : 5);
            break;
        case KEY_DOWN:
            change_volume(wheel ? 2 : -5);
            break;
        case KEY_PLAY:
            player_toggle();
            dirty = 1;
            break;
        case KEY_BACK:
        case KEY_SELECT:
            screen = SCR_MENU;
            marquee_start = time_ms();
            dirty = 1;
            break;
        }
        return;
    }
    switch (key) {
    case KEY_UP:
        move_cursor(-1);
        break;
    case KEY_DOWN:
        move_cursor(1);
        break;
    case KEY_SELECT:
        select_item(0);
        break;
    case KEY_PLAY:
        if (!level_total(cur()))
            player_toggle();
        else
            select_item(1);
        dirty = 1;
        break;
    case KEY_BACK:
        if (depth > 1) {
            depth--;
            marquee_start = time_ms();
        } else if (P.have_track) {
            show_now_playing();
        }
        dirty = 1;
        break;
    }
}

/* ---- server polling ---------------------------------------------------- */

static void run_command(char **f, int nf)
{
    const char *verb = f[1];
    if (!strcmp(verb, "play") && nf >= 3) {
        int first = nf >= 4 ? atoi(f[3]) : -1;
        int shuffle = nf >= 5 ? atoi(f[4]) : 0;
        if (player_play_node(f[2], first, shuffle) == 0 && !standby)
            show_now_playing();
    } else if (!strcmp(verb, "pause")) {
        player_pause();
    } else if (!strcmp(verb, "resume")) {
        player_resume();
    } else if (!strcmp(verb, "toggle")) {
        player_toggle();
    } else if (!strcmp(verb, "stop")) {
        player_stop();
    } else if (!strcmp(verb, "next")) {
        player_next();
    } else if (!strcmp(verb, "prev")) {
        player_prev();
    } else if (!strcmp(verb, "vol") && nf >= 3) {
        player_set_volume(atoi(f[2]));
        vol_until = time_ms() + 1500;
    } else if (!strcmp(verb, "seek") && nf >= 3) {
        player_seek_ms(strtoul(f[2], 0, 10));
    } else if (!strcmp(verb, "msg") && nf >= 3) {
        strlcpy(P.status, f[2], sizeof(P.status));
        P.status_until = time_ms() + 5000;
    }
    dirty = 1;
}

void ui_poll_server(void)
{
    static const char *const states[] = {"stop", "buffer", "play", "pause"};
    static const char *const repeats[] = {"off", "all", "one"};
    char path[256], buf[2048];
    snprintf(path, sizeof(path),
             "/ng/poll?mac=%s&st=%s&id=%lx&pos=%lu&vol=%d&qi=%d&qn=%d&shuf=%d&rep=%s&ver=" VERSION,
             net_mac(), standby ? "standby" : states[P.state],
             (unsigned long)(P.have_track ? P.cur.fid : 0), (unsigned long)player_position_ms(),
             P.volume, P.qi, P.qlen, P.shuffle, repeats[P.repeat]);
    int n = http_get(path, buf, sizeof(buf), 1500);
    if (n <= 0)
        return;
    for (char *line = buf; line && *line;) {
        char *next = strchr(line, '\n');
        if (next)
            *next++ = 0;
        char *f[6] = {0};
        int nf = 0;
        for (char *p = line; p && nf < 6;) {
            f[nf++] = p;
            p = strchr(p, '\t');
            if (p)
                *p++ = 0;
        }
        if (nf >= 2 && !strcmp(f[0], "lib")) {
            uint32_t v = strtoul(f[1], 0, 10);
            if (lib_version && v != lib_version) {
                for (int i = 0; i < depth; i++)
                    stack[i].loaded = 0; /* library changed: refetch menus */
                dirty = 1;
            }
            lib_version = v;
        } else if (nf >= 2 && !strcmp(f[0], "cmd")) {
            run_command(f, nf);
        }
        line = next;
    }
}

/* ---- main hooks -------------------------------------------------------- */

void ui_init(void)
{
    depth = 0;
    push_level("root", "Music", 0);
    last_key = time_ms();
}

void ui_tick(uint32_t now)
{
    if (standby)
        return;
    if (backlight_auto && backlight_on && now - last_key > BACKLIGHT_TIMEOUT) {
        display_backlight(0);
        backlight_on = 0;
    }
    static uint32_t last_second;
    static enum pstate last_state;
    static int last_qi;
    if (screen == SCR_NOW && player_position_ms() / 1000 != last_second) {
        last_second = player_position_ms() / 1000;
        dirty = 1;
    }
    if (P.state != last_state || P.qi != last_qi) {
        last_state = P.state;
        last_qi = P.qi;
        marquee_start = now;
        dirty = 1;
    }
    if (animating && now - last_render >= 120)
        dirty = 1;
    if ((int32_t)(vol_until - now) <= 0 && vol_until && now - vol_until < 200)
        dirty = 1;
    if (dirty)
        render();
}
