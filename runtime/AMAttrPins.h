// Named-attribute pins: games that keep a character's stats in a by-name table (Survivor: Ace's
// AttributeData.AttrDic, an OrderedDictionary<string, FP>) instead of fields. A pin {"attr": "Attack"} reads the
// stat with the game's GetAttributeValue(string) and writes it with SetAttributeValue(string, FP), so the game's
// own setter runs (its update events, a recalculated max health). FP is Photon's fixed point: a long holding
// value * 65536.
//
// Per (pin, object) it keeps the game's own value (the base) and what it wrote: a multiply writes base * factor,
// and when the game changes the stat itself (a level-up, a pickup) that becomes the new base. Turning the switch off
// puts the base back on the objects it can still reach, if they still hold what the pin wrote; objects that are gone
// (the fight ended) are only forgotten, never touched. Included once, by AMTweaks.gen.m. Data calls only: no JIT.

#import <Foundation/Foundation.h>
#include <math.h>
#import "AMIl2cppData.h"

#define AM_ATTR_SCALE 65536.0

static NSMutableDictionary<NSString *, NSMutableDictionary *> *sAttrState;

static NSString *AMAttrKey(const char *pin, void *obj) {
    return [NSString stringWithFormat:@"%s/%p", pin, obj];
}

static BOOL AMAttrGet(void *obj, const char *cls, const char *getter, const char *name, int64_t *raw) {
    void *str = AMIl2cppNewString(name), *boxed = NULL;
    void *argv[1] = {str};
    if (!str || !AMIl2cppCall(obj, cls, getter, 1, argv, &boxed)) return NO;
    int64_t *p = AMIl2cppUnbox(boxed);  // FP is a struct holding one long
    if (!p) return NO;
    *raw = *p;
    return YES;
}

static BOOL AMAttrSet(void *obj, const char *cls, const char *setter, const char *name, int64_t raw) {
    void *str = AMIl2cppNewString(name);
    void *argv[2] = {str, &raw};  // a struct argument goes by address
    return str && AMIl2cppCall(obj, cls, setter, 2, argv, NULL);
}

// The game's getter and setter come in by name (the generated code passes them hidden: no plain game names in the
// .dylib). One tick on one object. op: 0 set (value is the plain number), 1 multiply (value is the factor).
// when: 0 always, 1 only while lower, 2 only while higher. Returns -1 when the stat can't be read (no such
// object or method), 0 when it was read and needed nothing, 1 when it wrote.
__attribute__((unused)) static int AMAttrApply(const char *pin, void *obj, const char *cls, const char *getter, const char *setter, const char *name, int op,
                                               double value, int when) {
    int64_t cur = 0;
    if (!obj || !AMAttrGet(obj, cls, getter, name, &cur)) return -1;
    if (!sAttrState) sAttrState = [NSMutableDictionary new];
    NSString *key = AMAttrKey(pin, obj);
    NSMutableDictionary *st = sAttrState[key];
    if (!st) st = sAttrState[key] = [@{@"base": @(cur)} mutableCopy];
    NSNumber *wrote = st[@"wrote"];
    // the game's own value: until the pin has written, whatever the game has (a stat at 0 until a skill gives it);
    // after, a value other than ours means the game set it since (a level-up, a pickup)
    if (!wrote || cur != wrote.longLongValue) st[@"base"] = @(cur);
    int64_t base = [st[@"base"] longLongValue];
    int64_t want = op == 1 ? (int64_t)llround((double)base * value) : (int64_t)llround(value * AM_ATTR_SCALE);
    BOOL go = when == 1 ? cur < want : when == 2 ? cur > want : cur != want;
    if (!go) return 0;
    if (!AMAttrSet(obj, cls, setter, name, want)) return -1;
    int64_t back = want;
    st[@"wrote"] = @(AMAttrGet(obj, cls, getter, name, &back) ? back : want);  // what the game kept (its setter may clamp)
    return 1;
}

// Turned off: the game's value back on this object if it still holds what the pin wrote. YES when restored.
__attribute__((unused)) static BOOL AMAttrRestore(const char *pin, void *obj, const char *cls, const char *getter, const char *setter, const char *name) {
    NSString *key = AMAttrKey(pin, obj);
    NSDictionary *st = sAttrState[key];
    [sAttrState removeObjectForKey:key];
    int64_t cur = 0;
    if (!st || !st[@"wrote"] || !AMAttrGet(obj, cls, getter, name, &cur) || cur != [st[@"wrote"] longLongValue]) return NO;
    return AMAttrSet(obj, cls, setter, name, [st[@"base"] longLongValue]);
}

// After a restore: forget objects of this pin that weren't reachable (a finished fight's heroes).
__attribute__((unused)) static void AMAttrForget(const char *pin) {
    NSString *prefix = [NSString stringWithFormat:@"%s/", pin];
    for (NSString *key in sAttrState.allKeys)
        if ([key hasPrefix:prefix]) [sAttrState removeObjectForKey:key];
}
