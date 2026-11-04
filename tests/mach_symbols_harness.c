// Host fixture for AMMachSymbols.c: a 64-bit image laid out as dyld maps it, with a
// __LINKEDIT symbol table holding one exported, one plain and one undefined symbol.
// Two shapes: a framework (header segment at vmaddr 0) and an executable (__PAGEZERO,
// then __TEXT at 0x100000000), the two images the runtime may hand the reader.
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

enum { FRAMEWORK, EXECUTABLE };

static AMMachSegment64 segment(const char *name, uint64_t vmaddr, uint64_t vmsize, uint64_t fileoff,
                               uint64_t filesize) {
    AMMachSegment64 seg = {.cmd = AM_MACH_LC_SEGMENT_64, .cmdsize = sizeof seg, .vmaddr = vmaddr,
                           .vmsize = vmsize, .fileoff = fileoff, .filesize = filesize};
    strncpy(seg.segname, name, sizeof seg.segname);
    return seg;
}

// Writes the image into `storage`; returns its header and sets *symtab to its LC_SYMTAB
// (so a test can bend it) and *header_bytes to what the runtime would pass.
static AMMachHeader64 *fixture(int shape, size_t *header_bytes, AMMachSymtab **symtab) {
    unsigned char *b = storage.bytes;
    memset(b, 0, sizeof storage.bytes);
    uint64_t text_vmaddr = shape == EXECUTABLE ? 0x100000000ull : 0;

    AMMachHeader64 *h = (void *)b;
    h->magic = AM_MACH_MH_MAGIC_64;
    AMMachSegment64 *at = (void *)(b + sizeof *h);
    if (shape == EXECUTABLE) {
        *at++ = segment("__PAGEZERO", 0, 0x100000000ull, 0, 0);
        h->ncmds++;
    }
    *at++ = segment("__TEXT", text_vmaddr, 0x4000, 0, 0x4000);
    *at++ = segment("__LINKEDIT", text_vmaddr + 0x4000, 0x4000, 0x4000, 0x4000);
    h->ncmds += 2;
    AMMachSymtab *sym = (void *)at;
    *sym = (AMMachSymtab){.cmd = AM_MACH_LC_SYMTAB, .cmdsize = sizeof *sym,
        .symoff = 0x4100, .nsyms = 3, .stroff = 0x4200, .strsize = 0x100};
    h->ncmds++;
    h->sizeofcmds = (uint32_t)((unsigned char *)(sym + 1) - (b + sizeof *h));

    char *table = (char *)b + 0x4200;
    size_t used = 1;  // byte 0 is the empty string
    uint32_t x_domain = (uint32_t)used;
    memcpy(table + used, "_il2cpp_domain_get", 19);  // 18 chars + NUL
    used += 19;
    uint32_t x_other = (uint32_t)used;
    memcpy(table + used, "_il2cpp_other", 14);
    used += 14;
    uint32_t x_undefined = (uint32_t)used;
    memcpy(table + used, "_il2cpp_undefined", 18);

    AMMachNlist64 *nl = (void *)(b + 0x4100);
    nl[0] = (AMMachNlist64){.strx = x_domain, .type = 0x0f, .value = text_vmaddr + 0x4000};  // N_SECT | N_EXT
    nl[1] = (AMMachNlist64){.strx = x_other, .type = 0x0e, .value = text_vmaddr + 0x5000};   // N_SECT, local
    nl[2] = (AMMachNlist64){.strx = x_undefined, .type = 0x01, .value = 0};                  // N_UNDF | N_EXT

    *header_bytes = sizeof *h + h->sizeofcmds;
    *symtab = sym;
    return h;
}

static void lookups(const char *shape, AMMachHeader64 *h, size_t header_bytes) {
    char what[64];
#define CHECK(label, name, want) \
    (snprintf(what, sizeof what, "%s: %s", shape, label), check(what, ammach_lookup_symbol(h, header_bytes, name), want))
    CHECK("plain name", "il2cpp_domain_get", storage.bytes + 0x4000);
    CHECK("leading underscore input", "_il2cpp_other", storage.bytes + 0x5000);
    CHECK("local symbol by plain name", "il2cpp_other", storage.bytes + 0x5000);
    CHECK("undefined is skipped", "il2cpp_undefined", NULL);
    CHECK("absent", "il2cpp_absent", NULL);
    CHECK("empty name", "", NULL);
#undef CHECK
}

int main(void) {
    size_t header_bytes = 0;
    AMMachSymtab *symtab;
    AMMachHeader64 *h;

    h = fixture(FRAMEWORK, &header_bytes, &symtab);
    lookups("framework", h, header_bytes);
    check("framework: truncated header", ammach_lookup_symbol(h, 8, "il2cpp_domain_get"), NULL);

    AMMachHeader64 bad = *h;
    bad.magic = 0;
    check("framework: bad magic", ammach_lookup_symbol(&bad, header_bytes, "il2cpp_domain_get"), NULL);

    // Malformed tables answer NULL instead of reading outside __LINKEDIT.
    symtab->nsyms = 0x10000000;
    check("framework: symbol table past __LINKEDIT", ammach_lookup_symbol(h, header_bytes, "il2cpp_domain_get"), NULL);
    symtab->nsyms = 3;
    symtab->symoff = 0x3000;
    check("framework: symbol table before __LINKEDIT", ammach_lookup_symbol(h, header_bytes, "il2cpp_domain_get"), NULL);
    symtab->symoff = 0x4100;
    symtab->strsize = 0x4000;
    check("framework: string table past __LINKEDIT", ammach_lookup_symbol(h, header_bytes, "il2cpp_domain_get"), NULL);
    symtab->strsize = 0x100;
    ((AMMachNlist64 *)(storage.bytes + 0x4100))[0].strx = 0x200;  // name index past the string table
    check("framework: bad string index skips that symbol", ammach_lookup_symbol(h, header_bytes, "il2cpp_domain_get"), NULL);
    check("framework: other symbols still resolve", ammach_lookup_symbol(h, header_bytes, "il2cpp_other"),
          storage.bytes + 0x5000);

    h = fixture(EXECUTABLE, &header_bytes, &symtab);
    lookups("executable", h, header_bytes);

    printf("failures=%d\n", failures);
    return failures ? 1 : 0;
}
