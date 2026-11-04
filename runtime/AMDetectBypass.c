// Hides substrate-like images and jailbreak artifacts from a game's own detection routines, so a cleanly
// re-signed sideload survives its local self-checks (the PAG crash SDK scans loaded images for
// Substrate/MobileSubstrate/TweakInject/CydiaSubstrate/libsubstrate; Unity Ads checks /private/jailbreak.txt).
// This does NOT touch server-side validation; a server that refuses the account still refuses it.
//
// Method: dyld interposing (__DATA,__interpose). dyld rebinds stat/access/fopen/dlopen/_dyld_get_image_name
// to the replacements below across every image in the process, including chained-fixup images like
// UnityFramework, and does the binding itself. There is no manual fixup walk and no vm_write, so the write
// crashes the hand-rolled rebinder hit on Into the Dead 2 and Survivor cannot happen here. The originals are
// resolved through the real dlsym (which is deliberately NOT interposed) and called through a pointer, so a
// replacement never recurses into itself.
#include <dlfcn.h>
#include <errno.h>
#include <mach-o/dyld.h>
#include <os/log.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void am_log(const char *message) {
    os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", message);
}

static const char *(*am_orig_image_name)(uint32_t);
static void *(*am_orig_dlopen)(const char *, int);
static int (*am_orig_stat)(const char *, struct stat *);
static int (*am_orig_access)(const char *, int);
static FILE *(*am_orig_fopen)(const char *, const char *);

// Resolve an original through the real dlsym once. dlsym returns the symbol's own address (not the
// interposed bind), so calling through the returned pointer is a direct call and is never interposed.
static void *am_orig(void **slot, const char *name) {
    if (!*slot) *slot = dlsym(RTLD_DEFAULT, name);
    return *slot;
}

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
    const char *(*orig)(uint32_t) = am_orig((void **)&am_orig_image_name, "_dyld_get_image_name");
    if (!orig) return NULL;
    const char *name = orig(index);
    return am_hidden_image(name) ? "/usr/lib/libSystem.B.dylib" : name;
}

static void *am_rep_dlopen(const char *path, int mode) {
    if (am_hidden_image(path)) {
        errno = ENOENT;
        return NULL;
    }
    void *(*orig)(const char *, int) = am_orig((void **)&am_orig_dlopen, "dlopen");
    return orig ? orig(path, mode) : NULL;
}

static int am_rep_stat(const char *path, struct stat *info) {
    if (am_jailbreak_path(path)) {
        errno = ENOENT;
        return -1;
    }
    int (*orig)(const char *, struct stat *) = am_orig((void **)&am_orig_stat, "stat");
    return orig ? orig(path, info) : -1;
}

static int am_rep_access(const char *path, int mode) {
    if (am_jailbreak_path(path)) {
        errno = ENOENT;
        return -1;
    }
    int (*orig)(const char *, int) = am_orig((void **)&am_orig_access, "access");
    return orig ? orig(path, mode) : -1;
}

static FILE *am_rep_fopen(const char *path, const char *mode) {
    if (am_jailbreak_path(path)) {
        errno = ENOENT;
        return NULL;
    }
    FILE *(*orig)(const char *, const char *) = am_orig((void **)&am_orig_fopen, "fopen");
    return orig ? orig(path, mode) : NULL;
}

// dyld applies one of these tuples wherever any image binds the named symbol. Replacement first, then the
// real symbol; dyld handles classic and chained-fixup images alike.
#define AM_INTERPOSE(replacement, replacee)                                                          \
    __attribute__((used)) static const struct {                                                      \
        const void *replacement;                                                                     \
        const void *replacee;                                                                        \
    } am_interpose_##replacee __attribute__((section("__DATA,__interpose"))) = {                      \
        (const void *)(unsigned long)&replacement, (const void *)(unsigned long)&replacee}

AM_INTERPOSE(am_rep_stat, stat);
AM_INTERPOSE(am_rep_access, access);
AM_INTERPOSE(am_rep_fopen, fopen);
AM_INTERPOSE(am_rep_dlopen, dlopen);
AM_INTERPOSE(am_rep_image_name, _dyld_get_image_name);

__attribute__((constructor)) static void am_detect_bypass_install(void) {
    // The interpose tuples are already live by the time this runs; resolve the originals up front so the
    // first interposed call does not have to.
    am_orig((void **)&am_orig_image_name, "_dyld_get_image_name");
    am_orig((void **)&am_orig_dlopen, "dlopen");
    am_orig((void **)&am_orig_stat, "stat");
    am_orig((void **)&am_orig_access, "access");
    am_orig((void **)&am_orig_fopen, "fopen");
    char line[64];
    snprintf(line, sizeof line, "bypass: interpose active images=%u", (unsigned)_dyld_image_count());
    am_log(line);
}
