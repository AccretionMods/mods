#include "am_il2cpp.h"

#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

#define LOAD(field, symbol) \
    do { api->field = dlsym(RTLD_DEFAULT, symbol); if (!api->field) return false; } while (0)

bool am_il2cpp_api_load(AMIl2cppApi *api) {
    LOAD(domain_get, "il2cpp_domain_get");
    LOAD(domain_get_assemblies, "il2cpp_domain_get_assemblies");
    LOAD(assembly_get_image, "il2cpp_assembly_get_image");
    LOAD(image_get_name, "il2cpp_image_get_name");
    LOAD(class_from_name, "il2cpp_class_from_name");
    LOAD(class_get_method_from_name, "il2cpp_class_get_method_from_name");
    // optional: without them nested classes are not found
    api->class_get_nested_types = dlsym(RTLD_DEFAULT, "il2cpp_class_get_nested_types");
    api->class_get_name = dlsym(RTLD_DEFAULT, "il2cpp_class_get_name");
    // optional: without them AM_HOOK_POINTER swaps only the MethodInfo
    api->image_get_class_count = dlsym(RTLD_DEFAULT, "il2cpp_image_get_class_count");
    api->image_get_class = dlsym(RTLD_DEFAULT, "il2cpp_image_get_class");
    api->class_is_subclass_of = dlsym(RTLD_DEFAULT, "il2cpp_class_is_subclass_of");
    api->method_get_flags = dlsym(RTLD_DEFAULT, "il2cpp_method_get_flags");
    return true;
}

bool am_il2cpp_ready(const AMIl2cppApi *api) {
    void *domain = api->domain_get();
    if (!domain) return false;
    size_t count = 0;
    void **assemblies = api->domain_get_assemblies(domain, &count);
    return assemblies && count > 0;
}

// "Assembly-CSharp.dll" and "Assembly-CSharp" name the same image.
static bool same_image(const char *want, const char *have) {
    if (!want[0]) return true;
    if (!have) return false;
    size_t w = strlen(want), h = strlen(have);
    if (w > 4 && strcmp(want + w - 4, ".dll") == 0) w -= 4;
    if (h > 4 && strcmp(have + h - 4, ".dll") == 0) h -= 4;
    return w == h && strncmp(want, have, w) == 0;
}

// A class by namespace and name; "Outer.Inner" walks the nested types of Outer (Il2CppDumper's spelling).
static void *find_class(const AMIl2cppApi *api, void *image, const char *ns, const char *name) {
    const char *dot = strchr(name, '.');
    if (!dot) return api->class_from_name(image, ns, name);
    if (!api->class_get_nested_types || !api->class_get_name) return NULL;
    char part[256];
    size_t len = (size_t)(dot - name);
    if (len >= sizeof part) return NULL;
    memcpy(part, name, len);
    part[len] = 0;
    void *klass = api->class_from_name(image, ns, part);
    while (klass && dot) {
        name = dot + 1;
        dot = strchr(name, '.');
        len = dot ? (size_t)(dot - name) : strlen(name);
        void *iter = NULL, *nested, *found = NULL;
        while (!found && (nested = api->class_get_nested_types(klass, &iter))) {
            const char *n = api->class_get_name(nested);
            if (n && strlen(n) == len && strncmp(n, name, len) == 0) found = nested;
        }
        klass = found;
    }
    return klass;
}

// The method's MethodInfo, and its class in *klass_out.
static void *find_method(const AMIl2cppApi *api, const AMHookSpec *spec, void **klass_out) {
    void *domain = api->domain_get();
    if (!domain) return NULL;
    size_t count = 0;
    void **assemblies = api->domain_get_assemblies(domain, &count);
    for (size_t i = 0; assemblies && i < count; i++) {
        void *image = api->assembly_get_image(assemblies[i]);
        if (!image || !same_image(spec->assembly, api->image_get_name(image))) continue;
        void *klass = find_class(api, image, spec->ns, spec->cls);
        if (!klass) continue;
        void *info = api->class_get_method_from_name(klass, spec->method, spec->args);
        // MethodInfo begins with its native function pointer.
        if (info && *(void **)info) {
            *klass_out = klass;
            return info;
        }
    }
    return NULL;
}

void *am_il2cpp_resolve(const AMIl2cppApi *api, const AMHookSpec *spec) {
    void *klass = NULL;
    void *info = find_method(api, spec, &klass);
    return info ? *(void **)info : NULL;
}

// An Il2CppClass ends with its vtable, pairs of {function, MethodInfo}; the struct before it differs between
// Unity versions, so the pair is searched for. 8 KB covers the header and about 500 slots; a class near
// the end of mapped memory is read in a smaller window.
#define VTABLE_WINDOW 8192

