#include "AMMachSymbols.h"
#include <string.h>

#define N_STAB 0xe0
#define N_TYPE 0x0e
#define N_UNDF 0x00

_Static_assert(sizeof(AMMachHeader64) == 32, "mach_header_64 wire size");
_Static_assert(sizeof(AMMachSegment64) == 72, "segment_command_64 wire size");
_Static_assert(sizeof(AMMachSymtab) == 24, "symtab_command wire size");
_Static_assert(sizeof(AMMachNlist64) == 16, "nlist_64 wire size");

static bool named(const AMMachSegment64 *seg, const char *name) {
    char text[17];
    memcpy(text, seg->segname, 16);
    text[16] = 0;
    return strncmp(text, name, 16) == 0;
}

void *ammach_lookup_symbol(const void *header, size_t header_bytes, const char *name) {
    if (!header || !name || !*name || header_bytes < sizeof(AMMachHeader64)) return NULL;
    const AMMachHeader64 *mh = header;
    if (mh->magic != AM_MACH_MH_MAGIC_64) return NULL;
    if (mh->sizeofcmds > header_bytes - sizeof *mh) return NULL;

    const uint8_t *base = header;
    const uint8_t *end = base + sizeof *mh + mh->sizeofcmds;
    const AMMachSegment64 *header_segment = NULL, *linkedit = NULL;
    const AMMachSymtab *symtab = NULL;
    const uint8_t *at = base + sizeof *mh;
    for (uint32_t i = 0; i < mh->ncmds; i++) {
        if (at + sizeof(AMMachLoadCommand) > end) return NULL;
        const AMMachLoadCommand *cmd = (const void *)at;
        if (cmd->cmdsize < sizeof *cmd || at + cmd->cmdsize > end) return NULL;
        if (cmd->cmd == AM_MACH_LC_SEGMENT_64 && cmd->cmdsize >= sizeof(AMMachSegment64)) {
            const AMMachSegment64 *seg = (const void *)cmd;
            // The segment that holds the header: file offset 0 with bytes behind it
            // (__PAGEZERO has offset 0 too but no file bytes and vmaddr 0).
            if (!header_segment && seg->fileoff == 0 && seg->filesize) header_segment = seg;
            if (named(seg, "__LINKEDIT")) linkedit = seg;
        } else if (cmd->cmd == AM_MACH_LC_SYMTAB && cmd->cmdsize >= sizeof(AMMachSymtab)) {
            symtab = (const void *)cmd;
        }
        at += cmd->cmdsize;
    }
    if (!linkedit || !symtab || !symtab->nsyms) return NULL;
    if (symtab->symoff < linkedit->fileoff || symtab->stroff < linkedit->fileoff) return NULL;

    uint64_t syms = symtab->symoff - linkedit->fileoff;
    uint64_t strs = symtab->stroff - linkedit->fileoff;
    if (syms + (uint64_t)symtab->nsyms * sizeof(AMMachNlist64) > linkedit->filesize) return NULL;
    if (strs + symtab->strsize > linkedit->filesize) return NULL;

    // The header's segment vmaddr is the base the other segments and the symbol values
    // are relative to (0 for a dylib/framework, 0x100000000 for an executable).
    uint64_t base_vmaddr = header_segment ? header_segment->vmaddr : 0;
    if (linkedit->vmaddr < base_vmaddr) return NULL;
    const uint8_t *link = base + (linkedit->vmaddr - base_vmaddr);
    const AMMachNlist64 *symbols = (const void *)(link + syms);
    const char *strings = (const char *)(link + strs);

    for (uint32_t i = 0; i < symtab->nsyms; i++) {
        const AMMachNlist64 *sym = (const void *)(symbols + i);
        if (sym->type & N_STAB) continue;
        if ((sym->type & N_TYPE) == N_UNDF) continue;  // imported, not defined here
        if (!sym->value || sym->strx >= symtab->strsize) continue;
        const char *candidate = strings + sym->strx;
        if (!memchr(candidate, 0, symtab->strsize - sym->strx)) continue;  // runs into the table's end
        if (strcmp(candidate, name) != 0 && !(candidate[0] == '_' && strcmp(candidate + 1, name) == 0))
            continue;
        if (sym->value < base_vmaddr) continue;
        return (void *)(uintptr_t)(base + (sym->value - base_vmaddr));
    }
    return NULL;
}
