#import "AMIl2cpp.h"

#import <dlfcn.h>

#include "am_il2cpp.h"

// am_hooks_data.c, written by scripts/embed_hooks.py from the mod's hooks.json
extern const AMHookSpec am_hook_specs[];
extern const size_t am_hook_spec_count;

static AMHookReplacement replacements[64];
static size_t replacement_count = 0;
static BOOL started = NO;

static void logLine(const char *message) {
    NSLog(@"[AccretionMods] %s", message);
}

// Cydia Substrate (cyan injects it); looked up so a missing one skips the hooks instead of failing to load.
static bool substrateHook(void *target, void *replacement, void **original) {
    typedef void (*MSHookFunctionFn)(void *, void *, void **);
    static MSHookFunctionFn hook;
    if (!hook) hook = (MSHookFunctionFn)dlsym(RTLD_DEFAULT, "MSHookFunction");
    if (!hook) return false;
    hook(target, replacement, original);
    return true;
}

void AMIl2cppRegister(const char *name, void *replacement, void **original) {
    if (replacement_count >= sizeof replacements / sizeof replacements[0]) {
        logLine("too many replacements registered; extra ones ignored");
        return;
    }
    replacements[replacement_count++] = (AMHookReplacement){name, replacement, original};
}

static void attempt(int triesLeft) {
    AMIl2cppApi api;
    if (am_il2cpp_api_load(&api) && am_il2cpp_ready(&api)) {
        AMHookReport r = am_il2cpp_install(&api, am_hook_specs, am_hook_spec_count,
                                           replacements, replacement_count, substrateHook, logLine);
        NSLog(@"[AccretionMods] hooks: %d installed, %d missing, %d without replacement, %d failed",
              r.hooked, r.missing, r.unregistered, r.failed);
        return;
    }
    if (triesLeft <= 0) {
        logLine("Unity runtime not found; no hooks installed");
        return;
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{ attempt(triesLeft - 1); });
}

void AMIl2cppStart(void) {
    if (started) return;
    started = YES;
    dispatch_async(dispatch_get_main_queue(), ^{ attempt(120); });
}
