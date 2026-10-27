#ifndef UI_H
#define UI_H

#include <stdint.h>

void ui_boot_screen(const char *line1, const char *line2);
void ui_init(void);
void ui_key(int key);
void ui_tick(uint32_t now);
void ui_poll_server(void);
void ui_dirty(void);

#endif
