// Live pins: switches that change without rebuilding. `dev.py push <mod>` (Studio: Push switches) copies the mod's
// tweaks.json into the app as Documents/live_tweaks.json; this reads it at launch and again whenever it changes
// (checked every 2 s), adds menu items for switches the build doesn't have, and runs their pins on each tick the
// same way the generated code does (field writes, setter calls with read-back, timed calls; the menu's status dot).
// A compiled switch whose key is in the file steps aside (AMLivePinsHas), so the file always wins.
// Included once, by AMTweaks.gen.m (static functions: no Makefile change). Data calls only: no JIT needed.

#import <Foundation/Foundation.h>
#import "AMIl2cppData.h"
#import "../menu/AMMenu.h"

static NSArray<NSDictionary *> *sLiveTweaks;
static NSSet<NSString *> *sLiveKeys;
static NSDate *sLiveStamp;
static CFAbsoluteTime sLiveCheckedAt;
static NSMutableDictionary<NSString *, NSNumber *> *sLiveState;  // peaks, multiply bases, call timers

static NSString *AMLivePath(void) {
    NSString *docs = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
    return [docs stringByAppendingPathComponent:@"live_tweaks.json"];
}

static BOOL AMLivePinsHas(const char *key) {
    return key && [sLiveKeys containsObject:@(key)];
}

static double AMLiveRead(void *p, NSString *t) {
    if ([t isEqualToString:@"int32_t"]) return *(int32_t *)p;
    if ([t isEqualToString:@"int64_t"]) return (double)*(int64_t *)p;
    if ([t isEqualToString:@"float"]) return *(float *)p;
    if ([t isEqualToString:@"double"]) return *(double *)p;
    return *(bool *)p ? 1 : 0;
}

static void AMLiveWrite(void *p, NSString *t, double v) {
    if ([t isEqualToString:@"int32_t"]) *(int32_t *)p = (int32_t)v;
    else if ([t isEqualToString:@"int64_t"]) *(int64_t *)p = (int64_t)v;
    else if ([t isEqualToString:@"float"]) *(float *)p = (float)v;
    else if ([t isEqualToString:@"double"]) *(double *)p = v;
    else *(bool *)p = v != 0;
}

// The live objects a pin acts on: "Class[]" every one, "Class.static" the singleton, "Class" the first found.
static NSArray<NSValue *> *AMLiveObjects(NSString *object) {
    NSMutableArray *out = [NSMutableArray new];
    if ([object hasSuffix:@"[]"]) {
        int32_t n = 0;
        void *arr = AMIl2cppFindObjects([object substringToIndex:object.length - 2].UTF8String, &n);
        for (int32_t i = 0; i < n; i++) { void *o = AMIl2cppArrayAt(arr, i); if (o) [out addObject:[NSValue valueWithPointer:o]]; }
    } else {
        NSRange dot = [object rangeOfString:@"."];
        void *o = dot.location != NSNotFound
            ? AMIl2cppStaticObject([object substringToIndex:dot.location].UTF8String, [object substringFromIndex:dot.location + 1].UTF8String)
            : AMIl2cppFindObject(object.UTF8String);
        if (o) [out addObject:[NSValue valueWithPointer:o]];
    }
    return out;
}

static BOOL AMLiveGet(void *obj, NSString *cls, NSString *getter, NSString *t, double *out) {
    void *boxed = NULL;
    if (!AMIl2cppCall(obj, cls.UTF8String, getter.UTF8String, 0, NULL, &boxed)) return NO;
    void *p = AMIl2cppUnbox(boxed);
    if (!p) return NO;
    *out = AMLiveRead(p, t);
    return YES;
}

static BOOL AMLiveSet(void *obj, NSString *cls, NSString *setter, NSString *t, double v) {
    union { int32_t i32; int64_t i64; float f; double d; bool b; } arg = {0};
    AMLiveWrite(&arg, t, v);
    void *argv[1] = {&arg};
    return AMIl2cppCall(obj, cls.UTF8String, setter.UTF8String, 1, argv, NULL);
}

