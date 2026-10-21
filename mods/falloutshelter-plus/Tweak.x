// Fallout Shelter+ (personal build, never published to the store): single-player gameplay switches.
//
// Built on the shared IL2CPP runtime (runtime/AMIl2cpp.h): every hook is named in hooks.json, looked up by
// class and method in the running game, and skipped (and logged) when a version no longer has it.
// Without JIT the runtime swaps method pointers (MethodInfo and vtable slots), which catches only calls
// that go through them: the first group of switches hooks overrides and property getters the game reaches
// through its vtables, so they work on any phone. The switches marked (JIT) hook plain methods the game
// calls directly; without JIT their hooks are installed but never reached. Signatures checked against the
// 2.7.0 dump (C:\AccretionMods\work\dump-falloutshelter-2.7.0). No paid content is touched.

#import <UIKit/UIKit.h>
#import <dlfcn.h>
#import <os/log.h>
#import "menu/AMMenu.h"
#import "runtime/AMIl2cpp.h"

// Current switch and slider values. The hooks run on Unity's player loop and the menu's callbacks on UIKit's,
// both on the main thread (the exit backtrace in the device log shows the loop under CADisplayLink), so plain
// variables are enough. Sliders start at 1 and the saved values are clamped to 1 when loaded, so every
// factor below is >= 1.
static BOOL sNoCombatDamage, sNoIncidentDamage;
static float sWeaponDamage = 1, sProduction = 1, sTraining = 1, sWasteland = 1;
// (JIT)
static BOOL sRushWins, sNoStatDamage, sNoRadiation, sNoIncidents;
static float sOutput = 1, sSpeed = 1;
// extras (no JIT needed): caps multiplier, full resources, dweller boosts, instant returns
static float sCaps = 1;
static BOOL sFullResources, sHappy, sMaxSpecial, sFastLevels, sInstantReturn;
static float sGameTimeScale = 1;  // what the game itself last asked for (pause sets 0)

// IL2CPP methods take self (instance methods), the C# arguments, then a MethodInfo pointer. The runtime
// fills one original per hooked method, so every hooked class gets its own replacement; the macros below
// generate the pairs (orig_<name>, hook_<name>) that REG in registerHooks ties to a hooks.json entry.

// GetHit(Targetable from, float damageRatio) on both fighter classes: skipped while "no combat damage" is on.
#define SKIP_GETHIT(name)                                                                     \
    static void (*orig_##name)(void *, void *, float, const void *);                         \
    static void hook_##name(void *self, void *from, float ratio, const void *mi) {           \
        if (!sNoCombatDamage) orig_##name(self, from, ratio, mi);                            \
    }
SKIP_GETHIT(GetHit)
SKIP_GETHIT(GetHitQuest)

