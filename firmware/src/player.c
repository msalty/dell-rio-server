/*
 * Playback: fetches the queue from the server, streams each track over HTTP,
 * decodes MP3 with libmad (fixed point; the receiver has no FPU) and feeds
 * 44.1kHz stereo PCM to the audio device.
 */
#include "player.h"
#include "hw.h"
#include "mad.h"
#include "net.h"
#include "sys.h"

#define INBUF_SIZE (96 * 1024)
#define PREBUFFER (48 * 1024)
#define REBUFFER (16 * 1024)
#define OUT_FRAMES 7200 /* 1152 frames upsampled from 8kHz to 44.1kHz */
#define MAX_QUEUE 10000

struct player P;

static int fd = -1;
static uint8_t *inbuf;
static int in_start, in_end;
static int eof, guard_added, rebuffer;
static long received;     /* body bytes received for the current track */
static long stream_base;  /* byte offset the current request started at */
static int reconnects;
static int fail_count;
static uint32_t samples_out; /* at 44.1kHz since stream_base */
static uint32_t base_ms;     /* position corresponding to stream_base */
static int threshold;
static int starved; /* waiting for data after running dry mid-track */
static uint32_t starved_at;
static unsigned underruns;   /* since the last report to the server */
static uint32_t underrun_ms;
static uint32_t decode_us, net_us; /* CPU time, since the last report */

static struct mad_stream mst;
static struct mad_frame mfr;
static struct mad_synth msy;
static int16_t pcm_out[OUT_FRAMES * 2];

/* linear resampler state */
static uint32_t rs_pos;  /* 16.16 */
static int32_t rs_prev[2];
static unsigned rs_rate;

static void set_status(const char *msg)
{
    strlcpy(P.status, msg, sizeof(P.status));
    P.status_until = time_ms() + 4000;
}

#ifdef MAD_ROCKBOX
/* Rockbox's libmad leaves its work buffers to the caller instead of
 * allocating them itself (and freeing them in *_finish()). As in Rockbox's
 * own player on a single-core CPU, the previous frame's subband samples share
 * the current frame's buffer. */
static unsigned char mad_main_data[MAD_BUFFER_MDLEN];
static mad_fixed_t mad_overlap[2][32][18];
static mad_fixed_t mad_sbsample[2][36][32];
#endif

static void decoder_init(void)
{
#ifdef MAD_ROCKBOX
    mst.main_data = &mad_main_data;
    mfr.overlap = &mad_overlap;
    mfr.sbsample = mfr.sbsample_prev = &mad_sbsample;
#endif
    mad_stream_init(&mst);
    mad_frame_init(&mfr);
    mad_synth_init(&msy);
}

static void decoder_finish(void)
{
    mad_synth_finish(&msy);
#ifndef MAD_ROCKBOX
    mad_frame_finish(&mfr);
    mad_stream_finish(&mst);
#endif
}

void player_init(void)
{
    memset(&P, 0, sizeof(P));
    P.volume = 50;
    inbuf = malloc(INBUF_SIZE + MAD_BUFFER_GUARD);
    decoder_init();
    audio_set_volume(P.volume);
}

static void close_stream(void)
{
    net_close(fd);
    fd = -1;
}

/* Count the time spent waiting since the stream ran dry. */
static void end_starve(void)
{
    if (starved) {
        underrun_ms += time_ms() - starved_at;
        starved = 0;
    }
}

static void reset_decoder(void)
{
    end_starve();
    audio_idle(); /* a new stream: a pause before it is expected */
    decoder_finish();
    decoder_init();
    in_start = in_end = 0;
    eof = guard_added = 0;
    rebuffer = 1;
    rs_pos = 0;
    rs_prev[0] = rs_prev[1] = 0;
    rs_rate = 0;
}

