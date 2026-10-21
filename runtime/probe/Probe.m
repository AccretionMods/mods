// Runtime probe: checks the IL2CPP runtime in any Unity game on a real phone, before a mod uses it.
//
// Inject AMIl2cppProbe.dylib into a Unity game (Accretion: Sign -> Tweaks -> add the .dylib), install, open
// the game and wait. After about 20 s an alert shows the hook mode (code hooks with JIT, pointer hooks
// without), each probe hook and how often each was called; Copy puts the text on the clipboard.
//
// The three hooks (hooks.json) exist in every Unity game and only count calls, then call the original:
//   UnityEngine.Object.ToString     virtual: pointer hooks catch it, through every subclass's vtable
//   Application.get_targetFrameRate static: pointer hooks catch it only through reflection
//   Time.get_deltaTime              static, called directly by game code: only code hooks (JIT) catch it

#import <UIKit/UIKit.h>
#import <stdatomic.h>

#import "runtime/AMIl2cpp.h"

static atomic_int toStringCalls, frameRateCalls, deltaTimeCalls;

static void *(*orig_ToString)(void *self, const void *method);
static void *rep_ToString(void *self, const void *method) {
    atomic_fetch_add(&toStringCalls, 1);
    return orig_ToString(self, method);
}

static int (*orig_get_targetFrameRate)(const void *method);
static int rep_get_targetFrameRate(const void *method) {
    atomic_fetch_add(&frameRateCalls, 1);
    return orig_get_targetFrameRate(method);
}

static float (*orig_get_deltaTime)(const void *method);
static float rep_get_deltaTime(const void *method) {
    atomic_fetch_add(&deltaTimeCalls, 1);
    return orig_get_deltaTime(method);
}

static UIViewController *topController(void) {
    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
        if (![scene isKindOfClass:UIWindowScene.class]) continue;
        for (UIWindow *window in ((UIWindowScene *)scene).windows) {
            if (!window.isKeyWindow) continue;
            UIViewController *vc = window.rootViewController;
            while (vc.presentedViewController) vc = vc.presentedViewController;
            return vc;
        }
    }
    return nil;
}

static void showReport(NSString *report) {
    NSString *text = [NSString stringWithFormat:@"%@\n\ncalls: ToString %d, get_targetFrameRate %d, get_deltaTime %d",
                      report, atomic_load(&toStringCalls), atomic_load(&frameRateCalls), atomic_load(&deltaTimeCalls)];
    NSLog(@"[AccretionMods] probe: %@", text);
    UIViewController *vc = topController();
    if (!vc) return;
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Accretion Mods runtime probe"
                                                                   message:text
                                                            preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Copy" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        UIPasteboard.generalPasteboard.string = text;
    }]];
    [alert addAction:[UIAlertAction actionWithTitle:@"Close" style:UIAlertActionStyleCancel handler:nil]];
    [vc presentViewController:alert animated:YES completion:nil];
}

__attribute__((constructor)) static void probeStart(void) {
    AMIl2cppRegister("UnityEngine.Object.ToString", (void *)rep_ToString, (void **)&orig_ToString);
    AMIl2cppRegister("UnityEngine.Application.get_targetFrameRate", (void *)rep_get_targetFrameRate,
                     (void **)&orig_get_targetFrameRate);
    AMIl2cppRegister("UnityEngine.Time.get_deltaTime", (void *)rep_get_deltaTime, (void **)&orig_get_deltaTime);
    AMIl2cppSetReportHandler(^(NSString *report) {
        // give the game time to call the hooked methods before counting
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(15 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            showReport(report);
        });
    });
    AMIl2cppStart();
}
