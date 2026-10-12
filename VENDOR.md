# Upstream projects in vendor/

Each is a separate git clone, ignored by this repo. This table pins the commit we worked against. `scripts/vendor-pins.sh` prints fresh rows.

To contribute to one: fork it on GitHub, add the fork as a remote in its `vendor/` clone, work on a branch, open a PR. Read its contributor notes first (listed below).

| dir | upstream | licence | commit | commit date | used for | contributor notes |
|---|---|---|---|---|---|---|
| digikit | https://github.com/m-dwyer/digikit | GPL-2.0-or-later | 21b38b9 | 2026-09-24 | ColdFire emulator (boots DN2 1.10E/1.11), SHARC+ analysis database | `CLAUDE.md` |
| elekloader | https://github.com/irpina/elekloader | GPL-2.0-or-later (since 2026-09-27; `js/` GPL-3.0-or-later) | 793b2e4 | 2026-10-04 | mod loader; Digitakt II 1.17 profile and core are the template for DN2 | `AGENTS.md`, `docs/DEVICES.md` |
| elektron-firmware-tool | https://github.com/mischa85/elektron-firmware-tool | MIT | a5bce9a | 2026-09-08 | unpack/rebuild `.syx`; verified on DN2 1.12 (byte-exact `-r`, HMAC ok) | `README.md` |
| octabam | https://github.com/sambanks/octabam | MIT | 86a81970 | 2026-10-05 | Octatrack remixer: design reference only (different SoC and OS) | `CONTRIBUTING.md`, `AGENTS.md` |
| selache | https://github.com/js216/selache | GPL-3.0 (tools), MIT (runtime) | 2b26d3b | 2026-06-04 | SHARC+ compiler, assembler, linker, LDR writer, disassembler (same pin as digikit) | `README.md` |

## Our branches (published as forks, 2026-10-12)

- digikit `dn2-1.12`: https://github.com/naturarum/digikit/tree/dn2-1.12
- elekloader `dn2-1.12`: https://github.com/naturarum/elekloader/tree/dn2-1.12

| clone | branch | commits on top of the pin | what it holds |
|---|---|---|---|
| digikit | `dn2-1.12` | `9f880fd`, `abebfb7` | Digitone II 1.12 support, `tools/acceptoracle.py` (device oracle), `tools/recovery_harness.py`, `tools/acceptoracle_dt2.py` |
| elekloader | `dn2-1.12` | `457c1bf` | Digitone II 1.12 device profile, the `dense` packer, `more_stages` and `receive_max`, tests (units 19, Digitone II 20, Digitakt II 18 pass) |

