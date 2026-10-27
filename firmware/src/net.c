#include "net.h"
#include "sys.h"

/* socketcall() sub-calls */
#define SYS_SOCKET 1
#define SYS_BIND 2
#define SYS_CONNECT 3
#define SYS_SEND 9
#define SYS_RECV 10
#define SYS_SENDTO 11
#define SYS_RECVFROM 12
#define SYS_SETSOCKOPT 14

#define AF_INET 2
#define SOCK_STREAM 1
#define SOCK_DGRAM 2
#define SOL_SOCKET 1
#define SO_REUSEADDR 2
#define SO_BROADCAST 6
#define MSG_NOSIGNAL 0x4000
#define SIOCGIFHWADDR 0x8927

#define SSDP_PORT 21075
static const char ssdp_request[] = "upnp:uuid:1D274DB0-F053-11d3-BF72-0050DA689B2F";

struct sockaddr_in {
    uint16_t family;
    uint16_t port; /* network order */
    uint32_t addr; /* network order */
    uint8_t zero[8];
};

struct server g_server;
static char mac_str[18];

static uint16_t htons(uint16_t v) { return (uint16_t)((v << 8) | (v >> 8)); }

static uint32_t parse_ip(const char *s, const char **end)
{
    uint32_t parts[4] = {0};
    for (int i = 0; i < 4; i++) {
        char *e;
        parts[i] = strtoul(s, &e, 10);
        if (e == s || parts[i] > 255)
            return 0;
        s = e;
        if (i < 3) {
            if (*s != '.')
                return 0;
            s++;
        }
    }
    if (end)
        *end = s;
    /* network byte order on a little-endian CPU */
    return parts[0] | (parts[1] << 8) | (parts[2] << 16) | (parts[3] << 24);
}

static int set_server(const char *text)
{
    const char *p = strstr(text, "http://");
    p = p ? p + 7 : text;
    while (*p == ' ')
        p++;
    const char *e;
    uint32_t ip = parse_ip(p, &e);
    if (!ip)
        return -1;
    int port = 12078;
    if (*e == ':')
        port = atoi(e + 1);
    g_server.ip = ip;
    g_server.port = (uint16_t)port;
    snprintf(g_server.host, sizeof(g_server.host), "%u.%u.%u.%u", (unsigned)(ip & 255),
             (unsigned)((ip >> 8) & 255), (unsigned)((ip >> 16) & 255), (unsigned)(ip >> 24));
    return 0;
}

static int sock(int type)
{
    long a[3] = {AF_INET, type, 0};
    return (int)sys_socketcall(SYS_SOCKET, a);
}

int net_set_nonblocking(int fd, int on)
{
    int fl = sys_fcntl(fd, F_GETFL, 0);
    if (fl < 0)
        return fl;
    return sys_fcntl(fd, F_SETFL, on ? (fl | O_NONBLOCK) : (fl & ~O_NONBLOCK));
}

void net_close(int fd)
{
    if (fd >= 0)
        sys_close(fd);
}

int net_wait_readable(int fd, int timeout_ms)
{
    fdset_t r;
    fd_zero(&r);
    fd_add(fd, &r);
    struct timeval tv = {timeout_ms / 1000, (timeout_ms % 1000) * 1000};
    return sys_select(fd + 1, &r, 0, 0, &tv);
}

static int wait_writable(int fd, int timeout_ms)
{
    fdset_t w;
    fd_zero(&w);
    fd_add(fd, &w);
    struct timeval tv = {timeout_ms / 1000, (timeout_ms % 1000) * 1000};
    return sys_select(fd + 1, 0, &w, 0, &tv);
}

int net_recv(int fd, void *buf, int len)
{
    long a[4] = {fd, (long)buf, len, 0};
    return (int)sys_socketcall(SYS_RECV, a);
}

static int send_all(int fd, const char *buf, int len)
{
    while (len > 0) {
        long a[4] = {fd, (long)buf, len, MSG_NOSIGNAL};
        int n = (int)sys_socketcall(SYS_SEND, a);
        if (n == -EINTR)
            continue;
        if (n <= 0)
            return -1;
        buf += n;
        len -= n;
    }
    return 0;
}

