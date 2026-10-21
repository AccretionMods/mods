// The Wanderer+ (personal build, never published to the store): single-player gameplay switches for
// The Wanderer: Post-Nuclear RPG 10.1 (com.JamieParish.TheWanderer).
//
// Built on the shared IL2CPP runtime (runtime/AMIl2cpp.h), same layout as Fallout Shelter+: every hook is
// named in hooks.json, looked up by class and method in the running game, and skipped (and logged) when a
// version no longer has it. Without JIT the runtime swaps method pointers, which catches only calls that go
// through them: here that is the two Unity callbacks (ExperienceLevel.Update, Lock.Update) the "stats never
// drop" switches run from. Everything else in this game is a plain method the game calls directly, so those
// switches are marked (JIT) and work only with JIT on. Signatures checked against the 10.1 dump
// (C:\AccretionMods\work\dump-wanderer-10.1\out\dump.cs).
//
// Left out on purpose: caps and tokens (sold in the shop as 1000caps/tokens packs), bag capacity (max_bag),
// fuel (vancrate/vanwarranty), fast travel, revives, supply drops and experience boosts (bought with tokens).

#import <UIKit/UIKit.h>
#import <dlfcn.h>
#import <os/log.h>
#import "menu/AMMenu.h"
#import "runtime/AMIl2cpp.h"

// Current switch and slider values. Hooks and menu callbacks both run on the main thread, so plain
// variables are enough. Sliders start at 1 and saved values are clamped to 1 when loaded.
static BOOL sKeepStats, sKeepLockpicks;  // any phone (phone-checked 2026-10-21: stats hold)
static BOOL sGiveItems, sGiveWeapons;    // any phone: the game's own debug givers, called every 10 s while on
static float sSpawnAmount = 1;           // how many of a picked item "Spawn an item" gives
// (JIT)
static BOOL sPlayerNoDamage, sPetNoDamage, sCompanionNoDamage, sEnemiesMiss, sAlwaysCrit, sEnemiesNoHeal;
static BOOL sNoRadiationDamage, sFreeCrafting, sNoWear, sSkillPoints, sPetFull;
static float sWeaponDamage = 1;

// IL2CPP methods take self (instance methods), the C# arguments, then a MethodInfo pointer. The runtime
// fills one original per hooked method; the macros make the (orig_<name>, hook_<name>) pairs that REG in
// registerHooks ties to a hooks.json entry.

