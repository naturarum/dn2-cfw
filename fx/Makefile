# SPDX-License-Identifier: GPL-2.0-or-later
# T3.11 FX workbench (and T5.2, the Space Echo). Mac builds with clang; build outputs go to ../out/T3.11/.
#   make            build fxrender, fxgen, fxtest
#   make test       run the tests
#   make dry        generate the dry test material (out/T3.11/dry/)
#   make renders    render every A/B example and write out/T3.11/ab.html
#   make stock      the stock DN2 effects on the same dry files, in the T3.6 SHARC emulator (~30 s,
#                   cached), then the page again; needs out/T3.6/state (tools/sharcvm)
#   make sharc      compile the effects with selache's selcc for the SHARC+ and count instructions
#   make bench      Mac speed of each effect (relative numbers only)
#   make listen     serve out/T3.11 on http://127.0.0.1:8711/ab.html (gapless A/B switching)

ROOT    := ..
OUT     := $(ROOT)/out/T3.11
BIN     := $(OUT)/bin
CC      := clang
# the effect code must stay clean under these: float only, no implicit double
FXWARN  := -std=c99 -Wall -Wextra -Wpedantic -Wdouble-promotion -Wfloat-conversion -Wshadow
CFLAGS  := -O2 -fno-strict-aliasing -ffp-contract=off $(FXWARN)
SELCC   := $(ROOT)/vendor/selache/target/release/selcc

FX_SRC  := tapedelay.c mbcomp.c flanger.c spaceecho.c
FX_HDR  := fxmath.h tapedelay.h mbcomp.h flanger.h flanger_unit.h spaceecho.h

all: $(BIN)/fxrender $(BIN)/fxgen $(BIN)/fxtest

$(BIN):
	mkdir -p $(BIN)

$(BIN)/fxrender: render.c wav.c wav.h $(FX_SRC) $(FX_HDR) | $(BIN)
	$(CC) $(CFLAGS) -Wno-double-promotion -Wno-float-conversion -o $@ render.c wav.c $(FX_SRC) -lm

# SC01: fxrender with SE01's Space Echo (its INTENSITY law; tools/dspsplice/src/se01/), for the se_fdbk group
SE01 := ../tools/dspsplice/src/se01
$(BIN)/fxrender_se01: render.c wav.c wav.h tapedelay.c mbcomp.c flanger.c $(SE01)/spaceecho.c $(SE01)/spaceecho.h $(FX_HDR) | $(BIN)
	$(CC) $(CFLAGS) -Wno-double-promotion -Wno-float-conversion -I$(SE01) -I. -o $@ render.c wav.c tapedelay.c mbcomp.c flanger.c $(SE01)/spaceecho.c -lm

# SC02: fxrender with SC01's Space Echo (FDBK/64 per head; tools/dspsplice/src/sc01/), for the se_share group
SC01 := ../tools/dspsplice/src/sc01
$(BIN)/fxrender_sc01: render.c wav.c wav.h tapedelay.c mbcomp.c flanger.c $(SC01)/spaceecho.c $(SC01)/spaceecho.h $(FX_HDR) | $(BIN)
	$(CC) $(CFLAGS) -Wno-double-promotion -Wno-float-conversion -I$(SC01) -I. -o $@ render.c wav.c tapedelay.c mbcomp.c flanger.c $(SC01)/spaceecho.c -lm

$(BIN)/fxgen: gen.c wav.c wav.h | $(BIN)
	$(CC) -O2 -std=c99 -Wall -Wextra -o $@ gen.c wav.c -lm

# test.c includes the effect sources itself, to reach their internals
$(BIN)/fxtest: test.c test_spaceecho.c $(FX_SRC) $(FX_HDR) | $(BIN)
	$(CC) $(CFLAGS) -Wno-double-promotion -Wno-float-conversion -o $@ test.c -lm

test: $(BIN)/fxtest
	$(BIN)/fxtest

dry: $(BIN)/fxgen
	$(BIN)/fxgen $(OUT)/dry

renders: $(BIN)/fxrender $(BIN)/fxrender_se01 $(BIN)/fxrender_sc01 dry
	./renders.sh $(OUT)
	python3 make_ab.py $(OUT)

stock: renders
	python3 stock_renders.py $(OUT)
	python3 make_ab.py $(OUT)

sharc:
	./sharc_cost.sh $(OUT)/sharc

bench: $(BIN)/fxtest
	$(BIN)/fxtest -bench

listen:
	@echo "open http://127.0.0.1:8711/ab.html  (ctrl-C to stop)"
	cd $(OUT) && python3 -m http.server 8711 --bind 127.0.0.1

clean:
	rm -rf $(BIN) $(OUT)/sharc

.PHONY: all test dry renders stock sharc bench listen clean
