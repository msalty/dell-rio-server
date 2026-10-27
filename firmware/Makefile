# RioServer NG receiver firmware.
#
#   make            build build/receiver-ng.arf (fetches libmad and the
#                   RioPlay kernel image from SourceForge on first use)
#   make init       build just the player binary (build/init)
#   make clean
#
# Requires arm-none-eabi-gcc (any recent version) and python3.

CROSS   ?= arm-none-eabi-
CC      := $(CROSS)gcc
OBJCOPY := $(CROSS)objcopy
BUILD   ?= build
DEPS    ?= $(BUILD)/deps

LIBMAD_VER := 0.15.1b
LIBMAD_DIR := $(DEPS)/libmad-$(LIBMAD_VER)
LIBMAD_SRC := bit.c fixed.c frame.c huffman.c layer12.c layer3.c stream.c synth.c timer.c version.c
KERNEL_ARF := $(DEPS)/receiver-rioplay.arf

GCC_INC := $(shell $(CC) -print-file-name=include)

ARCH    := -mcpu=arm7tdmi -marm -mfloat-abi=soft
CFLAGS  := $(ARCH) -O2 -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns \
           -fno-strict-aliasing -fno-unwind-tables -fno-asynchronous-unwind-tables \
           -ffunction-sections -fdata-sections -nostdinc -isystem $(GCC_INC) \
           -Iinclude -Isrc -I$(LIBMAD_DIR) -Wall -Wextra -Wno-unused-parameter
# FPM_ARM, ASO_INTERLEAVE1 and ASO_IMDCT (with imdct_l_arm.S) are what
# libmad's own configure selects for ARM targets.
MADFLAGS := -DFPM_ARM -DASO_INTERLEAVE1 -DASO_IMDCT -DOPT_SPEED -DSIZEOF_INT=4 -DSIZEOF_LONG=4 -DSIZEOF_LONG_LONG=8 -DNDEBUG \
            -Wno-sign-compare -Wno-implicit-fallthrough -Wno-unused-but-set-variable -w
# Load address and page alignment match the binaries the receiver's
# Linux 2.2 kernel was built to run (RioPlay links the same way).
LDFLAGS := $(ARCH) -nostdlib -static -Wl,--gc-sections -Wl,-Ttext-segment=0x02000000 \
           -Wl,-z,max-page-size=0x8000 -Wl,-z,noexecstack -Wl,--build-id=none

FW_SRC  := $(wildcard src/*.c)
FW_OBJ  := $(patsubst src/%.c,$(BUILD)/obj/%.o,$(FW_SRC))
MAD_OBJ := $(patsubst %.c,$(BUILD)/obj/mad/%.o,$(LIBMAD_SRC)) $(BUILD)/obj/mad/imdct_l_arm.o

all: $(BUILD)/receiver-ng.arf

init: $(BUILD)/init

$(LIBMAD_DIR)/mad.h:
	python3 tools/fetch_deps.py libmad $(DEPS)

$(KERNEL_ARF):
	python3 tools/fetch_deps.py rioplay $(DEPS)

$(BUILD)/obj/%.o: src/%.c $(wildcard src/*.h) $(LIBMAD_DIR)/mad.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -DFPM_ARM -c $< -o $@

$(BUILD)/obj/mad/imdct_l_arm.o: $(LIBMAD_DIR)/mad.h
	@mkdir -p $(dir $@)
	$(CC) $(ARCH) -c $(LIBMAD_DIR)/imdct_l_arm.S -o $@

$(BUILD)/obj/mad/%.o: $(LIBMAD_DIR)/mad.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(MADFLAGS) -c $(LIBMAD_DIR)/$*.c -o $@

$(BUILD)/init: $(FW_OBJ) $(MAD_OBJ)
	$(CC) $(LDFLAGS) -o $@ $^ -lgcc
	python3 tools/elf_oabi.py $@
	$(CROSS)size $@

$(BUILD)/receiver-ng.arf: $(BUILD)/init $(KERNEL_ARF) tools/mkarf.py
	python3 tools/mkarf.py --kernel-from $(KERNEL_ARF) --init $(BUILD)/init -o $@

# CPU benchmark (see tools/cpubench.py, which generates bench_mp3.c and runs it)
$(BUILD)/obj/bench.o: bench/bench.c $(wildcard src/*.h) $(LIBMAD_DIR)/mad.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -DFPM_ARM -c $< -o $@

$(BUILD)/obj/bench_mp3.o: $(BUILD)/bench_mp3.c
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/bench: $(BUILD)/obj/bench.o $(BUILD)/obj/bench_mp3.o $(BUILD)/obj/sys.o $(BUILD)/obj/gfx.o $(MAD_OBJ)
	$(CC) $(LDFLAGS) -o $@ $^ -lgcc

clean:
	rm -rf $(BUILD)/obj $(BUILD)/init $(BUILD)/receiver-ng.arf $(BUILD)/bench $(BUILD)/bench_mp3.c

.PHONY: all init clean
