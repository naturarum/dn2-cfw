# SPDX-License-Identifier: GPL-2.0-or-later
# Digiwerk Patcher (app/) and digiwerk-patch (the Terminal tool), carrying one or more tested builds ("packs")
# (README.md; notes/R1.1-patcher.md). Both call the core, dn2patch.c (dn2patch.h).
#
#   make                       PACKS="OB01": the pack(s), then out/patcher/cli/OB01/digiwerk-patch
#   make PACKS="CMP4 OB01 FL01"   three packs, listed in this order: out/patcher/cli/CMP4+OB01+FL01/digiwerk-patch
#   make app PACKS="..."       the app with those packs, unsigned (app/build.sh): out/patcher/app/SET/Digiwerk Patcher.app
#   make test                  test.sh: every pack byte for byte, one-pack and three-pack tools, the refusals, and the
#                              app's core path (app/test.sh)
#   make clean                 removes out/patcher/
#
# A pack (out/patcher/ID/patch.bin) is made from out/ID/ID.syx, its hand-written JSON (out/ID/pack.json or
# packs/ID.json) and its brickcheck report (out/brickcheck/ID-*/). The binaries are universal (arm64 and x86_64); the
# core is libc only. mkpatch.py needs python3 and the stock 1.12 file in elektron-firmware/ (developer side only).

ROOT    := $(abspath ../..)
PACKS   ?= OB01
empty   :=
space   := $(empty) $(empty)
SET     := $(subst $(space),+,$(strip $(PACKS)))
CLI     := $(ROOT)/out/patcher/cli/$(SET)
PATCHES := $(foreach b,$(PACKS),$(ROOT)/out/patcher/$(b)/patch.bin)
PYTHON  ?= python3
CC      := clang
CFLAGS  := -std=c99 -O2 -Wall -Wextra -arch arm64 -arch x86_64 -mmacosx-version-min=11.0

$(CLI)/digiwerk-patch: cli.c dn2patch.c dn2patch.h $(CLI)/packs.h
	$(CC) $(CFLAGS) -I. -I$(CLI) -o $@ cli.c dn2patch.c
	@ls -l $@ | awk '{print $$5 " bytes: " $$9}'

$(CLI)/packs.h: $(PATCHES) mkpatch.py
	$(PYTHON) mkpatch.py --embed $@ $(PATCHES)

.SECONDEXPANSION:
$(ROOT)/out/patcher/%/patch.bin: $(ROOT)/out/$$*/$$*.syx mkpatch.py stockcheck.py \
		$$(wildcard packs/$$*.json $(ROOT)/out/$$*/pack.json $(ROOT)/out/brickcheck/$$*-*/report.json)
	$(PYTHON) mkpatch.py $(ROOT)/out/$*/$*.syx --name $* --out $(dir $@)

app: $(PATCHES)
	sh app/build.sh $(PACKS)

test:
	sh test.sh

clean:
	rm -rf $(ROOT)/out/patcher

.PHONY: app test clean
