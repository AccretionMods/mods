// Live pins: switches that change without rebuilding. `dev.py push <mod>` (Studio: Push switches) copies the mod's
// tweaks.json into the app as Documents/live_tweaks.json; this reads it at launch and again whenever it changes
// (checked every 2 s), adds menu items for switches the build doesn't have, and runs their pins on each tick the
// same way the generated code does (field writes, setter calls with read-back, timed calls; the menu's status dot).
// A compiled switch whose key is in the file steps aside (AMLivePinsHas), so the file always wins.
// Like the generated code, a switch going off puts the game's own values back (restorable pins), and a slider
// shows the game's own value under its title. A top-level "test": {"id": "...", "on": [keys]} (am test) turns
// those switches on (sliders to a test value) and, when the id changes or the block goes, turns them off again.
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

// "Game value 2" under a slider (AMMenuNote); used by the generated code too.
static void AMNoteValue(const char *key, double v) {
    char text[48];
    snprintf(text, sizeof text, "Game value %.4g", v);
    AMMenuNote(key, text);
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
        NSRange dot = [object rangeOfString:@"." options:NSBackwardsSearch];  // "Core.Entities.Foo.instance"
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

// Same rule as gen_tweaks.restorable: a single object whose field or setter the pin forces; not floors, peaks,
// refills, calls or "restore": false.
static BOOL AMLiveRestorable(NSDictionary *tweak, NSDictionary *pin) {
    NSString *object = pin[@"object"];
    if ([object hasSuffix:@"[]"] || pin[@"call"] || [pin[@"value"] isEqual:@"peak"]) return NO;
    if ([pin[@"restore"] isEqual:@NO] || [tweak[@"restore"] isEqual:@NO] || [pin[@"value"] isKindOfClass:NSDictionary.class]) return NO;
    if (pin[@"setter"] && ![pin[@"setter"] hasPrefix:@"set_"]) return NO;
    return [(pin[@"when"] ?: @"always") isEqual:@"always"] || [pin[@"op"] isEqual:@"multiply"];
}

static NSString *AMLiveClass(NSString *object) {
    NSRange last = [object rangeOfString:@"." options:NSBackwardsSearch];
    return [object hasSuffix:@"[]"] ? [object substringToIndex:object.length - 2]
         : last.location != NSNotFound ? [object substringToIndex:last.location] : object;
}

// The object a pin writes (after "via"), or NULL. Single objects only (restore and notes).
static void *AMLiveTarget(NSDictionary *pin, NSString **tclsOut) {
    NSString *cls = AMLiveClass(pin[@"object"]);
    void *obj = AMLiveObjects(pin[@"object"]).firstObject.pointerValue;
    if (obj && pin[@"via"]) {
        void *via = NULL;
        obj = AMIl2cppCall(obj, cls.UTF8String, [pin[@"via"] UTF8String], 0, NULL, &via) ? via : NULL;
    }
    if (tclsOut) *tclsOut = pin[@"class"] ?: cls;
    return obj;
}

// The switch went off: the game's own value back on each restorable pin's object, if it still holds ours.
static void AMLiveRestore(NSDictionary *tweak) {
    NSString *key = tweak[@"key"];
    NSArray *pins = tweak[@"pins"];
    BOOL restored = NO;
    for (NSUInteger i = 0; i < pins.count; i++) {
        NSDictionary *pin = pins[i];
        NSString *sk = [NSString stringWithFormat:@"%@/%lu", key, (unsigned long)i];
        NSNumber *was = sLiveState[[sk stringByAppendingString:@"/obj"]], *orig = sLiveState[[sk stringByAppendingString:@"/orig"]];
        NSNumber *wrote = sLiveState[[sk stringByAppendingString:@"/wrote"]];
        for (NSString *suffix in @[@"/obj", @"/orig", @"/wrote", @"/set"]) [sLiveState removeObjectForKey:[sk stringByAppendingString:suffix]];
        if (!AMLiveRestorable(tweak, pin) || !was || !orig || !wrote) continue;
        NSString *tcls = nil, *t = pin[@"ctype"] ?: @"int32_t";
        void *obj = AMLiveTarget(pin, &tcls);
        if (!obj || (uintptr_t)obj != was.unsignedLongLongValue) continue;
        if (pin[@"field"]) {
            void *p = AMIl2cppField(obj, tcls.UTF8String, [pin[@"field"] UTF8String]);
            if (p && AMLiveRead(p, t) == wrote.doubleValue) { AMLiveWrite(p, t, orig.doubleValue); restored = YES; }
        } else {
            double cur = 0;
            NSString *getter = [@"get_" stringByAppendingString:[pin[@"setter"] substringFromIndex:4]];
            if (AMLiveGet(obj, tcls, getter, t, &cur) && cur == wrote.doubleValue && AMLiveSet(obj, tcls, pin[@"setter"], t, orig.doubleValue)) restored = YES;
        }
    }
    if (restored) AMIl2cppLog(@"pin %@ off: the game's values are back", key);
}

// "Game value 2" under a live slider (AMLivePinsTick, every 3 s). While it is on, the value from before it.
static void AMLiveNote(NSDictionary *tweak) {
    NSDictionary *pin = [tweak[@"pins"] firstObject];
    if (![tweak[@"kind"] isEqual:@"slider"] || ![pin isKindOfClass:NSDictionary.class] || pin[@"call"] || [pin[@"object"] hasSuffix:@"[]"]) return;
    NSString *key = tweak[@"key"], *sk = [key stringByAppendingString:@"/0"];
    if (sLiveState[[key stringByAppendingString:@"/on"]]) {
        NSNumber *v = [pin[@"op"] isEqual:@"multiply"] ? sLiveState[[sk stringByAppendingString:@"/base"]] : sLiveState[[sk stringByAppendingString:@"/orig"]];
        if (v) AMNoteValue(key.UTF8String, v.doubleValue);
        return;
    }
    NSString *tcls = nil, *t = pin[@"ctype"] ?: @"int32_t";
    void *obj = AMLiveTarget(pin, &tcls);
    if (!obj) return;
    double v = 0;
    if (pin[@"field"]) {
        void *p = AMIl2cppField(obj, tcls.UTF8String, [pin[@"field"] UTF8String]);
        if (!p) return;
        v = AMLiveRead(p, t);
    } else if (![pin[@"setter"] hasPrefix:@"set_"] || !AMLiveGet(obj, tcls, [@"get_" stringByAppendingString:[pin[@"setter"] substringFromIndex:4]], t, &v)) {
        return;
    }
    AMNoteValue(key.UTF8String, v);
}

static void AMLiveRunTweak(NSDictionary *tweak) {
    NSString *key = tweak[@"key"];
    BOOL slider = [tweak[@"kind"] isEqual:@"slider"];
    double sliderValue = slider ? [AMMenu floatForKey:key] : 0;
    BOOL on = slider ? sliderValue != [tweak[@"default"] ?: tweak[@"min"] doubleValue] : [AMMenu boolForKey:key];
    NSString *onKey = [key stringByAppendingString:@"/on"];
    if (!on) {
        if (sLiveState[onKey]) { [sLiveState removeObjectForKey:onKey]; AMLiveRestore(tweak); }
        return;
    }
    sLiveState[onKey] = @YES;
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
        NSString *cls = AMLiveClass(object);
        BOOL keep = AMLiveRestorable(tweak, pin);
        NSString *objKey = [stateKey stringByAppendingString:@"/obj"], *origKey = [stateKey stringByAppendingString:@"/orig"];
        NSString *wroteKey = [stateKey stringByAppendingString:@"/wrote"];
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
            if (keep && sLiveState[objKey].unsignedLongLongValue != (uintptr_t)obj) {  // the game's own value, once per object
                double orig = cur;
                BOOL known = p != NULL || getter != nil;
                if (!known) known = AMLiveGet(obj, tcls, [@"get_" stringByAppendingString:[pin[@"setter"] substringFromIndex:4]], t, &orig);
                sLiveState[objKey] = @((uintptr_t)obj);
                if (known) sLiveState[origKey] = @(orig); else [sLiveState removeObjectForKey:origKey];
                [sLiveState removeObjectForKey:wroteKey];
            }
            if ([pin[@"value"] isEqual:@"peak"] && (!sLiveState[stateKey] || cur > sLiveState[stateKey].doubleValue)) sLiveState[stateKey] = @(cur);
            if (!AMLiveValue(pin, stateKey, obj, tcls, t, sliderValue, &value)) continue;
            if ([pin[@"op"] isEqual:@"multiply"] && p) {  // field = base * slider; the game recalculating it gives a new base
                NSString *setKey = [stateKey stringByAppendingString:@"/set"], *baseKey = [stateKey stringByAppendingString:@"/base"];
                if (!sLiveState[setKey] || cur != sLiveState[setKey].doubleValue) sLiveState[baseKey] = @(cur);
                double want = sLiveState[baseKey].doubleValue * value;
                if (want > 0 && cur != want) AMLiveWrite(p, t, want);
                sLiveState[setKey] = @(want);
                sLiveState[origKey] = sLiveState[baseKey];  // off: the base goes back while the field holds ours
                sLiveState[wroteKey] = @(want);
                continue;
            }
            double limit = pin[@"threshold"] ? [pin[@"threshold"] doubleValue] : value;
            BOOL go = [when isEqual:@"lower"] ? cur < limit : [when isEqual:@"higher"] ? cur > limit : YES;
            if (!go) continue;
            if (p) {
                // the game fights back: what this wrote half a second ago is gone again, tick after tick
                NSString *fightKey = [stateKey stringByAppendingString:@"/fights"];
                if (keep && sLiveState[wroteKey] && cur != sLiveState[wroteKey].doubleValue) {
                    sLiveState[fightKey] = @(sLiveState[fightKey].intValue + 1);
                    if (sLiveState[fightKey].intValue >= 6) {
                        reached = NO;
                        why = [NSString stringWithFormat:@"The game resets %@ faster than this switch writes it: it may not hold", pin[@"field"]];
                    }
                } else {
                    [sLiveState removeObjectForKey:fightKey];
                }
                AMLiveWrite(p, t, value);
                if (keep) sLiveState[wroteKey] = @(AMLiveRead(p, t));
            } else if (AMLiveSet(obj, tcls, pin[@"setter"], t, value)) {
                if (keep) sLiveState[wroteKey] = @(value);
                double back = 0;  // read back: a setter that ignores the value turns the dot red
                if (getter && AMLiveGet(obj, tcls, getter, t, &back) && back != value) {
                    reached = NO;
                    why = [NSString stringWithFormat:@"%@.%@ didn't take the value (the game clamps or recomputes it)", tcls, pin[@"setter"]];
                }
            }
        }
    }
    if (tried) AMMenuStatus(key.UTF8String, reached, reached ? NULL : why.UTF8String);
}

