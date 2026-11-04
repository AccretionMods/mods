// Hides substrate-like images and jailbreak artifacts from a game's own detection routines (the PAG
// crash SDK scans loaded images for Substrate/MobileSubstrate/TweakInject/CydiaSubstrate/libsubstrate;
// Unity Ads checks /private/jailbreak.txt). Two rebind paths:
//   * classic images: rewrite __got / __la_symbol_ptr through the indirect symbol table;
//   * chained-fixup images (UnityFramework, most modern dylibs): walk LC_DYLD_CHAINED_FIXUPS binds and
//     rewrite the slot of each matching import.
// Both paths only touch resolved import slots and validate every index before reading or writing.
#include <dlfcn.h>
#include <errno.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <mach/mach.h>
#include <os/log.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

static void am_log(const char *message) {
    os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", message);
}

static const char *(*am_orig_image_name)(uint32_t);
static void *(*am_orig_dlopen)(const char *, int);
static void *(*am_orig_dlsym)(void *, const char *);
static int (*am_orig_stat)(const char *, struct stat *);
static int (*am_orig_access)(const char *, int);
static FILE *(*am_orig_fopen)(const char *, const char *);

static bool am_hidden_image(const char *name) {
    static const char *const words[] = {"substrate", "Substrate", "TweakInject", "CydiaSubstrate",
                                        "MobileSubstrate", "SubstrateLoader", "ellekit", "ElleKit"};
    if (!name) return false;
    for (size_t i = 0; i < sizeof words / sizeof *words; i++)
        if (strstr(name, words[i])) return true;
    return false;
}

static bool am_jailbreak_path(const char *path) {
    if (!path) return false;
    return strcmp(path, "/private/jailbreak.txt") == 0 || strstr(path, "Cydia.app") ||
           strcmp(path, "/bin/bash") == 0 || strcmp(path, "/usr/sbin/sshd") == 0 ||
           strstr(path, "libsubstrate") || strstr(path, "MobileSubstrate") || strstr(path, "TweakInject");
}

static const char *am_rep_image_name(uint32_t index) {
    const char *name = am_orig_image_name ? am_orig_image_name(index) : _dyld_get_image_name(index);
    return am_hidden_image(name) ? "/usr/lib/libSystem.B.dylib" : name;
}

static void *am_rep_dlopen(const char *path, int mode) {
    if (am_hidden_image(path)) {
        errno = ENOENT;
        return NULL;
    }
    return am_orig_dlopen(path, mode);
}

static void *am_rep_dlsym(void *handle, const char *name) {
    if (name && (strcmp(name, "MSHookFunction") == 0 || strcmp(name, "MSHookMessageEx") == 0)) return NULL;
    return am_orig_dlsym(handle, name);
}

static int am_rep_stat(const char *path, struct stat *info) {
    if (am_jailbreak_path(path)) {
        errno = ENOENT;
        return -1;
    }
    return am_orig_stat(path, info);
}

static int am_rep_access(const char *path, int mode) {
    if (am_jailbreak_path(path)) {
        errno = ENOENT;
        return -1;
    }
    return am_orig_access(path, mode);
}

static FILE *am_rep_fopen(const char *path, const char *mode) {
    if (am_jailbreak_path(path)) {
        errno = ENOENT;
        return NULL;
    }
    return am_orig_fopen(path, mode);
}

static bool am_slot_readable(const void *slot) {
    vm_address_t address = (vm_address_t)slot;
    vm_address_t region = address & ~((vm_address_t)vm_page_size - 1);
    vm_size_t region_size = 0;
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    kern_return_t kr = vm_region_64(mach_task_self(), &region, &region_size, VM_REGION_BASIC_INFO_64,
                                    (vm_region_info_t)&info, &count, &object);
    if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
    if (kr != KERN_SUCCESS) return false;
    return address >= region && address + sizeof(void *) <= region + region_size && (info.protection & VM_PROT_READ);
}

static bool am_range_readable(const void *start, size_t size) {
    if (!start || size == 0) return false;
    const char *cursor = (const char *)start;
    const char *end = cursor + size;
    while (cursor < end) {
        if (!am_slot_readable(cursor)) return false;
        cursor += vm_page_size;
    }
    return true;
}

