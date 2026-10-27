/*
 * Receiver hardware: display, IR remote / front panel, audio.
 *
 * Device interfaces follow RioPlay (David Flowerday): /dev/display is an
 * mmap()able frame buffer refreshed with ioctl 'd' 0, /dev/ir yields 32-bit
 * key codes, /dev/audio takes 16-bit stereo 44.1kHz PCM in 4608 byte blocks
 * and /dev/mixer sets the volume.
 *
 * For development the same binary runs under qemu-arm with these
 * environment variables instead of the devices:
 *   RIO_SIM_DISPLAY  file that receives the 4096-byte frame buffer on refresh
 *   RIO_SIM_IR       FIFO delivering 32-bit little-endian key codes
 *   RIO_SIM_AUDIO    file receiving the PCM stream (played back in real time)
 */
#ifndef HW_H
#define HW_H

#include <stdint.h>

enum key {
    KEY_NONE = 0,
    KEY_UP,      /* remote up, panel wheel anticlockwise */
    KEY_DOWN,    /* remote down, panel wheel clockwise */
    KEY_SELECT,  /* remote enter, wheel push */
    KEY_BACK,    /* menu button */
    KEY_PLAY,    /* play/pause */
    KEY_STOP,
    KEY_NEXT,
    KEY_PREV,
    KEY_VOLUP,   /* remote only; the panel wheel doubles as volume */
    KEY_VOLDOWN,
    KEY_POWER,
    KEY_POWER_HELD, /* front panel power held for 5 seconds */
    KEY_RANDOM,
    KEY_SEARCH,
    KEY_LIST,
};

/* Codes from the wheel carry this flag so the UI can tell wheel from remote. */
#define KEY_FROM_WHEEL 0x100

int hw_init(void);
int hw_simulated(void);

void display_push(void);
void display_power(int on);
void display_backlight(int on);

int input_fd(void);
int input_read(void); /* returns an enum key (| KEY_FROM_WHEEL), or KEY_NONE */

int audio_open(void);
/* Queue interleaved stereo 16-bit samples at 44.1kHz. Blocks when the
 * device buffer is full, which paces the main loop. */
void audio_play(const int16_t *pcm, int frames);
void audio_set_volume(int percent);

#endif
