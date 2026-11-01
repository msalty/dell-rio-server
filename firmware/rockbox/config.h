/*
 * What Rockbox's own build supplies to its copy of libmad (lib/rbcodec/
 * codecs/libmad), for the receiver: an ARM720T, i.e. a "classic" ARMv4T
 * core, with no on-chip RAM to place code or tables in.
 */
#ifndef ROCKBOX_CONFIG_H
#define ROCKBOX_CONFIG_H

#define CPU_ARM_CLASSIC 1
#define ARM_ARCH 4

#define ICODE_ATTR
#define ICONST_ATTR
#define IBSS_ATTR
#define IDATA_ATTR
#define MEM_ALIGN_ATTR __attribute__((aligned(16)))

#ifdef __ASSEMBLER__
/* Pop registers and return. The firmware is all ARM code (no Thumb), so the
 * return address can go straight into pc. */
.macro ldmpc cond="", order="ia", regs
    ldm\cond\order sp!, { \regs, pc }
.endm
#endif

#endif