static bool am_write_slot(void *slot, void *value) {
    if (!slot || !value) return false;
    if (!am_slot_readable(slot)) {
        am_log("bypass: write refused (unmapped slot)");
        return false;
    }
    vm_address_t address = (vm_address_t)slot;
    vm_address_t page = address & ~((vm_address_t)vm_page_size - 1);
    kern_return_t kr = vm_protect(mach_task_self(), page, vm_page_size, false,
                                  VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
    if (kr != KERN_SUCCESS)
        kr = vm_protect(mach_task_self(), page, vm_page_size, false, VM_PROT_READ | VM_PROT_WRITE);
    if (kr != KERN_SUCCESS) {
        am_log("bypass: write refused (protect)");
        return false;
    }
    uintptr_t raw = (uintptr_t)value;
    kr = vm_write(mach_task_self(), address, (vm_offset_t)&raw, (mach_msg_type_number_t)sizeof raw);
    if (kr != KERN_SUCCESS) {
        am_log("bypass: write refused (vm_write)");
        return false;
    }
    return true;
}

static bool am_rebind_symbol(const char *name, void **slot) {
    if (!name || !slot) return false;
    while (name[0] == '_') name++;
    if (strcmp(name, "dyld_get_image_name") == 0) return am_write_slot(slot, (void *)am_rep_image_name);
    if (strcmp(name, "dlopen") == 0) return am_write_slot(slot, (void *)am_rep_dlopen);
    if (strcmp(name, "dlsym") == 0) return am_write_slot(slot, (void *)am_rep_dlsym);
    if (strcmp(name, "stat") == 0) return am_write_slot(slot, (void *)am_rep_stat);
    if (strcmp(name, "access") == 0) return am_write_slot(slot, (void *)am_rep_access);
    if (strcmp(name, "fopen") == 0) return am_write_slot(slot, (void *)am_rep_fopen);
    return false;
}

// MARK: chained fixups

struct am_chained_header {
    uint32_t version, starts_offset, imports_offset, symbols_offset, imports_count, imports_format, symbols_format;
};
struct am_starts_image {
    uint32_t seg_count;
    uint32_t seg_info[1];
};
struct am_starts_segment {
    uint32_t size;
    uint16_t page_size, pointer_format;
    uint64_t segment_offset;
    uint32_t max_valid_pointer;
    uint16_t page_count;
    uint16_t page_start[1];
};
struct am_import {
    uint32_t lib_ordinal : 8, weak_import : 1, name_offset : 23;
};
struct am_import_addend {
    uint32_t lib_ordinal : 8, weak_import : 1, name_offset : 23;
    uint32_t addend;
};
struct am_import_addend64 {
    uint64_t lib_ordinal : 16, weak_import : 1, reserved : 15, name_offset : 32;
    uint64_t addend;
};

static const char *am_chained_import_name(const struct am_chained_header *header, const unsigned char *payload,
                                          uint32_t payload_size, uint32_t ordinal) {
    if (ordinal >= header->imports_count) return NULL;
    if (header->imports_offset >= payload_size || header->symbols_offset >= payload_size) return NULL;
    size_t item_size = 0;
    if (header->imports_format == 1) item_size = sizeof(struct am_import);
    else if (header->imports_format == 2) item_size = sizeof(struct am_import_addend);
    else if (header->imports_format == 3) item_size = sizeof(struct am_import_addend64);
    if (!item_size) return NULL;
    size_t item_offset = (size_t)header->imports_offset + (size_t)ordinal * item_size;
    if (item_offset + item_size > payload_size) return NULL;
    const unsigned char *item = payload + item_offset;
    uint32_t name_offset;
    if (header->imports_format == 1) name_offset = ((const struct am_import *)item)->name_offset;
    else if (header->imports_format == 2) name_offset = ((const struct am_import_addend *)item)->name_offset;
    else name_offset = (uint32_t)((const struct am_import_addend64 *)item)->name_offset;
    size_t name_start = (size_t)header->symbols_offset + name_offset;
    if (name_start >= payload_size) return NULL;
    const char *name = (const char *)(payload + name_start);
    size_t limit = payload_size - name_start;
    if (limit > 256) limit = 256;
    if (strnlen(name, limit) == limit) return NULL;  // no NUL inside the payload: refuse to read past it
    return name;
}

static void am_rebind_chained(const unsigned char *payload, uint32_t payload_size, uintptr_t image,
                              const struct segment_command_64 *const *segments, size_t segment_count) {
    if (payload_size < sizeof(struct am_chained_header)) return;
    const struct am_chained_header *header = (const void *)payload;
    if (header->version != 0 || header->starts_offset == 0 || header->imports_count == 0) return;
    if ((size_t)header->starts_offset + sizeof(struct am_starts_image) > payload_size) return;
    const struct am_starts_image *starts = (const void *)(payload + header->starts_offset);
    if (starts->seg_count > segment_count) return;
    if ((size_t)header->starts_offset + 4 + (size_t)starts->seg_count * 4 > payload_size) return;
    int binds = 0, writes = 0;
    for (uint32_t s = 0; s < starts->seg_count; s++) {
        uint32_t info_offset = starts->seg_info[s];
        if (info_offset < 4) continue;
        size_t info_at = (size_t)header->starts_offset + info_offset;
        if (info_at + sizeof(struct am_starts_segment) > payload_size) continue;
        const struct am_starts_segment *segment = (const void *)(payload + info_at);
        if (segment->size < sizeof *segment || info_at + segment->size > payload_size) continue;
        if (segment->pointer_format != 2 && segment->pointer_format != 6) continue;  // PTR_64 / PTR_64_OFFSET
        if (segment->page_size != 4096 && segment->page_size != 16384) continue;
        if ((size_t)segment->size < sizeof *segment + (size_t)segment->page_count * 2) continue;
        // Import chains live in writable data; never touch a chain in __TEXT (writing there faults).
        const char *segname = segments[s]->segname;
        if (strcmp(segname, "__DATA") != 0 && strcmp(segname, "__DATA_CONST") != 0 &&
            strcmp(segname, "__DATA_DIRTY") != 0)
            continue;
        uintptr_t segment_base = image + segments[s]->vmaddr;
        uintptr_t segment_end = segment_base + segments[s]->vmsize;
        for (uint16_t page = 0; page < segment->page_count; page++) {
            uint16_t start = segment->page_start[page];
            if (start == 0xFFFF || (start & 0x8000)) continue;  // none / multi-chain page
            unsigned char *slot = (unsigned char *)segment_base + (size_t)page * segment->page_size + start;
            for (int guard = 0; guard < 100000; guard++) {
                if ((uintptr_t)slot < segment_base || (uintptr_t)slot + 8 > segment_end) break;
                if (!am_slot_readable(slot)) break;
                uint64_t value = *(const uint64_t *)slot;
                uint32_t next = (value >> 51) & 0xFFF;
                if ((value >> 63) & 1) {
                    binds++;
                    const char *name = am_chained_import_name(header, payload, payload_size, (uint32_t)(value & 0xFFFFFF));
                    if (name && am_rebind_symbol(name, (void **)slot)) writes++;
                }
                if (next == 0) break;
                slot += (size_t)next * 4;
            }
        }
    }
    if (binds) {
        char line[96];
        snprintf(line, sizeof line, "bypass: chained binds=%d rewrites=%d", binds, writes);
        am_log(line);
    }
}

// MARK: classic fixups

static void am_rebind_classic(const struct mach_header_64 *header, intptr_t slide,
                              const struct symtab_command *symtab, const struct dysymtab_command *dysymtab,
                              const struct segment_command_64 *linkedit, const struct section_64 *const *sections,
                              size_t section_count) {
    if (!symtab || !dysymtab || !linkedit || !section_count) return;
    if (dysymtab->nindirectsyms == 0 || symtab->nsyms == 0 || symtab->strsize == 0) return;
    if (symtab->symoff < linkedit->fileoff || symtab->stroff < linkedit->fileoff ||
        dysymtab->indirectsymoff < linkedit->fileoff)
        return;
    if (symtab->symoff - linkedit->fileoff + (uint64_t)symtab->nsyms * sizeof(struct nlist_64) > linkedit->filesize ||
        symtab->stroff - linkedit->fileoff + symtab->strsize > linkedit->filesize ||
        dysymtab->indirectsymoff - linkedit->fileoff + (uint64_t)dysymtab->nindirectsyms * sizeof(uint32_t) >
            linkedit->filesize)
        return;
    uintptr_t image = (uintptr_t)header + (uintptr_t)slide;
    uintptr_t link = image + linkedit->vmaddr;
    const struct nlist_64 *symbols = (const void *)(link + (symtab->symoff - linkedit->fileoff));
    const char *strings = (const char *)(link + (symtab->stroff - linkedit->fileoff));
    const uint32_t *indirect = (const uint32_t *)(link + (dysymtab->indirectsymoff - linkedit->fileoff));
    int writes = 0;
    for (size_t s = 0; s < section_count; s++) {
        const struct section_64 *section = sections[s];
        if (section->reserved1 >= dysymtab->nindirectsyms || section->size == 0) continue;
        void **slots = (void **)(image + section->addr);
        if (!am_range_readable(slots, section->size)) continue;
        size_t count = section->size / sizeof(void *);
        size_t available = dysymtab->nindirectsyms - section->reserved1;
        if (count > available) count = available;
        for (size_t i = 0; i < count; i++) {
            uint32_t symbol_index = indirect[section->reserved1 + i];
            if (symbol_index == INDIRECT_SYMBOL_LOCAL || symbol_index == INDIRECT_SYMBOL_ABS ||
                symbol_index >= symtab->nsyms)
                continue;
            uint32_t strx = symbols[symbol_index].n_un.n_strx;
            if (strx >= symtab->strsize) continue;
            const char *name = strings + strx;
            size_t limit = symtab->strsize - strx;
            if (limit > 256) limit = 256;
            if (strnlen(name, limit) == limit) continue;
            if (am_rebind_symbol(name, &slots[i])) writes++;
        }
    }
    if (writes) {
        char line[96];
        snprintf(line, sizeof line, "bypass: classic rewrites=%d", writes);
        am_log(line);
    }
}

static void am_rebind_image(const struct mach_header_64 *header, intptr_t slide) {
    if (!header || header->magic != MH_MAGIC_64 || header->cputype != 0x100000c) return;
    const struct symtab_command *symtab = NULL;
    const struct dysymtab_command *dysymtab = NULL;
    const struct segment_command_64 *linkedit = NULL;
    const struct section_64 *sections[64];
    const struct segment_command_64 *segments[64];
    size_t section_count = 0, segment_count = 0;
    const struct linkedit_data_command *chained = NULL;
    const unsigned char *base = (const unsigned char *)header;
    size_t at = sizeof(struct mach_header_64);
    size_t end = at + header->sizeofcmds;
    for (uint32_t i = 0; i < header->ncmds && at < end; i++) {
        const struct load_command *command = (const void *)(base + at);
        if (command->cmdsize < 8 || at + command->cmdsize > end) break;
        if (command->cmd == LC_SYMTAB) symtab = (const void *)command;
        else if (command->cmd == LC_DYSYMTAB) dysymtab = (const void *)command;
        else if (command->cmd == 0x80000034u) chained = (const void *)command;
        else if (command->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *segment = (const void *)command;
            if (segment_count < 64) segments[segment_count++] = segment;
            if (strcmp(segment->segname, "__LINKEDIT") == 0) linkedit = segment;
            const struct section_64 *section = (const void *)(segment + 1);
            for (uint32_t s = 0; s < segment->nsects && section_count < 64; s++, section++) {
                uint32_t type = section->flags & SECTION_TYPE;
                if (type == S_NON_LAZY_SYMBOL_POINTERS || type == S_LAZY_SYMBOL_POINTERS)
                    sections[section_count++] = section;
            }
        }
        at += command->cmdsize;
    }
    if (!linkedit) return;
    if (chained) {
        uintptr_t image = (uintptr_t)header + (uintptr_t)slide;
        if (chained->dataoff < linkedit->fileoff || chained->datasize == 0 ||
            chained->dataoff - linkedit->fileoff > linkedit->filesize ||
            chained->datasize > linkedit->filesize - (chained->dataoff - linkedit->fileoff))
            return;
        const unsigned char *payload = (const unsigned char *)(image + linkedit->vmaddr + (chained->dataoff - linkedit->fileoff));
        am_log("bypass: chained image");
        am_rebind_chained(payload, (uint32_t)chained->datasize, image, segments, segment_count);
        return;
    }
    am_rebind_classic(header, slide, symtab, dysymtab, linkedit, sections, section_count);
}

static void am_image_added(const struct mach_header *header, intptr_t slide) {
    const char *name = NULL;
    uint32_t count = _dyld_image_count();
    for (uint32_t i = 0; i < count; i++)
        if (_dyld_get_image_header(i) == header) { name = _dyld_get_image_name(i); break; }
    if (!name || strncmp(name, "/System/", 8) == 0 || strncmp(name, "/usr/lib/", 9) == 0) return;
    Dl_info self;
    if (dladdr((void *)am_rebind_image, &self) && self.dli_fbase == header) return;  // never rewrite our own imports
    char line[256];
    snprintf(line, sizeof line, "bypass: image %s", name);
    am_log(line);
    am_rebind_image((const struct mach_header_64 *)header, slide);
}

__attribute__((constructor)) static void am_detect_bypass_install(void) {
    char line[64];
    snprintf(line, sizeof line, "bypass: install images=%u", (unsigned)_dyld_image_count());
    am_log(line);
    am_orig_image_name = dlsym(RTLD_DEFAULT, "_dyld_get_image_name");
    am_orig_dlopen = dlsym(RTLD_DEFAULT, "dlopen");
    am_orig_dlsym = dlsym(RTLD_DEFAULT, "dlsym");
    am_orig_stat = dlsym(RTLD_DEFAULT, "stat");
    am_orig_access = dlsym(RTLD_DEFAULT, "access");
    am_orig_fopen = dlsym(RTLD_DEFAULT, "fopen");
    if (!am_orig_image_name || !am_orig_dlopen || !am_orig_dlsym || !am_orig_stat || !am_orig_access || !am_orig_fopen)
        return;
    _dyld_register_func_for_add_image(am_image_added);
    for (uint32_t i = 0; i < _dyld_image_count(); i++)
        am_image_added(_dyld_get_image_header(i), _dyld_get_image_vmaddr_slide(i));
}
