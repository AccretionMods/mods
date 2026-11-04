// Data-table hook for stripped IL2CPP builds: patches Il2CppCodeGenModule.methodPointers before IL2CPP
// initialises its MethodInfos, so a mod can target a game that hides every il2cpp_* name and without
// touching executable memory (which faults on iOS 26+ with the common hooking libraries).
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const char *name;    // "Class.Method", for logs only
    uint32_t module;     // Il2CppCodeGenModule index (dump-time, per build)
    uint32_t index;      // methodPointers index inside that module (dump-time, per build)
    void *replacement;   // filled by the caller
    void **original;     // filled by the runtime
} AMTableHookSpec;

// Install the hooks once UnityFramework is mapped. Call from a constructor, before IL2CPP starts.
// registrationRva is the Il2CppCodeRegistration RVA that Il2CppDumper prints for this build; modulesOffset
// is where codeGenModulesCount sits inside it (120, 104 or 96 by IL2CPP version), as scripts/table_hooks.py
// detected from the binary. Every read goes through vm_read_overwrite, so a wrong RVA or offset logs a line
// instead of crashing the game.
void AMTableHookInstall(AMTableHookSpec *hooks, size_t count, uint64_t registrationRva, uint32_t modulesOffset);

#ifdef __cplusplus
}
#endif
