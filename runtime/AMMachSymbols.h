// Mach-O symbol-table lookup: the runtime's fallback when dlsym has no export for
// an il2cpp_* name. A stripped build can keep its LC_SYMTAB even after the export
// trie is gone; this reads that table without touching code pages or scanning bytes.
//
// Fixed-width wire layouts (little-endian), shared by the parser and host fixtures,
// so this file compiles on the host without the Apple SDK (as AMImportBindings.h does).
#ifndef AM_MACH_SYMBOLS_H
#define AM_MACH_SYMBOLS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct { uint32_t magic, cpu, sub, filetype, ncmds, sizeofcmds, flags, reserved; } AMMachHeader64;
typedef struct { uint32_t cmd, cmdsize; } AMMachLoadCommand;
typedef struct { uint32_t cmd, cmdsize; char segname[16]; uint64_t vmaddr, vmsize, fileoff, filesize;
    uint32_t maxprot, initprot, nsects, flags; } AMMachSegment64;
typedef struct { uint32_t cmd, cmdsize, symoff, nsyms, stroff, strsize; } AMMachSymtab;
typedef struct { uint32_t strx; uint8_t type, sect; uint16_t desc; uint64_t value; } AMMachNlist64;

#define AM_MACH_MH_MAGIC_64 0xfeedfacf
#define AM_MACH_LC_SEGMENT_64 0x19
#define AM_MACH_LC_SYMTAB 0x02

// The runtime address of `name` from the image's LC_SYMTAB, or NULL when the name
// is absent, undefined in this image, or the table is malformed. The image must be
// mapped at its preferred vmaddrs. `name` may omit Darwin's leading underscore.
void *ammach_lookup_symbol(const void *header, size_t header_bytes, const char *name);

#endif