// am test: {"id": "...", "on": ["key", ...]} turns those switches on (a slider goes to its max, or its min
// when the default is the max) and logs "test <id> on <n>". The keys it turned on are kept in the app's
// settings, so a new id, or a file without the block, turns them off again even after a relaunch.
static void AMLiveTest(id test) {
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    NSString *runningId = [d stringForKey:@"AM__testId"];
    NSString *newId = [test isKindOfClass:NSDictionary.class] && [test[@"id"] isKindOfClass:NSString.class] ? test[@"id"] : nil;
    if (runningId && [runningId isEqualToString:newId ?: @""]) return;
    AMMenu *menu = AMMenu.shared;
    for (NSString *key in [d arrayForKey:@"AM__testKeys"] ?: @[]) {
        NSArray<NSNumber *> *range = [menu sliderRange:key];
        if (range) [menu setSlider:key value:range[2].floatValue];
        else [menu setSwitch:key on:NO];
    }
    [d removeObjectForKey:@"AM__testKeys"];
    [d removeObjectForKey:@"AM__testId"];
    if (runningId) AMIl2cppLog(@"test %@ off", runningId);
    if (!newId) return;
    NSMutableArray *done = [NSMutableArray new];
    for (NSString *key in [test[@"on"] isKindOfClass:NSArray.class] ? test[@"on"] : @[]) {
        if (![key isKindOfClass:NSString.class]) continue;
        NSArray<NSNumber *> *range = [menu sliderRange:key];
        BOOL set = range ? [menu setSlider:key value:(range[2].floatValue == range[1].floatValue ? range[0] : range[1]).floatValue]
                         : [menu setSwitch:key on:YES];
        if (set) [done addObject:key];
    }
    [d setObject:done forKey:@"AM__testKeys"];
    [d setObject:newId forKey:@"AM__testId"];
    AMIl2cppLog(@"test %@ on %lu", newId, (unsigned long)done.count);
}

