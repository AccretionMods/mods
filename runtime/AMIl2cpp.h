// Accretion Mods IL2CPP runtime, for tweaks of Unity games (a mod with a hooks.json).
//
// In Tweak.x, register a replacement for each hook you want, then start:
//
//   static void (*orig_TakeDamage)(void *self, float amount, void *method);
//   static void rep_TakeDamage(void *self, float amount, void *method) {
//       if (![AMMenu boolForKey:@"god"]) orig_TakeDamage(self, amount, method);
//   }
//   %ctor {
//       AMIl2cppRegister("PlayerHealth.TakeDamage", (void *)rep_TakeDamage, (void **)&orig_TakeDamage);
//       AMIl2cppStart();
//   }
//
// Every method in hooks.json that the running game still has and that has a replacement is hooked. The
// others are skipped and logged ("[AccretionMods] skipped ..." in the device log); the app keeps running.
//
// With JIT on (StikDebug) the hooks patch code and catch every call. Without JIT they swap IL2CPP's method
// pointers, which catches virtual/override methods, Unity callbacks (Update, ...) and delegates, but not a
// direct call from other game code (am_il2cpp.h). scripts/find_cheats.py marks which hooks need JIT.

#import <Foundation/Foundation.h>
#include "am_hide.h"
#include "AMTableHook.h"

#ifdef __cplusplus
extern "C" {
#endif

// name: "Class.method" or "Namespace.Class.method", as in hooks.json.
void AMIl2cppRegister(const char *name, void *replacement, void **original);

// Waits for Unity's runtime (up to 60 s), then installs the registered hooks once.
void AMIl2cppStart(void);

// For a game that hides every il2cpp_* name. A mod with a hooks.json "table" section gets here through
// AMIl2cppStart (scripts/embed_hooks.py generates the table). By hand: register the replacements with
// AMIl2cppRegister("Namespace.Class.Method", ...) and pass the table scripts/table_hooks.py generated for
// this build, with the registration RVA and the codeGenModules offset it found. The runtime swaps
// Il2CppCodeGenModule.methodPointers before IL2CPP initialises its MethodInfos; it needs no il2cpp_*
// function and touches no executable page. The report handler runs right after the table is installed.
void AMIl2cppStartTable(AMTableHookSpec *specs, size_t count, uint64_t registrationRva, uint32_t modulesOffset);

// True when JIT is on, so the hooks are code hooks that catch every call. A mod can label or hide the
// switches whose hooks need JIT (hooks.json "needs_jit") when this is false.
BOOL AMIl2cppHasJIT(void);

// Code hooks patch executable pages, which iOS 26+ faults (KERN_PROTECTION_FAILURE) with ElleKit's
// MSHookFunction; pointer hooks are the default. Call with YES before AMIl2cppStart to opt in (the
// runtime then prefers ElleKit's hardware-breakpoint EKJITLessHook, which touches no code page).
void AMIl2cppSetCodeHooks(BOOL enabled);

// The game's own function behind one of our replacements (what the runtime saved in *original), or NULL when
// pointer is not a registered replacement. AMIl2cppCall uses it so a pin acts on the game, not on our hook.
void *AMIl2cppOriginalOf(void *pointer);

// Called once on the main queue when AMIl2cppStart is done, with every log line (one per hook, then the
// counts). Optional; set it before AMIl2cppStart.
void AMIl2cppSetReportHandler(void (^handler)(NSString *report));

#ifdef __cplusplus
}
#endif