static void parse_track_info(char *text, struct track_info *t)
{
    for (char *line = text; line && *line;) {
        char *next = strchr(line, '\n');
        if (next)
            *next++ = 0;
        char *val = strchr(line, '\t');
        if (val) {
            *val++ = 0;
            if (!strcmp(line, "title"))
                strlcpy(t->title, val, sizeof(t->title));
            else if (!strcmp(line, "artist"))
                strlcpy(t->artist, val, sizeof(t->artist));
            else if (!strcmp(line, "album"))
                strlcpy(t->album, val, sizeof(t->album));
            else if (!strcmp(line, "track"))
                t->track = atoi(val);
            else if (!strcmp(line, "tracks"))
                t->tracks = atoi(val);
            else if (!strcmp(line, "dur"))
                t->dur_ms = strtoul(val, 0, 10);
            else if (!strcmp(line, "size"))
                t->size = strtoul(val, 0, 10);
            else if (!strcmp(line, "kbps"))
                t->kbps = atoi(val);
            else if (!strcmp(line, "start"))
                t->start_ms = strtoul(val, 0, 10);
        }
        line = next;
    }
}

/* Open (or re-open, for seeking/recovery) the audio stream at byte `from`. */
static int open_stream(long from)
{
    char path[48];
    close_stream();
    reset_decoder();
    snprintf(path, sizeof(path), "/content/%lx", (unsigned long)P.cur.fid);
    int s = http_request(path, from, 5000);
    if (s < 0)
        return -1;
    long clen;
    int extra;
    int status = http_read_headers(s, &clen, (char *)inbuf, INBUF_SIZE, &extra, 5000);
    if (status != 200 && status != 206) {
        dlog("player: %s -> HTTP %d\n", path, status);
        net_close(s);
        return -1;
    }
    in_end = extra;
    received = extra;
    stream_base = status == 206 ? from : 0;
    samples_out = 0;
    base_ms = P.cur.size ? (uint32_t)((uint64_t)stream_base * P.cur.dur_ms / P.cur.size) : 0;
    net_set_nonblocking(s, 1);
    fd = s;
    threshold = PREBUFFER;
    P.state = P_BUFFERING;
    return 0;
}

static int start_current(void)
{
    char buf[1024];
    char path[48];
    if (P.qi < 0 || P.qi >= P.qlen) {
        player_stop();
        return -1;
    }
    memset(&P.cur, 0, sizeof(P.cur));
    P.cur.fid = P.queue[P.qi];
    P.have_track = 1;
    snprintf(path, sizeof(path), "/ng/track/%lx", (unsigned long)P.cur.fid);
    if (http_get(path, buf, sizeof(buf), 3000) > 0)
        parse_track_info(buf, &P.cur);
    else
        strlcpy(P.cur.title, "Unknown track", sizeof(P.cur.title));
    reconnects = 0;
    /* A podcast episode carries on where it was left (the server says where). */
    long from = 0;
    if (P.cur.start_ms && P.cur.dur_ms && P.cur.size && P.cur.start_ms < P.cur.dur_ms)
        from = (long)((uint64_t)P.cur.start_ms * P.cur.size / P.cur.dur_ms);
    if (open_stream(from) < 0) {
        set_status("Can't reach server");
        P.state = P_STOPPED;
        return -1;
    }
    dlog("player: playing %lx \"%s\"\n", (unsigned long)P.cur.fid, P.cur.title);
    return 0;
}

static void advance(int manual)
{
    if (P.repeat == REPEAT_ONE && !manual) {
        start_current();
        return;
    }
    if (P.qi + 1 < P.qlen) {
        P.qi++;
    } else if (P.repeat == REPEAT_ALL && P.qlen) {
        P.qi = 0;
    } else {
        player_stop();
        P.qi = P.qlen ? P.qlen - 1 : 0;
        return;
    }
    start_current();
}

static void shuffle_queue(int keep_current)
{
    int from = keep_current ? P.qi + 1 : 0;
    for (int i = P.qlen - 1; i > from; i--) {
        int j = from + (int)(rand32() % (uint32_t)(i - from + 1));
        uint32_t t = P.queue[i];
        P.queue[i] = P.queue[j];
        P.queue[j] = t;
    }
}

