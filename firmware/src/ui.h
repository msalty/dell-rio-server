#ifndef UI_H
#define UI_H

#include <stdint.h>

void ui_boot_screen(const char *line1, const char *line2);
void ui_boot_intro(const char *line1);
void ui_init(int wake_port);
void ui_key(int key);
void ui_tick(uint32_t now);
/* Status polls run alongside playback: start one, wait on ui_poll_fd()
 * (*want_write set while it is connecting), and call ui_poll_step() when the
 * socket is ready or the timeout has passed. ui_poll_step() returns -1 while
 * the poll is in flight, then the number of commands it ran. stall_ms
 * reports the longest hold-up of the main loop during playback. */
int ui_poll_start(int timeout_ms, unsigned stall_ms);
int ui_poll_fd(int *want_write);
int ui_poll_step(void);
void ui_dirty(void);

#endif
