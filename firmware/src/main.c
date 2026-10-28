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
/* Polls run alongside playback, so a slow server only delays commands. A
 * poll that gives up loses any commands the server has already handed out,
 * so allow plenty of time. */
#define POLL_TIMEOUT 3000

/* Wait for the remote, the player's socket, a wake-up from the server or
 * progress on the status poll. Returns WAIT_* flags. */
#define WAIT_KEY 1
#define WAIT_WAKE 2
#define WAIT_POLL 4
static int wait_input(int player_fd, int wake_fd, int poll_fd, int poll_write, int timeout_ms)
{
    fdset_t r, w;
    int maxfd = -1;
    fd_zero(&r);
    fd_zero(&w);
    int ir = input_fd();
    int fds[4] = {ir, player_fd, wake_fd, poll_write ? -1 : poll_fd};
    for (int i = 0; i < 4; i++) {
        if (fds[i] < 0)
            continue;
        fd_add(fds[i], &r);
        if (fds[i] > maxfd)
            maxfd = fds[i];
    }
    if (poll_write && poll_fd >= 0) {
        fd_add(poll_fd, &w);
        if (poll_fd > maxfd)
            maxfd = poll_fd;
    }
    struct timeval tv = {timeout_ms / 1000, (timeout_ms % 1000) * 1000};
    if (maxfd < 0) {
        if (timeout_ms)
            sys_nanosleep(timeout_ms);
        return 0;
    }
    if (sys_select(maxfd + 1, &r, &w, 0, &tv) <= 0)
        return 0;
    return (ir >= 0 && fd_has(ir, &r) ? WAIT_KEY : 0) | (wake_fd >= 0 && fd_has(wake_fd, &r) ? WAIT_WAKE : 0) |
           (poll_fd >= 0 && (fd_has(poll_fd, &r) || fd_has(poll_fd, &w)) ? WAIT_POLL : 0);
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
        int key = wait_input(-1, -1, -1, 0, 1000) ? input_read() : KEY_NONE;
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
    uint32_t last_poll = time_ms(), next_poll = last_poll, poll_started = 0;
    int polling = 0, poll_again = 0;
    /* The longest the loop went round while playing, i.e. how long audio
     * output was held up; the audio device only buffers a fraction of a
     * second. Reported to the server with each poll. */
    uint32_t loop_at = time_ms(), stall = 0;
    int was_playing = 0;

    for (;;) {
        uint32_t t = time_ms();
        if (was_playing && P.state == P_PLAYING && t - loop_at > stall)
            stall = t - loop_at;
        loop_at = t;
        was_playing = P.state == P_PLAYING;

        int busy = 0;
        int pfd = player_service(&busy);
        int poll_write = 0;
        int qfd = polling ? ui_poll_fd(&poll_write) : -1;
        int ev = wait_input(pfd, wake_fd, qfd, poll_write, busy ? 0 : 40);
        if (ev & WAIT_KEY) {
            int key;
            while ((key = input_read()) != KEY_NONE)
                ui_key(key);
        }
        if ((ev & WAIT_WAKE) && net_wake_drain(wake_fd)) {
            if (polling)
                poll_again = 1; /* the poll in flight may predate the command */
            else
                next_poll = last_poll + POLL_MIN_GAP;
        }
        uint32_t now = time_ms();
        if (polling && ((ev & WAIT_POLL) || now - poll_started >= POLL_TIMEOUT)) {
            int ran = ui_poll_step();
            if (ran >= 0) {
                polling = 0;
                last_poll = time_ms();
                next_poll = last_poll + (poll_again ? POLL_MIN_GAP : ran ? POLL_SOON : POLL_INTERVAL);
                poll_again = 0;
            }
        } else if (!polling && (int32_t)(now - next_poll) >= 0) {
            if (ui_poll_start(POLL_TIMEOUT, stall) == 0) {
                polling = 1;
                poll_started = now;
                stall = 0;
            } else {
                last_poll = now;
                next_poll = now + POLL_INTERVAL;
            }
        }
        ui_tick(now);
    }
}