/* Replace the queue with the track list at `path`. */
static int load_queue(const char *path)
{
    int cap = 128 * 1024;
    char *buf = malloc((size_t)cap);
    if (!buf)
        return -1;
    int n = http_get(path, buf, cap, 5000);
    if (n <= 0) {
        free(buf);
        set_status("Can't reach server");
        return -1;
    }
    free(P.queue);
    free(P.orig);
    P.queue = malloc(MAX_QUEUE * sizeof(uint32_t));
    P.orig = malloc(MAX_QUEUE * sizeof(uint32_t));
    P.qlen = 0;
    for (char *line = buf; line && *line;) {
        char *next = strchr(line, '\n');
        if (next)
            *next++ = 0;
        if (line[0] && !strchr(line, '\t') && strncmp(line, "RNG", 3) && P.qlen < MAX_QUEUE) {
            P.queue[P.qlen] = strtoul(line, 0, 16);
            P.orig[P.qlen] = P.queue[P.qlen];
            P.qlen++;
        }
        line = next;
    }
    free(buf);
    return P.qlen;
}

int player_play_node(const char *node, int first, int shuffle)
{
    char path[200];
    snprintf(path, sizeof(path), "/ng/queue/%s?first=%d&shuffle=%d", node, first, shuffle ? 1 : 0);
    if (load_queue(path) < 0)
        return -1;
    P.shuffle = shuffle;
    P.qi = 0;
    fail_count = 0;
    if (!P.qlen) {
        set_status("Nothing to play");
        return -1;
    }
    return start_current();
}

int player_set_queue(const char *node, int qi, int start, int shuffle)
{
    char path[200];
    snprintf(path, sizeof(path), "/ng/queue/%s", node);
    if (load_queue(path) < 0)
        return -1;
    P.shuffle = shuffle;
    if (qi >= P.qlen)
        qi = P.qlen - 1;
    P.qi = qi < 0 ? 0 : qi;
    if (!P.qlen) {
        player_stop();
        return 0;
    }
    if (start) {
        fail_count = 0;
        return start_current();
    }
    return 0; /* the current track plays on; what follows it has changed */
}

void player_set_repeat(enum repeat_mode mode)
{
    P.repeat = mode;
}

void player_stop(void)
{
    end_starve();
    audio_idle();
    close_stream();
    P.state = P_STOPPED;
}

void player_pause(void)
{
    end_starve(); /* a pause isn't waiting for data */
    audio_idle();
    if (P.state == P_PLAYING || P.state == P_BUFFERING)
        P.state = P_PAUSED;
}

void player_resume(void)
{
    if (P.state == P_PAUSED)
        P.state = fd >= 0 ? P_BUFFERING : P_STOPPED;
    if (P.state == P_STOPPED && P.qlen)
        start_current();
    threshold = REBUFFER;
}

void player_toggle(void)
{
    if (P.state == P_PLAYING || P.state == P_BUFFERING)
        player_pause();
    else
        player_resume();
}

void player_next(void)
{
    if (P.qlen)
        advance(1);
}

void player_prev(void)
{
    if (!P.qlen)
        return;
    if (player_position_ms() > 3000 || P.qi == 0) {
        start_current();
        return;
    }
    P.qi--;
    start_current();
}

void player_seek_ms(uint32_t ms)
{
    if (!P.have_track || !P.cur.dur_ms || !P.cur.size)
        return;
    if (ms >= P.cur.dur_ms)
        ms = P.cur.dur_ms - 1;
    long off = (long)((uint64_t)ms * P.cur.size / P.cur.dur_ms);
    int paused = P.state == P_PAUSED;
    if (open_stream(off) == 0 && paused)
        P.state = P_PAUSED;
}

