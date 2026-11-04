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
    uint32_t start_ms; /* where to start playing (a podcast episode left part-way) */
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
    /* Live mode (1.8): an endless stream such as an AirPlay or Spotify
     * session. No queue, no size or duration, no seeking. */
    int live;
    char live_path[96];
    char live_label[48]; /* "AirPlay · Anna's iPhone" */
    uint32_t meta_changed; /* time_ms() of the last metadata change, for the UI */
};

extern struct player P;

void player_init(void);
/* Replace the queue with the tracks of a server menu node and start playing
 * at `first` (an index into that node's track list). */
int player_play_node(const char *node, int first, int shuffle);
/* Replace the queue with a node's tracks without interrupting the current
 * track (the server has changed what follows it); position at `qi`, and
 * start playing that track if `start`. */
int player_set_queue(const char *node, int qi, int start, int shuffle);
/* Play the endless MP3 stream at `path` on the server (live mode). */
int player_play_live(const char *path, const char *label);
/* What a live stream is playing now (any argument may be 0 to keep it). */
void player_set_meta(const char *label, const char *title, const char *artist, const char *album);
void player_set_repeat(enum repeat_mode mode);
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
/* Times the stream ran dry mid-track since the last report, and how long
 * playback waited for data (a wait still going on counts up to now). */
void player_underruns(unsigned *count, uint32_t *ms);
/* The figures above have been reported; start counting again. */
void player_underruns_reported(void);
/* Time spent decoding MP3 (to PCM, before it goes to the audio device) and
 * reading from the network, in microseconds, since the last call. */
void player_cpu(uint32_t *decode_us, uint32_t *net_us);

/* Do a slice of playback work. Returns the socket the player is waiting on
 * (to include in select), -1 if none, and sets *busy when it made progress. */
int player_service(int *busy);

#endif
