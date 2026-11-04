// Survivor: Ace login fallback probe: the game's platform-SDK login fails on a re-signed copy and shows
// LoginViewModule.ShowSDKLoginRetryPopup. The login view also owns a direct-login path
// (LoginViewModule_SDKLogin.TriggerDirectLogin) and a custom/guest login (LoginViewModule_CustomLogin).
// Try the direct path shortly after the login view appears, once, and log what happened.
#import <Foundation/Foundation.h>
#import <os/log.h>
#import "AMIl2cppData.h"

static void am_survivor_log(const char *text) {
    os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", text);
}

static void am_survivor_login_try(void) {
    static BOOL tried;
    if (tried) return;
    if (!AMIl2cppClass("LoginViewModule")) return;          // VM/class not ready yet
    if (!AMIl2cppClass("LoginViewModule_SDKLogin")) return;
    void *view = AMIl2cppFindObject("LoginViewModule");
    if (!view) return;
    tried = YES;
    am_survivor_log("login: LoginViewModule found");
    void *sdk = AMIl2cppField(view, "LoginViewModule", "m_sdkLogin");
    if (sdk) {
        bool ok = AMIl2cppCall(sdk, "LoginViewModule_SDKLogin", "TriggerDirectLogin", 0, NULL, NULL);
        char line[96];
        snprintf(line, sizeof line, "login: TriggerDirectLogin ok=%d", ok);
        am_survivor_log(line);
    } else {
        am_survivor_log("login: no m_sdkLogin");
    }
}

__attribute__((constructor)) static void am_survivor_login_init(void) {
    for (int i = 0; i < 20; i++) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(8 + i * 2) * NSEC_PER_SEC),
                       dispatch_get_main_queue(), ^{
                           am_survivor_login_try();
                       });
    }
}