void player_set_shuffle(int on)
{
    if (on == P.shuffle)
        return;
    P.shuffle = on;
    if (!P.qlen)
        return;
    if (on) {
        shuffle_queue(1);
    } else {
        uint32_t cur = P.queue[P.qi];
        memcpy(P.queue, P.orig, (size_t)P.qlen * sizeof(uint32_t));
        for (int i = 0; i < P.qlen; i++)
            if (P.queue[i] == cur) {
                P.qi = i;
                break;
            }
    }
}

void player_set_volume(int v)
{
    if (v < 0)
        v = 0;
    if (v > 100)
        v = 100;
    P.volume = v;
    audio_set_volume(v);
}

void player_underruns(unsigned *count, uint32_t *ms)
{
    *count = underruns;
    *ms = underrun_ms + (starved ? time_ms() - starved_at : 0);
}

void player_cpu(uint32_t *dec, uint32_t *net)
{
    *dec = decode_us;
    *net = net_us;
    decode_us = net_us = 0;
}

void player_underruns_reported(void)
{
    underruns = 0;
    underrun_ms = 0;
    if (starved)
        starved_at = time_ms();
}

uint32_t player_position_ms(void)
{
    if (!P.have_track)
        return 0;
    return base_ms + (uint32_t)((uint64_t)samples_out * 10 / 441);
}

static inline int16_t scale(mad_fixed_t s)
{
    s += 1L << (MAD_F_FRACBITS - 16);
    if (s >= MAD_F_ONE)
        s = MAD_F_ONE - 1;
    else if (s < -MAD_F_ONE)
        s = -MAD_F_ONE;
    return (int16_t)(s >> (MAD_F_FRACBITS + 1 - 16));
}

/* Convert the decoded frame to 16-bit 44.1kHz stereo in pcm_out; returns
 * the number of frames. */
static int output_pcm(void)
{
    struct mad_pcm *pcm = &msy.pcm;
    int n = pcm->length;
    const mad_fixed_t *l = pcm->samples[0];
    const mad_fixed_t *r = pcm->channels > 1 ? pcm->samples[1] : pcm->samples[0];
    int frames = 0;
    if (pcm->samplerate == 44100 || pcm->samplerate == 0) {
        for (int i = 0; i < n; i++) {
            pcm_out[2 * i] = scale(l[i]);
            pcm_out[2 * i + 1] = scale(r[i]);
        }
        frames = n;
    } else {
        if (rs_rate != pcm->samplerate) {
            rs_rate = pcm->samplerate;
            rs_pos = 0;
        }
        uint32_t step = (uint32_t)(((uint64_t)rs_rate << 16) / 44100);
        /* virtual input: index 0 is the last sample of the previous frame */
        while (frames < OUT_FRAMES) {
            uint32_t idx = rs_pos >> 16;
            if ((int)idx >= n)
                break;
            int32_t frac = (int32_t)(rs_pos & 0xffff);
            int32_t la = idx ? scale(l[idx - 1]) : rs_prev[0];
            int32_t ra = idx ? scale(r[idx - 1]) : rs_prev[1];
            int32_t lb = scale(l[idx]), rb = scale(r[idx]);
            pcm_out[2 * frames] = (int16_t)(la + (((lb - la) * frac) >> 16));
            pcm_out[2 * frames + 1] = (int16_t)(ra + (((rb - ra) * frac) >> 16));
            frames++;
            rs_pos += step;
        }
        rs_pos -= (uint32_t)n << 16;
        rs_prev[0] = scale(l[n - 1]);
        rs_prev[1] = scale(r[n - 1]);
    }
    samples_out += (uint32_t)frames;
    return frames;
}

