"""Synthetic 64-bit Mach-O images for tests: a dyld export trie, an LC_SYMTAB and, when asked, an
LC_ENCRYPTION_INFO_64, laid out like a real framework (header segment at 0) or executable (__PAGEZERO,
__TEXT at 0x100000000). Names are spelled as Darwin stores them, with the leading underscore."""

import struct

MH_MAGIC_64 = 0xFEEDFACF
MH_EXECUTE = 2
MH_DYLIB = 6
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x02
LC_ENCRYPTION_INFO_64 = 0x2C
LC_DYLD_EXPORTS_TRIE = 0x80000033
N_SECT = 0x0E
N_EXT = 0x01
N_UNDF = 0x00
ARM64 = 0x0100000C
X86_64 = 0x01000007

SYMOFF, STROFF, STRSIZE, TRIEOFF = 0x4100, 0x4200, 0x200, 0x4400
IMAGE_SIZE = 0x8000


def uleb(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def export_trie(names, address=0x4000):
    """A trie with one root and one child per name (edges may share prefixes; readers match edge by edge)."""
    names = list(names)
    children = []
    for name in names:
        payload = uleb(0) + uleb(address)  # flags, address
        children.append(uleb(len(payload)) + payload + b"\x00")  # terminal, no children
    # The root's size depends on the ULEB offsets of its children: iterate until stable.
    offsets = [0] * len(names)
    for _ in range(8):
        root = b"\x00" + bytes([len(names)])
        for name, off in zip(names, offsets):
            root += name.encode() + b"\x00" + uleb(off)
        new = []
        at = len(root)
        for child in children:
            new.append(at)
            at += len(child)
        if new == offsets:
            return root + b"".join(children)
        offsets = new
    raise AssertionError("export trie did not settle")


def segment(name, vmaddr, vmsize, fileoff, filesize):
    return struct.pack("<II16sQQQQIIII", LC_SEGMENT_64, 72, name, vmaddr, vmsize, fileoff, filesize, 7, 7, 0, 0)


def thin_image(*, exported=(), defined=(), undefined=(), cputype=ARM64, executable=False, cryptid=None):
    """One image. `exported` names are in the export trie and the symbol table; `defined` only in the
    symbol table (local); `undefined` are imports. `cryptid` adds LC_ENCRYPTION_INFO_64 with that value."""
    text_vmaddr = 0x100000000 if executable else 0
    strings = bytearray(b"\x00")
    nlists = []

    def add(name, ntype, value):
        strx = len(strings)
        strings.extend(name.encode() + b"\x00")
        nlists.append(struct.pack("<IBBHQ", strx, ntype, 1 if ntype & N_SECT else 0, 0, value))

    for name in exported:
        add(name, N_SECT | N_EXT, text_vmaddr + 0x4000)
    for name in defined:
        add(name, N_SECT, text_vmaddr + 0x4100)
    for name in undefined:
        add(name, N_UNDF | N_EXT, 0)
    assert len(strings) <= STRSIZE
    trie = export_trie(exported) if exported else b""

    cmds = bytearray()
    if executable:
        cmds += segment(b"__PAGEZERO", 0, 0x100000000, 0, 0)
    cmds += segment(b"__TEXT", text_vmaddr, 0x4000, 0, 0x4000)
    cmds += segment(b"__LINKEDIT", text_vmaddr + 0x4000, 0x4000, 0x4000, 0x4000)
    cmds += struct.pack("<IIIIII", LC_SYMTAB, 24, SYMOFF, len(nlists), STROFF, STRSIZE)
    if trie:
        cmds += struct.pack("<IIII", LC_DYLD_EXPORTS_TRIE, 16, TRIEOFF, len(trie))
    if cryptid is not None:
        cmds += struct.pack("<IIIIII", LC_ENCRYPTION_INFO_64, 24, 0x4000, 0x1000, cryptid, 0)
    ncmds = 3 + bool(executable) + bool(trie) + (cryptid is not None)
    header = struct.pack("<IiiIIIII", MH_MAGIC_64, cputype, 0, MH_EXECUTE if executable else MH_DYLIB,
                         ncmds, len(cmds), 0, 0)

    data = bytearray(IMAGE_SIZE)
    data[0:len(header)] = header
    data[len(header):len(header) + len(cmds)] = cmds
    data[SYMOFF:SYMOFF + 16 * len(nlists)] = b"".join(nlists)
    data[STROFF:STROFF + len(strings)] = strings
    data[TRIEOFF:TRIEOFF + len(trie)] = trie
    return bytes(data)


def fat_image(slices):
    """A fat binary from (cputype, thin_bytes) pairs, each slice page-aligned."""
    align = 0x1000
    offsets, at = [], align
    for _cpu, body in slices:
        offsets.append(at)
        at += ((len(body) + align - 1) // align) * align
    out = bytearray(at)
    struct.pack_into(">II", out, 0, 0xCAFEBABE, len(slices))
    for i, ((cpu, body), off) in enumerate(zip(slices, offsets)):
        struct.pack_into(">IIIII", out, 8 + 20 * i, cpu, 0, off, len(body), 12)
        out[off:off + len(body)] = body
    return bytes(out)
