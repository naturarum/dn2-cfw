"""Small PyGhidra helper for the Digitone II firmware work.

Run with the venv python:   venv/bin/python script.py
Requires GHIDRA_INSTALL_DIR and JAVA_HOME (set below if missing).
"""
import os
import sys

os.environ.setdefault("GHIDRA_INSTALL_DIR", "/opt/homebrew/opt/ghidra/libexec")
os.environ.setdefault("JAVA_HOME", "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")

import pyghidra  # noqa: E402

pyghidra.start()

from java.io import File  # noqa: E402  (needs the JVM running)
from ghidra.base.project import GhidraProject  # noqa: E402
from ghidra.program.model.lang import LanguageID  # noqa: E402
from ghidra.program.util import DefaultLanguageService  # noqa: E402
from ghidra.util.task import ConsoleTaskMonitor  # noqa: E402
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the project folder

PROJECT_DIR = os.path.join(ROOT, "ghidra")
PROJECT_NAME = "dn2"
CF = "68000:BE:32:Coldfire"


def open_project():
    os.makedirs(PROJECT_DIR, exist_ok=True)
    if os.path.exists(os.path.join(PROJECT_DIR, PROJECT_NAME + ".gpr")):
        return GhidraProject.openProject(PROJECT_DIR, PROJECT_NAME, True)
    return GhidraProject.createProject(PROJECT_DIR, PROJECT_NAME, False)


def import_raw(project, binary_path, prog_name, base, lang=CF, block_name=None,
               extra_blocks=()):
    """Import a raw binary at `base`. extra_blocks: (name, start, length, rwx) uninitialised."""
    from ghidra.program.model.address import AddressSet  # noqa: F401
    service = DefaultLanguageService.getLanguageService()
    language = service.getLanguage(LanguageID(lang))
    comp = language.getDefaultCompilerSpec()
    prog = project.importProgram(File(binary_path), language, comp)
    if prog is None:
        raise RuntimeError("import failed for " + binary_path)
    project.saveAs(prog, "/", prog_name, True)
    mem = prog.getMemory()
    space = prog.getAddressFactory().getDefaultAddressSpace()
    tx = prog.startTransaction("place blocks")
    try:
        blk = mem.getBlocks()[0]
        mem.moveBlock(blk, space.getAddress(base), ConsoleTaskMonitor())
        blk.setName(block_name or prog_name)
        blk.setRead(True)
        blk.setWrite(True)
        blk.setExecute(True)
        for name, start, length, rwx in extra_blocks:
            b = mem.createUninitializedBlock(name, space.getAddress(start), length, False)
            b.setRead("r" in rwx)
            b.setWrite("w" in rwx)
            b.setExecute("x" in rwx)
            b.setVolatile("v" in rwx)
    finally:
        prog.endTransaction(tx, True)
    return prog


def seed_functions(prog, addrs, prefix="FUN_seed"):
    """Disassemble at and create a function for each address in addrs."""
    from ghidra.program.flatapi import FlatProgramAPI
    flat = FlatProgramAPI(prog)
    space = prog.getAddressFactory().getDefaultAddressSpace()
    tx = prog.startTransaction("seed")
    n = 0
    try:
        for a in addrs:
            ad = space.getAddress(a)
            if not prog.getMemory().contains(ad):
                continue
            if flat.getFunctionAt(ad) is None:
                flat.disassemble(ad)
                if flat.createFunction(ad, None) is not None:
                    n += 1
    finally:
        prog.endTransaction(tx, True)
    return n


def analyze(prog):
    pyghidra.analyze(prog)


def decompile_all(prog, out_path, timeout=60, only=None):
    from ghidra.app.decompiler import DecompInterface
    ifc = DecompInterface()
    ifc.openProgram(prog)
    fm = prog.getFunctionManager()
    mon = ConsoleTaskMonitor()
    n = 0
    with open(out_path, "w") as fh:
        for f in fm.getFunctions(True):
            if only and not only(f):
                continue
            res = ifc.decompileFunction(f, timeout, mon)
            d = res.getDecompiledFunction()
            fh.write("// ---- %s @ %s  (size %d)\n" % (f.getName(), f.getEntryPoint(), f.getBody().getNumAddresses()))
            if d is None:
                fh.write("// decompile failed: %s\n\n" % res.getErrorMessage())
            else:
                fh.write(d.getC() + "\n")
            n += 1
    ifc.dispose()
    return n
