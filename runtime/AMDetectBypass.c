// Hides substrate-like images and jailbreak artifacts from a game's own detection routines (the PAG
// crash SDK scans loaded images for Substrate/MobileSubstrate/TweakInject/CydiaSubstrate/libsubstrate;
// Unity Ads checks /private/jailbreak.txt). The rebind is a runtime GOT rewrite, so it also works for
// images built with chained fixups (it rewrites the resolved slots, not the file encodings).
#include <dlfcn.h>
#include <errno.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <mach/mach.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

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

// One import-slot rebind for a loaded image: walks __got / __la_symbol_ptr and rewrites the slot for
// each named symbol. Safe on chained-fixup images because dyld has already resolved the slots.
static bool am_write_slot(void *slot, void *value) {
    vm_address_t page = (vm_address_t)slot & ~(vm_page_size - 1);
    if (vm_protect(mach_task_self(), page, vm_page_size, false, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY) !=
            KERN_SUCCESS &&
        vm_protect(mach_task_self(), page, vm_page_size, false, VM_PROT_READ | VM_PROT_WRITE) != KERN_SUCCESS)
        return false;
    *(void **)slot = value;
    return *(void **)slot == value;
}

static void am_rebind_image(const struct mach_header_64 *header, intptr_t slide) {
    if (!header || header->magic != MH_MAGIC_64 || header->cputype != 0x100000c) return;
    const struct symtab_command *symtab = NULL;
    const struct dysymtab_command *dysymtab = NULL;
    const struct segment_command_64 *linkedit = NULL;
    const struct section_64 *sections[64];
    size_t section_count = 0;
    bool chained = false;
    const unsigned char *base = (const unsigned char *)header;
    uintptr_t image = (uintptr_t)header + (uintptr_t)slide;
    size_t at = sizeof(struct mach_header_64);
    size_t end = at + header->sizeofcmds;
    for (uint32_t i = 0; i < header->ncmds && at < end; i++) {
        const struct load_command *command = (const void *)(base + at);
        if (command->cmdsize < 8 || at + command->cmdsize > end) break;
        if (command->cmd == LC_SYMTAB) symtab = (const void *)command;
        else if (command->cmd == LC_DYSYMTAB) dysymtab = (const void *)command;
        else if (command->cmd == 0x80000034u) chained = true;  // LC_DYLD_CHAINED_FIXUPS: no classic indirect table
        else if (command->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *segment = (const void *)command;
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
    if (!symtab || !dysymtab || !linkedit || !section_count) return;
    if (chained || dysymtab->nindirectsyms == 0 || symtab->nsyms == 0 || symtab->strsize == 0) return;
    if (symtab->symoff < linkedit->fileoff || symtab->stroff < linkedit->fileoff ||
        dysymtab->indirectsymoff < linkedit->fileoff)
        return;
    uintptr_t link = image + linkedit->vmaddr;
    const struct nlist_64 *symbols = (const void *)(link + (symtab->symoff - linkedit->fileoff));
    const char *strings = (const char *)(link + (symtab->stroff - linkedit->fileoff));
    const uint32_t *indirect = (const uint32_t *)(link + (dysymtab->indirectsymoff - linkedit->fileoff));
    for (size_t s = 0; s < section_count; s++) {
        const struct section_64 *section = sections[s];
        if (section->reserved1 >= dysymtab->nindirectsyms) continue;
        void **slots = (void **)(image + section->addr);
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
            if (name[0] == '_') name++;
            if (strcmp(name, "dyld_get_image_name") == 0) am_write_slot(&slots[i], (void *)am_rep_image_name);
            else if (strcmp(name, "dlopen") == 0) am_write_slot(&slots[i], (void *)am_rep_dlopen);
            else if (strcmp(name, "dlsym") == 0) am_write_slot(&slots[i], (void *)am_rep_dlsym);
            else if (strcmp(name, "stat") == 0) am_write_slot(&slots[i], (void *)am_rep_stat);
            else if (strcmp(name, "access") == 0) am_write_slot(&slots[i], (void *)am_rep_access);
            else if (strcmp(name, "fopen") == 0) am_write_slot(&slots[i], (void *)am_rep_fopen);
        }
    }
}

static void am_image_added(const struct mach_header *header, intptr_t slide) {
    const char *name = NULL;
    uint32_t count = _dyld_image_count();
    for (uint32_t i = 0; i < count; i++)
        if (_dyld_get_image_header(i) == header) { name = _dyld_get_image_name(i); break; }
    if (!name || strncmp(name, "/System/", 8) == 0 || strncmp(name, "/usr/lib/", 9) == 0) return;
    Dl_info self;
    if (dladdr((void *)am_rebind_image, &self) && self.dli_fbase == header) return;  // never rewrite our own imports
    am_rebind_image((const struct mach_header_64 *)header, slide);
}

__attribute__((constructor)) static void am_detect_bypass_install(void) {
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
