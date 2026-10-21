// Accretion Mods IL2CPP runtime: finds the hooks.json methods in a running Unity game and hooks them.
//
// Plain C so it can be tested with a fake IL2CPP API (tests/test_il2cpp_runtime.py).
// A method that is not found, or has no replacement registered, is skipped and logged; the rest still install.
//
// Two ways to hook (AMHookMode):
// - AM_HOOK_CODE patches the function's machine code (Substrate). Catches every call, but iOS kills the app
//   unless JIT is on (SideStore + StikDebug).
// - AM_HOOK_POINTER swaps the pointers IL2CPP keeps in writable memory: the method's MethodInfo and the
//   vtable slots of its class and subclasses. No code is changed, so it runs on any phone, but it catches
//   only calls that go through those pointers: virtual/override and interface calls, Unity callbacks
//   (Update, OnTriggerEnter, ...), delegates and reflection. A direct call from other game code is missed.

#ifndef AM_IL2CPP_H
#define AM_IL2CPP_H

#include <stdbool.h>
#include <stddef.h>

// One hooks.json entry, written into am_hooks_data.c by scripts/embed_hooks.py.
typedef struct {
    const char *assembly;  // "" = search every assembly
    const char *ns;        // "" = global namespace
    const char *cls;       // "Outer.Inner" for a nested class
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
    // Optional, for nested classes ("Outer.Inner" in hooks.json); without them those hooks are missing.
    void *(*class_get_nested_types)(void *klass, void **iter);
    const char *(*class_get_name)(void *klass);
    // Optional, for AM_HOOK_POINTER's vtable scan; when one is missing only the MethodInfo is swapped.
    size_t (*image_get_class_count)(void *image);
    void *(*image_get_class)(void *image, size_t index);
    bool (*class_is_subclass_of)(void *klass, void *klass_c, bool check_interfaces);
    unsigned int (*method_get_flags)(const void *method, unsigned int *iflags);  // skips the scan for non-virtual
    // Copies size bytes at address into out; false (nothing copied) when that memory is not readable.
    bool (*read_memory)(const void *address, void *out, size_t size);
} AMIl2cppApi;

typedef enum { AM_HOOK_CODE, AM_HOOK_POINTER } AMHookMode;

// A replacement the tweak registers for one hooks.json entry, named "Class.method" or "Namespace.Class.method".
typedef struct {
    const char *name;
    void *replacement;
    void **original;  // receives the original function
} AMHookReplacement;

typedef void (*AMLogFn)(const char *message);
// Installs a code hook on a function; returns false on failure (MSHookFunction in the tweak).
typedef bool (*AMHookFn)(void *target, void *replacement, void **original);

typedef struct {
    int hooked;
    int missing;         // method not found in the game
    int unregistered;    // in hooks.json, no replacement registered
    int unused;          // replacement registered, not in hooks.json
    int failed;          // found, but installing the hook failed
    int vtable_slots;    // AM_HOOK_POINTER: vtable entries swapped, over all hooks
} AMHookReport;

// Fills api from the loaded process (dlsym); false when the game has no IL2CPP exports.
// read_memory is left for the caller to set.
bool am_il2cpp_api_load(AMIl2cppApi *api);

// True once the VM has loaded its assemblies (hooks can resolve).
bool am_il2cpp_ready(const AMIl2cppApi *api);

// The native function behind a method, or NULL.
void *am_il2cpp_resolve(const AMIl2cppApi *api, const AMHookSpec *spec);

// Points every vtable entry {original, info} in klass at replacement; returns how many it changed.
// Exposed for the tests.
int am_il2cpp_swap_vtable(const AMIl2cppApi *api, void *klass, const void *info, void *original, void *replacement);

// Hook every spec that has a replacement, in the given mode (hook is used only by AM_HOOK_CODE).
// Never aborts on one bad entry.
AMHookReport am_il2cpp_install(const AMIl2cppApi *api,
                               const AMHookSpec *specs, size_t spec_count,
                               const AMHookReplacement *replacements, size_t replacement_count,
                               AMHookMode mode, AMHookFn hook, AMLogFn log);

#endif