// The number a pin writes on obj, or NO when it can't be known yet.
static BOOL AMLiveValue(NSDictionary *pin, NSString *stateKey, void *obj, NSString *cls, NSString *t, double slider, double *out) {
    id v = pin[@"value"];
    if ([v isKindOfClass:NSNumber.class]) { *out = [v doubleValue]; return YES; }
    if ([v isEqual:@"slider"]) { *out = slider; return YES; }
    if ([v isEqual:@"peak"]) { NSNumber *p = sLiveState[stateKey]; if (!p) return NO; *out = p.doubleValue; return YES; }
    if ([v isKindOfClass:NSDictionary.class] && v[@"getter"]) return AMLiveGet(obj, cls, v[@"getter"], t, out);
    if ([v isKindOfClass:NSDictionary.class] && v[@"field"]) {
        void *p = AMIl2cppField(obj, cls.UTF8String, [v[@"field"] UTF8String]);
        if (!p) return NO;
        *out = AMLiveRead(p, t);
        return YES;
    }
    return NO;
}

static void AMLiveRunTweak(NSDictionary *tweak) {
    NSString *key = tweak[@"key"];
    BOOL slider = [tweak[@"kind"] isEqual:@"slider"];
    double sliderValue = slider ? [AMMenu floatForKey:key] : 0;
    BOOL on = slider ? sliderValue != [tweak[@"default"] ?: tweak[@"min"] doubleValue] : [AMMenu boolForKey:key];
    if (!on) return;
    BOOL tried = NO, reached = NO;
    NSString *why = nil;
    NSString *waiting = tweak[@"waiting"] ?: @"Waiting for the game: load a save first";
    NSArray *pins = tweak[@"pins"];
    for (NSUInteger i = 0; i < pins.count; i++) {
        NSDictionary *pin = pins[i];
        NSString *stateKey = [NSString stringWithFormat:@"%@/%lu", key, (unsigned long)i];
        double every = [pin[@"every"] doubleValue];
        if (pin[@"call"] && every > 0) {
            CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
            NSString *at = [stateKey stringByAppendingString:@"/at"];
            if (now - sLiveState[at].doubleValue < every) continue;
            sLiveState[at] = @(now);
        }
        tried = YES;
        if (!why) why = waiting;
        NSString *object = pin[@"object"];
        NSString *cls = [object hasSuffix:@"[]"] ? [object substringToIndex:object.length - 2] : [object componentsSeparatedByString:@"."].firstObject;
        for (NSValue *box in AMLiveObjects(object)) {
            void *obj = box.pointerValue;
            NSString *tcls = cls;
            if (pin[@"via"]) {
                void *via = NULL;
                if (!AMIl2cppCall(obj, cls.UTF8String, [pin[@"via"] UTF8String], 0, NULL, &via) || !via) continue;
                obj = via;
            }
            if (pin[@"class"]) tcls = pin[@"class"];
            NSString *member = pin[@"field"] ?: pin[@"setter"] ?: pin[@"call"];
            why = [NSString stringWithFormat:@"Needs an update for this game version (%@.%@): run am check", tcls, member];
            NSString *t = pin[@"ctype"] ?: @"int32_t";
            if (pin[@"call"]) {
                NSArray *args = pin[@"args"] ?: @[], *types = pin[@"arg_ctypes"] ?: @[];
                union { int32_t i32; int64_t i64; float f; double d; bool b; } vals[8] = {0};
                void *argv[8];
                for (NSUInteger a = 0; a < args.count && a < 8; a++) {
                    AMLiveWrite(&vals[a], a < types.count ? types[a] : @"int32_t", [args[a] doubleValue]);
                    argv[a] = &vals[a];
                }
                if (AMIl2cppCall(obj, tcls.UTF8String, [pin[@"call"] UTF8String], (int)args.count, argv, NULL)) reached = YES;
                continue;
            }
            NSString *when = pin[@"when"] ?: @"always";
            double cur = 0, value = 0;
            void *p = NULL;
            NSString *getter = nil;
            if (pin[@"field"]) {
                p = AMIl2cppField(obj, tcls.UTF8String, [pin[@"field"] UTF8String]);
                if (!p) continue;
                cur = AMLiveRead(p, t);
            } else if (![when isEqual:@"always"] || [pin[@"value"] isEqual:@"peak"]) {
                getter = [@"get_" stringByAppendingString:[pin[@"setter"] substringFromIndex:4]];
                if (!AMLiveGet(obj, tcls, getter, t, &cur)) continue;
            }
            reached = YES;
            if ([pin[@"value"] isEqual:@"peak"] && (!sLiveState[stateKey] || cur > sLiveState[stateKey].doubleValue)) sLiveState[stateKey] = @(cur);
            if (!AMLiveValue(pin, stateKey, obj, tcls, t, sliderValue, &value)) continue;
            if ([pin[@"op"] isEqual:@"multiply"] && p) {  // field = base * slider; the game recalculating it gives a new base
                NSString *setKey = [stateKey stringByAppendingString:@"/set"], *baseKey = [stateKey stringByAppendingString:@"/base"];
                if (!sLiveState[setKey] || cur != sLiveState[setKey].doubleValue) sLiveState[baseKey] = @(cur);
                double want = sLiveState[baseKey].doubleValue * value;
                if (want > 0 && cur != want) AMLiveWrite(p, t, want);
                sLiveState[setKey] = @(want);
                continue;
            }
            double limit = pin[@"threshold"] ? [pin[@"threshold"] doubleValue] : value;
            BOOL go = [when isEqual:@"lower"] ? cur < limit : [when isEqual:@"higher"] ? cur > limit : YES;
            if (!go) continue;
            if (p) {
                AMLiveWrite(p, t, value);
            } else if (AMLiveSet(obj, tcls, pin[@"setter"], t, value) && getter) {
                double back = 0;  // read back: a setter that ignores the value turns the dot red
                if (AMLiveGet(obj, tcls, getter, t, &back) && back != value) {
                    reached = NO;
                    why = [NSString stringWithFormat:@"%@.%@ didn't take the value (the game clamps or recomputes it)", tcls, pin[@"setter"]];
                }
            }
        }
    }
    if (tried) AMMenuStatus(key.UTF8String, reached, reached ? NULL : why.UTF8String);
}

