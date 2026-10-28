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
