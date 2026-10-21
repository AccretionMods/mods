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

#import <Foundation/Foundation.h>

#ifdef __cplusplus
extern "C" {
#endif

// name: "Class.method" or "Namespace.Class.method", as in hooks.json.
void AMIl2cppRegister(const char *name, void *replacement, void **original);

// Waits for Unity's runtime (up to 60 s), then installs the registered hooks once.
void AMIl2cppStart(void);

#ifdef __cplusplus
}
#endif
