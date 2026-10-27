// Sideload compatibility for re-signed personal builds.
//
// A re-signed app runs under another team ID and without the original iCloud and Game Center
// entitlements. Calls that depend on them can stall a game's loading screen forever (iCloud save
// setup, Game Center sign-in that never answers on iOS 18+, keychain items in the original
// team's access group, an iOS 26 age-range request that never returns). These hooks make those
// services answer "unavailable" right away, so the game takes its own fallback, as it does when
// iCloud or Game Center is switched off or Apple's age service fails.
// They unlock nothing: they only replace answers the re-signed copy can never get.
//
// Everything here changes data, never code: Objective-C method tables and the app's own import
// pointers (fishhook-style rebinding). On a phone without JIT, iOS kills an app as soon as it runs
// a code page that was patched (CODESIGNING "Invalid Page"), and ElleKit's MSHookFunction patches
// code, so this file must not use it.

#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <string.h>
#include "AMImportBindings.c"  // one shared walker; existing mod Makefiles need no extra source
#if __has_include("AMDetectionProfile.gen.h")
#define AM_DETECTION_ENABLED 1
#include "AMDetection.m"
#else
#define AM_DETECTION_ENABLED 0
#endif

// Each part can be left out at build time (-DAM_COMPAT_KEYCHAIN=0 and so on) to find which one a
// game doesn't get along with. All are on by default.
#ifndef AM_COMPAT_KEYCHAIN
#define AM_COMPAT_KEYCHAIN 1
#endif
#ifndef AM_COMPAT_ICLOUD
#define AM_COMPAT_ICLOUD 1
#endif
#ifndef AM_COMPAT_GAME_CENTER
#define AM_COMPAT_GAME_CENTER 1
#endif
#ifndef AM_COMPAT_AGE
#define AM_COMPAT_AGE 1
#endif

// MARK: keychain: drop the original team's access group, so items use this copy's own group

static NSDictionary *am_without_access_group(CFDictionaryRef dictionary) {
    if (!dictionary || CFGetTypeID(dictionary) != CFDictionaryGetTypeID() ||
        !CFDictionaryContainsKey(dictionary, kSecAttrAccessGroup)) return nil;
    NSMutableDictionary *copy = [(__bridge NSDictionary *)dictionary mutableCopy];
    [copy removeObjectForKey:(__bridge id)kSecAttrAccessGroup];
    return copy;
}

static OSStatus am_SecItemAdd(CFDictionaryRef attributes, CFTypeRef *result) {
    NSDictionary *fixed = am_without_access_group(attributes);
    return SecItemAdd(fixed ? (__bridge CFDictionaryRef)fixed : attributes, result);
}

static OSStatus am_SecItemCopyMatching(CFDictionaryRef query, CFTypeRef *result) {
    NSDictionary *fixed = am_without_access_group(query);
    return SecItemCopyMatching(fixed ? (__bridge CFDictionaryRef)fixed : query, result);
}

static OSStatus am_SecItemUpdate(CFDictionaryRef query, CFDictionaryRef changes) {
    NSDictionary *fixed_query = am_without_access_group(query);
    NSDictionary *fixed_changes = am_without_access_group(changes);
    return SecItemUpdate(fixed_query ? (__bridge CFDictionaryRef)fixed_query : query,
                         fixed_changes ? (__bridge CFDictionaryRef)fixed_changes : changes);
}

static OSStatus am_SecItemDelete(CFDictionaryRef query) {
    NSDictionary *fixed = am_without_access_group(query);
    return SecItemDelete(fixed ? (__bridge CFDictionaryRef)fixed : query);
}

// The am_ functions above call the real SecItem functions through this file's own imports, which
// are never rebound (am_image_added skips this image).
static const struct { const char *name; void *replacement; } am_rebindings[] = {
    {"SecItemAdd", (void *)am_SecItemAdd},
    {"SecItemCopyMatching", (void *)am_SecItemCopyMatching},
    {"SecItemUpdate", (void *)am_SecItemUpdate},
    {"SecItemDelete", (void *)am_SecItemDelete},
};

