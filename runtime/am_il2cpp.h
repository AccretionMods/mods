// Accretion Mods IL2CPP runtime: finds the hooks.json methods in a running Unity game and hooks them.
//
// Plain C so it can be tested with a fake IL2CPP API (tests/test_il2cpp_runtime.py).
// A method that is not found, or has no replacement registered, is skipped and logged; the rest still install.

#ifndef AM_IL2CPP_H
#define AM_IL2CPP_H

#include <stdbool.h>
#include <stddef.h>

// One hooks.json entry, written into am_hooks_data.c by scripts/embed_hooks.py.
typedef struct {
    const char *assembly;  // "" = search every assembly
    const char *ns;        // "" = global namespace
    const char *cls;
    const char *method;
    int args;              // -1 = any argument count
} AMHookSpec;

// The il2cpp_* functions the runtime uses (exported by UnityFramework).
typedef struct {
    void *(*domain_get)(void);
    void **(*domain_get_assemblies)(void *domain, size_t *count);
    void *(*assembly_get_image)(void *assembly);
    const char *(*image_get_name)(void *image);
    void *(*class_from_name)(void *image, const char *ns, const char *name);
    void *(*class_get_method_from_name)(void *klass, const char *name, int args);
} AMIl2cppApi;

// A replacement the tweak registers for one hooks.json entry, named "Class.method" or "Namespace.Class.method".
typedef struct {
    const char *name;
    void *replacement;
    void **original;  // receives the original function
} AMHookReplacement;

typedef void (*AMLogFn)(const char *message);
// Installs a hook on a function; returns false on failure (MSHookFunction in the tweak).
typedef bool (*AMHookFn)(void *target, void *replacement, void **original);

typedef struct {
    int hooked;
    int missing;         // method not found in the game
    int unregistered;    // in hooks.json, no replacement registered
    int unused;          // replacement registered, not in hooks.json
    int failed;          // found, but installing the hook failed
} AMHookReport;

// Fills api from the loaded process (dlsym); false when the game has no IL2CPP exports.
bool am_il2cpp_api_load(AMIl2cppApi *api);

// True once the VM has loaded its assemblies (hooks can resolve).
bool am_il2cpp_ready(const AMIl2cppApi *api);

// The native function behind a method, or NULL.
void *am_il2cpp_resolve(const AMIl2cppApi *api, const AMHookSpec *spec);

// Hook every spec that has a replacement. Never aborts on one bad entry.
AMHookReport am_il2cpp_install(const AMIl2cppApi *api,
                               const AMHookSpec *specs, size_t spec_count,
                               const AMHookReplacement *replacements, size_t replacement_count,
                               AMHookFn hook, AMLogFn log);

#endif