Both are candidates for upstream pull requests (ROADMAP C1, C2); before one, compare with the open PRs upstream (dn2_firmware_explore's author has many in digikit). Before a pull request:
run the image-pinned tests that need 1.11 and the Digitakt II files, and replace any workspace-specific paths.

## Read-only clones from the prior-art survey (2026-10-06)

Shallow clones (`git clone --depth 1`), read for `notes/prior-art-2026-10.md`. Nothing in them was run or changed;
none is a contribution target yet. Pins are the commit cloned.

| dir | upstream | licence | commit | commit date | one line |
|---|---|---|---|---|---|
| dn2_firmware_explore | https://github.com/angellinares/dn2_firmware_explore | AGPL-3.0-or-later | b989647 | 2026-10-06 | **DN2 1.11 mod project ("dnfw")**: hardware-confirmed mods (midiarp, lfo4, fxmod, transients, ...) and Waverider, new SHARC code baked into section 7 |
| DNX | https://github.com/NoiseAndMatter/DNX | AGPL-3.0 | 79288c1 | 2026-10-06 | same author as dnfw: browser tools and decoded DN1/DN2 project, pattern, kit, song and +Drive formats |
| digikit-sharc-emulator | https://github.com/m-dwyer/digikit (branch `work/sharc-emulator`) | GPL-2.0-or-later | 277760a | 2026-10-04 | coupled ColdFire + SHARC emulator in Rust, renders DN2 1.11 voices, ~1.22x slower than real time |
| syxgrid-digitone-ii | https://github.com/xrcstrecords/syxgrid-digitone-ii | AGPL-3.0 (SysEx map CC BY 4.0) | e806863 | 2026-10-04 | DN2 pattern editor in the browser; public SysEx and +Drive RPC map |
| capicola | https://github.com/heavylight-industries/capicola | AGPL-3.0 | 120b0b8 | 2026-10-01 | Eurorack (Daisy/STM32) time stretcher; no Elektron link; DSP ideas only |
| DigiFilter | https://github.com/DigiAlchemydsp/DigiFilter | GPL-2.0-or-later | a415ed1 | 2026-10-05 | Digitakt mk1 per-voice filter modes, ColdFire EMAC code at the per-voice filter call; FLTR curve via ev_draw |
| RingTone | https://github.com/DigiAlchemydsp/RingTone | GPL-2.0-or-later | 2d61311 | 2026-10-05 | Digitone mk1 master FX on the main ColdFire; `DSP.md` maps the mk1's two-CPU audio |
| Tone-FX | https://github.com/DigiAlchemydsp/Tone-FX | GPL-2.0-or-later | 7f329d8 | 2026-10-05 | release-only repo of RingTone's suite (no source) |
| DigiSplash | https://github.com/DigiAlchemydsp/DigiSplash | GPL-2.0 | cf65965 | 2026-09-30 | mk1 boot splash mods and a PNG/GIF frame converter |
| digistring | https://github.com/Alez22/digistring | MIT | b23f061 | 2026-10-06 | Karplus-Strong machine for Digitakt mk1 (render hook 0x40077fb2, borrows SLICE's parameters) |
| digisophie | https://github.com/soejrd/digisophie | MIT | 961c39c | 2026-10-02 | 2-op percussion machine for Digitakt mk1; render-deadline test in digiemu |
| digineighbor | https://github.com/irpina/digineighbor | GPL-2.0-or-later | d09574a | 2026-10-01 | pitch-shifter machine for Digitakt mk1 |
| digihealth | https://github.com/irpina/digihealth | GPL-2.0-or-later | 6d2a956 | 2026-10-01 | CPU/DSP load readout (DTCN0 timing, idle-task timing) and FAST AUDIO for Digitakt mk1 |
| digirta | https://github.com/Alez22/digirta | MIT | 5a0d596 | 2026-10-04 | octave-band analyser for Digitakt mk1, events only |
| digislicer | https://github.com/irpina/digislicer | GPL-2.0-or-later | 0c1b680 | 2026-10-05 | slicer machine for Digitakt mk1; big tables on the firmware heap |
| digitables | https://github.com/irpina/digitables | GPL-2.0-or-later | 5bbd9ad | 2026-10-04 | pitch tables for Digitone mk1 via core-dn1's params, pages and project data |
| digiemu | https://github.com/irpina/digiemu | GPL-2.0-or-later | c1b5735 | 2026-10-03 | digikit fork: mk1 emulator apps with live audio; firmware check for modded builds |
| elekloader-qa | https://github.com/irpina/elekloader-qa | GPL-2.0-or-later (`js/` GPL-3.0-or-later) | 7922d13 | 2026-10-05 | snapshot of elekloader 0.4.0's kit-release work (TypeScript engine, website kit); no DN2 |
| modwerk | https://github.com/repeat98/modwerk | GPL-3.0 | 1077590 | 2026-10-06 | community hub; DN2 listed as "research"; octabam's DSP dynload (run-time DSP code upload) |
| digitakt2-perform | https://github.com/toonst/digitakt2-perform | GPL-2.0-or-later | 09688a6 | 2026-10-05 | two DT2 1.17 elekloader mods (PERFORM KIT keys and levels); no drawing code |
| digisplash-ii | https://github.com/Fadedlimes/digisplash-ii | none stated | 5ae9c64 | 2026-10-05 | DT2 1.17 boot animation replacer and research notes (contradict its code in places) |
| digitakt-ii-splash-screens | https://github.com/octplane/digitakt-ii-splash-screens | MIT | d5e0669 | 2026-09-25 | DT2 1.16 intro selector patch |
| dt2-re-kb | https://github.com/Fadedlimes/My-personal-Digitakt-II-reverse-engeneering-knowledge-base | none stated | d383048 | 2026-10-05 | personal DT2 reverse-engineering notes |
| digitakt-ii-firmware-research-public | https://github.com/lalzart/digitakt-ii-firmware-research-public | CC0-1.0 | b2f93c1 | 2026-09-15 | DT2 OS 1.15C architecture research (SHARC audio task chain, machine selector) |
| elektron-model-tweaks | https://github.com/drumkilla/elektron-model-tweaks | MIT | adc22ed | 2026-10-05 | Model:Cycles/Samples 1.13 ColdFire tweaks in image caves |