static bool am_write_pointer(void **slot, void *expected, void *value, void *context) {
    (void)context;
    if (*slot != expected) return false;
    vm_address_t region = (vm_address_t)slot;
    vm_size_t size = 0;
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    vm_prot_t protection = VM_PROT_READ | VM_PROT_WRITE;
    kern_return_t found = vm_region_64(mach_task_self(), &region, &size, VM_REGION_BASIC_INFO_64,
                                       (vm_region_info_t)&info, &count, &object);
    if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
    if (found != KERN_SUCCESS || (uintptr_t)slot < region || sizeof(void *) > size ||
        (uintptr_t)slot - region > size - sizeof(void *) || (info.protection & VM_PROT_EXECUTE)) return false;
    protection = info.protection;
    if (!(protection & VM_PROT_WRITE)) {
        vm_address_t page = (vm_address_t)slot & ~(vm_address_t)(vm_page_size - 1);
        if (vm_protect(mach_task_self(), page, vm_page_size, FALSE, VM_PROT_READ | VM_PROT_WRITE) != KERN_SUCCESS &&
            vm_protect(mach_task_self(), page, vm_page_size, FALSE, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY) !=
                KERN_SUCCESS)
            return false;
        if (*slot != expected) {
            vm_protect(mach_task_self(), page, vm_page_size, FALSE, protection);
            return false;
        }
        *slot = value;
        if (vm_protect(mach_task_self(), page, vm_page_size, FALSE, protection) != KERN_SUCCESS) {
            *slot = expected;
            vm_protect(mach_task_self(), page, vm_page_size, FALSE, protection);
            return false;
        }
        return *slot == value;
    }
    *slot = value;
    return *slot == value;
}

// Points the image's import slots (__got, __la_symbol_ptr) for the SecItem functions at the
// replacements above, the way fishhook does.
static void am_rebind_image(const struct mach_header_64 *header, intptr_t slide) {
    AMImportBinding bindings[sizeof am_rebindings / sizeof *am_rebindings];
    size_t count=AM_COMPAT_KEYCHAIN ? sizeof bindings / sizeof *bindings : 0;
    for (size_t i=0;i<count;i++)
        bindings[i]=(AMImportBinding){am_rebindings[i].name,NULL,am_rebindings[i].replacement,NULL};
#if AM_DETECTION_ENABLED
    AMDetectionBindImage(header,sizeof *header+header->sizeofcmds,slide,bindings,count,am_write_pointer,NULL);
#else
    if (count)
    AMImportBindImage(header,sizeof *header+header->sizeofcmds,slide,bindings,
                     count,am_write_pointer,NULL);
#endif
}

// MARK: iCloud: report no account and no container, so saves stay local

static id am_no_identity_token(id self, SEL _cmd) {
    (void)self; (void)_cmd;
    return nil;
}

static NSURL *am_no_ubiquity_container(id self, SEL _cmd, NSString *identifier) {
    (void)self; (void)_cmd; (void)identifier;
    return nil;
}

// MARK: Game Center: answer "not signed in" at once instead of never

static NSError *am_game_center_error(void) {
    // GKErrorNotAuthenticated (6) in GKErrorDomain.
    return [NSError errorWithDomain:@"GKErrorDomain" code:6
                           userInfo:@{NSLocalizedDescriptionKey: @"Game Center isn't available in this copy of the app."}];
}

static void am_set_authenticate_handler(id self, SEL _cmd, id handler) {
    (void)self; (void)_cmd;
    if (!handler) return;
    void (^callback)(id, NSError *) = handler;  // (UIViewController *viewController, NSError *error)
    dispatch_async(dispatch_get_main_queue(), ^{ callback(nil, am_game_center_error()); });
}

static void am_authenticate_with_completion(id self, SEL _cmd, id handler) {
    (void)self; (void)_cmd;
    if (!handler) return;
    void (^callback)(NSError *) = handler;
    dispatch_async(dispatch_get_main_queue(), ^{ callback(am_game_center_error()); });
}

static BOOL am_not_authenticated(id self, SEL _cmd) {
    (void)self; (void)_cmd;
    return NO;
}

// MARK: age range: answer "unavailable" at once (Essential Kit's age-compliance request)
//
// iOS 26 age-range requests run in Apple's age-attestation extension, which can't find a
// re-signed copy's app record (LaunchServices), so the request never answers and a game that
// waits for it before its main menu stays on the loading screen. Unity games built with Voxel
// Busters Essential Kit ask through +[AgeComplianceHelper requestInfoForAgeCompliance:in:
// completionHandler:]; answering with an error runs the game's own handling for an unavailable
// age service (Fallout Shelter 2.6.0, for one, then continues as it does when Apple's service fails).

