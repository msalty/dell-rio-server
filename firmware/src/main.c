/*
 * RioServer NG receiver firmware.
 *
 * Runs as /sbin/init on the Rio Receiver / Dell Digital Audio Receiver once
 * the kernel has mounted its root filesystem from the server. A single event
 * loop handles the remote, the display, server polling and playback.
 *
 * This firmware is free software, distributed under the GNU General Public
 * License version 2 or later (see COPYING).
 */
#include "gfx.h"
#include "hw.h"
#include "net.h"
#include "player.h"
#include "sys.h"
#include "ui.h"

#define POLL_INTERVAL 3000

static int wait_input(int extra_fd, int timeout_ms)
{
    fdset_t r;
    int maxfd = -1;
    fd_zero(&r);
    int ir = input_fd();
    if (ir >= 0) {
        fd_add(ir, &r);
        maxfd = ir;
    }
    if (extra_fd >= 0) {
        fd_add(extra_fd, &r);
        if (extra_fd > maxfd)
            maxfd = extra_fd;
    }
    struct timeval tv = {timeout_ms / 1000, (timeout_ms % 1000) * 1000};
    if (maxfd < 0) {
        if (timeout_ms)
            sys_nanosleep(timeout_ms);
        return 0;
    }
    int n = sys_select(maxfd + 1, &r, 0, 0, &tv);
    return n > 0 && ir >= 0 && fd_has(ir, &r);
}

int main(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    dlog("\nRioServer NG receiver firmware starting\n");
    hw_init();
    display_power(1);
    display_backlight(1);
    srand32(time_ms() ^ 0x5eed1234u);
    ui_boot_screen("Starting...", 0);
    audio_open();
    player_init();

    /* find the music server */
    for (int tries = 1; net_discover(3) != 0; tries++) {
        char msg[32];
        snprintf(msg, sizeof(msg), "attempt %d", tries);
        ui_boot_screen("Looking for server", msg);
        int key = wait_input(-1, 1000) ? input_read() : KEY_NONE;
        if (key == KEY_POWER_HELD)
            sys_reboot();
    }
    {
        char msg[40];
        snprintf(msg, sizeof(msg), "%s:%u", g_server.host, g_server.port);
        ui_boot_screen("Found music server", msg);
    }
    ui_init();
    ui_poll_server();
    uint32_t next_poll = time_ms() + POLL_INTERVAL;

    for (;;) {
        int busy = 0;
        int pfd = player_service(&busy);
        if (wait_input(pfd, busy ? 0 : 40)) {
            int key;
            while ((key = input_read()) != KEY_NONE)
                ui_key(key);
        }
        uint32_t now = time_ms();
        if ((int32_t)(now - next_poll) >= 0) {
            ui_poll_server();
            next_poll = time_ms() + POLL_INTERVAL;
        }
        ui_tick(now);
    }
}
