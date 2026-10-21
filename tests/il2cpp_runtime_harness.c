// Test harness for runtime/am_il2cpp.c: a fake IL2CPP VM with one assembly, one class, two methods.
#include <stdio.h>
#include <string.h>

#include "am_il2cpp.h"

typedef struct { void *fn; } FakeMethod;
static void fn_take_damage(void) {}
static void fn_get_speed(void) {}
static FakeMethod take_damage = {(void *)fn_take_damage};
static FakeMethod get_speed = {(void *)fn_get_speed};
static int dummy_domain, dummy_assembly, dummy_image, dummy_class;
static const char *image_name = "Assembly-CSharp.dll";

static void *domain_get(void) { return &dummy_domain; }
static void **domain_get_assemblies(void *d, size_t *n) {
    static void *list[1] = {&dummy_assembly};
    *n = 1;
    return list;
}
static void *assembly_get_image(void *a) { return &dummy_image; }
static const char *image_get_name(void *i) { return image_name; }
static void *class_from_name(void *i, const char *ns, const char *name) {
    return strcmp(ns, "Game") == 0 && strcmp(name, "Player") == 0 ? &dummy_class : NULL;
}
static void *class_get_method_from_name(void *k, const char *name, int args) {
    if (strcmp(name, "TakeDamage") == 0 && (args == 2 || args == -1)) return &take_damage;
    if (strcmp(name, "GetSpeed") == 0 && (args == 0 || args == -1)) return &get_speed;
    return NULL;
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
static void *orig_a, *orig_b, *orig_c;

int main(int argc, char **argv) {
    AMIl2cppApi api = {domain_get, domain_get_assemblies, assembly_get_image, image_get_name,
                       class_from_name, class_get_method_from_name};
    printf("ready=%d\n", am_il2cpp_ready(&api));
    const AMHookSpec specs[] = {
        {"Assembly-CSharp", "Game", "Player", "TakeDamage", 2},  // found
        {"", "Game", "Player", "GetSpeed", 0},                   // found, no assembly given
        {"", "Game", "Player", "Gone", 0},                       // method missing
        {"", "Game", "Gone", "TakeDamage", 2},                   // class missing
        {"Other", "Game", "Player", "TakeDamage", 2},            // wrong assembly
        {"", "Game", "Player", "TakeDamage", 5},                 // wrong arg count
        {"", "Game", "Player", "Unregistered", -1},
    };
    const AMHookReplacement reps[] = {
        {"Player.TakeDamage", (void *)rep_a, &orig_a},
        {"Game.Player.GetSpeed", (void *)rep_b, &orig_b},
        {"Player.Gone", (void *)rep_c, &orig_c},
        {"Player.Gone2", (void *)rep_c, &orig_c},
        {"Gone.TakeDamage", (void *)rep_c, &orig_c},
    };
    AMHookReport r = am_il2cpp_install(&api, specs, 7, reps, 5, fake_hook, log_line);
    printf("hooked=%d missing=%d unregistered=%d unused=%d failed=%d\n",
           r.hooked, r.missing, r.unregistered, r.unused, r.failed);
    printf("orig_a=%d orig_b=%d\n", orig_a == (void *)fn_take_damage, orig_b == (void *)fn_get_speed);
    return 0;
}
