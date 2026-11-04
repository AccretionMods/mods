// Host fixture for AMMachSymbols.c: a 64-bit image mapped at its preferred
// vmaddrs, with a __LINKEDIT symbol table holding one exported, one plain and
// one undefined symbol.
#include "AMMachSymbols.h"

#include <stdio.h>
#include <string.h>

static union { uint64_t align; unsigned char bytes[0x8000]; } storage;
static int failures;

static void check(const char *what, void *got, void *want) {
    if (got == want) {
        printf("ok %s\n", what);
    } else {
        printf("FAIL %s: got %p want %p\n", what, got, want);
        failures++;
    }
}

static AMMachHeader64 *fixture(size_t *header_bytes) {
    unsigned char *b = storage.bytes;
    memset(b, 0, sizeof storage.bytes);
    AMMachHeader64 *h = (void *)b;
    h->magic = AM_MACH_MH_MAGIC_64;
    h->ncmds = 3;
    h->sizeofcmds = sizeof(AMMachSegment64) * 2 + sizeof(AMMachSymtab);

    unsigned char *at = b + sizeof *h;
    AMMachSegment64 *text = (void *)at;
    *text = (AMMachSegment64){.cmd = AM_MACH_LC_SEGMENT_64, .cmdsize = sizeof *text,
        .vmaddr = 0, .vmsize = 0x4000, .fileoff = 0, .filesize = 0x4000};
    memcpy(text->segname, "__TEXT", 7);
    AMMachSegment64 *link = (void *)(at + sizeof *text);
    *link = (AMMachSegment64){.cmd = AM_MACH_LC_SEGMENT_64, .cmdsize = sizeof *link,
        .vmaddr = 0x4000, .vmsize = 0x4000, .fileoff = 0x4000, .filesize = 0x4000};
    memcpy(link->segname, "__LINKEDIT", 11);
    AMMachSymtab *sym = (void *)(at + sizeof *text + sizeof *link);
    *sym = (AMMachSymtab){.cmd = AM_MACH_LC_SYMTAB, .cmdsize = sizeof *sym,
        .symoff = 0x4100, .nsyms = 3, .stroff = 0x4200, .strsize = 0x100};

    char *table = (char *)b + 0x4200;
    size_t used = 1;  // byte 0 is the empty string
    table[0] = 0;
    uint32_t x_domain = (uint32_t)used;
    memcpy(table + used, "_il2cpp_domain_get", 19);  // 18 chars + NUL
    used += 19;
    uint32_t x_other = (uint32_t)used;
    memcpy(table + used, "_il2cpp_other", 14);  // 13 chars + NUL
    used += 14;
    uint32_t x_undefined = (uint32_t)used;
    memcpy(table + used, "_il2cpp_undefined", 18);  // 17 chars + NUL

    AMMachNlist64 *nl = (void *)(b + 0x4100);
    nl[0] = (AMMachNlist64){.strx = x_domain, .type = 0x0f, .value = 0x4000};  // N_SECT | N_EXT
    nl[1] = (AMMachNlist64){.strx = x_other, .type = 0x0f, .value = 0x5000};
    nl[2] = (AMMachNlist64){.strx = x_undefined, .type = 0x01, .value = 0};  // N_UNDF | N_EXT

    *header_bytes = sizeof *h + h->sizeofcmds;
    return h;
}

int main(void) {
    size_t header_bytes = 0;
    AMMachHeader64 *h = fixture(&header_bytes);

    check("plain name", ammach_lookup_symbol(h, header_bytes, "il2cpp_domain_get"), storage.bytes + 0x4000);
    check("leading underscore input", ammach_lookup_symbol(h, header_bytes, "_il2cpp_other"),
          storage.bytes + 0x5000);
    check("plain name for second symbol", ammach_lookup_symbol(h, header_bytes, "il2cpp_other"),
          storage.bytes + 0x5000);
    check("undefined is skipped", ammach_lookup_symbol(h, header_bytes, "il2cpp_undefined"), NULL);
    check("absent", ammach_lookup_symbol(h, header_bytes, "il2cpp_absent"), NULL);
    check("truncated header", ammach_lookup_symbol(h, 8, "il2cpp_domain_get"), NULL);

    AMMachHeader64 bad = *h;
    bad.magic = 0;
    check("bad magic", ammach_lookup_symbol(&bad, header_bytes, "il2cpp_domain_get"), NULL);

    printf("failures=%d\n", failures);
    return failures ? 1 : 0;
}
