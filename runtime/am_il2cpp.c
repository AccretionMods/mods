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

void *am_il2cpp_resolve(const AMIl2cppApi *api, const AMHookSpec *spec) {
    void *domain = api->domain_get();
    if (!domain) return NULL;
    size_t count = 0;
    void **assemblies = api->domain_get_assemblies(domain, &count);
    for (size_t i = 0; assemblies && i < count; i++) {
        void *image = api->assembly_get_image(assemblies[i]);
        if (!image || !same_image(spec->assembly, api->image_get_name(image))) continue;
        void *klass = api->class_from_name(image, spec->ns, spec->cls);
        if (!klass) continue;
        void *info = api->class_get_method_from_name(klass, spec->method, spec->args);
        // MethodInfo begins with its native function pointer.
        if (info && *(void **)info) return *(void **)info;
    }
    return NULL;
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
                               AMHookFn hook, AMLogFn log) {
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
        void *target = am_il2cpp_resolve(api, &specs[i]);
        if (!target) {
            report.missing++;
            snprintf(line, sizeof line, "skipped %s: method not found in this version", name);
            log(line);
            continue;
        }
        if (!hook(target, rep->replacement, rep->original)) {
            report.failed++;
            snprintf(line, sizeof line, "skipped %s: hook could not be installed", name);
            log(line);
            continue;
        }
        report.hooked++;
        snprintf(line, sizeof line, "hooked %s", name);
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