// Reads live_tweaks.json when it changed; new switches and sliders join the menu (in their sections).
static void AMLivePinsLoad(void) {
    NSString *path = AMLivePath();
    NSDate *stamp = [NSFileManager.defaultManager attributesOfItemAtPath:path error:NULL].fileModificationDate;
    if (!stamp) {
        if (sLiveKeys.count) { sLiveTweaks = @[]; sLiveKeys = [NSSet set]; AMIl2cppLog(@"live pins: file removed, the build's own switches again"); }
        if (!sLiveStamp) AMLiveTest(nil);  // a test left on by a file that is gone
        sLiveStamp = nil;
        return;
    }
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
    AMLiveTest([json isKindOfClass:NSDictionary.class] ? json[@"test"] : nil);
    if (!sLiveState) sLiveState = [NSMutableDictionary new];
    AMIl2cppLog(@"live pins: %lu switches from live_tweaks.json", (unsigned long)withPins.count);
}

// Once per tick (AMTweaksTick): reload when the file changed, then run every live switch that is on.
static void AMLivePinsTick(void) {
    CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
    static CFAbsoluteTime notesAt;
    BOOL notes = now - notesAt >= 3;
    if (notes) notesAt = now;
    if (now - sLiveCheckedAt >= 2) { sLiveCheckedAt = now; AMLivePinsLoad(); }
    for (NSDictionary *t in sLiveTweaks) {
        AMLiveRunTweak(t);
        if (notes) AMLiveNote(t);
    }
}
