/* Rockbox's codec runtime, as far as its libmad uses it: memory functions,
 * which the firmware's own runtime provides, and a byte swap. */
#ifndef ROCKBOX_CODECLIB_H
#define ROCKBOX_CODECLIB_H

#include <stddef.h>
#include <stdint.h>
#include "sys.h"

/* Big-endian to host order; the receiver's ARM runs little-endian. */
static inline uint32_t betoh32(uint32_t x) { return __builtin_bswap32(x); }

#endif
