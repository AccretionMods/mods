// Pokémon GO+: a personal Accretion mod for Pokémon GO (com.nianticlabs.pokemongo).
//
// The menu items and their hooks live in tweaks.json (scripts/dev.py add pokemongo-plus <Class.method> ...) and are
// generated into AMTweaks.gen.m. This file holds what the generator can't express: hook_<ident>() bodies for
// "custom" hooks, and the ticker below for no-JIT switches that write the game's data (runtime/AMIl2cppData.h).
// Without JIT, hooks catch only virtual methods, Unity callbacks and delegates; a plain method the game calls
// directly needs a data path here instead (find the object, set the field or call the setter twice a second).

#import <UIKit/UIKit.h>
#import "menu/AMMenu.h"
#import "runtime/AMIl2cpp.h"
#import "runtime/AMIl2cppData.h"
#import "AMTweaks.gen.h"

// Runs twice a second once the runtime is up (main thread).
static void tick(void) {
    UIApplication.sharedApplication.idleTimerDisabled = am_keepAwake;
    AMTweaksTick();  // the pins from tweaks.json (dev.py pin ...): field writes and setter calls, no JIT needed
    // Hand-written no-JIT logic goes here, for example:
    // void *player = AMIl2cppStaticObject("Player", "instance");
    // if (am_godMode && player) *(float *)AMIl2cppField(player, "Player", "health") = 100;
}

%ctor {
    AMIl2cppDataSetLogTag(AMTweaksLogTag.UTF8String);
    AMTweaksRegister();
    AMIl2cppSetReportHandler(^(NSString *report) {
        AMIl2cppLog(@"%@", report);
        AMIl2cppEvery(0.5, ^{ tick(); });
        // again later, so a device-log capture started after launch still sees which hooks installed
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 20 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            AMIl2cppLog(@"report again: %@", report);
        });
    });
    AMIl2cppStart();
    [AMMenu.shared install];
}