// Reads live_tweaks.json when it changed; new switches and sliders join the menu (in their sections).
static void AMLivePinsLoad(void) {
    NSString *path = AMLivePath();
    NSDate *stamp = [NSFileManager.defaultManager attributesOfItemAtPath:path error:NULL].fileModificationDate;
    if (!stamp) { if (sLiveKeys.count) { sLiveTweaks = @[]; sLiveKeys = [NSSet set]; AMIl2cppLog(@"live pins: file removed, the build's own switches again"); } sLiveStamp = nil; return; }
    if (sLiveStamp && [stamp isEqualToDate:sLiveStamp]) return;
    sLiveStamp = stamp;
    NSData *data = [NSData dataWithContentsOfFile:path];
    id json = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
    NSArray *tweaks = [json isKindOfClass:NSDictionary.class] ? json[@"tweaks"] : nil;
    if (![tweaks isKindOfClass:NSArray.class]) { AMIl2cppLog(@"live pins: %@ is not a tweaks file", path); return; }
    NSMutableArray *withPins = [NSMutableArray new];
    NSMutableSet *keys = [NSMutableSet new];
    AMMenu *menu = AMMenu.shared;
    for (NSDictionary *t in tweaks) {
        if (![t isKindOfClass:NSDictionary.class] || ![t[@"key"] isKindOfClass:NSString.class] || ![t[@"pins"] count]) continue;
        [withPins addObject:t];
        [keys addObject:t[@"key"]];
        if (![menu hasItem:t[@"key"]]) {  // a switch the build doesn't have yet
            if (t[@"section"]) [menu addSection:t[@"section"]];
            NSString *title = t[@"title"] ?: t[@"key"];
            if ([t[@"kind"] isEqual:@"slider"]) {
                [menu addSlider:t[@"key"] title:title min:[t[@"min"] floatValue] max:[t[@"max"] floatValue]
                   defaultValue:[t[@"default"] ?: t[@"min"] floatValue] onChange:nil];
            } else {
                [menu addSwitch:t[@"key"] title:title defaultValue:[t[@"default"] boolValue] onChange:nil];
            }
        }
    }
    sLiveTweaks = withPins;
    sLiveKeys = keys;
    if (!sLiveState) sLiveState = [NSMutableDictionary new];
    AMIl2cppLog(@"live pins: %lu switches from live_tweaks.json", (unsigned long)withPins.count);
}

// Once per tick (AMTweaksTick): reload when the file changed, then run every live switch that is on.
static void AMLivePinsTick(void) {
    CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
    if (now - sLiveCheckedAt >= 2) { sLiveCheckedAt = now; AMLivePinsLoad(); }
    for (NSDictionary *t in sLiveTweaks) AMLiveRunTweak(t);
}
