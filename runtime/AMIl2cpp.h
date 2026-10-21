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

#ifdef __cplusplus
extern "C" {
#endif

// name: "Class.method" or "Namespace.Class.method", as in hooks.json.
void AMIl2cppRegister(const char *name, void *replacement, void **original);

// Waits for Unity's runtime (up to 60 s), then installs the registered hooks once.
void AMIl2cppStart(void);

// Called once on the main queue when AMIl2cppStart is done, with every log line (one per hook, then the
// counts). Optional; set it before AMIl2cppStart.
void AMIl2cppSetReportHandler(void (^handler)(NSString *report));

#ifdef __cplusplus
}
#endif