const char *net_mac(void)
{
    if (mac_str[0])
        return mac_str;
    static const char *const ifaces[] = {"eth0", "il0", "pna0"};
    int fd = sock(SOCK_DGRAM);
    if (fd < 0)
        return "";
    for (unsigned i = 0; i < sizeof(ifaces) / sizeof(ifaces[0]); i++) {
        struct {
            char name[16];
            uint16_t family;
            uint8_t data[14];
        } ifr;
        memset(&ifr, 0, sizeof(ifr));
        strlcpy(ifr.name, ifaces[i], sizeof(ifr.name));
        if (sys_ioctl(fd, SIOCGIFHWADDR, (long)&ifr) == 0) {
            snprintf(mac_str, sizeof(mac_str), "%02x:%02x:%02x:%02x:%02x:%02x", ifr.data[0],
                     ifr.data[1], ifr.data[2], ifr.data[3], ifr.data[4], ifr.data[5]);
            break;
        }
    }
    sys_close(fd);
    return mac_str;
}

static int ssdp_once(int timeout_ms)
{
    int fd = sock(SOCK_DGRAM);
    if (fd < 0)
        return -1;
    int one = 1;
    long so1[5] = {fd, SOL_SOCKET, SO_REUSEADDR, (long)&one, 4};
    sys_socketcall(SYS_SETSOCKOPT, so1);
    long so2[5] = {fd, SOL_SOCKET, SO_BROADCAST, (long)&one, 4};
    sys_socketcall(SYS_SETSOCKOPT, so2);
    struct sockaddr_in local = {AF_INET, htons(SSDP_PORT), 0, {0}};
    long b[3] = {fd, (long)&local, sizeof(local)};
    if (sys_socketcall(SYS_BIND, b) < 0) {
        /* port busy (e.g. under the simulator next to the server): any port */
        local.port = 0;
        sys_socketcall(SYS_BIND, b);
    }
    struct sockaddr_in to = {AF_INET, htons(SSDP_PORT), 0xffffffffu, {0}};
    long s[6] = {fd, (long)ssdp_request, sizeof(ssdp_request) - 1, 0, (long)&to, sizeof(to)};
    sys_socketcall(SYS_SENDTO, s);
    int found = -1;
    uint32_t deadline = time_ms() + (uint32_t)timeout_ms;
    for (;;) {
        int left = (int)(deadline - time_ms());
        if (left <= 0 || net_wait_readable(fd, left) <= 0)
            break;
        char buf[256];
        long r[6] = {fd, (long)buf, sizeof(buf) - 1, 0, 0, 0};
        int n = (int)sys_socketcall(SYS_RECVFROM, r);
        if (n <= 0)
            continue;
        buf[n] = 0;
        if (strncmp(buf, "http://", 7) == 0 && set_server(buf) == 0) {
            found = 0;
            break;
        }
    }
    sys_close(fd);
    return found;
}

int net_discover(int attempts)
{
    const char *env = getenv("RIO_SERVER");
    if (env && set_server(env) == 0)
        return 0;
    for (int i = 0; i < attempts; i++) {
        if (ssdp_once(1500) == 0) {
            dlog("ssdp: music server at %s:%u\n", g_server.host, g_server.port);
            return 0;
        }
    }
    /* RioServer NG writes its address into the boot image as a fallback. */
    int fd = sys_open("/etc/rio-server", O_RDONLY, 0);
    if (fd >= 0) {
        char buf[64];
        long n = sys_read(fd, buf, sizeof(buf) - 1);
        sys_close(fd);
        if (n > 0) {
            buf[n] = 0;
            if (set_server(buf) == 0) {
                dlog("using server %s:%u from /etc/rio-server\n", g_server.host, g_server.port);
                return 0;
            }
        }
    }
    return -1;
}

