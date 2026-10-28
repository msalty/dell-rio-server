/*
 * RioServer NG receiver firmware - freestanding runtime.
 *
 * The receiver runs a Linux 2.2 ARM kernel that only understands the old ARM
 * syscall ABI ("OABI": swi 0x900000+nr, arguments in r0-r4). Modern
 * toolchains only build EABI user space, so instead of a C library this
 * firmware carries its own tiny runtime and talks to the kernel directly.
 * Every structure passed to the kernel uses only 32-bit fields, so the
 * EABI/OABI layout differences never come into play.
 */
#ifndef SYS_H
#define SYS_H

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

#define SYS_BASE 0x900000

#define NR_exit 1
#define NR_read 3
#define NR_write 4
#define NR_open 5
#define NR_close 6
#define NR_lseek 19
#define NR_getpid 20
#define NR_sync 36
#define NR_ioctl 54
#define NR_fcntl 55
#define NR_gettimeofday 78
#define NR_reboot 88
#define NR_mmap 90 /* old_mmap: takes a pointer to an argument block */
#define NR_socketcall 102
#define NR_select 142 /* _newselect */
#define NR_nanosleep 162

#define SYSCALL5(nr, a, b, c, d, e)                                          \
    ({                                                                       \
        register long _r0 __asm__("r0") = (long)(a);                         \
        register long _r1 __asm__("r1") = (long)(b);                         \
        register long _r2 __asm__("r2") = (long)(c);                         \
        register long _r3 __asm__("r3") = (long)(d);                         \
        register long _r4 __asm__("r4") = (long)(e);                         \
        __asm__ volatile("swi %[n]"                                          \
                         : "+r"(_r0)                                         \
                         : [n] "i"(SYS_BASE + (nr)), "r"(_r1), "r"(_r2),     \
                           "r"(_r3), "r"(_r4)                                \
                         : "memory", "cc");                                  \
        _r0;                                                                 \
    })
#define SYSCALL3(nr, a, b, c) SYSCALL5(nr, a, b, c, 0, 0)

/* errno values (Linux) */
#define EINTR 4
#define EAGAIN 11
#define EINPROGRESS 115
#define EALREADY 114
#define EISCONN 106

#define O_RDONLY 0
#define O_WRONLY 1
#define O_RDWR 2
#define O_CREAT 0100
#define O_TRUNC 01000
#define O_NONBLOCK 04000

#define F_GETFL 3
#define F_SETFL 4

struct timeval {
    long tv_sec;
    long tv_usec;
};

struct timespec {
    long tv_sec;
    long tv_nsec;
};

typedef struct {
    uint32_t bits[32];
} fdset_t;

static inline void fd_zero(fdset_t *s) { __builtin_memset(s, 0, sizeof(*s)); }
static inline void fd_add(int fd, fdset_t *s) { s->bits[fd >> 5] |= 1u << (fd & 31); }
static inline int fd_has(int fd, const fdset_t *s) { return (s->bits[fd >> 5] >> (fd & 31)) & 1; }

/* syscalls: return a negative errno on failure */
long sys_read(int fd, void *buf, size_t n);
long sys_write(int fd, const void *buf, size_t n);
int sys_open(const char *path, int flags, int mode);
int sys_close(int fd);
long sys_lseek(int fd, long off, int whence);
int sys_ioctl(int fd, unsigned long req, long arg);
int sys_fcntl(int fd, int cmd, long arg);
int sys_select(int n, fdset_t *r, fdset_t *w, fdset_t *e, struct timeval *tv);
void *sys_mmap(void *addr, size_t len, int prot, int flags, int fd, long off);
int sys_nanosleep(long ms);
void sys_reboot(void);
void sys_exit(int code) __attribute__((noreturn));
long sys_socketcall(int call, long *args);
uint32_t time_ms(void); /* monotonic-ish milliseconds */
long time_sec(void);

/* environment (for the qemu simulator) */
const char *getenv(const char *name);

/* string/memory */
void *memcpy(void *d, const void *s, size_t n);
void *memmove(void *d, const void *s, size_t n);
void *memset(void *d, int c, size_t n);
int memcmp(const void *a, const void *b, size_t n);
size_t strlen(const char *s);
int strcmp(const char *a, const char *b);
int strncmp(const char *a, const char *b, size_t n);
char *strchr(const char *s, int c);
char *strstr(const char *h, const char *n);
size_t strlcpy(char *d, const char *s, size_t n);
long strtol(const char *s, char **end, int base);
unsigned long strtoul(const char *s, char **end, int base);
int atoi(const char *s);

/* allocation */
void *malloc(size_t n);
void *calloc(size_t a, size_t b);
void *realloc(void *p, size_t n);
void free(void *p);

/* formatting / logging */
int vsnprintf(char *buf, size_t cap, const char *fmt, va_list ap);
int snprintf(char *buf, size_t cap, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
void dlog(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* pseudo random numbers */
void srand32(uint32_t seed);
uint32_t rand32(void);

#endif
