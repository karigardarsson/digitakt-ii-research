# fmt: off
"""Import a SHARC blob (container section 7) into Ghidra with its real memory map.

    uv run python -m tools.sharc_dev.sharc_import out/sections/dt2-1.16/section_7_BLOB.bin \
        --name dt2-1.16_SHARC --seed-calls --analyze

The blob is an ADI boot stream: tools/sharc_dev/sharcldr.py parses it into blocks that
each name a target address and carry (or zero-fill) that many bytes. Those
targets are the only ground truth about where anything lives, so this builds
merged Ghidra memory spans and replays the ADI blocks in stream order rather
than dumping the file flat.

ADDRESSING. The core executes at 16-bit short-word addresses (0x1cxxxx,
0x12xxxx) while the loader normally writes byte addresses (0x28xxxxxx,
0x80xxxxxx), related by byte = 2 * sw + 0x28000000. The loader's L2 byte
window is a second, bounded mapping: 0x20000000 through 0x2001ffff maps to
execution SW 0x00b80000 through 0x00b8ffff. The language,
SHARC_VISA:LE:32:default (tools/coldfire/ghidra/install-sharc.sh), has a code space
with wordsize 2: an address offset counts bytes, Ghidra shows it as a
short-word address, and a decoded branch target is a short-word address. So
the normal program byte offsets are

    ghidra_offset = 2 * short_word_address = byte_address - 0x28000000

and an L2 byte offset is 2 * 0x00b80000 + byte_address - 0x20000000.

A pointer STORED in the image holds the short-word value, so use
--label-table to lay one out as function pointers; it doubles each entry.

Bounded FILL blocks are materialized with their repeated little-endian argument
so later fills preserve loader last-write-wins semantics. Absurdly large fills
(including a 32 MB DDR clear) are deliberately omitted from the analysis map.
"""
import argparse
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
from tools.sharc_dev import sharcldr as L  # noqa: E402
from tools.sharc_dev import sharcscan as S  # noqa: E402

LANGUAGE_ID = 'SHARC_VISA:LE:32:default'
SPACE_BASE = 0x28000000          # byte_address - this == ghidra byte offset, L1 alias only
# Upper bound of the loader's L1 system-alias byte window (SPACE_BASE..
# L1_ALIAS_LIMIT), i.e. byte = 2*sw + SPACE_BASE addresses. The loader's own
# L1 targets top out at 0x2839c000 (docs/findings/05-sharc-isa-and-decoding.md);
# 0x28400000 is a round, safely-above-observed limit. Outside this window
# (notably external memory such as 0x80000000..0x82a001c4) a loader byte
# address is not an alias of anything, so it is its own Ghidra byte offset --
# the same rule the DM byte-address translation in
# tools/sharc_dev/sharcspec/ghidra/gen_sleigh.py (dm_byte_addr_to_ram_unit) applies to
# an external DM literal.
L1_ALIAS_LIMIT = 0x28400000
DEFAULT_PROJECT = os.path.expanduser('~/ghidra-projects/elektron-sharc')
DEFAULT_PROJECT_NAME = 'elektron-sharc'
DEFAULT_GHIDRA = '/opt/homebrew/Cellar/ghidra/12.1.3/libexec'
MAX_UNINIT = 0x400000            # skip absurd fill blocks (a 32 MB DDR clear)
REPLAY_CHUNK = 0x10000


def ghidra_addr(byte_address):
    """Loader byte address -> Ghidra byte offset at its execution address.

    The L2 translation deliberately applies only to its measured byte window.
    The L1 system alias (SPACE_BASE..L1_ALIAS_LIMIT) is the only other
    address family that is not already its own Ghidra byte offset: it maps
    byte = 2*sw + SPACE_BASE down to offset 2*sw. Every other loader
    address -- notably external memory such as 0x80000000..0x82a001c4 --
    keeps its own value unchanged.
    """
    if L.L2_BYTE_BASE <= byte_address < L.L2_BYTE_LIMIT:
        return 2 * L.L2_SW_BASE + byte_address - L.L2_BYTE_BASE
    if SPACE_BASE <= byte_address < L1_ALIAS_LIMIT:
        return byte_address - SPACE_BASE
    return byte_address


def mapped_segments(byte_address, byte_count):
    """Split a loader range where the piecewise address mapping changes.

    Yields ``(source_offset, ghidra_offset, length)`` tuples.
    """
    end = byte_address + byte_count
    cuts = [byte_address, end]
    for boundary in (L.L2_BYTE_BASE, L.L2_BYTE_LIMIT, SPACE_BASE, L1_ALIAS_LIMIT):
        if byte_address < boundary < end:
            cuts.append(boundary)
    cuts.sort()
    for start, stop in zip(cuts, cuts[1:], strict=False):
        yield start - byte_address, ghidra_addr(start), stop - start