int http_request(const char *path, long range_from, int timeout_ms)
{
    if (!g_server.ip)
        return -1;
    int fd = sock(SOCK_STREAM);
    if (fd < 0)
        return fd;
    net_set_nonblocking(fd, 1);
    struct sockaddr_in to = {AF_INET, htons(g_server.port), g_server.ip, {0}};
    long c[3] = {fd, (long)&to, sizeof(to)};
    int rc = (int)sys_socketcall(SYS_CONNECT, c);
    if (rc == -EINPROGRESS || rc == -EAGAIN) {
        if (wait_writable(fd, timeout_ms) <= 0) {
            sys_close(fd);
            return -2;
        }
        rc = (int)sys_socketcall(SYS_CONNECT, c); /* reports the final result */
        if (rc == -106 /* EISCONN */)
            rc = 0;
    }
    if (rc < 0) {
        sys_close(fd);
        return -3;
    }
    net_set_nonblocking(fd, 0);
    char req[512];
    int n;
    if (range_from > 0)
        n = snprintf(req, sizeof(req),
                     "GET %s HTTP/1.0\r\nHost: %s\r\nRange: bytes=%ld-\r\nUser-Agent: RioServerNG/1\r\n\r\n",
                     path, g_server.host, range_from);
    else
        n = snprintf(req, sizeof(req), "GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: RioServerNG/1\r\n\r\n",
                     path, g_server.host);
    if (n >= (int)sizeof(req) || send_all(fd, req, n) < 0) {
        sys_close(fd);
        return -4;
    }
    return fd;
}

int http_read_headers(int fd, long *content_length, char *extra, int extra_cap, int *extra_len,
                      int timeout_ms)
{
    char buf[2048];
    int have = 0;
    uint32_t deadline = time_ms() + (uint32_t)timeout_ms;
    *extra_len = 0;
    if (content_length)
        *content_length = -1;
    for (;;) {
        int left = (int)(deadline - time_ms());
        if (left <= 0 || net_wait_readable(fd, left) <= 0)
            return -1;
        int n = net_recv(fd, buf + have, (int)sizeof(buf) - 1 - have);
        if (n == -EINTR || n == -EAGAIN)
            continue;
        if (n <= 0)
            return -1;
        have += n;
        buf[have] = 0;
        char *end = strstr(buf, "\r\n\r\n");
        if (!end) {
            if (have >= (int)sizeof(buf) - 1)
                return -1;
            continue;
        }
        *end = 0;
        int status = 0;
        char *sp = strchr(buf, ' ');
        if (sp)
            status = atoi(sp + 1);
        for (char *line = strchr(buf, '\n'); line; line = strchr(line + 1, '\n')) {
            if (content_length && (strncmp(line + 1, "Content-Length:", 15) == 0 ||
                                   strncmp(line + 1, "content-length:", 15) == 0))
                *content_length = strtol(line + 16, 0, 10);
        }
        int body = have - (int)(end + 4 - buf);
        if (body > extra_cap)
            body = extra_cap; /* callers size `extra` generously */
        memcpy(extra, end + 4, (size_t)body);
        *extra_len = body;
        return status;
    }
}

int http_get(const char *path, char *buf, int cap, int timeout_ms)
{
    int fd = http_request(path, 0, timeout_ms);
    if (fd < 0)
        return fd;
    long clen;
    int got;
    int status = http_read_headers(fd, &clen, buf, cap - 1, &got, timeout_ms);
    if (status != 200) {
        sys_close(fd);
        return status < 0 ? status : -status;
    }
    uint32_t deadline = time_ms() + (uint32_t)timeout_ms;
    while (got < cap - 1) {
        int left = (int)(deadline - time_ms());
        if (left <= 0 || net_wait_readable(fd, left) <= 0)
            break;
        int n = net_recv(fd, buf + got, cap - 1 - got);
        if (n == -EINTR || n == -EAGAIN)
            continue;
        if (n <= 0)
            break;
        got += n;
    }
    sys_close(fd);
    buf[got] = 0;
    return got;
}