// A float getter scaled by a slider: amounts are multiplied, cycle durations divided (shorter = faster).
#define SCALE_FLOAT(name, expr)                                                               \
    static float (*orig_##name)(void *, const void *);                                       \
    static float hook_##name(void *self, const void *mi) { float v = orig_##name(self, mi); return (expr); }
#define MULTIPLY_FLOAT(name, factor) SCALE_FLOAT(name, v * (factor))
#define DIVIDE_FLOAT(name, factor) SCALE_FLOAT(name, v / MAX((factor), 1))
MULTIPLY_FLOAT(WeaponDamage, sWeaponDamage)
MULTIPLY_FLOAT(WeaponDamageQuest, sWeaponDamage)
DIVIDE_FLOAT(ProductionCycle, sProduction)
DIVIDE_FLOAT(CraftingCycle, sProduction)
MULTIPLY_FLOAT(TrainingXP, sTraining)
DIVIDE_FLOAT(WastelandCycle, sWasteland)

// Incident damage per cycle. Each creature controller has two getters (damage to the dwellers, damage to
// the room), each invader controller one (damage to the room); all answer 0 while "incidents do no damage"
// is on. The two lists are the only place an enemy is named: they generate the hooks here and the
// registrations in registerHooks (the hooks.json entries stay explicit).
#define CREATURES(X) X(Deathclaw) X(Ghoul) X(Molerat) X(Radroach) X(Radscorpion) X(Securitron)
#define INVADERS(X) X(Aliens) X(EnclavePowerArmor) X(EnclaveSecurity) X(Gen1Synth) X(Gen2Synth) X(Raiders)

#define ZERO_FLOAT(name)                                                                      \
    static float (*orig_##name)(void *, const void *);                                       \
    static float hook_##name(void *self, const void *mi) { return sNoIncidentDamage ? 0 : orig_##name(self, mi); }
#define CREATURE_HOOKS(enemy) ZERO_FLOAT(enemy##Dweller) ZERO_FLOAT(enemy##Damage)
#define INVADER_HOOKS(enemy) ZERO_FLOAT(enemy##Room)
CREATURES(CREATURE_HOOKS)
INVADERS(INVADER_HOOKS)

// (JIT) plain methods the game calls directly: the same hooks as Fallout Shelter+ 1.0
static BOOL (*orig_rush)(const void *);
static BOOL hook_rush(const void *mi) { return sRushWins ? YES : orig_rush(mi); }

static float (*orig_resource)(void *, float, const void *);
static float hook_resource(void *self, float value, const void *mi) { return orig_resource(self, value, mi); }  // the factor now lives in RoomParameters

static void (*orig_stat_damage)(void *, float, float, float, void *, const void *);
static void hook_stat_damage(void *self, float value, float base, float level, void *stats, const void *mi) {
    if (!sNoStatDamage) orig_stat_damage(self, value, base, level, stats, mi);
}

static void (*orig_set_radiation)(void *, float, const void *);
static void hook_set_radiation(void *self, float value, const void *mi) {
    orig_set_radiation(self, sNoRadiation ? 0 : value, mi);
}

static void (*orig_start_emergency)(void *, const void *);
static void hook_start_emergency(void *self, const void *mi) { if (!sNoIncidents) orig_start_emergency(self, mi); }

static void (*orig_start_random)(void *, const void *);
static void hook_start_random(void *self, const void *mi) { if (!sNoIncidents) orig_start_random(self, mi); }

static void (*orig_force_emergency)(void *, void *, const void *);
static void hook_force_emergency(void *self, void *room, const void *mi) {
    if (!sNoIncidents) orig_force_emergency(self, room, mi);
}

// Time.set_timeScale is the game's own speed control (pause sets 0, resume 1): the hook remembers the game's
// value and scales it, and applySpeed re-applies the product when the slider moves. Without JIT the game's
// calls bypass the hook, but orig_set_timescale is still the real setter (the runtime fills it when it swaps
// the MethodInfo), so the slider should still take effect until the game's next pause or resume resets the
// speed to x1; not checked on the phone.
static void (*orig_set_timescale)(float, const void *);
static void hook_set_timescale(float value, const void *mi) {
    sGameTimeScale = value;
    orig_set_timescale(value > 0 ? value * sSpeed : value, mi);
}

static void applySpeed(void) {
    if (orig_set_timescale) orig_set_timescale(sGameTimeScale > 0 ? sGameTimeScale * sSpeed : sGameTimeScale, NULL);
}

static void applyKeepAwake(void) {
    UIApplication.sharedApplication.idleTimerDisabled = [AMMenu boolForKey:@"keepAwake"];
}

static void *il2cpp(const char *name) { return dlsym(RTLD_DEFAULT, name); }

// The MainMenu scene loads twice on a re-signed copy (found 2026-10-21 on the phone: 2.7.0, iOS 27.2). The second
// MainMenuScene is the one drawn and tapped, but MonoSingleton.Awake skips its OnAwake (an instance already
// exists), so its button table m_joystickButtonsByState stays null: every frame's OnVaultList throws in
// EnableJoystickButtonStatesForView before it handles a tap, and the game's ExceptionHandler turns that into a
// quit (why entering a vault closed the game, 1.0 and 2.0). And when a vault loads, the game removes only the
// first, registered menu, so the second stayed drawn over the vault.
// Fix: give the second menu its button table, built the way OnAwake builds it, and destroy it once the game has
// removed the first. Running OnAwake again, or making the second menu the singleton, crashed the game (segfault).

// Field offsets below come from the 2.7.0 dump. Hooks are found by name and simply stay off when a version lacks
// them, but raw offsets on another version would read or write the wrong memory, so everything that uses them
// (the main-menu fix, the no-JIT switches, the extras, the item picker) runs only on 2.7.0 ("2.7.0" or a
// re-signed copy's "2.7.0-mod...").
static NSString *const kTestedVersion = @"2.7.0";

static NSString *gameVersion(void) {
    return [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"] ?: @"";
}

static BOOL offsetsMatch(void) {
    NSString *v = gameVersion();
    return [v isEqualToString:kTestedVersion] || [v hasPrefix:[kTestedVersion stringByAppendingString:@"-"]];
}

// IL2CPP helpers: a class by assembly and name, and a call through il2cpp_runtime_invoke (NULL on an exception).
static void *klass(const char *assembly, const char *ns, const char *name) {
    void *(*domain_get)(void) = il2cpp("il2cpp_domain_get");
    void *(*assembly_open)(void *, const char *) = il2cpp("il2cpp_domain_assembly_open");
    void *(*assembly_get_image)(void *) = il2cpp("il2cpp_assembly_get_image");
    void *(*class_from_name)(void *, const char *, const char *) = il2cpp("il2cpp_class_from_name");
    if (!domain_get || !assembly_open || !assembly_get_image || !class_from_name) return NULL;
    void *a = assembly_open(domain_get(), assembly);
    return a ? class_from_name(assembly_get_image(a), ns, name) : NULL;
}

static void *invoke(void *cls, const char *method, int argc, void *obj, void **args) {
    void *(*method_from_name)(void *, const char *, int) = il2cpp("il2cpp_class_get_method_from_name");
    void *(*runtime_invoke)(void *, void *, void **, void **) = il2cpp("il2cpp_runtime_invoke");
    void *m = cls && method_from_name && runtime_invoke ? method_from_name(cls, method, argc) : NULL;
    void *exception = NULL;
    void *r = m ? runtime_invoke(m, obj, args, &exception) : NULL;
    return exception ? NULL : r;
}

// Every object of a MonoBehaviour class, inactive ones too: FindObjectsByType(Type, Include, None), an IL2CPP
// array (length at 0x18, items from 0x20) or NULL.
static void *allObjects(const char *assembly, const char *ns, const char *name) {
    void *(*class_get_type)(void *) = il2cpp("il2cpp_class_get_type");
    void *(*type_get_object)(void *) = il2cpp("il2cpp_type_get_object");
    void *cls = klass(assembly, ns, name);
    if (!cls || !class_get_type || !type_get_object) return NULL;
    int32_t include = 1, sortNone = 0;
    void *args[3] = {type_get_object(class_get_type(cls)), &include, &sortNone};
    return invoke(klass("UnityEngine.CoreModule", "UnityEngine", "Object"), "FindObjectsByType", 3, NULL, args);
}

static void *allMenus(void) { return allObjects("Assembly-CSharp", "", "MainMenuScene"); }

static void logMenu(const char *what) { os_log(OS_LOG_DEFAULT, "[Fallout Shelter+] main menu: %{public}s", what); }

static void *sTableClass;  // UIJoystickButtonSelector[][], taken from the menu whose OnAwake finished
static void *sOriginalMenu, *sDuplicateMenu;
static dispatch_source_t sRepairTimer;  // kept so the timer keeps firing

static void repairMenus(void) {
    void *menus = sTableClass ? allMenus() : NULL;
    int32_t n = menus ? *(int32_t *)((char *)menus + 0x18) : 0;
    BOOL originalAlive = NO, duplicateAlive = NO;
    for (int32_t i = 0; i < n; i++) {
        char *m = ((void **)((char *)menus + 0x20))[i];
        if (m == sOriginalMenu) originalAlive = YES;
        if (m && m == sDuplicateMenu) duplicateAlive = YES;
        if (!m || m == sOriginalMenu || *(void **)(m + 0x218)) continue;
        // the table: its six button arrays (0x1E8 main screen ... 0x210 credits) in field order, at 0x218
        void *(*array_new)(void *, uintptr_t) = il2cpp("il2cpp_array_new");
        void *(*element_class)(void *) = il2cpp("il2cpp_class_get_element_class");
        void (*wbarrier)(void *, void **, void *) = il2cpp("il2cpp_gc_wbarrier_set_field");  // keeps the GC informed
        if (!array_new || !element_class || !wbarrier) return;
        void *rowClass = element_class(sTableClass);
        void *table = array_new(rowClass, 6);
        if (!table) continue;
        void **items = (void **)((char *)table + 0x20);
        for (int r = 0; r < 6; r++) {
            void *row = *(void **)(m + 0x1E8 + 8 * r);
            if (!row) row = array_new(element_class(rowClass), 0);
            wbarrier(table, &items[r], row);
        }
        wbarrier(m, (void **)(m + 0x218), table);
        sDuplicateMenu = m;
        duplicateAlive = YES;
        logMenu("second copy given its button table");
    }
    if (sDuplicateMenu && duplicateAlive && !originalAlive) {
        void *root = invoke(klass("UnityEngine.CoreModule", "UnityEngine", "Component"), "get_gameObject", 0, sDuplicateMenu, NULL);
        void *args[1] = {root};
        if (root) invoke(klass("UnityEngine.CoreModule", "UnityEngine", "Object"), "Destroy", 1, NULL, args);
        logMenu("first copy removed by the game (vault loading), second copy destroyed");
        sDuplicateMenu = NULL;
    }
}

// The (JIT) switches without JIT: their hooks sit on plain methods the game calls directly, so without JIT they are
// never reached. Once a second this does the same through the game's own calls and data instead (it also runs
// with JIT, where the hooks act first):
//   rushes: the game's own cheat flag, ProductionRoom.set_CheatRushAlwaysSucceeds (static)
//   game speed: UnityEngine.Time.timeScale, re-applied whenever the game resets it (pause stays 0)
//   no damage: each living dweller's Health (Dweller.m_health 0x1A0 -> DwellerHealth.m_dwellerHealth 0x20 ->
//     m_healthValue 0x10 / m_healthMax 0x14) is topped up
//   no radiation: DwellerHealth.RemoveAllRadiation() on dwellers with radiation (m_radiationValue 0x2C), as RadAway
//   no incidents: fires and infestations (not raiders or other invaders) are stopped as they start
//     (Vault.m_emergencyState 0x160: ForceStopEmergencyInRoom on each emergency room), and production rooms'
//     disaster chance on a failed rush (ProductionRoom.m_disasterChanceBase 0x268) is set to 0
//   room output: GetResourceWithBonuses multiplies by GameParameters.m_roomParameters (0x40) ->
//     RoomParameters.m_noRushResourcesMultiplier (0x4C; rushes use m_rushResourcesMultiplier 0x40); both are set
//     to the game's own value times the slider
static BOOL unboxBool(void *boxed) { return boxed && *(uint8_t *)((char *)boxed + 0x10); }

static void applyNoJitSwitches(void) {
    const char *assembly = "Assembly-CSharp";
    static int lastRush = -1;
    if (lastRush != sRushWins) {
        uint8_t on = sRushWins;
        void *args[1] = {&on};
        invoke(klass(assembly, "", "ProductionRoom"), "set_CheatRushAlwaysSucceeds", 1, NULL, args);
        lastRush = sRushWins;
    }

    static float lastScale;
    void *time = klass("UnityEngine.CoreModule", "UnityEngine", "Time");
    void *scaleBox = invoke(time, "get_timeScale", 0, NULL, NULL);
    float scale = scaleBox ? *(float *)((char *)scaleBox + 0x10) : 0, want = sSpeed;
    if (scale > 0 && (want > 1 || lastScale > 1) && fabsf(scale - want) > 0.01f) {
        void *args[1] = {&want};
        invoke(time, "set_timeScale", 1, NULL, args);
        lastScale = want;
    }

    static char *roomParams;
    static float baseNoRush, baseRush;
    void *params = allObjects(assembly, "", "GameParameters");
    char *gp = params && *(int32_t *)((char *)params + 0x18) > 0 ? ((char **)((char *)params + 0x20))[0] : NULL;
    char *rp = gp ? *(char **)(gp + 0x40) : NULL;
    if (rp && rp != roomParams) {
        roomParams = rp;
        baseNoRush = *(float *)(rp + 0x4C);
        baseRush = *(float *)(rp + 0x40);
    }
    if (rp) {
        *(float *)(rp + 0x4C) = baseNoRush * sOutput;
        *(float *)(rp + 0x40) = baseRush * sOutput;
    }

    if (sNoStatDamage || sNoRadiation) {
        void *dwellers = allObjects(assembly, "", "Dweller");
        int32_t n = dwellers ? *(int32_t *)((char *)dwellers + 0x18) : 0;
        void *healthClass = n ? klass(assembly, "", "DwellerHealth") : NULL;
        for (int32_t i = 0; i < n && healthClass; i++) {
            char *dweller = ((void **)((char *)dwellers + 0x20))[i];
            char *health = dweller ? *(char **)(dweller + 0x1A0) : NULL;
            if (!health) continue;
            if (sNoRadiation && *(float *)(health + 0x2C) > 0) invoke(healthClass, "RemoveAllRadiation", 0, health, NULL);
            char *hp = *(char **)(health + 0x20);
            if (sNoStatDamage && hp && *(float *)(hp + 0x10) > 0 && *(float *)(hp + 0x10) < *(float *)(hp + 0x14))
                *(float *)(hp + 0x10) = *(float *)(hp + 0x14);
        }
    }

    if (sNoIncidents) {
        void *rooms = allObjects(assembly, "", "ProductionRoom");
        int32_t n = rooms ? *(int32_t *)((char *)rooms + 0x18) : 0;
        for (int32_t i = 0; i < n; i++) {
            char *room = ((void **)((char *)rooms + 0x20))[i];
            if (room) *(float *)(room + 0x268) = 0;
        }
        void *vaults = allObjects(assembly, "", "Vault");
        char *vault = vaults && *(int32_t *)((char *)vaults + 0x18) > 0 ? ((char **)((char *)vaults + 0x20))[0] : NULL;
        char *state = vault ? *(char **)(vault + 0x160) : NULL;
        void *stateClass = state ? klass(assembly, "", "VaultEmergencyState") : NULL;
        if (stateClass && unboxBool(invoke(stateClass, "get_InEmergency", 0, state, NULL)) &&
            !unboxBool(invoke(stateClass, "get_IsInvaderEmergency", 0, state, NULL))) {
            char *list = invoke(stateClass, "get_EmergencyRooms", 0, state, NULL);  // List<Room>: _items 0x10, _size 0x18
            char *items = list ? *(char **)(list + 0x10) : NULL;
            int32_t count = list ? *(int32_t *)(list + 0x18) : 0;
            void *stop[16];
            int32_t k = 0;
            for (int32_t i = 0; i < count && k < 16 && items; i++) stop[k++] = ((void **)(items + 0x20))[i];
            for (int32_t i = 0; i < k; i++) {
                void *args[1] = {stop[i]};
                invoke(stateClass, "ForceStopEmergencyInRoom", 1, state, args);
            }
        }
    }
}

// Extras, applied once a second like the switches above; every offset is from the 2.7.0 dump.
//   caps: Vault.m_storage (0x150) -> Storage.m_resources (0x18) -> GameResources get/set_Nuka; each gain since the
//     last tick is multiplied (gains only, spending is untouched)
//   full resources: food, power and water set to Storage.m_maxResources (0x10)
//   happy: DwellerHappiness (Dweller 0x198) AddHappiness up to its max
//   max SPECIAL: DwellerStats (Dweller 0x1D8) m_stats List<SpecialStat> (0x18): SetValueAndMinExp(10) per stat
//   fast levels: DwellerExperience (Dweller 0x1E0) AddExp(500)
//   instant return: WastelandTeam with ElapsedReturningTime (0x9C) > 0 gets its own SkipReturningTime()
static float getFloat(void *cls, const char *getter, void *obj) {
    void *box = invoke(cls, getter, 0, obj, NULL);
    return box ? *(float *)((char *)box + 0x10) : 0;
}

static void setFloat(void *cls, const char *setter, void *obj, float value) {
    void *args[1] = {&value};
    invoke(cls, setter, 1, obj, args);
}

static char *theVault(void) {
    void *vaults = allObjects("Assembly-CSharp", "", "Vault");
    return vaults && *(int32_t *)((char *)vaults + 0x18) > 0 ? ((char **)((char *)vaults + 0x20))[0] : NULL;
}

static void applyExtras(void) {
    const char *assembly = "Assembly-CSharp";
    char *vault = theVault();
    char *storage = vault ? *(char **)(vault + 0x150) : NULL;
    char *res = storage ? *(char **)(storage + 0x18) : NULL, *max = storage ? *(char **)(storage + 0x10) : NULL;
    void *gr = res ? klass(assembly, "", "GameResources") : NULL;
    static char *lastRes;
    static float lastCaps;
    if (gr) {
        float caps = getFloat(gr, "get_Nuka", res);
        if (res == lastRes && sCaps > 1 && caps > lastCaps) {
            caps += (caps - lastCaps) * (sCaps - 1);
            setFloat(gr, "set_Nuka", res, caps);
        }
        lastRes = res;
        lastCaps = caps;
        if (sFullResources && max) {
            setFloat(gr, "set_Food", res, getFloat(gr, "get_Food", max));
            setFloat(gr, "set_Power", res, getFloat(gr, "get_Power", max));
            setFloat(gr, "set_Water", res, getFloat(gr, "get_Water", max));
        }
    }

    if (sHappy || sMaxSpecial || sFastLevels) {
        void *dwellers = allObjects(assembly, "", "Dweller");
        int32_t n = dwellers ? *(int32_t *)((char *)dwellers + 0x18) : 0;
        void *happyClass = klass(assembly, "", "DwellerHappiness"), *statClass = klass(assembly, "", "SpecialStat");
        void *expClass = klass(assembly, "", "DwellerExperience");
        for (int32_t i = 0; i < n; i++) {
            char *d = ((char **)((char *)dwellers + 0x20))[i];
            if (!d) continue;
            char *happy = *(char **)(d + 0x198);
            if (sHappy && happy) {
                void *maxBox = invoke(happyClass, "get_HappinessMax", 0, happy, NULL);
                float top = maxBox ? *(int32_t *)((char *)maxBox + 0x10) : 100, now = *(float *)(happy + 0x20);
                if (now < top) setFloat(happyClass, "AddHappiness", happy, top - now);
            }
            char *stats = *(char **)(d + 0x1D8);
            char *list = sMaxSpecial && stats ? *(char **)(stats + 0x18) : NULL;  // List<SpecialStat>: _items 0x10, _size 0x18
            char *items = list ? *(char **)(list + 0x10) : NULL;
            for (int32_t k = 0; items && k < *(int32_t *)(list + 0x18); k++) {
                char *stat = ((char **)(items + 0x20))[k];
                if (stat && *(int32_t *)(stat + 0x10) > 0 && *(int32_t *)(stat + 0x10) < 10) {
                    int32_t ten = 10;
                    void *args[1] = {&ten};
                    invoke(statClass, "SetValueAndMinExp", 1, stat, args);
                }
            }
            char *exp = *(char **)(d + 0x1E0);
            if (sFastLevels && exp) setFloat(expClass, "AddExp", exp, 500);
        }
    }

    if (sInstantReturn) {
        void *teams = allObjects(assembly, "", "WastelandTeam");
        int32_t n = teams ? *(int32_t *)((char *)teams + 0x18) : 0;
        void *teamClass = n ? klass(assembly, "", "WastelandTeam") : NULL;
        for (int32_t i = 0; i < n && teamClass; i++) {
            char *team = ((char **)((char *)teams + 0x20))[i];
            if (team && *(int32_t *)(team + 0x9C) > 0) invoke(teamClass, "SkipReturningTime", 0, team, NULL);
        }
    }
}

// Item picker: rare and legendary weapons and outfits from GameParameters.m_items (0x28) -> ItemParameters
// (m_weaponsList 0xB0, m_outfitList 0x10); each DwellerBaseItem has name (0x10), m_itemRarity (0x20) and
// m_isHiddenItem (0x48), weapons m_WeaponId (0x88) and damage (0x90/0x94), outfits m_outfitId (0x98). Added with
// the vault inventory's (Vault 0x158) own AddAndRecordDwellerItem(type, id, 1).
static NSString *nsString(void *s) {
    if (!s) return @"";
    return [NSString stringWithCharacters:(const unichar *)((char *)s + 0x14) length:*(int32_t *)((char *)s + 0x10)];
}

static NSMutableArray<NSString *> *sItemIds;
static NSMutableArray<NSNumber *> *sItemTypes;

static NSArray *itemEntries(BOOL weapons) {
    if (!offsetsMatch()) return @[];
    NSMutableArray *entries = [NSMutableArray array];
    sItemIds = [NSMutableArray array];
    sItemTypes = [NSMutableArray array];
    void *params = allObjects("Assembly-CSharp", "", "GameParameters");
    char *gp = params && *(int32_t *)((char *)params + 0x18) > 0 ? ((char **)((char *)params + 0x20))[0] : NULL;
    char *items = gp ? *(char **)(gp + 0x28) : NULL;
    char *array = items ? *(char **)(items + (weapons ? 0xB0 : 0x10)) : NULL;
    int32_t n = array ? *(int32_t *)(array + 0x18) : 0;
    for (int rarity = 4; rarity >= 3; rarity--) {
        for (int32_t i = 0; i < n; i++) {
            char *item = ((char **)(array + 0x20))[i];
            if (!item || *(int32_t *)(item + 0x20) != rarity || *(uint8_t *)(item + 0x48)) continue;
            NSString *itemId = nsString(*(void **)(item + (weapons ? 0x88 : 0x98)));
            if (!itemId.length) continue;
            NSString *detail = rarity == 4 ? @"Legendary" : @"Rare";
            if (weapons) detail = [detail stringByAppendingFormat:@" · damage %d-%d", *(int32_t *)(item + 0x90), *(int32_t *)(item + 0x94)];
            NSString *name = nsString(*(void **)(item + 0x10));
            [entries addObject:@{@"title": name.length ? name : itemId, @"detail": detail}];
            [sItemIds addObject:itemId];
            [sItemTypes addObject:@(weapons ? 2 : 1)];  // EItemType: Outfit 1, Weapon 2
        }
    }
    return entries;
}

static void addItem(NSInteger index) {
    if (index < 0 || index >= (NSInteger)sItemIds.count) return;
    void *(*string_new)(const char *) = il2cpp("il2cpp_string_new");
    char *vault = theVault();
    char *inventory = vault ? *(char **)(vault + 0x158) : NULL;
    if (!string_new || !inventory) return;
    int32_t type = sItemTypes[index].intValue, count = 1;
    void *args[3] = {&type, string_new(sItemIds[index].UTF8String), &count};
    invoke(klass("Assembly-CSharp", "", "Inventory"), "AddAndRecordDwellerItem", 3, inventory, args);
}

// Hooked to learn the table's class from the menu whose OnAwake finished, and to start the repair.
static void (*orig_MainMenuAwake)(void *, const void *);
static void hook_MainMenuAwake(void *self, const void *mi) {
    orig_MainMenuAwake(self, mi);
    if (!offsetsMatch()) return;
    void *table = *(void **)((char *)self + 0x218);
    void *(*object_get_class)(void *) = il2cpp("il2cpp_object_get_class");
    if (table) sOriginalMenu = self;  // every time a menu finishes OnAwake (also on later returns to the menu)
    if (table && object_get_class && !sTableClass) {
        sTableClass = object_get_class(table);
        // four times a second for the rest of the session (the menu loads again when the player returns to it);
        // the (JIT) switches are applied on every fourth tick
        dispatch_source_t timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
        dispatch_source_set_timer(timer, DISPATCH_TIME_NOW, NSEC_PER_SEC / 4, NSEC_PER_SEC / 50);
        static unsigned ticks;
        dispatch_source_set_event_handler(timer, ^{
            repairMenus();
            if (++ticks % 4 == 0) {
                applyNoJitSwitches();
                applyExtras();
            }
        });
        dispatch_resume(timer);
        sRepairTimer = timer;
    }
}

#define REG(name, hook) AMIl2cppRegister(name, (void *)hook_##hook, (void **)&orig_##hook)

static void registerHooks(void) {
    REG("Dweller.DwellerFighting.GetHit", GetHit);
    REG("Dweller.DwellerFightingQuest.GetHit", GetHitQuest);
    REG("Dweller.DwellerFighting.GetWeaponDamage", WeaponDamage);
    REG("Dweller.DwellerFightingQuest.GetWeaponDamage", WeaponDamageQuest);
    REG("ProductionRoom.ProductionRoomWorking.GetTaskCycle", ProductionCycle);
    REG("CraftingRoom.CraftingRoomWorking.GetTaskCycle", CraftingCycle);
    REG("TrainingRoom.GetExperiencePerCycle", TrainingXP);
    REG("WastelandCycle.GetCycleDurationInSeconds", WastelandCycle);

    // Room.Room<Enemy>EmergencyController getters, one registration per hook the CREATURES/INVADERS lists made
#define REG_CREATURE(enemy)                                                                   \
    REG("Room.Room" #enemy "EmergencyController.get_DwellerMaxDamagePerCycle", enemy##Dweller); \
    REG("Room.Room" #enemy "EmergencyController.get_DamagePerCycle", enemy##Damage);
#define REG_INVADER(enemy) REG("Room.Room" #enemy "EmergencyController.get_RoomDamagePerCycle", enemy##Room);
    CREATURES(REG_CREATURE)
    INVADERS(REG_INVADER)

    REG("ProductionRoom.get_CheatRushAlwaysSucceeds", rush);
    REG("ProductionRoom.GetResourceWithBonuses", resource);
    REG("DwellerHealth.DoStatReductedDamage", stat_damage);
    REG("DwellerHealth.set_RadiationValue", set_radiation);
    REG("VaultEmergencyState.StartEmergency", start_emergency);
    REG("VaultEmergencyState.StartRandomEmergencyTask", start_random);
    REG("VaultEmergencyState.ForceStartEmergencyInRoom", force_emergency);
    REG("UnityEngine.Time.set_timeScale", set_timescale);
    REG("MainMenuScene.OnAwake", MainMenuAwake);
}

%ctor {
    // Every switch works without JIT: the plain-method ones through applyNoJitSwitches.
    AMMenu *menu = AMMenu.shared;
    if (!offsetsMatch()) {
        NSString *note = [NSString stringWithFormat:@"Game %@ not supported: only the basic switches work", gameVersion()];
        [menu addList:note symbol:@"exclamationmark.triangle" entries:^NSArray *{
            return @[@{@"title": [NSString stringWithFormat:@"Checked against Fallout Shelter %@", kTestedVersion],
                       @"detail": @"Other switches stay off"}];
        } onPick:^(NSInteger i) {}];
    }
    menu.title = @"Fallout Shelter+";
    [menu addSwitch:@"noCombatDamage" title:@"Dwellers take no combat damage" defaultValue:NO onChange:^(BOOL on) { sNoCombatDamage = on; }];
    [menu addSwitch:@"noIncidentDamage" title:@"Incidents do no damage" defaultValue:NO onChange:^(BOOL on) { sNoIncidentDamage = on; }];
    [menu addSlider:@"weaponDamage" title:@"Weapon damage ×" min:1 max:10 defaultValue:1 onChange:^(float v) { sWeaponDamage = v; }];
    [menu addSlider:@"production" title:@"Production and crafting speed ×" min:1 max:10 defaultValue:1 onChange:^(float v) { sProduction = v; }];
    [menu addSlider:@"training" title:@"Training speed ×" min:1 max:10 defaultValue:1 onChange:^(float v) { sTraining = v; }];
    [menu addSlider:@"wasteland" title:@"Wasteland speed ×" min:1 max:10 defaultValue:1 onChange:^(float v) { sWasteland = v; }];
    [menu addSwitch:@"rushWins" title:@"Rushes always succeed" defaultValue:NO onChange:^(BOOL on) { sRushWins = on; }];
    [menu addSlider:@"output" title:@"Room output ×" min:1 max:10 defaultValue:1 onChange:^(float v) { sOutput = v; }];
    [menu addSlider:@"speed" title:@"Game speed ×" min:1 max:5 defaultValue:1 onChange:^(float v) {
        sSpeed = v;
        applySpeed();
    }];
    [menu addSwitch:@"noDamage" title:@"No damage from other sources" defaultValue:NO onChange:^(BOOL on) { sNoStatDamage = on; }];
    [menu addSwitch:@"noRadiation" title:@"No radiation" defaultValue:NO onChange:^(BOOL on) { sNoRadiation = on; }];
    [menu addSwitch:@"noIncidents" title:@"No fires or infestations" defaultValue:NO onChange:^(BOOL on) { sNoIncidents = on; }];
    [menu addSwitch:@"keepAwake" title:@"Keep screen awake" defaultValue:NO onChange:^(BOOL on) { applyKeepAwake(); }];
    [menu addSlider:@"caps" title:@"Caps gained ×" min:1 max:10 defaultValue:1 onChange:^(float v) { sCaps = v; }];
    [menu addSwitch:@"fullResources" title:@"Keep food, water and power full" defaultValue:NO onChange:^(BOOL on) { sFullResources = on; }];
    [menu addSwitch:@"happy" title:@"Dwellers always happy" defaultValue:NO onChange:^(BOOL on) { sHappy = on; }];
    [menu addSwitch:@"maxSpecial" title:@"Max SPECIAL for every dweller" defaultValue:NO onChange:^(BOOL on) { sMaxSpecial = on; }];
    [menu addSwitch:@"fastLevels" title:@"Fast level-ups" defaultValue:NO onChange:^(BOOL on) { sFastLevels = on; }];
    [menu addSwitch:@"instantReturn" title:@"Explorers return instantly" defaultValue:NO onChange:^(BOOL on) { sInstantReturn = on; }];
    [menu addList:@"Add a weapon" symbol:@"scope" entries:^NSArray *{ return itemEntries(YES); } onPick:^(NSInteger i) { addItem(i); }];
    [menu addList:@"Add an outfit" symbol:@"tshirt" entries:^NSArray *{ return itemEntries(NO); } onPick:^(NSInteger i) { addItem(i); }];

    // saved values from the last session
    sNoCombatDamage = [AMMenu boolForKey:@"noCombatDamage"];
    sNoIncidentDamage = [AMMenu boolForKey:@"noIncidentDamage"];
    sWeaponDamage = MAX([AMMenu floatForKey:@"weaponDamage"], 1);
    sProduction = MAX([AMMenu floatForKey:@"production"], 1);
    sTraining = MAX([AMMenu floatForKey:@"training"], 1);
    sWasteland = MAX([AMMenu floatForKey:@"wasteland"], 1);
    sRushWins = [AMMenu boolForKey:@"rushWins"];
    sNoStatDamage = [AMMenu boolForKey:@"noDamage"];
    sNoRadiation = [AMMenu boolForKey:@"noRadiation"];
    sNoIncidents = [AMMenu boolForKey:@"noIncidents"];
    sOutput = MAX([AMMenu floatForKey:@"output"], 1);
    sCaps = MAX([AMMenu floatForKey:@"caps"], 1);
    sFullResources = [AMMenu boolForKey:@"fullResources"];
    sHappy = [AMMenu boolForKey:@"happy"];
    sMaxSpecial = [AMMenu boolForKey:@"maxSpecial"];
    sFastLevels = [AMMenu boolForKey:@"fastLevels"];
    sInstantReturn = [AMMenu boolForKey:@"instantReturn"];
    sSpeed = MAX([AMMenu floatForKey:@"speed"], 1);

    registerHooks();
    AMIl2cppSetReportHandler(^(NSString *report) {
        os_log(OS_LOG_DEFAULT, "[Fallout Shelter+] %{public}@", report);
        applySpeed();
    });
    AMIl2cppStart();

    [NSNotificationCenter.defaultCenter addObserverForName:UIApplicationDidBecomeActiveNotification
                                                    object:nil
                                                     queue:NSOperationQueue.mainQueue
                                                usingBlock:^(NSNotification *note) {
        applyKeepAwake();
    }];
    [menu install];
}
