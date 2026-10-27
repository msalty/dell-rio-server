#include "hw.h"
#include "gfx.h"
#include "sys.h"

/* empeg display driver ioctls */
#define DIS_IOCREFRESH 0x00006400    /* _IO('d', 0) */
#define DIS_IOCSPOWER 0x40046401     /* _IOW('d', 1, int), value passed directly */
#define DIS_IOCSBACKLIGHT 0x4004640b /* _IOW('d', 11, int), pointer */
#define MIXER_WRITE_VOLUME 0xc0044d00 /* _IOWR('M', 0, int) */

#define AUDIO_BLOCK 4608 /* bytes per write expected by the driver */

static int sim;
static int disp_fd = -1, ir_fd = -1, audio_fd = -1, mixer_fd = -1;
static uint8_t *disp_map;
static const char *sim_display;
static uint8_t audio_buf[AUDIO_BLOCK];
static int audio_fill;
static uint32_t sim_audio_start;
static uint32_t sim_audio_frames;
static uint32_t power_pressed_at;

int hw_simulated(void) { return sim; }

int hw_init(void)
{
    sim_display = getenv("RIO_SIM_DISPLAY");
    sim = sim_display != 0;
    if (sim) {
        const char *ir = getenv("RIO_SIM_IR");
        if (ir)
            ir_fd = sys_open(ir, O_RDWR | O_NONBLOCK, 0); /* RDWR keeps the FIFO open */
        return 0;
    }
    disp_fd = sys_open("/dev/display", O_RDWR, 0);
    if (disp_fd >= 0) {
        disp_map = sys_mmap(0, GFX_BYTES, 3 /* PROT_READ|PROT_WRITE */, 1 /* MAP_SHARED */, disp_fd, 0);
        if ((long)disp_map < 0 && (long)disp_map > -4096)
            disp_map = 0;
    } else {
        dlog("display: open failed (%d)\n", disp_fd);
    }
    ir_fd = sys_open("/dev/ir", O_RDONLY | O_NONBLOCK, 0);
    if (ir_fd < 0)
        dlog("ir: open failed (%d)\n", ir_fd);
    return 0;
}

void display_push(void)
{
    if (sim) {
        int fd = sys_open(sim_display, O_WRONLY | O_CREAT, 0644);
        if (fd >= 0) {
            sys_write(fd, gfx_fb, GFX_BYTES);
            sys_close(fd);
        }
        return;
    }
    if (disp_map) {
        memcpy(disp_map, gfx_fb, GFX_BYTES);
        sys_ioctl(disp_fd, DIS_IOCREFRESH, 0);
    }
}

void display_power(int on)
{
    if (!sim && disp_fd >= 0)
        sys_ioctl(disp_fd, DIS_IOCSPOWER, on ? 1 : 0);
}

void display_backlight(int on)
{
    int state = on ? 4 : 0;
    if (!sim && disp_fd >= 0)
        sys_ioctl(disp_fd, DIS_IOCSBACKLIGHT, (long)&state);
}

int input_fd(void) { return ir_fd; }

/* Raw codes from /dev/ir. Panel buttons report press (even) and release
 * (odd); remote buttons report presses, with bit 31 set on auto-repeat. */
int input_read(void)
{
    uint32_t code;
    if (ir_fd < 0 || sys_read(ir_fd, &code, 4) != 4)
        return KEY_NONE;
    uint32_t base = code & 0x7fffffff;
    int repeat = (code & 0x80000000u) != 0;
    switch (base) {
    case 0x00028000: return KEY_DOWN | KEY_FROM_WHEEL; /* wheel clockwise */
    case 0x00028001: return KEY_UP | KEY_FROM_WHEEL;   /* wheel anticlockwise */
    case 0x0000018c: return KEY_UP;
    case 0x00000186: return KEY_DOWN;
    case 0x0000018a: return KEY_VOLUP;
    case 0x00000181: return KEY_VOLDOWN;
    }
    if (repeat)
        return KEY_NONE;
    switch (base) {
    case 0x00010010: case 0x00000182: return KEY_SELECT;
    case 0x00010012: case 0x00000188: return KEY_BACK;
    case 0x00010004: case 0x00000185: return KEY_PLAY;
    case 0x00010002: case 0x00000190: return KEY_STOP;
    case 0x00010008: case 0x00000187: return KEY_NEXT;
    case 0x0001000a: case 0x00000183: return KEY_PREV;
    case 0x00010014: case 0x00000189: return KEY_RANDOM;
    case 0x0000019f: return KEY_SEARCH;
    case 0x0000019d: return KEY_LIST;
    case 0x00000184: return KEY_POWER;
    case 0x00010018: /* panel power pressed */
        power_pressed_at = time_ms();
        return KEY_NONE;
    case 0x00010019: /* panel power released */
        if (power_pressed_at && time_ms() - power_pressed_at >= 5000)
            return KEY_POWER_HELD;
        return KEY_POWER;
    }
    if (!(base & 1))
        dlog("ir: unhandled code %08x\n", (unsigned)code);
    return KEY_NONE;
}

int audio_open(void)
{
    if (sim) {
        const char *path = getenv("RIO_SIM_AUDIO");
        if (path)
            audio_fd = sys_open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        return 0;
    }
    audio_fd = sys_open("/dev/audio", O_WRONLY, 0);
    mixer_fd = sys_open("/dev/mixer", O_WRONLY, 0);
    if (audio_fd < 0)
        dlog("audio: open failed (%d)\n", audio_fd);
    return audio_fd;
}

static void audio_flush_block(void)
{
    if (audio_fd >= 0)
        sys_write(audio_fd, audio_buf, AUDIO_BLOCK);
    if (sim) {
        /* Emulate a device that consumes audio in real time with ~0.3s of
         * buffering, so playback timing behaves as on the hardware. */
        uint32_t now = time_ms();
        if (!sim_audio_start || now - sim_audio_start > sim_audio_frames / 44 + 1000) {
            sim_audio_start = now;
            sim_audio_frames = 0;
        }
        sim_audio_frames += AUDIO_BLOCK / 4;
        uint32_t due = sim_audio_start + sim_audio_frames / 44; /* ~44.1 frames per ms */
        if ((int32_t)(due - now) > 300)
            sys_nanosleep((long)(due - now) - 300);
    }
    audio_fill = 0;
}

void audio_play(const int16_t *pcm, int frames)
{
    const uint8_t *src = (const uint8_t *)pcm;
    int bytes = frames * 4;
    while (bytes > 0) {
        int n = AUDIO_BLOCK - audio_fill;
        if (n > bytes)
            n = bytes;
        memcpy(audio_buf + audio_fill, src, (size_t)n);
        audio_fill += n;
        src += n;
        bytes -= n;
        if (audio_fill == AUDIO_BLOCK)
            audio_flush_block();
    }
}

void audio_set_volume(int percent)
{
    if (percent < 0)
        percent = 0;
    if (percent > 100)
        percent = 100;
    int v = percent | (percent << 8);
    if (mixer_fd >= 0)
        sys_ioctl(mixer_fd, MIXER_WRITE_VOLUME, (long)&v);
}
