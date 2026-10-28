#ifndef NET_H
#define NET_H

#include <stdint.h>

struct server {
    uint32_t ip; /* network byte order */
    uint16_t port;
    char host[20]; /* dotted quad */
};

extern struct server g_server;

/* Find the music server: SSDP broadcast, then /etc/rio-server, then
 * RIO_SERVER=ip:port (simulator). Returns 0 once found. */
int net_discover(int attempts);

/* Open a TCP connection to the server and send a GET request.
 * Returns a connected socket (blocking) or a negative value. */
int http_request(const char *path, long range_from, int timeout_ms);

/* Read the response headers. Leftover body bytes are stored in `extra`
 * (up to extra_cap) and their count in *extra_len. Returns the HTTP status
 * code, or a negative value on error/timeout. */
int http_read_headers(int fd, long *content_length, char *extra, int extra_cap, int *extra_len,
                      int timeout_ms);

/* GET a small text resource into buf (NUL terminated). Returns the body
 * length or a negative value. */
int http_get(const char *path, char *buf, int cap, int timeout_ms);

/* A GET that runs alongside playback instead of blocking the main loop.
 * Start it, then call http_async_step() whenever http_async_fd() is ready
 * (or at least every few hundred ms, for the timeout). */
struct http_async {
    int fd;          /* -1 when idle */
    int connected;
    int req_len, sent;
    int got, cap;
    char *buf;
    uint32_t deadline;
    char req[320];
};
int http_async_start(struct http_async *h, const char *path, char *buf, int cap, int timeout_ms);
/* The socket to wait on; *want_write is set while connecting/sending. */
int http_async_fd(const struct http_async *h, int *want_write);
/* Returns HTTP_PENDING while running, then the body length (body moved to
 * the start of buf, NUL terminated) or a negative value on error/timeout.
 * The request is finished (and the socket closed) once it returns anything
 * else than HTTP_PENDING. */
#define HTTP_PENDING (-1000)
int http_async_step(struct http_async *h);
void http_async_cancel(struct http_async *h);

/* UDP socket on which the server asks for an immediate poll, so commands
 * from the web interface take effect at once instead of at the next regular
 * poll. Stores the bound port in *port. */
int net_wake_open(int *port);
/* Empty the wake socket; returns 1 if the music server sent anything. */
int net_wake_drain(int fd);

int net_recv(int fd, void *buf, int len);       /* non-blocking-safe recv */
int net_set_nonblocking(int fd, int on);
void net_close(int fd);
int net_wait_readable(int fd, int timeout_ms);
const char *net_mac(void); /* "00:90:4b:..." or "" */

#endif
