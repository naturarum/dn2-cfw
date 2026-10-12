# Elektron firmware archive

`elektron-firmware/` holds the user's archive of stock Elektron OS files. **It is precious and read-only:** every file is `chmod a-w`, and git ignores the folder. Never modify, move or delete anything in it. To use a file, extract a copy to `out/firmware/` (ignored), as below.

- **The Digitone II 1.12 file** is Elektron's own download; its sha256 is pinned below and in the tools.
- **Source of the older files:** the archive.org item `elektron-firmware` ("Archive of firmware for Elektron devices that is no longer available on their website"), published 2026-10-02 and marked "checked for malware" by archive.org.
- **Checks run 2026-10-05:** every `.syx` passes elektron-firmware-tool's transport and content checksums (and the HMAC, where the file has one). Files whose sha256 matches a pin in another project are named in the table. For Digitone II 1.12, the pin is our own branch's, which matches the user's own download.
- **Copies:** extracted copies of every `.syx` are in `out/firmware/<zip name>/`. The full manifest, including readme files, is `out/firmware-manifest.json`.

| zip | OS file | device | version | checksums | sha256 | also pinned by |
|---|---|---|---|---|---|---|
| Analog-Drive-OS1.5.zip | Analog-Drive-OS1.5.syx | Analog Drive (0x09) | ? | ok | `f7464de2050323f7d9acafb33eee388e65dc8f5cc8969f28452d129048f36dc5` |  |
| Analog-Four_Analog-Keys_OS1.55B.zip | Analog-Four_Analog-Keys_OS1.55B.syx | Analog Four / Analog Keys (0x06) | 1.55B | ok | `80d9ada793aabe35adbd32bab0bad8bd6e937bcdc5008d3d08d2f657cbb5f6a6` |  |
| Analog-Four_Analog-Keys_OS1.55D_dist.zip | Analog-Four_Analog-Keys_OS1.55D.syx | Analog Four / Analog Keys (0x06) | 1.55D | ok | `6449d1a7718371332c48081ca8ac514da76e7ff47613511b2eb00f6d4118582b` |  |
| Analog-Four_Analog-Keys_OS1.56_dist.zip | Analog-Four_Analog-Keys_OS1.56.syx | Analog Four / Analog Keys (0x06) | 1.56 | ok | `cda4459d14bfba40635440ad9dfa8fc183e5317e5e3ffa7116704f307fdf7010` |  |
| Analog-Four_MKII_OS1.56_dist.zip | Analog-Four_MKII_OS1.56.syx | Analog Four MKII (0x0b) | 1.56 | ok | `5aea489db3e747547f71d5e08a2d20253149fe6258defad60306402d2fc23cb5` |  |
| Analog-HeatFX_OS1.02_dist.zip | Analog-Heat+FX_OS1.02.syx | Analog Heat +FX (0x13) | 1.02 | ok | `85abe773c9e2284cd1b2aaaf1f599ec0f800f56f4b34127214fe998f1a2cfe63` |  |
| Analog-Heat_MKII_OS1.22_dist.zip | Analog-Heat_MKII_OS1.22.syx | Analog Heat MKII (0x0e) | 1.22 | ok | `5401d6286a9a79366e6ed4bc0e1f9c849aa39c1c035360cccce2e7f78039a35d` |  |
| Analog-Heat_OS1.22_dist.zip | Analog-Heat_OS1.22.syx | Analog Heat (0x08) | 1.22 | ok | `4473055689e5982df49406a76e818c06dcf82d3831cfb86c587b97b88f33e109` |  |
| Analog-Rytm_MKII_OS1.71_dist_2.zip | Analog-Rytm_MKII_OS1.71.syx | Analog Rytm MKII (0x0c) | 1.71 | ok | `d32a8e013d84b04d1abdff9afba3ed0c89b3b84082f8cb5db57a08527168bba9` |  |
| Analog-Rytm_MKII_OS1.73_dist.zip | Analog-Rytm_MKII_OS1.73.syx | Analog Rytm MKII (0x0c) | 1.73 | ok | `8ad671087ec30433d5407c7380d2396e0915ab931c5d3dd18a78bf284d3d1e52` |  |
| Analog-Rytm_MKII_OS1.74_dist.zip | Analog-Rytm_MKII_OS1.74.syx | Analog Rytm MKII (0x0c) | 1.74 | ok | `cb0f99d620edde994fb860c0e79cc2464e427d06882828cfdfb00fc53700e123` |  |
| Analog-Rytm_OS1.71_dist_2.zip | Analog-Rytm_OS1.71.syx | Analog Rytm (0x07) | 1.71 | ok | `46550ad99fa737c3f4596cf52bd7f4194919929253ce8f3532eb16857768ae7d` |  |
| Analog-Rytm_OS1.73_dist.zip | Analog-Rytm_OS1.73.syx | Analog Rytm (0x07) | 1.73 | ok | `9115c3888354bb388f90410e0445cd312bf020593ed99f768a99e475d1d6157c` |  |
| Analog-Rytm_OS1.74_dist.zip | Analog-Rytm_OS1.74.syx | Analog Rytm (0x07) | 1.74 | ok | `4f0d59051b4b3ac01f336639918ceb8186e0ae1a1abf2ed9bc028b8718514cff` |  |
| Digitakt_OS1.52A_dist.zip | Digitakt_OS1.52A.syx | Digitakt (0x0a) | 1.52A | ok | `01315133041dcdb8b432146190cc74fc8695c47d8466b0f31bd78cef96fa56a4` |  |
| Digitakt_OS1.52_dist.zip | Digitakt_OS1.52.syx | Digitakt (0x0a) | 1.52 | ok | `0a8250f799904194cfb5c12b88adef2352f3645f8a5c6e57eb8539998aff1e45` |  |
| Digitakt_OS1.53_dist.zip | Digitakt_OS1.53.syx | Digitakt (0x0a) | 1.53 | ok | `9bdd44bb6102fb25c143cfab97bc92b7a89c463f795d3112dce89771e29bcc92` | elekloader |
| Digitakt_OS1.54_dist.zip | Digitakt_OS1.54.syx | Digitakt (0x0a) | 1.54 | ok | `f78ba80fa7b1da5fb0e1ff61ad61e9e71aafe79f4364fc49679f3651353e3cf6` | elekloader |
| Digitone_II_OS1.00A_dist.zip | Digitone_II_OS1.00A.syx | Digitone II (0x15) | 1.00A | ok | `3b0448246a025e4f29649b38fa48e752aa09676fb0eb3d92b9a6793e53d5fc7d` |  |
| Digitone_II_OS1.10A_dist-1.zip | Digitone_II_OS1.10A.syx | Digitone II (0x15) | 1.10A | ok | `4dcc46fa790c83c7f87a9a72f2c74bed3b7be4f17ce9cff61ab0eb09ff0090cc` |  |
| Digitone_II_OS1.10B_dist.zip | Digitone_II_OS1.10B.syx | Digitone II (0x15) | 1.10B | ok | `e8fb540aa62069b8a159458c119148d352ba62a25547c16a6e2c9a3078e66853` |  |
| Digitone_II_OS1.10C_dist.zip | Digitone_II_OS1.10C.syx | Digitone II (0x15) | 1.10C | ok | `8db03f8407fc0cb1adf0d6f828f7b94429226fc07e1289e6d9136462583d30b1` |  |
| Digitone_II_OS1.10E_dist.zip | Digitone_II_OS1.10E.syx | Digitone II (0x15) | 1.10E | ok | `9881ee5ca4624be2b833e5eb94e294f2f8425371e6729781baccd1c256cac504` | digikit |
| Digitone_II_OS1.11_dist.zip | Digitone_II_OS1.11.syx | Digitone II (0x15) | 1.11 | ok | `2af43e65e3d8390b41c9f66222620f8cce027d73ed87db00c80b440f628472e0` | digikit |
| Digitone_II_OS1.12_dist.zip | Digitone_II_OS1.12.syx | Digitone II (0x15) | 1.12 | ok | `7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35` | digikit |
| Digitone_and_Digitone_Keys_OS1.42_dist.zip | Digitone_and_Digitone_Keys_OS1.42.syx | Digitone / Digitone Keys (0x0d) | 1.42 | ok | `0de87bf55e6bc48bebef5479e0d054f262fb7a0c7f81052dbd195d38569fdfda` |  |
| Digitone_and_Digitone_Keys_OS1.43_dist.zip | Digitone_and_Digitone_Keys_OS1.43.syx | Digitone / Digitone Keys (0x0d) | 1.43 | ok | `c5a54cc05b921f2e4bd814834c5365c2a5aa01d7772a9a2961fac1c3095bf9aa` | elekloader |
| Digitone_and_Digitone_Keys_OS1.44_dist.zip | Digitone_and_Digitone_Keys_OS1.44.syx | Digitone / Digitone Keys (0x0d) | 1.44 | ok | `d4f200d04484333d82822db7744e6484d0def8f2db8ddf55ee2b780cc13c9659` | elekloader |
| Elektron_SFX6-60_OS1.32B.zip | Elektron_SFX6-60_OS1.32B.syx | Monomachine (0x03) | ? | ok | `fe13c09f661cdba5fe57813240fa1574de1ee0a3ddd7047fc39d0318cba1a710` |  |
| Elektron_SPS1-1UW_OS1.63.zip | Elektron_SPS1-1UW_OS1.63.syx | Machinedrum (0x02) | ? | ok | `a58cd61f2efacfb07add0c643162fb4365c30e73831f2021c17b4aa3b42cabd5` |  |
| OCTATRACK_OS1.40B.zip | OCTATRACK_OS1.40B.syx | Octatrack (0x05) | 1.40B | ok | `68d92d76324af55d63fe9f22a3d5838c7c7a2a76d5bc3f53cfc4709114eb146e` |  |
| OCTATRACK_OS1.40C_dist.zip | OCTATRACK_OS1.40C.syx | Octatrack (0x05) | 1.40C | ok | `0a8d2d2b35c2cc78fa338576adc1af0cbc094cef973255103165c73edd87f2b6` | elekloader |
| Syntakt-OS-1.40A.zip | Syntakt_OS1.40A.syx | Syntakt (0x16) | 1.40A | ok | `266a5a3022ff565bf470173147b7f3005b7da8ef80724b6271836818ac2b5828` |  |
| Syntakt-OS-1.41.zip | Syntakt_OS1.41.syx | Syntakt (0x16) | 1.41 | ok | `8e2488f462c4a5656396a895f113bcd415e9900fa8709340dccf45d4cb9ed19e` |  |
| Syntakt_OS1.30B_dist.zip | Syntakt_OS1.30B.syx | Syntakt (0x16) | 1.30B | ok | `500a75c42fd45ad8d8365bf6b18e15bf5be2d75a6b357ae673097241c27593ea` |  |
| Syntakt_OS1.30_dist_1.zip | Syntakt_OS1.30.syx | Syntakt (0x16) | 1.30 | ok | `f7c53691883a8b3f675f4f368ca3aea44f6ef91a3f98b727731ccb3909b69f16` |  |
| Syntakt_OS1.42_dist.zip | Syntakt_OS1.42.syx | Syntakt (0x16) | 1.42 | ok | `ded3c59fdb909eb9e2c3307b30a486452c8fa6947b12c14e0ce35a187b7f37c1` |  |
| model-cycles_OS1.13.zip | model-cycles_OS1.13.syx | Model:Cycles (0x11) | 1.13 | ok | `44fe586269631a0ca7da25a3383fc6733c314809505fc3cc52f1e0ed9800640c` |  |
| model-samples_OS1.13.zip | model-samples_OS1.13.syx | Model:Samples (0x0f) | 1.13 | ok | `e11859b68deb7e5e3fe86ab32581212093849c4be5d3950add011eac398a2ce8` |  |

Zips with no `.syx` (a different update format): Tonverk-OS-1.2.0.zip, Tonverk-OS-1.2.1.zip, Tonverk-OS-1.3.3.zip, Tonverk-OS-1.4.1.zip.

Release-by-release comparison of the Digitone II files: `notes/dn2-release-history.md`.