static void fill_from_network(int *busy)
{
    if (fd < 0 || eof)
        return;
    /* Reclaim consumed space only in large steps. Shifting the buffer after
     * every frame (as this once did) nearly doubled the CPU playback needs,
     * which the receiver's 74 MHz ARM can't spare. While
     * the buffer is full with less than half of it consumed, just don't read. */
    if (in_start >= INBUF_SIZE / 2) {
        memmove(inbuf, inbuf + in_start, (size_t)(in_end - in_start));
        in_end -= in_start;
        in_start = 0;
        rebuffer = 1;
    }
    int space = INBUF_SIZE - in_end;
    if (space <= 0)
        return;
    uint32_t began = time_us();
    int n = net_recv(fd, inbuf + in_end, space);
    net_us += time_us() - began;
    if (n > 0) {
        in_end += n;
        received += n;
        rebuffer = 1;
        *busy = 1;
    } else if (n == 0 || (n < 0 && n != -EAGAIN && n != -EINTR)) {
        long expected = P.cur.size ? (long)P.cur.size - stream_base : 0;
        if (expected && received < expected && reconnects < 3) {
            /* connection dropped early (e.g. after a long pause): resume */
            long pos = stream_base + received;
            int keep_start = in_start, keep_end = in_end;
            net_close(fd);
            fd = -1;
            char path[48];
            snprintf(path, sizeof(path), "/content/%lx", (unsigned long)P.cur.fid);
            int s = http_request(path, pos, 5000);
            int extra = 0;
            long clen;
            reconnects++;
            if (s >= 0 && http_read_headers(s, &clen, (char *)inbuf + keep_end,
                                            INBUF_SIZE - keep_end, &extra, 5000) == 206) {
                net_set_nonblocking(s, 1);
                fd = s;
                in_start = keep_start;
                in_end = keep_end + extra;
                received += extra;
                rebuffer = 1;
                return;
            }
            net_close(s);
        }
        eof = 1;
        close_stream();
        *busy = 1;
    }
}

int player_service(int *busy)
{
    *busy = 0;
    if (P.state == P_STOPPED)
        return -1;
    fill_from_network(busy);
    int avail = in_end - in_start;
    if (P.state == P_PAUSED)
        return -1; /* don't read further; the TCP window throttles the server */
    if (P.state == P_BUFFERING) {
        if (avail >= threshold || eof) {
            P.state = P_PLAYING;
            end_starve();
        } else
            return fd;
    }
    if (eof && !guard_added) {
        memset(inbuf + in_end, 0, MAD_BUFFER_GUARD);
        in_end += MAD_BUFFER_GUARD;
        guard_added = 1;
        rebuffer = 1;
        avail = in_end - in_start;
    }
    if (rebuffer) {
        mad_stream_buffer(&mst, inbuf + in_start, (unsigned long)avail);
        rebuffer = 0;
    }
    for (int attempts = 0; attempts < 8; attempts++) {
        uint32_t began = time_us();
        int decoded = mad_frame_decode(&mfr, &mst) == 0;
        int frames = 0;
        if (decoded) {
            in_start = (int)(mst.next_frame - inbuf);
            mad_synth_frame(&msy, &mfr);
            frames = output_pcm();
        }
        decode_us += time_us() - began;
        if (decoded) {
            audio_play(pcm_out, frames); /* may wait for room: not decoding time */
            fail_count = 0;
            *busy = 1;
            return fd;
        }
        if (mst.next_frame)
            in_start = (int)(mst.next_frame - inbuf);
        if (mst.error == MAD_ERROR_BUFLEN) {
            if (eof) {
                advance(0);
                *busy = 1;
                return fd;
            }
            /* underrun: wait for more data before resuming */
            if (!starved) {
                underruns++;
                starved = 1;
                starved_at = time_ms();
                dlog("player: ran out of data (%d bytes buffered)\n", in_end - in_start);
            }
            audio_idle(); /* reported as a run-out, not a dropout */
            P.state = P_BUFFERING;
            threshold = REBUFFER;
            rebuffer = 1;
            return fd;
        }
        if (!MAD_RECOVERABLE(mst.error)) {
            dlog("player: decode error %d\n", mst.error);
            if (++fail_count > 3) {
                set_status("Can't decode track");
                player_stop();
            } else {
                advance(0);
            }
            return fd;
        }
    }
    *busy = 1;
    return fd;
}
