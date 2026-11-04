#import "AMIl2cpp.h"

#import <dlfcn.h>
#import <execinfo.h>
#import <mach/mach.h>
#import <mach-o/dyld.h>
#import <mach-o/loader.h>
#import <os/log.h>
#import <string.h>
#import <unistd.h>

#include "am_il2cpp.h"
#include "AMDetectBypass.c"  // hide substrate images / jailbreak files from the game's own scanners

// am_hooks_data.c, written by scripts/embed_hooks.py from the mod's hooks.json
extern const AMHookSpec am_hook_specs[];
extern const size_t am_hook_spec_count;
extern void am_hook_specs_reveal(void);  // the names are encoded in the .dylib (runtime/am_hide.h)
// Set by am_hooks_data.c when hooks.json has a "table" section: a game without il2cpp_* exports.
extern AMTableHookSpec *am_hook_table_specs;
extern const size_t am_hook_table_count;
extern const uint64_t am_hook_table_registration;
extern const uint32_t am_hook_table_modules_offset;

static AMHookReplacement replacements[64];
static size_t replacement_count = 0;
static BOOL started = NO;
static BOOL codeHooksEnabled = NO;  // pointer hooks by default: code hooks fault on iOS 26+ (see AMIl2cpp.h)
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

// Cydia Substrate / ElleKit, looked up so a missing one skips the hooks instead of failing to load.
// ElleKit's EKJITLessHook sets a hardware breakpoint instead of patching a code page: on iOS 26+ a
// patched page faults (KERN_PROTECTION_FAILURE) when executed, so it is preferred when present. Its
// original stub only works for a pacibsp prologue; other methods use MSHookFunction.
static bool substrateHook(void *target, void *replacement, void **original) {
    typedef void (*MSHookFunctionFn)(void *, void *, void **);
    typedef void (*EKJITLessHookFn)(void *, void *, void **);
    static EKJITLessHookFn jitless;
    static MSHookFunctionFn hook;
    static bool looked;
    char line[128];
    if (!looked) {
        looked = true;
        jitless = (EKJITLessHookFn)dlsym(RTLD_DEFAULT, "EKJITLessHook");
        hook = (MSHookFunctionFn)dlsym(RTLD_DEFAULT, "MSHookFunction");
    }
    if (jitless && target && original && *(const uint32_t *)target == 0xD503237F) {  // pacibsp
        *original = NULL;
        jitless(target, replacement, original);
        snprintf(line, sizeof line, "hook %p via EKJITLessHook (original %s)", target,
                 *original ? "set" : "missing");
        logLine(line);
        return *original != NULL;
    }
    if (!hook) {
        snprintf(line, sizeof line, "hook %p: no EKJITLessHook or MSHookFunction", target);
        logLine(line);
        return false;
    }
    hook(target, replacement, original);
    snprintf(line, sizeof line, "hook %p via MSHookFunction", target);
    logLine(line);
    return true;
}

// Code hooks need JIT: iOS kills an app that rewrites its own code unless a debugger has marked it
// (CS_DEBUGGED, set by StikDebug, Xcode or a jailbreak). Without it the runtime swaps pointers instead.
#define CS_OPS_STATUS 0
#define CS_DEBUGGED 0x10000000
int csops(pid_t pid, unsigned int ops, void *useraddr, size_t usersize);

static AMHookMode hookMode(void) {
    if (!codeHooksEnabled) return AM_HOOK_POINTER;
    uint32_t flags = 0;
    if (csops(getpid(), CS_OPS_STATUS, &flags, sizeof flags) == 0 && (flags & CS_DEBUGGED)) return AM_HOOK_CODE;
    return AM_HOOK_POINTER;
}

void AMIl2cppSetCodeHooks(BOOL enabled) { codeHooksEnabled = enabled; }

// vm_read_overwrite reports unmapped memory as an error instead of crashing.
static bool readMemory(const void *address, void *out, size_t size) {
    vm_size_t got = 0;
    kern_return_t kr = vm_read_overwrite(mach_task_self(), (vm_address_t)address, size, (vm_address_t)out, &got);
    return kr == KERN_SUCCESS && got == size;
}

