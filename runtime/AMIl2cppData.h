// Accretion Mods IL2CPP data helpers: read and call the running game without hooking any code.
//
// Everything here goes through the game's own il2cpp_* exports (class and field lookups, il2cpp_runtime_invoke),
// which are plain data calls, so it works on any phone, JIT or not. It is what a no-JIT switch is built from: a
// ticker (dispatch timer on the main queue) that finds the live objects and writes their fields or calls their
// setters. Mods that use it list runtime/AMIl2cppData.m in their Makefile.
//
//   // every half second: full health, no enemy damage
//   void *stats = AMIl2cppStaticObject("StatHandler", "instance");       // StatHandler.instance
//   float max = *(float *)AMIl2cppField(stats, "StatHandler", "HealthMax");
//   void *argv[1] = {&max};
//   AMIl2cppCall(stats, "StatHandler", "set_Health", 1, argv, NULL);     // a setter, by name
//   int32_t n = 0;
//   void *enemies = AMIl2cppFindObjects("Enemy", &n);                     // UnityEngine.Object.FindObjectsByType
//   for (int32_t i = 0; i < n; i++) *(int64_t *)AMIl2cppField(AMIl2cppArrayAt(enemies, i), "Enemy", "EnemyDamage") = 0;
//
// Field offsets come from the game at run time (il2cpp_field_get_offset), so a new game version with moved
// fields still works as long as the names stay; dump.cs offsets never need copying by hand.
// Classes are looked up in Assembly-CSharp unless the name is "Assembly:Namespace.Class" (for example
// "UnityEngine.CoreModule:UnityEngine.Object"). A lookup that fails is logged once per name.
// Call these on the main thread (Unity's), like the hooks and the menu.

#import <Foundation/Foundation.h>
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Tag for the log lines ("[My Game+] ..."); defaults to "AccretionMods".
void AMIl2cppDataSetLogTag(const char *tag);
// Logs "[tag] ..." as a public os_log line (visible in the device log without <private>).
void AMIl2cppLog(NSString *format, ...) NS_FORMAT_FUNCTION(1, 2);

// A class: "Class", "Namespace.Class", "Outer.Inner" or "Assembly:Namespace.Class". NULL when not found.
void *AMIl2cppClass(const char *name);

// The value of a static field (a singleton like StatHandler.instance), or NULL.
void *AMIl2cppStaticObject(const char *cls, const char *field);
// The first live object of a class (UnityEngine.Object.FindFirstObjectByType), or NULL.
void *AMIl2cppFindObject(const char *cls);
// Every live object of a class as a managed array (UnityEngine.Object.FindObjectsByType, active only);
// *count receives how many. NULL when none or the lookup failed.
void *AMIl2cppFindObjects(const char *cls, int32_t *count);
// Elements of a managed array (IL2CPP layout: count at 0x18, elements from 0x20).
int32_t AMIl2cppArrayCount(void *array);
void *AMIl2cppArrayAt(void *array, int32_t index);

// Address of a field inside an object (instance) or of a static field (obj NULL): cast and read or write it.
// NULL when the class or field is not found. The offset is read from the game, not from a dump.
void *AMIl2cppField(void *obj, const char *cls, const char *field);

// Calls a method by name through il2cpp_runtime_invoke. obj NULL calls a static method. argv holds one pointer
// per argument: to the value for value types (int32_t, bool, float, ...), the object itself for references.
// out (optional) receives the result: a boxed value for value types, the object for references. Returns false
// when the method is missing or threw (the exception is logged once).
bool AMIl2cppCall(void *obj, const char *cls, const char *method, int argc, void **argv, void **out);
// The value inside a boxed result (int, float, bool...), or NULL.
void *AMIl2cppUnbox(void *boxed);

// Managed strings.
NSString *AMIl2cppString(void *str);
void *AMIl2cppNewString(const char *utf8);

// A repeating main-queue timer (kept alive for the life of the process). The usual home of a no-JIT switch.
void AMIl2cppEvery(double seconds, void (^block)(void));

// Pauses a Unity game (Time.timeScale 0) or resumes it at the scale it had. Resuming leaves the scale alone when
// the game changed it meanwhile. AMMenu calls it as the menu opens and closes (AMMenu.onOpenChange).
void AMIl2cppPauseGame(bool pause);

#ifdef __cplusplus
}
#endif
