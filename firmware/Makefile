# RioServer NG receiver firmware.
#
#   make            build build/receiver-ng.arf (fetches the MP3 decoder and
#                   the RioPlay kernel image on first use)
#   make init       build just the player binary (build/init)
#   make MAD=stock  use libmad as released instead of Rockbox's version
#   make clean
#
# Requires arm-none-eabi-gcc (any recent version) and python3, plus zopfli
# to replace the kernel's DELL/Rio splash screens with the RioServer NG logo.

CROSS   ?= arm-none-eabi-
CC      := $(CROSS)gcc
OBJCOPY := $(CROSS)objcopy
BUILD   ?= build
DEPS    ?= $(BUILD)/deps

# The MP3 decoder. Both are libmad 0.15.1b with the same API. Rockbox's
# copy (the default) adds hand-written ARM assembly for the synthesis
# filterbank, the most expensive stage, and tunes the rest for ARM cores of
# the receiver's generation; MAD=stock builds libmad as released.
MAD ?= rockbox
ifeq ($(MAD),rockbox)
LIBMAD_DIR := $(DEPS)/rockbox-libmad
LIBMAD_SRC := bit.c frame.c huffman.c layer12.c layer3.c stream.c synth.c
LIBMAD_ASM := imdct_l_arm.S synth_full_arm.S
# rockbox/ stands in for the parts of Rockbox's build its libmad expects;
# Rockbox code includes its config.h before mad.h, so ours does too.
MAD_INC  := -Irockbox
MAD_USE  := -include config.h -DMAD_ROCKBOX
MAD_HDRS := $(wildcard rockbox/*.h)
MADFLAGS := -DHAVE_CONFIG_H -DHAVE_LIMITS_H -DNDEBUG
else ifeq ($(MAD),stock)
LIBMAD_DIR := $(DEPS)/libmad-0.15.1b
LIBMAD_SRC := bit.c fixed.c frame.c huffman.c layer12.c layer3.c stream.c synth.c timer.c version.c
LIBMAD_ASM := imdct_l_arm.S
MAD_INC  :=
MAD_USE  :=
MAD_HDRS :=
# FPM_ARM, ASO_INTERLEAVE1 and ASO_IMDCT (with imdct_l_arm.S) are what
# libmad's own configure selects for ARM targets.
MADFLAGS := -DFPM_ARM -DASO_INTERLEAVE1 -DASO_IMDCT -DOPT_SPEED -DSIZEOF_INT=4 -DSIZEOF_LONG=4 -DSIZEOF_LONG_LONG=8 -DNDEBUG
else
$(error MAD must be rockbox or stock)
endif
KERNEL_ARF := $(DEPS)/receiver-rioplay.arf

GCC_INC := $(shell $(CC) -print-file-name=include)

ARCH    := -mcpu=arm7tdmi -marm -mfloat-abi=soft
CFLAGS  := $(ARCH) -O2 -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns \
           -fno-strict-aliasing -fno-unwind-tables -fno-asynchronous-unwind-tables \
           -ffunction-sections -fdata-sections -nostdinc -isystem $(GCC_INC) \
           -Iinclude -Isrc $(MAD_INC) -I$(LIBMAD_DIR) -Wall -Wextra -Wno-unused-parameter
MADFLAGS += -Wno-sign-compare -Wno-implicit-fallthrough -Wno-unused-but-set-variable -w
# Load address and page alignment match the binaries the receiver's
# Linux 2.2 kernel was built to run (RioPlay links the same way).
LDFLAGS := $(ARCH) -nostdlib -static -Wl,--gc-sections -Wl,-Ttext-segment=0x02000000 \
           -Wl,-z,max-page-size=0x8000 -Wl,-z,noexecstack -Wl,--build-id=none

FW_SRC  := $(wildcard src/*.c)
FW_OBJ  := $(patsubst src/%.c,$(BUILD)/obj/%.o,$(FW_SRC))
# Objects are kept apart per decoder, and build/mad records which one the
# last link used, so switching MAD= rebuilds what it must.
MAD_C_OBJ := $(patsubst %.c,$(BUILD)/obj/mad-$(MAD)/%.o,$(LIBMAD_SRC))
MAD_S_OBJ := $(patsubst %.S,$(BUILD)/obj/mad-$(MAD)/%.o,$(LIBMAD_ASM))
MAD_OBJ   := $(MAD_C_OBJ) $(MAD_S_OBJ)
MAD_STAMP := $(BUILD)/mad-$(MAD).stamp

all: $(BUILD)/receiver-ng.arf

init: $(BUILD)/init

$(DEPS)/libmad-0.15.1b/mad.h:
	python3 tools/fetch_deps.py libmad $(DEPS)

$(DEPS)/rockbox-libmad/mad.h:
	python3 tools/fetch_deps.py rockbox-libmad $(DEPS)

$(MAD_STAMP):
	@mkdir -p $(BUILD)
	rm -f $(BUILD)/mad-*.stamp
	touch $@

$(KERNEL_ARF):
	python3 tools/fetch_deps.py rioplay $(DEPS)

$(BUILD)/obj/%.o: src/%.c $(wildcard src/*.h) $(MAD_HDRS) $(LIBMAD_DIR)/mad.h $(MAD_STAMP)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(MAD_USE) -DFPM_ARM -c $< -o $@

# (The decoder's sources appear when mad.h is fetched, so they are named
# here rather than matched as prerequisites.)
$(MAD_S_OBJ): $(BUILD)/obj/mad-$(MAD)/%.o: $(LIBMAD_DIR)/mad.h $(MAD_HDRS)
	@mkdir -p $(dir $@)
	$(CC) $(ARCH) $(MAD_INC) -I$(LIBMAD_DIR) -c $(LIBMAD_DIR)/$*.S -o $@

$(MAD_C_OBJ): $(BUILD)/obj/mad-$(MAD)/%.o: $(LIBMAD_DIR)/mad.h $(MAD_HDRS)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(MADFLAGS) -c $(LIBMAD_DIR)/$*.c -o $@

$(BUILD)/init: $(FW_OBJ) $(MAD_OBJ) $(MAD_STAMP)
	$(CC) $(LDFLAGS) -o $@ $(FW_OBJ) $(MAD_OBJ) -lgcc
	python3 tools/elf_oabi.py $@
	$(CROSS)size $@

$(BUILD)/receiver-ng.arf: $(BUILD)/init $(KERNEL_ARF) tools/mkarf.py tools/kernel_splash.py $(wildcard art/*.pbm)
	python3 tools/mkarf.py --kernel-from $(KERNEL_ARF) --init $(BUILD)/init --splash art -o $@

# CPU benchmark (see tools/cpubench.py, which generates bench_mp3.c and runs it)
$(BUILD)/obj/bench.o: bench/bench.c $(wildcard src/*.h) $(LIBMAD_DIR)/mad.h $(MAD_STAMP)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(MAD_USE) -DFPM_ARM -c $< -o $@

$(BUILD)/obj/bench_mp3.o: $(BUILD)/bench_mp3.c
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/bench: $(BUILD)/obj/bench.o $(BUILD)/obj/bench_mp3.o $(BUILD)/obj/sys.o $(BUILD)/obj/gfx.o $(MAD_OBJ) $(MAD_STAMP)
	$(CC) $(LDFLAGS) -o $@ $(filter %.o,$^) -lgcc

clean:
	rm -rf $(BUILD)/obj $(BUILD)/init $(BUILD)/receiver-ng.arf $(BUILD)/bench $(BUILD)/bench_mp3.c $(BUILD)/mad-*.stamp

.PHONY: all init clean
