// Quago (Pokemon GO) local checks: enumerate the Quago classes and neutralise the local verdicts.
//   * isJailbroken...            -> NO
//   * no-arg authentic/integrity -> YES (authentic)
//   * no-arg tamper              -> NO
// The patch pass is synchronous and cheap; the full method listing is deferred so it cannot stall
// launch. The server-facing AUTHENTIC/UNAUTHENTIC report is separate from these local selectors.
#import <Foundation/Foundation.h>
#import <mach-o/dyld.h>
#import <objc/runtime.h>
#import <os/log.h>
#include <stdlib.h>
#include <string.h>

static BOOL am_quago_no(id self, SEL _cmd) {
    (void)self;
    (void)_cmd;
    return NO;
}

static BOOL am_quago_yes(id self, SEL _cmd) {
    (void)self;
    (void)_cmd;
    return YES;
}

static void am_quago_log(const char *text) {
    os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", text);
}

static BOOL am_quago_no_arg_bool(Method method) {
    if (method_getNumberOfArguments(method) != 2) return NO;
    const char *types = method_getTypeEncoding(method);
    return types && (types[0] == 'c' || types[0] == 'B');
}

static void am_quago_enumerate(void) {
    int count = objc_getClassList(NULL, 0);
    if (count <= 0) return;
    Class *classes = (__unsafe_unretained Class *)malloc(sizeof(Class) * (size_t)count);
    if (!classes) return;
    count = objc_getClassList(classes, count);
    int logged = 0;
    char line[256];
    for (int i = 0; i < count && logged < 400; i++) {
        const char *class_name = class_getName(classes[i]);
        if (!class_name || !strstr(class_name, "Quago")) continue;
        for (int meta = 0; meta < 2 && logged < 400; meta++) {
            Class owner = meta ? object_getClass(classes[i]) : classes[i];
            unsigned int method_count = 0;
            Method *methods = class_copyMethodList(owner, &method_count);
            for (unsigned int m = 0; m < method_count && logged < 400; m++) {
                const char *selector = sel_getName(method_getName(methods[m]));
                if (!selector) continue;
                snprintf(line, sizeof line, "quago: %s%s %s", class_name, meta ? "(+)" : "", selector);
                am_quago_log(line);
                logged++;
            }
            free(methods);
        }
    }
    free((void *)classes);
    snprintf(line, sizeof line, "quago: methods logged=%d", logged);
    am_quago_log(line);
}

static void am_quago_patch(void) {
    static BOOL done;
    if (done) return;
    if (!objc_getClass("QuagoManager")) return;
    done = YES;
    am_quago_log("quago: QuagoManager found");
    int count = objc_getClassList(NULL, 0);
    if (count <= 0) return;
    Class *classes = (__unsafe_unretained Class *)malloc(sizeof(Class) * (size_t)count);
    if (!classes) return;
    count = objc_getClassList(classes, count);
    SEL jailbreak = sel_registerName("isJailbrokenWithSkipAdvancedJailbreakValidation:");
    int patched = 0;
    char line[256];
    for (int i = 0; i < count; i++) {
        const char *class_name = class_getName(classes[i]);
        // The jailbreak selector can live on any class; patch it wherever it is implemented.
        Method method = class_getInstanceMethod(classes[i], jailbreak);
        if (method) {
            method_setImplementation(method, (IMP)am_quago_no);
            patched++;
        }
        Method class_method = class_getClassMethod(classes[i], jailbreak);
        if (class_method) {
            method_setImplementation(class_method, (IMP)am_quago_no);
            patched++;
        }
        if (!class_name || !strstr(class_name, "Quago")) continue;
        for (int meta = 0; meta < 2; meta++) {
            Class owner = meta ? object_getClass(classes[i]) : classes[i];
            unsigned int method_count = 0;
            Method *methods = class_copyMethodList(owner, &method_count);
            for (unsigned int m = 0; m < method_count; m++) {
                const char *selector = sel_getName(method_getName(methods[m]));
                if (!selector) continue;
                if (strstr(selector, "isJailbroken")) {
                    method_setImplementation(methods[m], (IMP)am_quago_no);
                    patched++;
                } else if (am_quago_no_arg_bool(methods[m]) &&
                           (strstr(selector, "authentic") || strstr(selector, "Authentic") ||
                            strstr(selector, "integrity") || strstr(selector, "Integrity"))) {
                    method_setImplementation(methods[m], (IMP)am_quago_yes);
                    patched++;
                } else if (am_quago_no_arg_bool(methods[m]) &&
                           (strstr(selector, "tamper") || strstr(selector, "Tamper"))) {
                    method_setImplementation(methods[m], (IMP)am_quago_no);
                    patched++;
                } else {
                    continue;
                }
                snprintf(line, sizeof line, "quago: patched %s%s %s", class_name, meta ? "(+)" : "", selector);
                am_quago_log(line);
            }
            free(methods);
        }
    }
    free((void *)classes);
    snprintf(line, sizeof line, "quago: selectors patched=%d", patched);
    am_quago_log(line);
    dispatch_async(dispatch_get_main_queue(), ^{
        am_quago_enumerate();
    });
}

static void am_quago_image(const struct mach_header *header, intptr_t slide) {
    (void)header;
    (void)slide;
    am_quago_patch();
}

__attribute__((constructor)) static void am_quago_init(void) {
    am_quago_log("quago: install");
    int count = objc_getClassList(NULL, 0);
    int quago = 0;
    if (count > 0) {
        Class *classes = (__unsafe_unretained Class *)malloc(sizeof(Class) * (size_t)count);
        if (classes) {
            count = objc_getClassList(classes, count);
            for (int i = 0; i < count; i++) {
                const char *name = class_getName(classes[i]);
                if (name && strstr(name, "Quago")) quago++;
            }
            free((void *)classes);
        }
    }
    char line[64];
    snprintf(line, sizeof line, "quago: classes=%d", quago);
    am_quago_log(line);
    _dyld_register_func_for_add_image(am_quago_image);
    am_quago_patch();
}
