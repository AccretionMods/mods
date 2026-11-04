#include "AMTableHook.h"

#include <mach-o/dyld.h>
#include <mach/mach.h>
#include <os/log.h>
#include <stdio.h>
#include <string.h>

static AMTableHookSpec *g_hooks;
static size_t g_count;
static uint64_t g_registrationRva;
static bool g_patched;

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

static void patchIfReady(void) {
    if (g_patched || !g_hooks || !g_count) return;
    const struct mach_header_64 *image = unityImage();
    if (!image) return;

    uintptr_t registration = (uintptr_t)image + (uintptr_t)g_registrationRva;
    uint64_t moduleCount = *(uint64_t *)(registration + 120);
    void **modules = *(void ***)(registration + 128);
    char line[128];
    snprintf(line, sizeof line, "table: modules=%llu", (unsigned long long)moduleCount);
    logLine(line);
    if (!modules || moduleCount == 0) return;

    // Make each touched module's methodPointers writable once.
    for (size_t i = 0; i < g_count; i++) {
        AMTableHookSpec *hook = &g_hooks[i];
        if (hook->module >= moduleCount) {
            snprintf(line, sizeof line, "table: %s module %u out of range", hook->name, hook->module);
            logLine(line);
            continue;
        }
        uint64_t *module = (uint64_t *)modules[hook->module];
        if (!module) continue;
        uint64_t count = module[1];
        void **pointers = (void **)module[2];
        if (hook->index >= count) {
            snprintf(line, sizeof line, "table: %s index %u >= %llu", hook->name, hook->index,
                     (unsigned long long)count);
            logLine(line);
            continue;
        }
        kern_return_t kr = vm_protect(mach_task_self(), (vm_address_t)pointers,
                                      (vm_size_t)(count * sizeof(void *)), false,
                                      VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
        if (kr != KERN_SUCCESS) {
            snprintf(line, sizeof line, "table: %s make writable failed", hook->name);
            logLine(line);
            continue;
        }
        if (hook->original) *hook->original = pointers[hook->index];
        if (hook->replacement) {
            pointers[hook->index] = hook->replacement;
            snprintf(line, sizeof line, "table: patched %s (module %u index %u)", hook->name, hook->module,
                     hook->index);
            logLine(line);
        }
    }
    g_patched = true;
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

void AMTableHookInstall(AMTableHookSpec *hooks, size_t count, uint64_t registrationRva) {
    g_hooks = hooks;
    g_count = count;
    g_registrationRva = registrationRva;
    patchIfReady();
}
