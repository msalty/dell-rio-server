#ifndef PLAYER_H
#define PLAYER_H

#include <stdint.h>

enum pstate { P_STOPPED, P_BUFFERING, P_PLAYING, P_PAUSED };
enum repeat_mode { REPEAT_OFF, REPEAT_ALL, REPEAT_ONE };

struct track_info {
    uint32_t fid;
    char title[96];
    char artist[64];
    char album[64];
    int track, tracks;
    uint32_t dur_ms;
    uint32_t size;
    int kbps;
};

struct player {
    enum pstate state;
    uint32_t *queue; /* playback order */
    uint32_t *orig;  /* order as delivered by the server (for un-shuffling) */
    int qlen, qi;
    int shuffle;
    enum repeat_mode repeat;
    int volume;
    struct track_info cur;
    int have_track;
    char status[48]; /* transient message for the UI, e.g. "Can't reach server" */
    uint32_t status_until;
};

extern struct player P;

void player_init(void);
/* Replace the queue with the tracks of a server menu node and start playing
 * at `first` (an index into that node's track list). */
int player_play_node(const char *node, int first, int shuffle);
void player_toggle(void);
void player_pause(void);
void player_resume(void);
void player_stop(void);
void player_next(void);
void player_prev(void);
void player_seek_ms(uint32_t ms);
void player_set_shuffle(int on);
void player_set_volume(int v);
uint32_t player_position_ms(void);

/* Do a slice of playback work. Returns the socket the player is waiting on
 * (to include in select), -1 if none, and sets *busy when it made progress. */
int player_service(int *busy);

#endif