static void am_age_unavailable(id self, SEL _cmd, id options, id presenter, id handler) {
    (void)self; (void)_cmd; (void)options; (void)presenter;
    if (!handler) return;
    void (^completion)(id, NSError *) = handler;  // (InfoForAgeCompliance *info, NSError *error)
    NSError *error = [NSError errorWithDomain:@"AccretionSideloadCompat" code:1
                                     userInfo:@{NSLocalizedDescriptionKey: @"Age range isn't available in this copy of the app."}];
    NSLog(@"[Accretion] Age range request answered as unavailable (re-signed copy)");
    dispatch_async(dispatch_get_main_queue(), ^{ completion(nil, error); });
}

// Replaces the method on this class only (an inherited one is overridden, not changed for the superclass).
static BOOL am_replace_method(Class cls, SEL selector, IMP replacement) {
    Method method = cls ? class_getInstanceMethod(cls, selector) : NULL;
    if (!method) return NO;
    class_replaceMethod(cls, selector, replacement, method_getTypeEncoding(method));
    return YES;
}

static void am_hook_age_helper(void) {
    static BOOL hooked;
    if (hooked) return;
    Class helper = objc_getClass("_TtC14UnityFramework19AgeComplianceHelper");
    if (!helper) return;
    hooked = am_replace_method(object_getClass(helper), NSSelectorFromString(@"requestInfoForAgeCompliance:in:completionHandler:"),
                               (IMP)am_age_unavailable);
    if (hooked) NSLog(@"[Accretion] Sideload compatibility: age range request answered locally");
}

// MARK: setup

static char am_bundle_path[1024];
static const void *am_own_header;

// Runs for every image already loaded and every one loaded later (Unity loads UnityFramework
// from main(), after this file's constructor).
static void am_image_added(const struct mach_header *header, intptr_t slide) {
    Dl_info info;
    if (header == am_own_header || !am_bundle_path[0] || !dladdr(header, &info) || !info.dli_fname) return;
    if (strncmp(info.dli_fname, am_bundle_path, strlen(am_bundle_path)) != 0) return;  // the app's own images only
    if (AM_COMPAT_KEYCHAIN || AM_DETECTION_ENABLED) am_rebind_image((const struct mach_header_64 *)header, slide);
    // Look the class up outside dyld's callback, once the main queue runs (before the game's first frame).
    if (AM_COMPAT_AGE) dispatch_async(dispatch_get_main_queue(), ^{ am_hook_age_helper(); });
}

#ifndef AM_COMPAT_AUTOSTART
#define AM_COMPAT_AUTOSTART 1
#endif
#if AM_COMPAT_AUTOSTART
__attribute__((constructor))
#endif
static void am_sideload_compat_init(void) {
    @autoreleasepool {
        Dl_info own;
        if (dladdr((const void *)am_sideload_compat_init, &own)) am_own_header = own.dli_fbase;
        const char *bundle = NSBundle.mainBundle.bundlePath.fileSystemRepresentation;
        if (bundle) snprintf(am_bundle_path, sizeof am_bundle_path, "%s/", bundle);
#if AM_DETECTION_ENABLED
        AMDetectionStart();
#endif
        _dyld_register_func_for_add_image(am_image_added);

        if (AM_COMPAT_ICLOUD) {
            Class file_manager = [NSFileManager class];
            am_replace_method(file_manager, @selector(ubiquityIdentityToken), (IMP)am_no_identity_token);
            am_replace_method(file_manager, NSSelectorFromString(@"URLForUbiquityContainerIdentifier:"),
                              (IMP)am_no_ubiquity_container);
        }

        Class player = nil;
        if (AM_COMPAT_GAME_CENTER) {
            // GameKit is linked by the game; load it by path in case it isn't mapped yet.
            player = objc_getClass("GKLocalPlayer");
            if (!player && dlopen("/System/Library/Frameworks/GameKit.framework/GameKit", RTLD_LAZY))
                player = objc_getClass("GKLocalPlayer");
            am_replace_method(player, NSSelectorFromString(@"setAuthenticateHandler:"), (IMP)am_set_authenticate_handler);
            am_replace_method(player, NSSelectorFromString(@"authenticateWithCompletionHandler:"),
                              (IMP)am_authenticate_with_completion);
            am_replace_method(player, NSSelectorFromString(@"isAuthenticated"), (IMP)am_not_authenticated);
        }
        NSLog(@"[Accretion] Sideload compatibility active: keychain group %s, iCloud %s, Game Center %s, age range %s",
              AM_COMPAT_KEYCHAIN ? "on" : "off", AM_COMPAT_ICLOUD ? "on" : "off",
              !AM_COMPAT_GAME_CENTER ? "off" : player ? "on" : "off (GameKit not loaded)", AM_COMPAT_AGE ? "on" : "off");
    }
}
