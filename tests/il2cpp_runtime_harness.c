// Test harness for runtime/am_il2cpp.c: a fake IL2CPP VM with one assembly, three classes, two methods.
// "harness" hooks with code hooks; "harness pointer" swaps pointers and prints what changed;
// "harness uninitialized" is a VM before il2cpp_init: corlib is NULL and every other call aborts.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "am_il2cpp.h"

// MethodInfo: methodPointer, then virtualMethodPointer (Unity 2021.2+) or invoker_method (older).
typedef struct { void *fn; void *second; } FakeMethod;
static void fn_take_damage(void) {}
static void fn_get_speed(void) {}
static void fn_invoker(void) {}
static FakeMethod take_damage = {(void *)fn_take_damage, (void *)fn_invoker};  // older Unity
static FakeMethod get_speed = {(void *)fn_get_speed, (void *)fn_get_speed};    // 2021.2+

// Il2CppClass: a header, then {function, MethodInfo} vtable pairs; memory after it is "unmapped".
typedef struct { void *header[5]; void *vtable[6]; void *padding[64]; } FakeClass;
static FakeClass player = {{0}, {(void *)fn_get_speed, &get_speed, (void *)fn_take_damage, &take_damage, 0, 0}};
static FakeClass enemy = {{0}, {(void *)fn_take_damage, &take_damage, 0, 0, 0, 0}};    // subclass
static FakeClass unrelated = {{0}, {(void *)fn_take_damage, &take_damage, 0, 0, 0, 0}};
static FakeClass *classes[] = {&player, &enemy, &unrelated};
static int dummy_domain, dummy_assembly, dummy_image;
static FakeClass fighting;  // nested: Player.Fighting
static void fn_get_hit(void) {}
static FakeMethod get_hit = {(void *)fn_get_hit, (void *)fn_get_hit};
static const char *image_name = "Assembly-CSharp.dll";

static bool initialized = true;
static void *get_corlib(void) { return initialized ? &dummy_image : NULL; }
static void *domain_get(void) {
    if (!initialized) {
        puts("domain_get called before il2cpp_init");
        abort();
    }
    return &dummy_domain;
}
static void **domain_get_assemblies(void *d, size_t *n) {
    static void *list[1] = {&dummy_assembly};
    *n = 1;
    return list;
}
static void *assembly_get_image(void *a) { return &dummy_image; }
static const char *image_get_name(void *i) { return image_name; }
static void *class_from_name(void *i, const char *ns, const char *name) {
    return strcmp(ns, "Game") == 0 && strcmp(name, "Player") == 0 ? (void *)&player : NULL;
}
static void *class_get_nested_types(void *k, void **iter) {
    if (k != (void *)&player || *iter) return NULL;
    *iter = &fighting;
    return &fighting;
}
static const char *class_get_name(void *k) { return k == (void *)&fighting ? "Fighting" : "Player"; }
static void *class_get_method_from_name(void *k, const char *name, int args) {
    if (k == (void *)&fighting) return strcmp(name, "GetHit") == 0 ? &get_hit : NULL;
    if (strcmp(name, "TakeDamage") == 0 && (args == 2 || args == -1)) return &take_damage;
    if (strcmp(name, "GetSpeed") == 0 && (args == 0 || args == -1)) return &get_speed;
    return NULL;
}

static size_t image_get_class_count(void *i) { return 3; }
static void *image_get_class(void *i, size_t n) { return classes[n]; }
static bool class_is_subclass_of(void *k, void *c, bool interfaces) { return k == (void *)&enemy && c == (void *)&player; }
static unsigned int method_get_flags(const void *m, unsigned int *iflags) {
    return m == (const void *)&take_damage ? 0x0040 : 0;  // TakeDamage is virtual
}
static bool read_memory(const void *address, void *out, size_t size) {
    if (size > sizeof(FakeClass)) return false;
    memcpy(out, address, size);
    return true;
}

static bool fake_hook(void *target, void *replacement, void **original) {
    if (replacement == NULL) return false;
    *original = target;
    return true;
}
static void log_line(const char *m) { printf("%s\n", m); }

static void rep_a(void) {}
static void rep_b(void) {}
static void rep_c(void) {}
static void rep_d(void) {}
static void *orig_a, *orig_b, *orig_c, *orig_d;

int main(int argc, char **argv) {
    bool pointer = argc > 1 && strcmp(argv[1], "pointer") == 0;
    initialized = !(argc > 1 && strcmp(argv[1], "uninitialized") == 0);
    AMIl2cppApi api = {domain_get, domain_get_assemblies, assembly_get_image, image_get_name,
                       class_from_name, class_get_method_from_name, class_get_nested_types, class_get_name,
                       image_get_class_count, image_get_class, class_is_subclass_of, method_get_flags, read_memory,
                       get_corlib};
    printf("ready=%d\n", am_il2cpp_ready(&api));
    if (!initialized) return 0;
    const AMHookSpec specs[] = {
        {"Assembly-CSharp", "Game", "Player", "TakeDamage", 2},  // found
        {"", "Game", "Player", "GetSpeed", 0},                   // found, no assembly given
        {"", "Game", "Player", "Gone", 0},                       // method missing
        {"", "Game", "Gone", "TakeDamage", 2},                   // class missing
        {"Other", "Game", "Player", "TakeDamage", 2},            // wrong assembly
        {"", "Game", "Player", "TakeDamage", 5},                 // wrong arg count
        {"", "Game", "Player", "Unregistered", -1},
        {"", "Game", "Player.Fighting", "GetHit", 2},            // nested class
        {"", "Game", "Player.Gone", "GetHit", 2},                // nested class missing
    };
    const AMHookReplacement reps[] = {
        {"Player.TakeDamage", (void *)rep_a, &orig_a},
        {"Game.Player.GetSpeed", (void *)rep_b, &orig_b},
        {"Player.Gone", (void *)rep_c, &orig_c},
        {"Player.Gone2", (void *)rep_c, &orig_c},
        {"Gone.TakeDamage", (void *)rep_c, &orig_c},
        {"Player.Fighting.GetHit", (void *)rep_d, &orig_d},
        {"Player.Gone.GetHit", (void *)rep_c, &orig_c},
    };
    AMHookReport r = am_il2cpp_install(&api, specs, 9, reps, 7, pointer ? AM_HOOK_POINTER : AM_HOOK_CODE,
                                       fake_hook, log_line);
    printf("hooked=%d missing=%d unregistered=%d unused=%d failed=%d slots=%d\n",
           r.hooked, r.missing, r.unregistered, r.unused, r.failed, r.vtable_slots);
    printf("orig_a=%d orig_b=%d orig_d=%d\n", orig_a == (void *)fn_take_damage, orig_b == (void *)fn_get_speed,
           orig_d == (void *)fn_get_hit);
    if (pointer) {
        printf("info_a=%d invoker_kept=%d info_b=%d virtual_b=%d\n", take_damage.fn == (void *)rep_a,
               take_damage.second == (void *)fn_invoker, get_speed.fn == (void *)rep_b, get_speed.second == (void *)rep_b);
        printf("player_a=%d enemy_a=%d unrelated_a=%d player_b=%d\n", player.vtable[2] == (void *)rep_a,
               enemy.vtable[0] == (void *)rep_a, unrelated.vtable[0] == (void *)rep_a, player.vtable[0] == (void *)rep_b);
    }
    return 0;
}