def loaded_bytes(data, block, offset, length):
    """Return one final-loader byte slice for a payload or little-endian fill."""
    if block['fill']:
        word = int(block['argument']).to_bytes(4, 'little')
        phase = offset % len(word)
        repeated = word * ((phase + length + len(word) - 1) // len(word))
        return repeated[phase:phase + length]
    start = block['payload_offset'] + offset
    return data[start:start + length]


def plan(data):
    """-> (blocks, entry_short_word_address). Blocks are sharcldr records.

    Per Table 40-29, a BFLAG_FIRST block's target_address is the start
    address of the application it begins, in the core's own SHORT-WORD
    space (0x1cxxxx/0x12xxxx), not a loader byte address like a data
    block's. A multi-application boot stream carries several BFLAG_FIRST
    blocks; the entry returned here is the LAST one, i.e. the final
    application's start address."""
    blocks = L.parse_blocks(data)
    eps = L.entry_points(blocks)
    entry = eps[-1] if eps else None
    return blocks, entry


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('blob')
    ap.add_argument('--name', help='program name in the project (default: blob basename)')
    ap.add_argument('--project', default=DEFAULT_PROJECT)
    ap.add_argument('--project-name', default=DEFAULT_PROJECT_NAME)
    ap.add_argument('--label-table', metavar='ADDR:COUNT', action='append', default=[],
                    help='short-word address of a table of COUNT function pointers; '
                         'each entry is doubled into a program address, labelled and '
                         'made an entry point. Repeatable.')
    ap.add_argument('--seed-calls', action='store_true',
                    help="also seed disassembly at every call target tools/sharc_dev/sharcscan.py "
                         "recovers from cjump encodings -- nothing in this processor "
                         "module knows where functions start, so without seeds "
                         "auto-analysis finds almost nothing")
    ap.add_argument('--analyze', action='store_true', help='run auto-analysis after import')
    ap.add_argument('--overwrite', action='store_true')
    args = ap.parse_args(argv)

    data = open(args.blob, 'rb').read()  # noqa: SIM115
    blocks, entry = plan(data)
    name = args.name or os.path.basename(args.blob)
    print('%s: %d blocks, entry sw %s' % (name, len(blocks),
                                          hex(entry) if entry else '(none)'))

    os.environ.setdefault('GHIDRA_INSTALL_DIR', DEFAULT_GHIDRA)
    import pyghidra
    pyghidra.start(verbose=False)

    from ghidra.base.project import GhidraProject
    from ghidra.program.database import ProgramDB
    from ghidra.program.model.lang import LanguageID
    from ghidra.program.model.symbol import SourceType
    from ghidra.program.util import DefaultLanguageService
    from ghidra.util.task import TaskMonitor
    from java.io import ByteArrayInputStream
    from java.lang import Object as JObject
    from jpype import JArray, JByte

    lang = DefaultLanguageService.getLanguageService().getLanguage(LanguageID(LANGUAGE_ID))
    if os.path.exists(os.path.join(args.project, args.project_name + '.gpr')):
        project = GhidraProject.openProject(args.project, args.project_name, False)
    else:
        os.makedirs(args.project, exist_ok=True)
        project = GhidraProject.createProject(args.project, args.project_name, False)
    try:
        existing = project.getProject().getProjectData().getFile('/' + name)
        if existing is not None:
            if not args.overwrite:
                print('program /%s already exists; pass --overwrite' % name)
                return 1
            existing.delete()

        consumer = JObject()
        program = ProgramDB(name, lang, lang.getDefaultCompilerSpec(), consumer)
        tx = program.startTransaction('import SHARC boot stream')
        try:
            space = program.getAddressFactory().getDefaultAddressSpace()
            mem = program.getMemory()

            # A boot stream may write the same region more than once, and
            # adjacent blocks routinely abut, so one Ghidra block per ADI
            # block both collides and fragments. Merge the ranges first,
            # then replay every write in stream order -- last write wins,
            # which is what the loader itself does.
            spans, skipped = [], 0
            for b in blocks:
                if b['byte_count'] == 0:
                    continue
                if b['fill'] and b['byte_count'] > MAX_UNINIT:
                    skipped += 1
                    continue
                spans.extend(
                    (mapped, length)
                    for _, mapped, length in mapped_segments(
                        b['target_address'], b['byte_count']))

            merged = []
            for start, length in sorted(spans):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], start + length)
                else:
                    merged.append([start, start + length])
            for i, (start, end) in enumerate(merged):
                mem.createInitializedBlock(
                    'mem%02d_%08x' % (i, start + SPACE_BASE),
                    space.getAddress(start),
                    ByteArrayInputStream(bytes(end - start)),
                    end - start, TaskMonitor.DUMMY, False)
            print('created %d merged memory block(s) from %d spans '
                  '(%d oversized fills skipped)' % (len(merged), len(spans), skipped))

            written = 0
            for b in blocks:
                if b['byte_count'] == 0:
                    continue
                if b['fill'] and b['byte_count'] > MAX_UNINIT:
                    continue
                for source, mapped, length in mapped_segments(
                        b['target_address'], b['byte_count']):
                    for local in range(0, length, REPLAY_CHUNK):
                        chunk = loaded_bytes(
                            data, b, source + local,
                            min(REPLAY_CHUNK, length - local))
                        mem.setBytes(
                            space.getAddress(mapped + local),
                            JArray(JByte)(list(
                                x - 256 if x > 127 else x for x in chunk)))
                written += 1
            print('replayed %d payload/fill block(s)' % written)

            symtab = program.getSymbolTable()
            seeds = []
            if entry is not None:
                ea = space.getAddress(2 * entry)   # marker blocks are short-word
                symtab.createLabel(ea, 'entry', SourceType.IMPORTED)
                symtab.addExternalEntryPoint(ea)
                seeds.append(ea)
                print('entry point at %s' % ea)

            for i, ep in enumerate(L.entry_points(blocks)):
                if ep == entry:
                    continue
                ea = space.getAddress(2 * ep)   # BFLAG_FIRST targets are short-word
                symtab.createLabel(ea, 'app_entry_%02d' % i, SourceType.IMPORTED)
                symtab.addExternalEntryPoint(ea)
                seeds.append(ea)
                print('application entry point at %s' % ea)

            for spec in args.label_table:
                addr_s, _, count_s = spec.partition(':')
                tsw = int(addr_s, 16)
                count = int(count_s, 0)
                tbl = space.getAddress(2 * tsw)
                symtab.createLabel(tbl, 'rpc_dispatch_table', SourceType.IMPORTED)
                for i in range(count):
                    raw = mem.getInt(tbl.add(4 * i)) & 0xFFFFFFFF
                    target = space.getAddress(2 * raw)
                    symtab.createLabel(target, 'rpc_handler_%02d' % i, SourceType.IMPORTED)
                    symtab.addExternalEntryPoint(target)
                    seeds.append(target)
                    print('  slot %2d: sw %#08x -> %s' % (i, raw, target))
            if args.seed_calls:
                targets = S.call_graph(data)
                added = 0
                for t in sorted(targets):
                    a = space.getAddress(2 * t)
                    if mem.contains(a):
                        seeds.append(a)
                        added += 1
                print('seeded %d of %d recovered call target(s)' % (added, len(targets)))

            # Nothing in this processor module knows where code starts, so
            # auto-analysis alone disassembles nothing. Seed it at the entry
            # point and each handler and let flow-following do the rest.
            from ghidra.app.cmd.disassemble import DisassembleCommand
            from ghidra.app.cmd.function import CreateFunctionCmd
            for addr in seeds:
                # A known entry can land inside a speculative flow-decoded
                # instruction. Prefer the immutable entry table over that
                # overlap so the handler can be decoded from its true start.
                prior = program.getListing().getInstructionContaining(addr)
                if prior is not None and prior.getAddress() != addr:
                    program.getListing().clearCodeUnits(
                        prior.getMinAddress(), prior.getMaxAddress(), False)
                DisassembleCommand(addr, None, True).applyTo(program, TaskMonitor.DUMMY)
                CreateFunctionCmd(addr).applyTo(program, TaskMonitor.DUMMY)
            print('seeded disassembly at %d address(es)' % len(seeds))
        finally:
            program.endTransaction(tx, True)

        project.saveAs(program, '/', name, True)
        print('saved /%s' % name)

        if args.analyze:
            from ghidra.app.plugin.core.analysis import AutoAnalysisManager
            tx = program.startTransaction('analyze')
            try:
                mgr = AutoAnalysisManager.getAnalysisManager(program)
                mgr.initializeOptions()
                mgr.reAnalyzeAll(None)
                mgr.startAnalysis(TaskMonitor.DUMMY)
            finally:
                program.endTransaction(tx, True)
            project.save(program)
            print('analysis complete; %d functions' %
                  program.getFunctionManager().getFunctionCount())
        program.release(consumer)
    finally:
        # We created the program rather than opening it through the project,
        # so the project is not one of its consumers and close() would fail
        # trying to release it. The save has already happened by here.
        try:  # noqa: SIM105
            project.close()
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
