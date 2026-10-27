/*
 * RioServer NG receiver firmware - freestanding runtime implementation.
 */
#include "sys.h"

int main(int argc, char **argv);

static char **g_envp;

/* Entry point: the kernel leaves argc, argv[], envp[] on the stack. */
__asm__(".text\n"
        ".global _start\n"
        "_start:\n"
        "  mov r0, sp\n"
        "  bic sp, sp, #7\n"
        "  bl cstart\n"
        "1: b 1b\n");

void cstart(long *sp) __attribute__((used, noreturn));
void cstart(long *sp)
{
    int argc = (int)sp[0];
    char **argv = (char **)(sp + 1);
    g_envp = argv + argc + 1;
    sys_exit(main(argc, argv));
}

/* ---- syscalls ---------------------------------------------------------- */

long sys_read(int fd, void *buf, size_t n) { return SYSCALL3(NR_read, fd, buf, n); }
long sys_write(int fd, const void *buf, size_t n) { return SYSCALL3(NR_write, fd, buf, n); }
int sys_open(const char *path, int flags, int mode) { return SYSCALL3(NR_open, path, flags, mode); }
int sys_close(int fd) { return SYSCALL3(NR_close, fd, 0, 0); }
long sys_lseek(int fd, long off, int whence) { return SYSCALL3(NR_lseek, fd, off, whence); }
int sys_ioctl(int fd, unsigned long req, long arg) { return SYSCALL3(NR_ioctl, fd, req, arg); }
int sys_fcntl(int fd, int cmd, long arg) { return SYSCALL3(NR_fcntl, fd, cmd, arg); }

int sys_select(int n, fdset_t *r, fdset_t *w, fdset_t *e, struct timeval *tv)
{
    return SYSCALL5(NR_select, n, r, w, e, tv);
}

void *sys_mmap(void *addr, size_t len, int prot, int flags, int fd, long off)
{
    long args[6] = {(long)addr, (long)len, prot, flags, fd, off};
    return (void *)SYSCALL3(NR_mmap, args, 0, 0);
}

int sys_nanosleep(long ms)
{
    struct timespec ts = {ms / 1000, (ms % 1000) * 1000000L};
    return SYSCALL3(NR_nanosleep, &ts, 0, 0);
}

void sys_reboot(void)
{
    SYSCALL3(NR_sync, 0, 0, 0);
    /* LINUX_REBOOT_MAGIC1, MAGIC2, LINUX_REBOOT_CMD_RESTART */
    SYSCALL5(NR_reboot, 0xfee1dead, 672274793, 0x01234567, 0, 0);
}

void sys_exit(int code)
{
    for (;;)
        SYSCALL3(NR_exit, code, 0, 0);
}

long sys_socketcall(int call, long *args) { return SYSCALL3(NR_socketcall, call, args, 0); }

uint32_t time_ms(void)
{
    struct timeval tv;
    SYSCALL3(NR_gettimeofday, &tv, 0, 0);
    return (uint32_t)tv.tv_sec * 1000u + (uint32_t)(tv.tv_usec / 1000);
}

long time_sec(void)
{
    struct timeval tv;
    SYSCALL3(NR_gettimeofday, &tv, 0, 0);
    return tv.tv_sec;
}

const char *getenv(const char *name)
{
    size_t n = strlen(name);
    for (char **e = g_envp; e && *e; e++)
        if (strncmp(*e, name, n) == 0 && (*e)[n] == '=')
            return *e + n + 1;
    return 0;
}

/* ---- memory and strings ------------------------------------------------ */

void *memcpy(void *d, const void *s, size_t n)
{
    uint8_t *dp = d;
    const uint8_t *sp = s;
    if ((((uintptr_t)dp | (uintptr_t)sp) & 3) == 0) {
        while (n >= 16) {
            ((uint32_t *)dp)[0] = ((const uint32_t *)sp)[0];
            ((uint32_t *)dp)[1] = ((const uint32_t *)sp)[1];
            ((uint32_t *)dp)[2] = ((const uint32_t *)sp)[2];
            ((uint32_t *)dp)[3] = ((const uint32_t *)sp)[3];
            dp += 16;
            sp += 16;
            n -= 16;
        }
        while (n >= 4) {
            *(uint32_t *)dp = *(const uint32_t *)sp;
            dp += 4;
            sp += 4;
            n -= 4;
        }
    }
    while (n--)
        *dp++ = *sp++;
    return d;
}

