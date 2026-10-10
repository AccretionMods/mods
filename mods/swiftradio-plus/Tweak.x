// Swift Radio+: two app-wide switches that prove the build, the injection and the menu work.

#import <UIKit/UIKit.h>
#import "menu/AMMenu.h"

static void applyDarkMode(void) {
    UIUserInterfaceStyle style = [AMMenu boolForKey:@"forceDark"] ? UIUserInterfaceStyleDark : UIUserInterfaceStyleUnspecified;
    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
        if (![scene isKindOfClass:UIWindowScene.class]) continue;
        for (UIWindow *window in ((UIWindowScene *)scene).windows) {
            window.overrideUserInterfaceStyle = style;
        }
    }
}

static void applyKeepAwake(void) {
    UIApplication.sharedApplication.idleTimerDisabled = [AMMenu boolForKey:@"keepAwake"];
}

%hook UIWindow

- (void)makeKeyAndVisible {
    %orig;
    applyDarkMode();
}

%end

%ctor {
    AMMenu *menu = AMMenu.shared;
    menu.title = @"Swift Radio+";
    [menu addSwitch:@"forceDark" title:@"Force dark mode" defaultValue:NO onChange:^(BOOL on) {
        applyDarkMode();
    }];
    [menu addSwitch:@"keepAwake" title:@"Keep screen awake" defaultValue:NO onChange:^(BOOL on) {
        applyKeepAwake();
    }];
    [NSNotificationCenter.defaultCenter addObserverForName:UIApplicationDidBecomeActiveNotification
                                                    object:nil
                                                     queue:NSOperationQueue.mainQueue
                                                usingBlock:^(NSNotification *note) {
        applyDarkMode();
        applyKeepAwake();
    }];
    [menu install];
}
