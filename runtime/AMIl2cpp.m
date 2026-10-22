#import "AMIl2cpp.h"

#import <dlfcn.h>
#import <execinfo.h>
#import <mach/mach.h>
#import <os/log.h>
#import <unistd.h>

#include "am_il2cpp.h"

// am_hooks_data.c, written by scripts/embed_hooks.py from the mod's hooks.json
extern const AMHookSpec am_hook_specs[];
extern const size_t am_hook_spec_count;

static AMHookReplacement replacements[64];
static size_t replacement_count = 0;
static BOOL started = NO;
static NSMutableArray<NSString *> *lines;
static void (^reportHandler)(NSString *);

static void logLine(const char *message) {
    // %{public}s: NSLog shows dynamic strings as <private> on a phone without a debugger
    os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", message);
    if (!lines) lines = [NSMutableArray array];
    [lines addObject:@(message)];
}

static void finish(void) {
    if (reportHandler) reportHandler([lines componentsJoinedByString:@"\n"]);
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

// Code hooks need JIT: iOS kills an app that rewrites its own code unless a debugger has marked it
// (CS_DEBUGGED, set by StikDebug, Xcode or a jailbreak). Without it the runtime swaps pointers instead.
#define CS_OPS_STATUS 0
#define CS_DEBUGGED 0x10000000
int csops(pid_t pid, unsigned int ops, void *useraddr, size_t usersize);

static AMHookMode hookMode(void) {
    uint32_t flags = 0;
    if (csops(getpid(), CS_OPS_STATUS, &flags, sizeof flags) == 0 && (flags & CS_DEBUGGED)) return AM_HOOK_CODE;
    return AM_HOOK_POINTER;
}

// vm_read_overwrite reports unmapped memory as an error instead of crashing.
static bool readMemory(const void *address, void *out, size_t size) {
    vm_size_t got = 0;
    kern_return_t kr = vm_read_overwrite(mach_task_self(), (vm_address_t)address, size, (vm_address_t)out, &got);
    return kr == KERN_SUCCESS && got == size;
}

void AMIl2cppRegister(const char *name, void *replacement, void **original) {
    if (replacement_count >= sizeof replacements / sizeof replacements[0]) {
        logLine("too many replacements registered; extra ones ignored");
        return;
    }
    replacements[replacement_count++] = (AMHookReplacement){name, replacement, original};
}

void *AMIl2cppOriginalOf(void *pointer) {
    if (!pointer) return NULL;
    for (size_t i = 0; i < replacement_count; i++) {
        if (replacements[i].replacement == pointer && replacements[i].original) return *replacements[i].original;
    }
    return NULL;
}

// A game that quits on its own (exit(), Application.Quit) leaves no crash report. The device log then
// shows who called it: each frame as image+offset, which the game's dump maps to a method.
static void logExit(void) {
    void *frames[32];
    int n = backtrace(frames, 32);
    NSMutableString *line = [NSMutableString stringWithString:@"exit() called; frames:"];
    for (int i = 1; i < n; i++) {
        Dl_info info;
        if (dladdr(frames[i], &info) && info.dli_fname) {
            const char *slash = strrchr(info.dli_fname, '/');
            [line appendFormat:@" %s+0x%lx", slash ? slash + 1 : info.dli_fname,
                               (unsigned long)((char *)frames[i] - (char *)info.dli_fbase)];
        } else {
            [line appendFormat:@" %p", frames[i]];
        }
    }
    logLine(line.UTF8String);
}

static void attempt(int triesLeft) {
    AMIl2cppApi api;
    bool loaded = am_il2cpp_api_load(&api);
    if (triesLeft % 20 == 0) {
        char line[128];
        snprintf(line, sizeof line, "waiting for the Unity VM (%s, corlib %p, %d tries left)",
                 loaded ? "API found" : "no IL2CPP exports", loaded && api.get_corlib ? api.get_corlib() : NULL, triesLeft);
        logLine(line);
    }
    if (loaded && am_il2cpp_ready(&api)) {
        api.read_memory = readMemory;
        AMHookMode mode = hookMode();
        logLine(mode == AM_HOOK_CODE ? "JIT is on: code hooks" : "no JIT: pointer hooks (virtual methods and Unity callbacks)");
        AMHookReport r = am_il2cpp_install(&api, am_hook_specs, am_hook_spec_count,
                                           replacements, replacement_count, mode, substrateHook, logLine);
        NSString *summary = [NSString stringWithFormat:@"hooks: %d installed, %d missing, %d without replacement, %d failed, %d vtable slots",
                             r.hooked, r.missing, r.unregistered, r.failed, r.vtable_slots];
        logLine(summary.UTF8String);
        finish();
        return;
    }
    if (triesLeft <= 0) {
        logLine("Unity runtime not found; no hooks installed");
        finish();
        return;
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{ attempt(triesLeft - 1); });
}

BOOL AMIl2cppHasJIT(void) {
    return hookMode() == AM_HOOK_CODE;
}

void AMIl2cppSetReportHandler(void (^handler)(NSString *report)) {
    reportHandler = [handler copy];
}

void AMIl2cppStart(void) {
    if (started) return;
    started = YES;
    atexit(logExit);
    dispatch_async(dispatch_get_main_queue(), ^{ attempt(120); });
}