// void methods skipped while a switch is on
#define SKIP0(name, flag)                                                                    \
    static void (*orig_##name)(void *, const void *);                                        \
    static void hook_##name(void *self, const void *mi) { if (!(flag)) orig_##name(self, mi); }
#define SKIP_INT_BOOL(name, flag)                                                            \
    static void (*orig_##name)(void *, int32_t, bool, const void *);                         \
    static void hook_##name(void *self, int32_t amount, bool crit, const void *mi) {         \
        if (!(flag)) orig_##name(self, amount, crit, mi);                                    \
    }
#define SKIP_LONG_BOOL(name, flag)                                                           \
    static void (*orig_##name)(void *, int64_t, bool, const void *);                         \
    static void hook_##name(void *self, int64_t dmg, bool crit, const void *mi) {            \
        if (!(flag)) orig_##name(self, dmg, crit, mi);                                       \
    }

// Combat: the player, the pet and the random companion each take damage through their own methods.
SKIP_INT_BOOL(PlayerTakeDamage, sPlayerNoDamage)      // PlayerCompanion.TakeDamage(int amount, bool crit)
SKIP_LONG_BOOL(EnemyDamagePlayer, sPlayerNoDamage)    // Enemy.DamagePlayer(long dmg, bool crit)
SKIP_LONG_BOOL(EnemyDealDamageToPlayer, sPlayerNoDamage)
SKIP0(PlayerBleed, sPlayerNoDamage)                   // PlayerCompanion.BleedDamage()
SKIP_INT_BOOL(PetTakeDamage, sPetNoDamage)            // PetCompanion.TakeDamage(int, bool)
SKIP_LONG_BOOL(EnemyDamagePet, sPetNoDamage)          // Enemy.DamagePet(long, bool)
SKIP_INT_BOOL(CompanionTakeDamage, sCompanionNoDamage)  // RandomCompanion.TakeDamage(int, bool); its 3-argument
                                                        // overload (attacker effects) is not hooked
SKIP0(EnemyHealthPotion, sEnemiesNoHeal)              // Enemy.UseHealthPotion()
SKIP0(RadiationDamage, sNoRadiationDamage)            // GameManager.TakeRadiationDamage()

// Enemy.get_AttackSuccessRate(): 0 means every enemy attack misses. Enemy_CriticalHitChance() false,
// Player_CriticalHitChance() true (both answer the fight's dice rolls).
static float (*orig_AttackSuccessRate)(void *, const void *);
static float hook_AttackSuccessRate(void *self, const void *mi) { return sEnemiesMiss ? 0 : orig_AttackSuccessRate(self, mi); }
static bool (*orig_EnemyCrit)(void *, const void *);
static bool hook_EnemyCrit(void *self, const void *mi) { return sEnemiesMiss ? false : orig_EnemyCrit(self, mi); }
static bool (*orig_PlayerCrit)(void *, const void *);
static bool hook_PlayerCrit(void *self, const void *mi) { return sAlwaysCrit ? true : orig_PlayerCrit(self, mi); }

// ItemStack.GetScaledDamage() and GetScaledDamageWithStrength(int): the weapon's damage per hit (long).
static int64_t (*orig_ScaledDamage)(void *, const void *);
static int64_t hook_ScaledDamage(void *self, const void *mi) { return (int64_t)(orig_ScaledDamage(self, mi) * sWeaponDamage); }
static int64_t (*orig_ScaledDamageStrength)(void *, int32_t, const void *);
static int64_t hook_ScaledDamageStrength(void *self, int32_t strength, const void *mi) {
    return (int64_t)(orig_ScaledDamageStrength(self, strength, mi) * sWeaponDamage);
}

// Crafting: the static checks and the consume step answer yes without touching the inventory.
static bool (*orig_ConsumeCraftItems)(void *, int32_t, const void *);
static bool hook_ConsumeCraftItems(void *item, int32_t amount, const void *mi) {
    return sFreeCrafting ? true : orig_ConsumeCraftItems(item, amount, mi);
}
static bool (*orig_HasCraftItems)(void *, int32_t, const void *);
static bool hook_HasCraftItems(void *item, int32_t amount, const void *mi) {
    return sFreeCrafting ? true : orig_HasCraftItems(item, amount, mi);
}

// InventoryData.SetDurability(Items item, int value): every durability change goes through it.
static void (*orig_SetDurability)(void *, void *, int32_t, const void *);
static void hook_SetDurability(void *self, void *item, int32_t value, const void *mi) {
    if (!sNoWear) orig_SetDurability(self, item, value, mi);
}

// SkillsManager.get_SkillPoints(): what the skill tree lets you spend.
static int32_t (*orig_SkillPoints)(void *, const void *);
static int32_t hook_SkillPoints(void *self, const void *mi) { return sSkillPoints ? 99 : orig_SkillPoints(self, mi); }

// Stats that must never get worse. Each is an int property (0-100) with a getter and a setter. The HUD percent
// IS the property (phone log 2026-10-21: StatHandler 12/4 -> 7/1 -> 4/0 as the bars drained), and it drains
// toward 0, so "better" is higher: the setters keep the higher of the old and the new value, and the ticker
// below pins them at 100. Health is a float capped by HealthMax. Getters are hooked only to get their originals.
#define PASS_GETTER(name, type)                                                              \
    static type (*orig_get_##name)(void *, const void *);                                    \
    static type hook_get_##name(void *self, const void *mi) { return orig_get_##name(self, mi); }
#define KEEP_INT(name, flag, worse)                                                          \
    PASS_GETTER(name, int32_t)                                                               \
    static void (*orig_set_##name)(void *, int32_t, const void *);                           \
    static void hook_set_##name(void *self, int32_t value, const void *mi) {                 \
        if (flag) { int32_t now = orig_get_##name(self, mi); if (value worse now) value = now; } \
        orig_set_##name(self, value, mi);                                                    \
    }
KEEP_INT(Hunger, sKeepStats, <)
KEEP_INT(Thirst, sKeepStats, <)
KEEP_INT(Morale, sKeepStats, <)
KEEP_INT(PetHunger, sPetFull, <)
KEEP_INT(PetThirst, sPetFull, <)
KEEP_INT(PetHealth, sPetFull, <)

PASS_GETTER(HealthMax, float)
PASS_GETTER(Health, float)
static void (*orig_set_Health)(void *, float, const void *);
static void hook_set_Health(void *self, float value, const void *mi) {
    if (sKeepStats) { float max = orig_get_HealthMax(self, mi); if (value < max) value = max; }
    orig_set_Health(self, value, mi);
}

// Lock.LockPickHealth counts down while picking; keeping it at its highest seen value means a pick never breaks.
PASS_GETTER(LockPickHealth, float)
static void (*orig_set_LockPickHealth)(void *, float, const void *);
static void hook_set_LockPickHealth(void *self, float value, const void *mi) {
    if (sKeepLockpicks) { float now = orig_get_LockPickHealth(self, mi); if (value < now) value = now; }
    orig_set_LockPickHealth(self, value, mi);
}

// Without JIT the setters above are never reached, but their originals are still the real getters and
// setters (the runtime fills them when it swaps the MethodInfo). Two Unity callbacks the engine calls through
// the swapped pointers re-apply the values every frame instead: ExperienceLevel.Update for the player's
// stats, Lock.Update for the lockpick. The StatHandler is found with UnityEngine.Object.FindFirstObjectByType
// (a data call into the VM), once a second.
static void *il2cpp(const char *name) { return dlsym(RTLD_DEFAULT, name); }

// The live object of a game class: its static field (StatHandler.instance, Pet.p) when it has one, else
// UnityEngine.Object.FindFirstObjectByType. Why a lookup failed is logged once per class.
static void *findObject(const char *className, const char *staticField) {
    static char loggedFor[8][32];
    static int loggedCount;
    const char *why = NULL;
    void *found = NULL;
    void *(*domain_get)(void) = il2cpp("il2cpp_domain_get");
    void *(*assembly_open)(void *, const char *) = il2cpp("il2cpp_domain_assembly_open");
    void *(*assembly_get_image)(void *) = il2cpp("il2cpp_assembly_get_image");
    void *(*class_from_name)(void *, const char *, const char *) = il2cpp("il2cpp_class_from_name");
    void *(*method_from_name)(void *, const char *, int) = il2cpp("il2cpp_class_get_method_from_name");
    void *(*field_from_name)(void *, const char *) = il2cpp("il2cpp_class_get_field_from_name");
    void (*field_static_get)(void *, void *) = il2cpp("il2cpp_field_static_get_value");
    void *(*class_get_type)(void *) = il2cpp("il2cpp_class_get_type");
    void *(*type_get_object)(void *) = il2cpp("il2cpp_type_get_object");
    void *(*runtime_invoke)(void *, void *, void **, void **) = il2cpp("il2cpp_runtime_invoke");
    if (!domain_get || !assembly_open || !assembly_get_image || !class_from_name || !method_from_name ||
        !class_get_type || !type_get_object || !runtime_invoke) { why = "il2cpp exports missing"; goto done; }
    void *domain = domain_get();
    void *game = assembly_open(domain, "Assembly-CSharp"), *core = assembly_open(domain, "UnityEngine.CoreModule");
    if (!game || !core) { why = "assembly not open"; goto done; }
    void *klass = class_from_name(assembly_get_image(game), "", className);
    if (!klass) { why = "class not found"; goto done; }
    if (staticField && field_from_name && field_static_get) {
        void *field = field_from_name(klass, staticField);
        if (field) { field_static_get(field, &found); if (found) goto done; why = "static field is null"; }
        else why = "static field not found";
    }
    void *object = class_from_name(assembly_get_image(core), "UnityEngine", "Object");
    void *find = object ? method_from_name(object, "FindFirstObjectByType", 1) : NULL;
    if (!find) { if (!why) why = "FindFirstObjectByType not found"; goto done; }
    void *args[1] = {type_get_object(class_get_type(klass))};
    void *exception = NULL;
    found = runtime_invoke(find, NULL, args, &exception);
    if (exception) { found = NULL; why = "FindFirstObjectByType threw"; }
    else if (!found) why = "no object in the scene";
done:
    if (!found && why && loggedCount < 8) {
        for (int i = 0; i < loggedCount; i++) if (!strcmp(loggedFor[i], className)) return NULL;
        strlcpy(loggedFor[loggedCount++], className, 32);
        os_log(OS_LOG_DEFAULT, "[The Wanderer+] %{public}s not found: %{public}s", className, why);
    }
    return found;
}

static void *sStats;        // the StatHandler last found
static CFAbsoluteTime sStatsFoundAt, sStatsLoggedAt;
static int32_t sPeakHunger = -1, sPeakThirst = -1, sPeakMorale = -1;  // best values seen since the switch went on
static BOOL sExperienceUpdateSeen, sLockUpdateSeen;  // whether Unity reached the swapped callbacks (device log)

// 100 is full for hunger, thirst and morale (the setter clamps to 0-100 anyway)
#define PIN_FULL(name)     if (orig_get_##name && orig_set_##name && orig_get_##name(sStats, NULL) < 100) orig_set_##name(sStats, 100, NULL);

static void keepStatsTick(void) {
    if (!sKeepStats && !sPlayerNoDamage) return;
    CFAbsoluteTime t = CFAbsoluteTimeGetCurrent();
    if (!sStats || t - sStatsFoundAt > 1) {
        void *found = findObject("StatHandler", "instance");
        if (found && !sStats) os_log(OS_LOG_DEFAULT, "[The Wanderer+] StatHandler found");
        sStats = found;
        sStatsFoundAt = t;
    }
    if (!sStats) return;
    if (orig_get_HealthMax && orig_get_Health && orig_set_Health) {
        float max = orig_get_HealthMax(sStats, NULL);
        if (orig_get_Health(sStats, NULL) < max) orig_set_Health(sStats, max, NULL);
    }
    if (sKeepStats) {
        PIN_FULL(Hunger)
        PIN_FULL(Thirst)
        PIN_FULL(Morale)
    }
    if (t - sStatsLoggedAt > 10 && orig_get_Health && orig_get_HealthMax && orig_get_Hunger && orig_get_Thirst && orig_get_Morale) {
        sStatsLoggedAt = t;
        os_log(OS_LOG_DEFAULT, "[The Wanderer+] stats: health %{public}.0f/%{public}.0f hunger %{public}d thirst %{public}d morale %{public}d (peaks %{public}d %{public}d %{public}d, Update hooks reached: exp %{public}d lock %{public}d)",
               orig_get_Health(sStats, NULL), orig_get_HealthMax(sStats, NULL), orig_get_Hunger(sStats, NULL),
               orig_get_Thirst(sStats, NULL), orig_get_Morale(sStats, NULL), sPeakHunger, sPeakThirst, sPeakMorale,
               sExperienceUpdateSeen, sLockUpdateSeen);
    }
}

static float sPeakLockpick = -1;
static void keepLockpick(void *lock) {
    if (!sKeepLockpicks || !lock || !orig_get_LockPickHealth || !orig_set_LockPickHealth) return;
    float now = orig_get_LockPickHealth(lock, NULL);
    if (now > sPeakLockpick) sPeakLockpick = now;
    else if (now < sPeakLockpick) orig_set_LockPickHealth(lock, sPeakLockpick, NULL);
}

// Diagnostics for the HUD (phone, 2026-10-21: the bars change while PlayerStats.Hunger/Thirst read by the
// StatHandler stay put, so the HUD follows something else). FoodThirstStats.Update is the HUD component's own
// Unity callback; it keeps the last values it drew in _lastHunger (0x50) and _lastThirst (0x54). The stats line
// prints those next to the candidates: StatHandler, PerkManager.HungerAmount/ThirstAmount, SkillsManager.Hunger
// (0xD4) / Thirst (0xE4).
static void *sHud;
static void (*orig_HudUpdate)(void *, const void *);
static void hook_HudUpdate(void *self, const void *mi) {
    orig_HudUpdate(self, mi);
    sHud = self;
}
PASS_GETTER(HungerAmount, int32_t)
PASS_GETTER(ThirstAmount, int32_t)


// The two Unity callbacks: if Unity reaches the swapped pointers they tick too, and the log says so.
static void (*orig_ExperienceUpdate)(void *, const void *);
static void hook_ExperienceUpdate(void *self, const void *mi) {
    orig_ExperienceUpdate(self, mi);
    if (!sExperienceUpdateSeen) { sExperienceUpdateSeen = YES; os_log(OS_LOG_DEFAULT, "[The Wanderer+] ExperienceLevel.Update reached"); }
    keepStatsTick();
}

static void (*orig_LockUpdate)(void *, const void *);
static void hook_LockUpdate(void *self, const void *mi) {
    orig_LockUpdate(self, mi);
    if (!sLockUpdateSeen) { sLockUpdateSeen = YES; os_log(OS_LOG_DEFAULT, "[The Wanderer+] Lock.Update reached"); }
    keepLockpick(self);
}

// First phone test (2026-10-21): with the switch on, the bars still went down, so the swapped Update pointers
// are not what Unity calls (it keeps its own copy of a MonoBehaviour's message addresses). The refill therefore
// runs from a main-queue timer, twice a second, between Unity's frames; the managed calls are plain function
// calls on the thread the VM already runs on. The Lock is looked up while a lockpick is open.
// Calls an instance method of a game class by name through il2cpp_runtime_invoke (a data call, no JIT).
static BOOL callMethodOut(void *obj, const char *className, const char *method, int argc, void **argv, void **out) {
    void *(*domain_get)(void) = il2cpp("il2cpp_domain_get");
    void *(*assembly_open)(void *, const char *) = il2cpp("il2cpp_domain_assembly_open");
    void *(*assembly_get_image)(void *) = il2cpp("il2cpp_assembly_get_image");
    void *(*class_from_name)(void *, const char *, const char *) = il2cpp("il2cpp_class_from_name");
    void *(*method_from_name)(void *, const char *, int) = il2cpp("il2cpp_class_get_method_from_name");
    void *(*runtime_invoke)(void *, void *, void **, void **) = il2cpp("il2cpp_runtime_invoke");
    if (!obj || !domain_get || !assembly_open || !assembly_get_image || !class_from_name || !method_from_name || !runtime_invoke) return NO;
    void *game = assembly_open(domain_get(), "Assembly-CSharp");
    void *klass = game ? class_from_name(assembly_get_image(game), "", className) : NULL;
    void *m = klass ? method_from_name(klass, method, argc) : NULL;
    if (!m) return NO;
    void *exception = NULL;
    void *result = runtime_invoke(m, obj, argv, &exception);
    if (out) *out = exception ? NULL : result;
    return exception == NULL;
}
#define callMethod(obj, cls, method, argc, argv) callMethodOut(obj, cls, method, argc, argv, NULL)

static NSString *il2cppString(void *str) {
    const uint16_t *(*chars)(void *) = il2cpp("il2cpp_string_chars");
    int32_t (*length)(void *) = il2cpp("il2cpp_string_length");
    if (!str || !chars || !length) return nil;
    return [[NSString alloc] initWithCharacters:chars(str) length:(NSUInteger)length(str)];
}

// Item spawner. The catalogue is ItemManager.instance.items (an SOHolder): StorageItems 0x18, MaterialItems
// 0x28, BarterItems 0x30, BasicItems 0x40, CollectableItems 0x48; PurchaseableItems and AdvertItems are the
// shop's and are left out. An Items object has its category flags as bytes at fixed offsets (dump 10.1) and
// get_DisplayName(). Items.AddItem(Items itemToGive, bool exploring, int amt, bool showText) adds it.
static NSMutableArray<NSValue *> *sCatalogue;  // Items pointers, in the order of the list rows

static NSArray<NSDictionary<NSString *, NSString *> *> *buildCatalogue(void) {
    NSMutableArray<NSDictionary *> *rows = [NSMutableArray new];
    sCatalogue = [NSMutableArray new];
    void *manager = findObject("ItemManager", "instance");
    void *holder = manager ? *(void **)((char *)manager + 0x20) : NULL;
    if (!holder) return rows;
    static const int lists[] = {0x18, 0x28, 0x30, 0x40, 0x48};
    NSMutableSet<NSNumber *> *seen = [NSMutableSet new];
    NSMutableArray<NSArray *> *pairs = [NSMutableArray new];
    for (size_t l = 0; l < sizeof lists / sizeof lists[0]; l++) {
        void *array = *(void **)((char *)holder + lists[l]);
        if (!array) continue;
        int32_t count = *(int32_t *)((char *)array + 0x18);
        void **items = (void **)((char *)array + 0x20);
        for (int32_t i = 0; i < count; i++) {
            char *it = items[i];
            if (!it || [seen containsObject:@((uintptr_t)it)]) continue;
            [seen addObject:@((uintptr_t)it)];
            void *nameObject = NULL;
            callMethodOut(it, "Items", "get_DisplayName", 0, NULL, &nameObject);
            NSString *name = il2cppString(nameObject) ?: il2cppString(*(void **)(it + 0x20)) ?: @"(unnamed)";
            BOOL weapon = it[0x101] || it[0x122], ammo = it[0x102] || it[0x11D], wearable = it[0x103];
            BOOL material = it[0x104] || it[0x110], water = it[0x129], consumable = it[0x100];
            NSString *symbol = weapon ? @"scope" : ammo ? @"circle.grid.3x3.fill" : wearable ? @"tshirt.fill"
                             : water ? @"drop.fill" : consumable ? @"fork.knife" : material ? @"hammer.fill" : @"shippingbox.fill";
            NSString *kind = weapon ? @"weapon" : ammo ? @"ammo" : wearable ? @"wearable"
                           : water ? @"water" : consumable ? @"consumable" : material ? @"material" : @"misc";
            [pairs addObject:@[@{@"title": name, @"symbol": symbol, @"detail": kind}, [NSValue valueWithPointer:it]]];
        }
    }
    [pairs sortUsingComparator:^NSComparisonResult(NSArray *a, NSArray *b) {
        return [a[0][@"title"] localizedCaseInsensitiveCompare:b[0][@"title"]];
    }];
    for (NSArray *pair in pairs) { [rows addObject:pair[0]]; [sCatalogue addObject:pair[1]]; }
    os_log(OS_LOG_DEFAULT, "[The Wanderer+] catalogue: %{public}lu items", (unsigned long)rows.count);
    return rows;
}

static void spawnItem(NSInteger index) {
    if (index < 0 || index >= (NSInteger)sCatalogue.count) return;
    void *item = sCatalogue[index].pointerValue;
    int32_t amount = MAX(1, (int32_t)sSpawnAmount);
    uint8_t exploring = 0, showText = 1;
    void *argv[4] = {item, &exploring, &amount, &showText};
    BOOL ok = callMethod(item, "Items", "AddItem", 4, argv);
    os_log(OS_LOG_DEFAULT, "[The Wanderer+] spawn item %{public}ld x%{public}d: %{public}s", (long)index, amount, ok ? "ok" : "failed");
}

// InventoryData.GiveRandomItem(int level = -1) / GiveRandomWeapon(int level = -1): the game's own debug buttons.
static CFAbsoluteTime sLastGiveAt;
static void giveTick(void) {
    if (!sGiveItems && !sGiveWeapons) return;
    CFAbsoluteTime t = CFAbsoluteTimeGetCurrent();
    if (t - sLastGiveAt < 10) return;
    sLastGiveAt = t;
    void *inv = findObject("InventoryData", "instance");
    int32_t level = -1;
    void *argv[1] = {&level};
    if (sGiveItems) os_log(OS_LOG_DEFAULT, "[The Wanderer+] give random item: %{public}s", callMethod(inv, "InventoryData", "GiveRandomItem", 1, argv) ? "ok" : "failed");
    if (sGiveWeapons) os_log(OS_LOG_DEFAULT, "[The Wanderer+] give random weapon: %{public}s", callMethod(inv, "InventoryData", "GiveRandomWeapon", 1, argv) ? "ok" : "failed");
}

// No-JIT versions of the combat, skill and pet switches (owner, 2026-10-21: "like Fallout"). Each tick writes
// the live objects' fields or calls their setters instead of hooking the plain methods the game calls directly.
// Offsets from the 10.1 dump: Enemy.EnemyDamage 0x60 (long), BaseDamage 0x68, HasHealthPotion 0xB8;
// SkillsManager.WeaponsNeverBreak 0x7B, CriticalHit 0x94; Pet.PetHealthMax 0x90; RandomCompanion.Health 0x78,
// HealthMax 0x7C; FightCompanion.Damage 0x38 (long); PlayerStats.Rads 0x44.
static void *findObjects(const char *className, int32_t *count) {  // UnityEngine.Object.FindObjectsByType(Type, Include, None)
    *count = 0;
    void *(*domain_get)(void) = il2cpp("il2cpp_domain_get");
    void *(*assembly_open)(void *, const char *) = il2cpp("il2cpp_domain_assembly_open");
    void *(*assembly_get_image)(void *) = il2cpp("il2cpp_assembly_get_image");
    void *(*class_from_name)(void *, const char *, const char *) = il2cpp("il2cpp_class_from_name");
    void *(*method_from_name)(void *, const char *, int) = il2cpp("il2cpp_class_get_method_from_name");
    void *(*class_get_type)(void *) = il2cpp("il2cpp_class_get_type");
    void *(*type_get_object)(void *) = il2cpp("il2cpp_type_get_object");
    void *(*runtime_invoke)(void *, void *, void **, void **) = il2cpp("il2cpp_runtime_invoke");
    if (!domain_get || !assembly_open || !assembly_get_image || !class_from_name || !method_from_name ||
        !class_get_type || !type_get_object || !runtime_invoke) return NULL;
    void *domain = domain_get();
    void *game = assembly_open(domain, "Assembly-CSharp"), *core = assembly_open(domain, "UnityEngine.CoreModule");
    if (!game || !core) return NULL;
    void *klass = class_from_name(assembly_get_image(game), "", className);
    void *object = class_from_name(assembly_get_image(core), "UnityEngine", "Object");
    void *find = klass && object ? method_from_name(object, "FindObjectsByType", 3) : NULL;
    if (!find) return NULL;
    int32_t include = 0, sortNone = 0;  // active objects only, unsorted
    void *args[3] = {type_get_object(class_get_type(klass)), &include, &sortNone};
    void *exception = NULL;
    void *found = runtime_invoke(find, NULL, args, &exception);
    if (!found || exception) return NULL;
    *count = *(int32_t *)((char *)found + 0x18);
    return found;
}

static void setInt(void *obj, const char *cls, const char *setter, int32_t value) {
    void *argv[1] = {&value};
    callMethod(obj, cls, setter, 1, argv);
}

static int64_t sDamageBase = -1, sDamageSet = -1;
static void noJitTick(void) {
    char *skills = findObject("SkillsManager", "instance");
    if (skills) {
        if (sNoWear) skills[0x7B] = 1;                                     // WeaponsNeverBreak
        if (sAlwaysCrit) *(int32_t *)(skills + 0x94) = 100;                // CriticalHit, percent
        if (sSkillPoints && orig_SkillPoints && orig_SkillPoints(skills, NULL) < 50) setInt(skills, "SkillsManager", "set_SkillPoints", 99);
    }
    if (sEnemiesMiss || sEnemiesNoHeal) {
        int32_t n = 0;
        void *enemies = findObjects("Enemy", &n);
        for (int32_t i = 0; i < n; i++) {
            char *e = ((void **)((char *)enemies + 0x20))[i];
            if (!e) continue;
            if (sEnemiesMiss) { *(int64_t *)(e + 0x60) = 0; *(int32_t *)(e + 0x68) = 0; }  // their hits do nothing
            if (sEnemiesNoHeal) e[0xB8] = 0;                                              // no health potion
        }
    }
    char *pet = findObject("Pet", "p");
    if (pet && (sPetFull || sPetNoDamage)) {
        int32_t max = *(int32_t *)(pet + 0x90);
        if (orig_get_PetHealth && max > 0 && orig_get_PetHealth(pet, NULL) < max) setInt(pet, "Pet", "set_PetHealth", max);
        if (sPetFull) {
            if (orig_get_PetHunger && orig_get_PetHunger(pet, NULL) < 100) setInt(pet, "Pet", "set_PetHunger", 100);
            if (orig_get_PetThirst && orig_get_PetThirst(pet, NULL) < 100) setInt(pet, "Pet", "set_PetThirst", 100);
        }
    }
    char *companion = findObject("RandomCompanion", "instance");
    if (companion && sCompanionNoDamage) *(int32_t *)(companion + 0x78) = *(int32_t *)(companion + 0x7C);
    char *player = findObject("PlayerCompanion", "instance");
    if (player && sWeaponDamage > 1) {
        int64_t *damage = (int64_t *)(player + 0x38);
        if (*damage != sDamageSet) sDamageBase = *damage;   // the game recalculated it: new base
        int64_t want = (int64_t)(sDamageBase * sWeaponDamage);
        if (want > 0 && *damage != want) *damage = want;
        sDamageSet = want;
    }
    if (sNoRadiationDamage) {
        void *stats = findObject("StatHandler", "instance"), *playerStats = NULL;
        if (stats) callMethodOut(stats, "StatHandler", "get_playerStats", 0, NULL, &playerStats);
        if (playerStats) *(int32_t *)((char *)playerStats + 0x44) = 0;  // Rads
    }
}

static dispatch_source_t sTicker;  // kept alive for the life of the process
static void startTicker(void) {
    if (sTicker) return;
    sTicker = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
    dispatch_source_set_timer(sTicker, DISPATCH_TIME_NOW, (uint64_t)(0.5 * NSEC_PER_SEC), (uint64_t)(0.1 * NSEC_PER_SEC));
    dispatch_source_set_event_handler(sTicker, ^{
        keepStatsTick();
        noJitTick();
        giveTick();
        if (sKeepLockpicks) keepLockpick(findObject("Lock", NULL));
    });
    dispatch_resume(sTicker);
}

static void applyKeepAwake(void) {
    UIApplication.sharedApplication.idleTimerDisabled = [AMMenu boolForKey:@"keepAwake"];
}

#define REG(name, hook) AMIl2cppRegister(name, (void *)hook_##hook, (void **)&orig_##hook)

static void registerHooks(void) {
    // any phone (Unity callbacks)
    REG("ExperienceLevel.Update", ExperienceUpdate);
    REG("Lock.Update", LockUpdate);
    REG("FoodThirstStats.Update", HudUpdate);
    REG("PerkManager.get_HungerAmount", get_HungerAmount);
    REG("PerkManager.get_ThirstAmount", get_ThirstAmount);

    // (JIT)
    REG("PlayerCompanion.TakeDamage", PlayerTakeDamage);
    REG("Enemy.DamagePlayer", EnemyDamagePlayer);
    REG("Enemy.DealDamageToPlayer", EnemyDealDamageToPlayer);
    REG("PlayerCompanion.BleedDamage", PlayerBleed);
    REG("PetCompanion.TakeDamage", PetTakeDamage);
    REG("Enemy.DamagePet", EnemyDamagePet);
    REG("RandomCompanion.TakeDamage", CompanionTakeDamage);
    REG("Enemy.UseHealthPotion", EnemyHealthPotion);
    REG("GameManager.TakeRadiationDamage", RadiationDamage);
    REG("Enemy.get_AttackSuccessRate", AttackSuccessRate);
    REG("Enemy.Enemy_CriticalHitChance", EnemyCrit);
    REG("Enemy.Player_CriticalHitChance", PlayerCrit);
    REG("ItemStack.GetScaledDamage", ScaledDamage);
    REG("ItemStack.GetScaledDamageWithStrength", ScaledDamageStrength);
    REG("InventoryData.ConsumeHandCraftAccessibleItems", ConsumeCraftItems);
    REG("InventoryData.HasHandCraftAccessibleItemCount", HasCraftItems);
    REG("InventoryData.SetDurability", SetDurability);
    REG("SkillsManager.get_SkillPoints", SkillPoints);
    REG("StatHandler.get_HealthMax", get_HealthMax);
    REG("StatHandler.get_Health", get_Health);
    REG("StatHandler.set_Health", set_Health);
    REG("StatHandler.get_Hunger", get_Hunger);
    REG("StatHandler.set_Hunger", set_Hunger);
    REG("StatHandler.get_Thirst", get_Thirst);
    REG("StatHandler.set_Thirst", set_Thirst);
    REG("StatHandler.get_Morale", get_Morale);
    REG("StatHandler.set_Morale", set_Morale);
    REG("Pet.get_PetHunger", get_PetHunger);
    REG("Pet.set_PetHunger", set_PetHunger);
    REG("Pet.get_PetThirst", get_PetThirst);
    REG("Pet.set_PetThirst", set_PetThirst);
    REG("Pet.get_PetHealth", get_PetHealth);
    REG("Pet.set_PetHealth", set_PetHealth);
    REG("Lock.get_LockPickHealth", get_LockPickHealth);
    REG("Lock.set_LockPickHealth", set_LockPickHealth);
}

%ctor {
    // Plain methods only switch with JIT; without it their switches are labelled so nobody expects them to work.
    NSString *jit = AMIl2cppHasJIT() ? @"" : @" (JIT)";
    AMMenu *menu = AMMenu.shared;
    menu.title = @"The Wanderer+";
    [menu addSwitch:@"keepStats" title:@"Health, hunger, thirst, morale never drop" defaultValue:NO onChange:^(BOOL on) {
        sKeepStats = on;
        sPeakHunger = sPeakThirst = sPeakMorale = -1;
    }];
    [menu addSwitch:@"keepLockpicks" title:@"Lockpicks never break" defaultValue:NO onChange:^(BOOL on) {
        sKeepLockpicks = on;
        sPeakLockpick = -1;
    }];
    [menu addSlider:@"spawnAmount" title:@"Spawn amount" min:1 max:10 defaultValue:1 onChange:^(float v) { sSpawnAmount = v; }];
    [menu addList:@"Spawn an item" symbol:@"shippingbox" entries:^{ return buildCatalogue(); } onPick:^(NSInteger index) { spawnItem(index); }];
    [menu addSwitch:@"giveItems" title:@"Give a random item every 10 s" defaultValue:NO onChange:^(BOOL on) { sGiveItems = on; }];
    [menu addSwitch:@"giveWeapons" title:@"Give a random weapon every 10 s" defaultValue:NO onChange:^(BOOL on) { sGiveWeapons = on; }];
    [menu addSwitch:@"playerNoDamage" title:@"Player takes no damage (health refilled)" defaultValue:NO onChange:^(BOOL on) { sPlayerNoDamage = on; }];
    [menu addSwitch:@"petNoDamage" title:@"Pet takes no damage" defaultValue:NO onChange:^(BOOL on) { sPetNoDamage = on; }];
    [menu addSwitch:@"companionNoDamage" title:@"Companion takes no damage" defaultValue:NO onChange:^(BOOL on) { sCompanionNoDamage = on; }];
    [menu addSwitch:@"enemiesMiss" title:@"Enemies deal no damage" defaultValue:NO onChange:^(BOOL on) { sEnemiesMiss = on; }];
    [menu addSwitch:@"alwaysCrit" title:@"Player always crits" defaultValue:NO onChange:^(BOOL on) { sAlwaysCrit = on; }];
    [menu addSwitch:@"enemiesNoHeal" title:@"Enemies never heal" defaultValue:NO onChange:^(BOOL on) { sEnemiesNoHeal = on; }];
    [menu addSlider:@"weaponDamage" title:@"Weapon damage ×" min:1 max:20 defaultValue:1 onChange:^(float v) { sWeaponDamage = v; }];
    [menu addSwitch:@"noRadiationDamage" title:@"No radiation damage" defaultValue:NO onChange:^(BOOL on) { sNoRadiationDamage = on; }];
    [menu addSwitch:@"freeCrafting" title:[@"Crafting uses no materials" stringByAppendingString:jit] defaultValue:NO onChange:^(BOOL on) { sFreeCrafting = on; }];
    [menu addSwitch:@"noWear" title:@"Items never wear out" defaultValue:NO onChange:^(BOOL on) { sNoWear = on; }];
    [menu addSwitch:@"skillPoints" title:@"Unlimited skill points" defaultValue:NO onChange:^(BOOL on) { sSkillPoints = on; }];
    [menu addSwitch:@"petFull" title:@"Pet never hungry, thirsty or hurt" defaultValue:NO onChange:^(BOOL on) { sPetFull = on; }];
    [menu addSwitch:@"keepAwake" title:@"Keep screen awake" defaultValue:NO onChange:^(BOOL on) { applyKeepAwake(); }];

    // saved values from the last session
    sKeepStats = [AMMenu boolForKey:@"keepStats"];
    sKeepLockpicks = [AMMenu boolForKey:@"keepLockpicks"];
    sSpawnAmount = MAX([AMMenu floatForKey:@"spawnAmount"], 1);
    sGiveItems = [AMMenu boolForKey:@"giveItems"];
    sGiveWeapons = [AMMenu boolForKey:@"giveWeapons"];
    sPlayerNoDamage = [AMMenu boolForKey:@"playerNoDamage"];
    sPetNoDamage = [AMMenu boolForKey:@"petNoDamage"];
    sCompanionNoDamage = [AMMenu boolForKey:@"companionNoDamage"];
    sEnemiesMiss = [AMMenu boolForKey:@"enemiesMiss"];
    sAlwaysCrit = [AMMenu boolForKey:@"alwaysCrit"];
    sEnemiesNoHeal = [AMMenu boolForKey:@"enemiesNoHeal"];
    sWeaponDamage = MAX([AMMenu floatForKey:@"weaponDamage"], 1);
    sNoRadiationDamage = [AMMenu boolForKey:@"noRadiationDamage"];
    sFreeCrafting = [AMMenu boolForKey:@"freeCrafting"];
    sNoWear = [AMMenu boolForKey:@"noWear"];
    sSkillPoints = [AMMenu boolForKey:@"skillPoints"];
    sPetFull = [AMMenu boolForKey:@"petFull"];

    registerHooks();
    AMIl2cppSetReportHandler(^(NSString *report) {
        os_log(OS_LOG_DEFAULT, "[The Wanderer+] %{public}@", report);
        startTicker();
        // again later, so a device-log capture started after launch still sees it
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 20 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            os_log(OS_LOG_DEFAULT, "[The Wanderer+] report again: %{public}@", report);
        });
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
