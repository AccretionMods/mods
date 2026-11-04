#include "AMTableHook.h"

#include <mach-o/dyld.h>
#include <mach/mach.h>
#include <os/log.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

static AMTableHookSpec *g_hooks;
static size_t g_count;
static uint64_t g_registrationRva;
static uint32_t g_modulesOffset;
static bool g_patched;

// Bounds a mod's table could never need; past them the registration RVA or offset is wrong for this build.
#define AM_TABLE_MAX_MODULES 4096
#define AM_TABLE_MAX_METHODS (1u << 24)

static void logLine(const char *msg) {
    os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", msg);
}

static const struct mach_header_64 *unityImage(void) {
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        if (name && strstr(name, "/UnityFramework.framework/"))
            return (const struct mach_header_64 *)_dyld_get_image_header(i);
    }
    return NULL;
}

// vm_read_overwrite reports unmapped memory as an error instead of crashing.
static bool readWord(uintptr_t address, uint64_t *out) {
    vm_size_t got = 0;
    kern_return_t kr = vm_read_overwrite(mach_task_self(), (vm_address_t)address, sizeof *out, (vm_address_t)out, &got);
    return kr == KERN_SUCCESS && got == sizeof *out;
}

static void patchIfReady(void) {
    if (g_patched || !g_hooks || !g_count) return;
    const struct mach_header_64 *image = unityImage();
    if (!image) return;
    g_patched = true;  // one pass per process: a failure below is logged, not retried on every image load

    uintptr_t registration = (uintptr_t)image + (uintptr_t)g_registrationRva;
    uint64_t moduleCount = 0, modulesWord = 0;
    char line[160];
    if (!readWord(registration + g_modulesOffset, &moduleCount) || !readWord(registration + g_modulesOffset + 8, &modulesWord)
        || moduleCount == 0 || moduleCount > AM_TABLE_MAX_MODULES || !modulesWord) {
        snprintf(line, sizeof line, "table: no codeGenModules at registration %#llx+%u (modules=%llu); wrong RVA or layout",
                 (unsigned long long)g_registrationRva, g_modulesOffset, (unsigned long long)moduleCount);
        logLine(line);
        return;
    }
    const void *const *modules = (const void *const *)(uintptr_t)modulesWord;
    snprintf(line, sizeof line, "table: modules=%llu", (unsigned long long)moduleCount);
    logLine(line);

    for (size_t i = 0; i < g_count; i++) {
        AMTableHookSpec *hook = &g_hooks[i];
        const char *name = hook->name ? hook->name : "?";
        if (hook->module >= moduleCount) {
            snprintf(line, sizeof line, "table: %s module %u out of range", name, hook->module);
            logLine(line);
            continue;
        }
        // Il2CppCodeGenModule: moduleName, methodPointerCount, methodPointers (stable since IL2CPP 24.2)
        uint64_t module = 0, count = 0, pointersWord = 0;
        if (!readWord((uintptr_t)&modules[hook->module], &module) || !module
            || !readWord((uintptr_t)module + 8, &count) || !readWord((uintptr_t)module + 16, &pointersWord)
            || count > AM_TABLE_MAX_METHODS || !pointersWord) {
            snprintf(line, sizeof line, "table: %s module %u unreadable", name, hook->module);
            logLine(line);
            continue;
        }
        void **pointers = (void **)(uintptr_t)pointersWord;
        if (hook->index >= count) {
            snprintf(line, sizeof line, "table: %s index %u >= %llu", name, hook->index, (unsigned long long)count);
            logLine(line);
            continue;
        }
        // methodPointers lives in __DATA_CONST: make its page a writable private copy before storing into it.
        kern_return_t kr = vm_protect(mach_task_self(), (vm_address_t)&pointers[hook->index], sizeof(void *), false,
                                      VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
        if (kr != KERN_SUCCESS) {
            snprintf(line, sizeof line, "table: %s make writable failed (%d)", name, kr);
            logLine(line);
            continue;
        }
        if (hook->original) *hook->original = pointers[hook->index];
        if (hook->replacement) {
            pointers[hook->index] = hook->replacement;
            snprintf(line, sizeof line, "table: patched %s (module %u index %u)", name, hook->module, hook->index);
            logLine(line);
        }
    }
}

static void onImage(const struct mach_header *mh, intptr_t slide) {
    (void)mh;
    (void)slide;
    patchIfReady();
}

__attribute__((constructor)) static void amTableHookInit(void) {
    _dyld_register_func_for_add_image(onImage);
    patchIfReady();
}

void AMTableHookInstall(AMTableHookSpec *hooks, size_t count, uint64_t registrationRva, uint32_t modulesOffset) {
    g_hooks = hooks;
    g_count = count;
    g_registrationRva = registrationRva;
    g_modulesOffset = modulesOffset;
    patchIfReady();
}
