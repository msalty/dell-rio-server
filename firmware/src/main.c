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

#define POLL_SOON 300   /* re-poll after a command, to report the new state */
#define POLL_MIN_GAP 150 /* never poll more often than this (CPU budget) */

/* Wait for the remote, the player's socket or a wake-up from the server.
 * Returns WAIT_KEY and/or WAIT_WAKE. */
#define WAIT_KEY 1
#define WAIT_WAKE 2
static int wait_input(int player_fd, int wake_fd, int timeout_ms)
{
    fdset_t r;
    int maxfd = -1;
    fd_zero(&r);
    int ir = input_fd();
    int fds[3] = {ir, player_fd, wake_fd};
    for (int i = 0; i < 3; i++) {
        if (fds[i] < 0)
            continue;
        fd_add(fds[i], &r);
        if (fds[i] > maxfd)
            maxfd = fds[i];
    }
    struct timeval tv = {timeout_ms / 1000, (timeout_ms % 1000) * 1000};
    if (maxfd < 0) {
        if (timeout_ms)
            sys_nanosleep(timeout_ms);
        return 0;
    }
    if (sys_select(maxfd + 1, &r, 0, 0, &tv) <= 0)
        return 0;
    return (ir >= 0 && fd_has(ir, &r) ? WAIT_KEY : 0) | (wake_fd >= 0 && fd_has(wake_fd, &r) ? WAIT_WAKE : 0);
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
    ui_boot_intro("Starting...");
    audio_open();
    player_init();

    /* find the music server */
    for (int tries = 1; net_discover(3) != 0; tries++) {
        char msg[32];
        snprintf(msg, sizeof(msg), "attempt %d", tries);
        ui_boot_screen("Looking for server", msg);
        int key = wait_input(-1, -1, 1000) ? input_read() : KEY_NONE;
        if (key == KEY_POWER_HELD)
            sys_reboot();
    }
    {
        char msg[40];
        snprintf(msg, sizeof(msg), "%s:%u", g_server.host, g_server.port);
        ui_boot_screen("Found music server", msg);
    }
    int wake_port = 0;
    int wake_fd = net_wake_open(&wake_port);
    ui_init(wake_fd >= 0 ? wake_port : 0);
    uint32_t last_poll = time_ms();
    uint32_t next_poll = last_poll + (ui_poll_server() ? POLL_SOON : POLL_INTERVAL);

    for (;;) {
        int busy = 0;
        int pfd = player_service(&busy);
        int ev = wait_input(pfd, wake_fd, busy ? 0 : 40);
        if (ev & WAIT_KEY) {
            int key;
            while ((key = input_read()) != KEY_NONE)
                ui_key(key);
        }
        if ((ev & WAIT_WAKE) && net_wake_drain(wake_fd))
            next_poll = last_poll + POLL_MIN_GAP;
        uint32_t now = time_ms();
        if ((int32_t)(now - next_poll) >= 0) {
            int ran = ui_poll_server();
            last_poll = time_ms();
            next_poll = last_poll + (ran ? POLL_SOON : POLL_INTERVAL);
        }
        ui_tick(now);
    }
}