int am_il2cpp_swap_vtable(const AMIl2cppApi *api, void *klass, const void *info, void *original, void *replacement) {
    if (!api->read_memory) return 0;
    static void *buf[VTABLE_WINDOW / sizeof(void *)];
    size_t size = VTABLE_WINDOW;
    while (size >= 256 && !api->read_memory(klass, buf, size)) size /= 2;
    if (size < 256) return 0;
    int swapped = 0;
    size_t n = size / sizeof(void *);
    for (size_t i = 0; i + 1 < n; i++) {
        if (buf[i] == original && buf[i + 1] == info) {
            ((void **)klass)[i] = replacement;
            swapped++;
            i++;
        }
    }
    return swapped;
}

#define METHOD_ATTRIBUTE_VIRTUAL 0x0040

// AM_HOOK_POINTER: the MethodInfo, then every vtable that holds it (its class and the subclasses).
static int swap_pointers(const AMIl2cppApi *api, void *klass, void *info, void *replacement, void **original) {
    void **fields = info;
    void *target = fields[0];
    *original = target;
    fields[0] = replacement;
    // Unity 2021.2+: the next field is virtualMethodPointer, the same function for a class's own method.
    // Older versions keep invoker_method there, which never equals methodPointer.
    if (fields[1] == target) fields[1] = replacement;

    if (api->method_get_flags) {
        unsigned int iflags = 0;
        if (!(api->method_get_flags(info, &iflags) & METHOD_ATTRIBUTE_VIRTUAL)) return 0;
    }
    if (!api->image_get_class_count || !api->image_get_class || !api->class_is_subclass_of) {
        return am_il2cpp_swap_vtable(api, klass, info, target, replacement);
    }
    int swapped = 0;
    size_t count = 0;
    void **assemblies = api->domain_get_assemblies(api->domain_get(), &count);
    for (size_t a = 0; assemblies && a < count; a++) {
        void *image = api->assembly_get_image(assemblies[a]);
        if (!image) continue;
        size_t classes = api->image_get_class_count(image);
        for (size_t c = 0; c < classes; c++) {
            void *k = api->image_get_class(image, c);
            if (k && (k == klass || api->class_is_subclass_of(k, klass, false))) {
                swapped += am_il2cpp_swap_vtable(api, k, info, target, replacement);
            }
        }
    }
    return swapped;
}

static void describe(const AMHookSpec *s, char *out, size_t size) {
    snprintf(out, size, "%s%s%s.%s", s->ns, s->ns[0] ? "." : "", s->cls, s->method);
}

// A replacement is named "Class.method" or "Namespace.Class.method".
static const AMHookReplacement *find_replacement(const AMHookReplacement *r, size_t n, const AMHookSpec *s) {
    char full[512], short_name[512];
    describe(s, full, sizeof full);
    snprintf(short_name, sizeof short_name, "%s.%s", s->cls, s->method);
    for (size_t i = 0; i < n; i++) {
        if (strcmp(r[i].name, full) == 0 || strcmp(r[i].name, short_name) == 0) return &r[i];
    }
    return NULL;
}

AMHookReport am_il2cpp_install(const AMIl2cppApi *api,
                               const AMHookSpec *specs, size_t spec_count,
                               const AMHookReplacement *replacements, size_t replacement_count,
                               AMHookMode mode, AMHookFn hook, AMLogFn log) {
    AMHookReport report = {0};
    char name[512], line[1024];
    for (size_t i = 0; i < spec_count; i++) {
        describe(&specs[i], name, sizeof name);
        const AMHookReplacement *rep = find_replacement(replacements, replacement_count, &specs[i]);
        if (!rep) {
            report.unregistered++;
            snprintf(line, sizeof line, "skipped %s: no replacement registered", name);
            log(line);
            continue;
        }
        void *klass = NULL;
        void *info = find_method(api, &specs[i], &klass);
        if (!info) {
            report.missing++;
            snprintf(line, sizeof line, "skipped %s: method not found in this version", name);
            log(line);
            continue;
        }
        if (mode == AM_HOOK_POINTER) {
            int slots = swap_pointers(api, klass, info, rep->replacement, rep->original);
            report.vtable_slots += slots;
            report.hooked++;
            snprintf(line, sizeof line, "hooked %s (pointers, %d vtable slots)", name, slots);
            log(line);
            continue;
        }
        if (!hook(*(void **)info, rep->replacement, rep->original)) {
            report.failed++;
            snprintf(line, sizeof line, "skipped %s: hook could not be installed", name);
            log(line);
            continue;
        }
        report.hooked++;
        snprintf(line, sizeof line, "hooked %s (code)", name);
        log(line);
    }
    for (size_t i = 0; i < replacement_count; i++) {
        bool listed = false;
        for (size_t j = 0; j < spec_count && !listed; j++) {
            listed = find_replacement(&replacements[i], 1, &specs[j]) != NULL;
        }
        if (!listed) {
            report.unused++;
            snprintf(line, sizeof line, "ignored replacement %s: not in hooks.json", replacements[i].name);
            log(line);
        }
    }
    return report;
}