void *memmove(void *d, const void *s, size_t n)
{
    uint8_t *dp = d;
    const uint8_t *sp = s;
    if (dp <= sp || dp >= sp + n)
        return memcpy(d, s, n);
    while (n--)
        dp[n] = sp[n];
    return d;
}

void *memset(void *d, int c, size_t n)
{
    uint8_t *dp = d;
    uint32_t v = (uint8_t)c * 0x01010101u;
    while (n && ((uintptr_t)dp & 3)) {
        *dp++ = (uint8_t)c;
        n--;
    }
    while (n >= 4) {
        *(uint32_t *)dp = v;
        dp += 4;
        n -= 4;
    }
    while (n--)
        *dp++ = (uint8_t)c;
    return d;
}

int memcmp(const void *a, const void *b, size_t n)
{
    const uint8_t *x = a, *y = b;
    for (; n; n--, x++, y++)
        if (*x != *y)
            return *x - *y;
    return 0;
}

size_t strlen(const char *s)
{
    const char *p = s;
    while (*p)
        p++;
    return (size_t)(p - s);
}

int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b)
        a++, b++;
    return (uint8_t)*a - (uint8_t)*b;
}

int strncmp(const char *a, const char *b, size_t n)
{
    for (; n; n--, a++, b++) {
        if (*a != *b)
            return (uint8_t)*a - (uint8_t)*b;
        if (!*a)
            return 0;
    }
    return 0;
}

char *strchr(const char *s, int c)
{
    for (;; s++) {
        if (*s == (char)c)
            return (char *)s;
        if (!*s)
            return 0;
    }
}

char *strstr(const char *h, const char *n)
{
    size_t len = strlen(n);
    for (; *h; h++)
        if (strncmp(h, n, len) == 0)
            return (char *)h;
    return len ? 0 : (char *)h;
}

size_t strlcpy(char *d, const char *s, size_t n)
{
    size_t len = strlen(s);
    if (n) {
        size_t c = len < n - 1 ? len : n - 1;
        memcpy(d, s, c);
        d[c] = 0;
    }
    return len;
}

unsigned long strtoul(const char *s, char **end, int base)
{
    unsigned long v = 0;
    while (*s == ' ' || *s == '\t')
        s++;
    if ((base == 0 || base == 16) && s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
        base = 16;
    }
    if (base == 0)
        base = 10;
    for (;; s++) {
        int d;
        if (*s >= '0' && *s <= '9')
            d = *s - '0';
        else if (*s >= 'a' && *s <= 'z')
            d = *s - 'a' + 10;
        else if (*s >= 'A' && *s <= 'Z')
            d = *s - 'A' + 10;
        else
            break;
        if (d >= base)
            break;
        v = v * (unsigned long)base + (unsigned long)d;
    }
    if (end)
        *end = (char *)s;
    return v;
}

long strtol(const char *s, char **end, int base)
{
    while (*s == ' ' || *s == '\t')
        s++;
    if (*s == '-')
        return -(long)strtoul(s + 1, end, base);
    if (*s == '+')
        s++;
    return (long)strtoul(s, end, base);
}

int atoi(const char *s) { return (int)strtol(s, 0, 10); }

/* ---- allocator --------------------------------------------------------- */
/*
 * First-fit allocator over a static arena. The arena lives in .bss, so pages
 * the firmware never touches cost no RAM.
 */
#define ARENA_SIZE (1024 * 1024)
#define ALIGN 8

struct block {
    size_t size; /* bytes including header */
    size_t used;
};

static uint8_t arena[ARENA_SIZE] __attribute__((aligned(ALIGN)));
static int arena_ready;

static struct block *next_block(struct block *b) { return (struct block *)((uint8_t *)b + b->size); }
static int in_arena(struct block *b) { return (uint8_t *)b < arena + ARENA_SIZE; }

void *malloc(size_t n)
{
    if (!arena_ready) {
        struct block *b = (struct block *)arena;
        b->size = ARENA_SIZE;
        b->used = 0;
        arena_ready = 1;
    }
    size_t need = (n + sizeof(struct block) + ALIGN - 1) & ~(size_t)(ALIGN - 1);
    for (struct block *b = (struct block *)arena; in_arena(b); b = next_block(b)) {
        if (b->used)
            continue;
        /* merge following free blocks */
        while (in_arena(next_block(b)) && !next_block(b)->used)
            b->size += next_block(b)->size;
        if (b->size < need)
            continue;
        if (b->size - need >= sizeof(struct block) + 32) {
            struct block *rest = (struct block *)((uint8_t *)b + need);
            rest->size = b->size - need;
            rest->used = 0;
            b->size = need;
        }
        b->used = 1;
        return b + 1;
    }
    dlog("malloc(%u) failed\n", (unsigned)n);
    return 0;
}

