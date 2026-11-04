// Quago (Pokemon GO) local jailbreak verdict: the SDK exposes
// -[<Quago class> isJailbrokenWithSkipAdvancedJailbreakValidation:] returning BOOL. Answer NO wherever the
// selector is implemented, before UnityFramework's initializers run. The AUTHENTIC/UNAUTHENTIC report is a
// separate server-facing check; this only clears the local jailbreak flag.
#import <Foundation/Foundation.h>
#import <mach-o/dyld.h>
#import <objc/runtime.h>
#import <os/log.h>
#include <stdlib.h>

static BOOL am_quago_not_jailbroken(id self, SEL _cmd) {
    (void)self;
    (void)_cmd;
    return NO;
}

static void am_quago_log(const char *text) {
    os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", text);
}

static void am_quago_patch(void) {
    static BOOL done;
    if (done) return;
    done = YES;
    int count = objc_getClassList(NULL, 0);
    if (count <= 0) return;
    Class *classes = (__unsafe_unretained Class *)malloc(sizeof(Class) * (size_t)count);
    if (!classes) return;
    count = objc_getClassList(classes, count);
    SEL selector = sel_registerName("isJailbrokenWithSkipAdvancedJailbreakValidation:");
    int patched = 0;
    for (int i = 0; i < count; i++) {
        Class cls = classes[i];
        Method method = class_getInstanceMethod(cls, selector);
        if (method) {
            method_setImplementation(method, (IMP)am_quago_not_jailbroken);
            patched++;
        }
        Method class_method = class_getClassMethod(cls, selector);
        if (class_method) {
            method_setImplementation(class_method, (IMP)am_quago_not_jailbroken);
            patched++;
        }
    }
    free((void *)classes);
    if (patched) {
        char line[96];
        snprintf(line, sizeof line, "quago: jailbreak selector patched in %d places", patched);
        am_quago_log(line);
    }
}

static void am_quago_image(const struct mach_header *header, intptr_t slide) {
    (void)header;
    (void)slide;
    am_quago_patch();
}

__attribute__((constructor)) static void am_quago_init(void) {
    _dyld_register_func_for_add_image(am_quago_image);
    am_quago_patch();
}
