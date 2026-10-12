"""Cold-boot the main OS in digikit's emulator to 70M instructions and list its
flash reads (task T1.2: shows where it fetches section 7 from).

    cd vendor/digikit && uv run python ../../scripts/main_flash_reads.py FILE.syx SECTIONS_DIR
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor', 'digikit'))
from emu import dspboot
syx, sec = sys.argv[1], sys.argv[2]
os.environ['DT2_SECTIONS'] = sec
img = open(os.path.join(sec, 'section_3_MAIN_OS.bin'), 'rb').read()
m, st, stop = dspboot.run(syx, img, limit=70_000_000)
print(os.path.basename(syx), 'stop:', stop)
for off, ln, dest in st['reads']:
    print('  flash %#09x len %8d -> %#010x' % (off, ln, dest))