// The image that holds IL2CPP: UnityFramework when the game has one, else the app's own
// executable (older Unity builds link IL2CPP into it). A build that hides its il2cpp_*
// exports still names them in LC_SYMTAB, which AMMachSymbols reads (no dlsym handle needed).
// UnityFramework is kept once found; until then every attempt looks again, so a framework
// that is mapped after the first try is still picked up.
static const struct mach_header_64 *il2cppImage(void) {
    static const struct mach_header_64 *unity;
    if (unity) return unity;
    uint32_t count = _dyld_image_count();
    for (uint32_t i = 0; i < count; i++) {
        const char *name = _dyld_get_image_name(i);
        if (name && strstr(name, "/UnityFramework.framework/")) {
            unity = (const struct mach_header_64 *)_dyld_get_image_header(i);
            return unity;
        }
    }
    // dyld lists the main executable first (index 0), before any dylib it loaded; we are a dylib
    // inside that executable, so the count is never 0 here.
    return count ? (const struct mach_header_64 *)_dyld_get_image_header(0) : NULL;
}

// Weak: a mod that does not compile runtime/AMMachSymbols.c keeps the dlsym-only path.
extern void *ammach_lookup_symbol(const void *header, size_t header_bytes, const char *name)
    __attribute__((weak_import));

static void *machoSymbol(void *context, const char *name) {
    const struct mach_header_64 *image = context;
    if (!image || !ammach_lookup_symbol) return NULL;
    return ammach_lookup_symbol(image, sizeof *image + image->sizeofcmds, name);
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
    const struct mach_header_64 *image = il2cppImage();
    if (image) am_il2cpp_set_symbol_resolver(machoSymbol, (void *)image);
    bool loaded = am_il2cpp_api_load(&api);
    if (triesLeft % 20 == 0) {
        char line[128];
        snprintf(line, sizeof line, "waiting for the Unity VM (%s, corlib %p, %d tries left)",
                 loaded ? "API found" : "no IL2CPP exports", loaded && api.get_corlib ? api.get_corlib() : NULL, triesLeft);
        logLine(line);
    }
    if (loaded && am_il2cpp_ready(&api)) {
        api.read_memory = readMemory;
#if AM_NO_HOOKS
        // built with --no-hooks: the game's methods stay as they are; pins and the menu work on its data
        logLine("built without hooks: no game methods hooked");
        finish();
        return;
#endif
        AMHookMode mode = hookMode();
        logLine(mode == AM_HOOK_CODE ? "JIT is on: code hooks" : "no JIT: pointer hooks (virtual methods and Unity callbacks)");
        am_hook_specs_reveal();
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
    if (am_hook_table_count > 0) {
        // hooks.json "table": the names resolved by scripts/table_hooks.py, patched before IL2CPP starts
        am_hook_specs_reveal();  // the table's names are encoded like the hooks' (used in logs)
        AMIl2cppStartTable(am_hook_table_specs, am_hook_table_count, am_hook_table_registration,
                           am_hook_table_modules_offset);
        return;
    }
    started = YES;
    atexit(logExit);
    dispatch_async(dispatch_get_main_queue(), ^{ attempt(120); });
}

void AMIl2cppStartTable(AMTableHookSpec *specs, size_t count, uint64_t registrationRva, uint32_t modulesOffset) {
    if (started) return;
    started = YES;
    atexit(logExit);
    size_t matched = 0;
    for (size_t i = 0; i < count; i++) {
        if (specs[i].replacement) continue;  // a generated pin already carries its replacement
        for (size_t j = 0; j < replacement_count; j++) {
            if (specs[i].name && strcmp(specs[i].name, replacements[j].name) == 0) {
                specs[i].replacement = replacements[j].replacement;
                specs[i].original = replacements[j].original;
                matched++;
                break;
            }
        }
    }
    char line[160];
    snprintf(line, sizeof line, "table hooks: %zu specs, %zu with a replacement", count, matched);
    logLine(line);
    // AMTableHookInstall defers to the dyld image callback, before IL2CPP initialises its MethodInfos.
    AMTableHookInstall(specs, count, registrationRva, modulesOffset);
    finish();  // the report handler gets the table lines; without il2cpp_* there is nothing more to wait for
}