void free(void *p)
{
    if (p)
        ((struct block *)p - 1)->used = 0;
}

void *calloc(size_t a, size_t b)
{
    void *p = malloc(a * b);
    if (p)
        memset(p, 0, a * b);
    return p;
}

void *realloc(void *p, size_t n)
{
    if (!p)
        return malloc(n);
    struct block *b = (struct block *)p - 1;
    size_t have = b->size - sizeof(struct block);
    if (have >= n)
        return p;
    void *q = malloc(n);
    if (q) {
        memcpy(q, p, have);
        free(p);
    }
    return q;
}

/* ---- formatting -------------------------------------------------------- */

static void put(char *buf, size_t cap, size_t *pos, char c)
{
    if (*pos + 1 < cap)
        buf[*pos] = c;
    (*pos)++;
}

int vsnprintf(char *buf, size_t cap, const char *fmt, va_list ap)
{
    size_t pos = 0;
    for (; *fmt; fmt++) {
        if (*fmt != '%') {
            put(buf, cap, &pos, *fmt);
            continue;
        }
        fmt++;
        int left = 0, zero = 0, width = 0, prec = -1;
        for (;; fmt++) {
            if (*fmt == '-')
                left = 1;
            else if (*fmt == '0')
                zero = 1;
            else
                break;
        }
        while (*fmt >= '0' && *fmt <= '9')
            width = width * 10 + (*fmt++ - '0');
        if (*fmt == '.') {
            prec = 0;
            fmt++;
            while (*fmt >= '0' && *fmt <= '9')
                prec = prec * 10 + (*fmt++ - '0');
        }
        while (*fmt == 'l' || *fmt == 'z')
            fmt++;
        char tmp[24];
        const char *s = tmp;
        int len = 0, neg = 0;
        switch (*fmt) {
        case 'd':
        case 'i':
        case 'u':
        case 'x':
        case 'X':
        case 'p': {
            unsigned long v;
            int base = (*fmt == 'x' || *fmt == 'X' || *fmt == 'p') ? 16 : 10;
            if (*fmt == 'd' || *fmt == 'i') {
                long sv = va_arg(ap, long);
                neg = sv < 0;
                v = neg ? (unsigned long)(-sv) : (unsigned long)sv;
            } else {
                v = va_arg(ap, unsigned long);
            }
            const char *digits = *fmt == 'X' ? "0123456789ABCDEF" : "0123456789abcdef";
            char rev[24];
            int n = 0;
            do {
                rev[n++] = digits[v % (unsigned)base];
                v /= (unsigned)base;
            } while (v);
            if (neg)
                tmp[len++] = '-';
            while (n)
                tmp[len++] = rev[--n];
            break;
        }
        case 's':
            s = va_arg(ap, const char *);
            if (!s)
                s = "(null)";
            len = (int)strlen(s);
            if (prec >= 0 && len > prec)
                len = prec;
            zero = 0;
            break;
        case 'c':
            tmp[0] = (char)va_arg(ap, int);
            len = 1;
            break;
        case '%':
            tmp[0] = '%';
            len = 1;
            break;
        default:
            continue;
        }
        int pad = width > len ? width - len : 0;
        if (!left && zero && neg && s == tmp) {
            put(buf, cap, &pos, '-');
            s++;
            len--;
        }
        if (!left)
            while (pad-- > 0)
                put(buf, cap, &pos, zero ? '0' : ' ');
        for (int i = 0; i < len; i++)
            put(buf, cap, &pos, s[i]);
        if (left)
            while (pad-- > 0)
                put(buf, cap, &pos, ' ');
    }
    if (cap)
        buf[pos < cap ? pos : cap - 1] = 0;
    return (int)pos;
}

int snprintf(char *buf, size_t cap, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, cap, fmt, ap);
    va_end(ap);
    return n;
}

void dlog(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > (int)sizeof(buf) - 1)
        n = sizeof(buf) - 1;
    sys_write(1, buf, (size_t)n);
}

/* ---- random ------------------------------------------------------------ */

static uint32_t rng_state = 0x9e3779b9u;

void srand32(uint32_t seed) { rng_state = seed ? seed : 0x9e3779b9u; }

uint32_t rand32(void)
{
    uint32_t x = rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return rng_state = x;
}
