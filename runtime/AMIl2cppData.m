// Accretion Mods IL2CPP data helpers (see AMIl2cppData.h). Data calls only: nothing here changes code.

#import "AMIl2cppData.h"
#import "AMIl2cpp.h"
#import <dlfcn.h>
#import <os/log.h>
#import <string.h>

static const char *sTag = "AccretionMods";

void AMIl2cppDataSetLogTag(const char *tag) {
    if (tag && *tag) sTag = strdup(tag);
}

void AMIl2cppLog(NSString *format, ...) {
    va_list args;
    va_start(args, format);
    NSString *text = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);
    os_log(OS_LOG_DEFAULT, "[%{public}s] %{public}@", sTag, text);
}

// One message per distinct failure, so a ticker that runs twice a second doesn't flood the log.
static void logOnce(NSString *what) {
    static NSMutableSet<NSString *> *seen;
    if (!seen) seen = [NSMutableSet new];
    if ([seen containsObject:what] || seen.count > 64) return;
    [seen addObject:what];
    AMIl2cppLog(@"%@", what);
}

// Looked up once per use site: dlsym(RTLD_DEFAULT) searches every loaded image, and AMIl2cppField runs per object
// on every tick (uncached, 4 pins over the vault's objectives froze Fallout Shelter 0.34 s twice a second).
// A miss is not kept, so a lookup before the game's library is ready is tried again.
#define IL2CPP(name) ({ static void *found_; if (!found_) found_ = dlsym(RTLD_DEFAULT, "il2cpp_" #name); \
    (__typeof__(name##_t) *)found_; })
typedef void *domain_get_t(void);
typedef void *domain_assembly_open_t(void *domain, const char *name);
typedef void *assembly_get_image_t(void *assembly);
typedef void *class_from_name_t(void *image, const char *ns, const char *name);
typedef void *class_get_nested_types_t(void *klass, void **iter);
typedef const char *class_get_name_t(void *klass);
typedef void *class_get_method_from_name_t(void *klass, const char *name, int argc);
typedef void *class_get_field_from_name_t(void *klass, const char *name);
typedef void field_static_get_value_t(void *field, void *out);
typedef size_t field_get_offset_t(void *field);
typedef void *class_get_type_t(void *klass);
typedef void *type_get_object_t(void *type);
typedef void *runtime_invoke_t(void *method, void *obj, void **params, void **exception);
typedef void *object_unbox_t(void *obj);
typedef const uint16_t *string_chars_t(void *str);
typedef int32_t string_length_t(void *str);
typedef void *string_new_t(const char *utf8);

static void *lookupClass(const char *name);

// Classes are looked up once per name (a ticker asks for the same few every half second).
void *AMIl2cppClass(const char *name) {
    static NSMutableDictionary<NSString *, NSValue *> *cache;
    if (!name || !*name) return NULL;
    if (!cache) cache = [NSMutableDictionary new];
    NSString *key = @(name);
    NSValue *hit = cache[key];
    if (hit) return hit.pointerValue;
    void *klass = lookupClass(name);
    if (klass) cache[key] = [NSValue valueWithPointer:klass];
    return klass;
}

static void *lookupClass(const char *name) {
    domain_get_t *domain_get = IL2CPP(domain_get);
    domain_assembly_open_t *assembly_open = IL2CPP(domain_assembly_open);
    assembly_get_image_t *get_image = IL2CPP(assembly_get_image);
    class_from_name_t *from_name = IL2CPP(class_from_name);
    if (!domain_get || !assembly_open || !get_image || !from_name) { logOnce(@"il2cpp exports missing"); return NULL; }

    char buffer[256];
    strlcpy(buffer, name, sizeof buffer);
    char *rest = buffer, *assembly = "Assembly-CSharp";
    char *colon = strchr(buffer, ':');
    if (colon) { *colon = 0; assembly = buffer; rest = colon + 1; }
    void *asm_ = assembly_open(domain_get(), assembly);
    if (!asm_) { logOnce([NSString stringWithFormat:@"assembly %s not open (for %s)", assembly, name]); return NULL; }
    void *image = get_image(asm_);

    // Try every split of "A.B.C" into namespace and class, longest namespace first; then nested classes.
    char *parts[16];
    int count = 0;
    for (char *p = strtok(rest, "."); p && count < 16; p = strtok(NULL, ".")) parts[count++] = p;
    if (!count) return NULL;
    for (int split = count - 1; split >= 0; split--) {
        char ns[256] = "";
        for (int i = 0; i < split; i++) { if (i) strlcat(ns, ".", sizeof ns); strlcat(ns, parts[i], sizeof ns); }
        void *klass = from_name(image, ns, parts[split]);
        if (!klass) continue;
        // the remaining parts name nested classes
        class_get_nested_types_t *nested = IL2CPP(class_get_nested_types);
        class_get_name_t *get_name = IL2CPP(class_get_name);
        for (int i = split + 1; i < count && klass; i++) {
            void *found = NULL, *iter = NULL, *inner;
            if (!nested || !get_name) { klass = NULL; break; }
            while ((inner = nested(klass, &iter))) {
                if (strcmp(get_name(inner), parts[i]) == 0) { found = inner; break; }
            }
            klass = found;
        }
        if (klass) return klass;
    }
    logOnce([NSString stringWithFormat:@"class %s not found", name]);
    return NULL;
}

// FieldInfo and MethodInfo pointers live as long as the game, so a lookup by name is done once: the pins ask
// for the same ones twice a second.
static NSString *lookupKey(const char *cls, const char *name, int argc) {
    return [NSString stringWithFormat:@"%s.%s/%d", cls, name, argc];
}

typedef void *class_get_parent_t(void *klass);

static void *field(const char *cls, const char *name) {
    static NSMutableDictionary<NSString *, NSValue *> *cache;
    NSString *key = lookupKey(cls, name, -1);
    NSValue *hit = cache[key];
    if (hit) return hit.pointerValue;
    void *klass = AMIl2cppClass(cls);
    class_get_field_from_name_t *from_name = IL2CPP(class_get_field_from_name);
    if (!klass || !from_name) return NULL;
    void *f = from_name(klass, name);
    // a base class's field: Singleton<GameManager>.m_t holds GameManager's instance on the generic base,
    // which only the class's own parent chain reaches (its inflated Singleton<GameManager>)
    class_get_parent_t *parent = IL2CPP(class_get_parent);
    for (void *k = klass; !f && parent && (k = parent(k));) f = from_name(k, name);
    if (!f) { logOnce([NSString stringWithFormat:@"field %s.%s not found", cls, name]); return NULL; }
    if (!cache) cache = [NSMutableDictionary new];
    cache[key] = [NSValue valueWithPointer:f];
    return f;
}

static void *methodNamed(void *klass, const char *cls, const char *name, int argc) {
    static NSMutableDictionary<NSString *, NSValue *> *cache;
    NSString *key = lookupKey(cls, name, argc);
    NSValue *hit = cache[key];
    if (hit) return hit.pointerValue;
    class_get_method_from_name_t *from_name = IL2CPP(class_get_method_from_name);
    void *m = from_name ? from_name(klass, name, argc) : NULL;
    if (!m) return NULL;
    if (!cache) cache = [NSMutableDictionary new];
    cache[key] = [NSValue valueWithPointer:m];
    return m;
}

#define FIELD_ATTRIBUTE_STATIC 0x10
typedef int field_get_flags_t(void *field);

static bool fieldIsStatic(void *f) {
    field_get_flags_t *flags = IL2CPP(field_get_flags);
    return flags ? (flags(f) & FIELD_ATTRIBUTE_STATIC) != 0 : true;  // without the export, trust the caller
}

typedef void *field_get_parent_t(void *field);
typedef void runtime_class_init_t(void *klass);

// The class a static field's storage belongs to (a generic base for Singleton<T>), set up before the read: an
// inflated generic class the game hasn't touched yet has no static storage.
static void *staticOwner(void *f) {
    field_get_parent_t *owner = IL2CPP(field_get_parent);
    runtime_class_init_t *init = IL2CPP(runtime_class_init);
    void *klass = owner ? owner(f) : NULL;
    if (klass && init) init(klass);
    return klass;
}

void *AMIl2cppStaticObject(const char *cls, const char *name) {
    void *f = field(cls, name);
    field_static_get_value_t *get = IL2CPP(field_static_get_value);
    if (!f || !get) return NULL;
    if (!fieldIsStatic(f)) { logOnce([NSString stringWithFormat:@"%s.%s is not a static field", cls, name]); return NULL; }
    staticOwner(f);
    // the value is copied with the field's own size: room for a struct, not just a pointer
    char value[64] = {0};
    get(f, value);
    return *(void **)value;
}

static void *structField(void *obj, const char *cls, const char *name);

void *AMIl2cppField(void *obj, const char *cls, const char *name) {
    // "outer.inner": a value inside a struct the object holds (Stats { float hp; } stats -> "stats.hp")
    if (obj && strchr(name, '.')) return structField(obj, cls, name);
    void *f = field(cls, name);
    field_get_offset_t *offset = IL2CPP(field_get_offset);
    if (!f || !offset) return NULL;
    size_t at = offset(f);
    bool isStatic = fieldIsStatic(f);
    if (obj && isStatic) { logOnce([NSString stringWithFormat:@"%s.%s is static: pass NULL as the object", cls, name]); return NULL; }
    if (!obj && !isStatic) return NULL;  // an instance field with no object (the singleton is not there yet)
    if (obj) return (char *)obj + at;
    // a static field: its offset is inside the class's static data (writable through
    // il2cpp_class_get_static_field_data where the game exports it); otherwise a copy of the value is
    // returned, and a write to it changes nothing in the game
    typedef void *class_get_static_field_data_t(void *klass);
    class_get_static_field_data_t *static_data = IL2CPP(class_get_static_field_data);
    void *klass = staticOwner(f) ?: AMIl2cppClass(cls);
    if (static_data && klass) {
        void *base = static_data(klass);
        if (base) return (char *)base + at;
    }
    static char scratch[64];
    field_static_get_value_t *get = IL2CPP(field_static_get_value);
    if (!get) return NULL;
    memset(scratch, 0, sizeof scratch);
    get(f, scratch);
    logOnce([NSString stringWithFormat:@"static %s.%s read as a copy (writes won't stick)", cls, name]);
    return scratch;
}

typedef void *object_get_class_t(void *obj);
typedef bool class_is_assignable_from_t(void *klass, void *other);
typedef bool class_is_valuetype_t(void *klass);
typedef void *field_get_type_t(void *field);
typedef int type_get_type_t(void *type);
typedef void *class_from_type_t(void *type);
typedef void field_get_value_t(void *obj, void *field, void *out);
typedef void *method_get_return_type_t(void *method);

// A value inside a struct field, "outer.inner" (one level): the outer field's offset in the object, plus the
// inner field's offset in the struct. IL2CPP counts a value type's field offsets as if it were boxed (after the
// 16-byte object header), so the header comes off. NULL when the outer field is not a struct or a name is wrong.
static void *structField(void *obj, const char *cls, const char *name) {
    char outer[128];
    const char *dot = strchr(name, '.');
    size_t n = (size_t)(dot - name);
    if (n == 0 || n >= sizeof outer || strchr(dot + 1, '.')) return NULL;
    memcpy(outer, name, n);
    outer[n] = 0;
    void *f = field(cls, outer);
    field_get_offset_t *offset = IL2CPP(field_get_offset);
    field_get_type_t *get_type = IL2CPP(field_get_type);
    class_from_type_t *from_type = IL2CPP(class_from_type);
    class_is_valuetype_t *is_value = IL2CPP(class_is_valuetype);
    class_get_field_from_name_t *from_name = IL2CPP(class_get_field_from_name);
    if (!f || fieldIsStatic(f) || !offset || !get_type || !from_type || !from_name) return NULL;
    void *klass = from_type(get_type(f));
    if (!klass || (is_value && !is_value(klass))) {
        logOnce([NSString stringWithFormat:@"%s.%s is not a struct", cls, outer]);
        return NULL;
    }
    void *inner = from_name(klass, dot + 1);
    if (!inner) {
        logOnce([NSString stringWithFormat:@"field %s.%s not found", cls, name]);
        return NULL;
    }
    size_t at = offset(inner);
    if (at < 2 * sizeof(void *)) return NULL;  // not a boxed-layout offset: refuse rather than write elsewhere
    return (char *)obj + offset(f) + at - 2 * sizeof(void *);
}
typedef uint32_t gchandle_new_t(void *obj, bool pinned);
typedef void gchandle_free_t(uint32_t handle);

NSArray<NSValue *> *AMIl2cppRootObjects(const char *objectSpec) {
    if (!objectSpec) return @[];
    NSString *object = @(objectSpec);
    NSMutableArray *out = [NSMutableArray new];
    if ([object hasSuffix:@"[]"]) {
        int32_t n = 0;
        void *array = AMIl2cppFindObjects([object substringToIndex:object.length - 2].UTF8String, &n);
        for (int32_t i = 0; i < n && i < 4096; i++) {
            void *obj = AMIl2cppArrayAt(array, i);
            if (obj) [out addObject:[NSValue valueWithPointer:obj]];
        }
    } else {
        NSRange dot = [object rangeOfString:@"." options:NSBackwardsSearch];
        void *obj = dot.location != NSNotFound
            ? AMIl2cppStaticObject([object substringToIndex:dot.location].UTF8String, [object substringFromIndex:dot.location + 1].UTF8String)
            : AMIl2cppFindObject(objectSpec);
        if (obj) [out addObject:[NSValue valueWithPointer:obj]];
    }
    return out;
}

// A reference only: never interpret an inline struct (FP, EntityRef, KeyValuePair...) as an object pointer.
static bool pathReferenceType(void *type) {
    type_get_type_t *kind = IL2CPP(type_get_type);
    class_from_type_t *from = IL2CPP(class_from_type);
    class_is_valuetype_t *value = IL2CPP(class_is_valuetype);
    if (!type || !kind) return false;
    int t = kind(type);
    if (t == 14 || t == 18 || t == 20 || t == 28 || t == 29) return true;
    void *klass = t == 21 && from ? from(type) : NULL;
    return klass && value && !value(klass);
}

static bool pathMatches(void *obj, void *wanted) {
    object_get_class_t *get = IL2CPP(object_get_class);
    class_is_assignable_from_t *assign = IL2CPP(class_is_assignable_from);
    return obj && wanted && get && assign && assign(wanted, get(obj));
}

// Enumerators may be boxed value types: runtime_invoke then needs the unboxed receiver.
static bool pathInvoke(void *obj, const char *name, bool reference, void **out) {
    object_get_class_t *get = IL2CPP(object_get_class);
    class_get_method_from_name_t *find = IL2CPP(class_get_method_from_name);
    runtime_invoke_t *run = IL2CPP(runtime_invoke);
    class_is_valuetype_t *value = IL2CPP(class_is_valuetype);
    method_get_return_type_t *returns = IL2CPP(method_get_return_type);
    if (out) *out = NULL;
    if (!obj || !get || !find || !run || !value) return false;
    void *klass = get(obj), *method = find(klass, name, 0);
    if (!method || (reference && (!returns || !pathReferenceType(returns(method))))) return false;
    void *receiver = value(klass) ? AMIl2cppUnbox(obj) : obj;
    if (!receiver) return false;
    void *exception = NULL;
    void *result = run(method, receiver, NULL, &exception);
    if (exception) { logOnce(@"object path getter or collection changed: waiting for the next tick"); return false; }
    if (out) *out = result;
    return true;
}

static void pathCollect(NSMutableArray<NSValue *> *out, void *obj, void *wanted, NSString *each, NSUInteger *budget) {
    if (!*budget) return;
    if (!obj) { --*budget; return; }
    if (!each) {
        --*budget;
        if (pathMatches(obj, wanted)) [out addObject:[NSValue valueWithPointer:obj]];
        return;
    }
    if ([each isEqual:@"array"]) {
        object_get_class_t *get = IL2CPP(object_get_class);
        class_get_type_t *type = IL2CPP(class_get_type);
        type_get_type_t *kind = IL2CPP(type_get_type);
        int t = get && type && kind ? kind(type(get(obj))) : 0;
        if (t != 20 && t != 29) return;
        int32_t count = AMIl2cppArrayCount(obj);
        // Element assignability is checked before reading pointer-sized array elements.
        typedef void *class_get_element_class_t(void *klass);
        class_get_element_class_t *element = IL2CPP(class_get_element_class);
        class_is_valuetype_t *value = IL2CPP(class_is_valuetype);
        void *e = element ? element(get(obj)) : NULL;
        if (!e || !value || value(e)) return;
        for (int32_t i = 0; i < count && *budget; i++) pathCollect(out, AMIl2cppArrayAt(obj, i), wanted, nil, budget);
        return;
    }
    if ([each isEqual:@"values"] && !pathInvoke(obj, "get_Values", true, &obj)) return;
    if (![each isEqual:@"values"] && ![each isEqual:@"list"]) return;
    void *enumerator = NULL;
    if (!pathInvoke(obj, "System.Collections.IEnumerable.GetEnumerator", false, &enumerator)
        && !pathInvoke(obj, "GetEnumerator", false, &enumerator)) return;
    gchandle_new_t *hold = IL2CPP(gchandle_new);
    gchandle_free_t *release = IL2CPP(gchandle_free);
    if (!enumerator || !hold || !release) return;
    uint32_t handle = hold(enumerator, true);
    if (!handle) return;
    while (*budget) {
        void *boxed = NULL;
        if (!pathInvoke(enumerator, "MoveNext", false, &boxed)) break;
        bool *next = AMIl2cppUnbox(boxed);
        if (!next || !*next) break;
        // Count all entries, including null and wrong-type entries, against the visit budget.
        --*budget;
        void *item = NULL;
        if (!pathInvoke(enumerator, "System.Collections.IEnumerator.get_Current", false, &item)
            && !pathInvoke(enumerator, "get_Current", false, &item)) break;
        if (pathMatches(item, wanted)) [out addObject:[NSValue valueWithPointer:item]];
    }
    pathInvoke(enumerator, "Dispose", false, NULL);
    release(handle);
}

NSArray<NSValue *> *AMIl2cppPathObjects(NSArray<NSValue *> *roots, const char *rootClass, NSArray<NSDictionary *> *path) {
    if (!rootClass || ![path isKindOfClass:NSArray.class] || path.count > 8) return @[];
    void *owner = AMIl2cppClass(rootClass);
    NSMutableArray<NSValue *> *current = [NSMutableArray new];
    NSUInteger budget = 4096;
    for (NSValue *root in roots) { if (!budget) break; pathCollect(current, root.pointerValue, owner, nil, &budget); }
    for (NSDictionary *step in path) {
        if (![step isKindOfClass:NSDictionary.class] || ![step[@"class"] isKindOfClass:NSString.class]) return @[];
        if ((step[@"field"] && ![step[@"field"] isKindOfClass:NSString.class])
            || (step[@"getter"] && ![step[@"getter"] isKindOfClass:NSString.class])
            || (step[@"field"] && step[@"getter"])
            || (step[@"each"] && (![@[@"array", @"list", @"values"] containsObject:step[@"each"]]
                || (!step[@"field"] && !step[@"getter"])))) return @[];
        void *wanted = AMIl2cppClass([step[@"class"] UTF8String]);
        if (!wanted) return @[];
        NSMutableArray<NSValue *> *next = [NSMutableArray new];
        budget = 4096;
        for (NSValue *box in current) {
            if (!budget) break;
            void *obj = box.pointerValue, *child = obj;
            if (step[@"field"]) {
                void *f = field(rootClass, [step[@"field"] UTF8String]);
                field_get_type_t *type = IL2CPP(field_get_type);
                field_get_value_t *get = IL2CPP(field_get_value);
                if (!f || fieldIsStatic(f) || !type || !get || !pathReferenceType(type(f))) continue;
                child = NULL;
                get(obj, f, &child);
            } else if (step[@"getter"]) {
                if (!pathInvoke(obj, [step[@"getter"] UTF8String], true, &child)) continue;
            }
            pathCollect(next, child, wanted, step[@"each"], &budget);
        }
        if (!budget) logOnce(@"object path visit limit reached");
        current = next;
        rootClass = [step[@"class"] UTF8String];
    }
    return current;
}

static void *unityObjectMethod(const char *name, int argc, bool quiet) {
    void *object = AMIl2cppClass("UnityEngine.CoreModule:UnityEngine.Object");
    class_get_method_from_name_t *from_name = IL2CPP(class_get_method_from_name);
    if (!object || !from_name) return NULL;
    void *m = from_name(object, name, argc);
    if (!m && !quiet) logOnce([NSString stringWithFormat:@"UnityEngine.Object.%s/%d not found", name, argc]);
    return m;
}

static void *typeObject(const char *cls) {
    void *klass = AMIl2cppClass(cls);
    class_get_type_t *get_type = IL2CPP(class_get_type);
    type_get_object_t *get_object = IL2CPP(type_get_object);
    if (!klass || !get_type || !get_object) return NULL;
    return get_object(get_type(klass));
}

static void *invoke(void *method, void *obj, void **argv, const char *what) {
    runtime_invoke_t *run = IL2CPP(runtime_invoke);
    if (!run) return NULL;
    void *exception = NULL;
    void *result = run(method, obj, argv, &exception);
    if (exception) {
        logOnce([NSString stringWithFormat:@"%s threw", what]);
        return NULL;
    }
    return result;
}

// Unity 2023+ has FindFirstObjectByType / FindObjectsByType(Type, FindObjectsInactive, FindObjectsSortMode);
// older engines only FindObjectOfType / FindObjectsOfType(Type). Both are tried, newest first.
void *AMIl2cppFindObject(const char *cls) {
    void *type = typeObject(cls);
    if (!type) return NULL;
    void *args[1] = {type};
    void *find = unityObjectMethod("FindFirstObjectByType", 1, true);
    if (find) return invoke(find, NULL, args, "FindFirstObjectByType");
    find = unityObjectMethod("FindObjectOfType", 1, false);
    return find ? invoke(find, NULL, args, "FindObjectOfType") : NULL;
}

// True when cls derives from UnityEngine.ScriptableObject (its parent chain names it); remembered per class.
static bool isScriptableObject(const char *cls) {
    static NSMutableDictionary<NSString *, NSNumber *> *cache;
    NSString *key = [NSString stringWithUTF8String:cls];
    NSNumber *hit = cache[key];
    if (hit) return hit.boolValue;
    bool found = false;
    class_get_parent_t *parent = IL2CPP(class_get_parent);
    class_get_name_t *get_name = IL2CPP(class_get_name);
    void *klass = AMIl2cppClass(cls);
    for (void *k = klass; k && parent && get_name && !found; k = parent(k)) {
        const char *name = get_name(k);
        found = name && strcmp(name, "ScriptableObject") == 0;
    }
    if (!cache) cache = [NSMutableDictionary new];
    cache[key] = @(found);
    return found;
}

void *AMIl2cppFindObjects(const char *cls, int32_t *count) {
    if (count) *count = 0;
    void *type = typeObject(cls);
    if (!type) return NULL;
    int32_t include = 0, sortNone = 0;  // FindObjectsInactive.Exclude, FindObjectsSortMode.None
    void *array = NULL;
    void *find = unityObjectMethod("FindObjectsByType", 3, true);
    if (find) {
        void *args[3] = {type, &include, &sortNone};
        array = invoke(find, NULL, args, "FindObjectsByType");
    } else if ((find = unityObjectMethod("FindObjectsByType", 2, true))) {
        void *args[2] = {type, &sortNone};
        array = invoke(find, NULL, args, "FindObjectsByType");
    } else if ((find = unityObjectMethod("FindObjectsOfType", 1, false))) {
        void *args[1] = {type};
        array = invoke(find, NULL, args, "FindObjectsOfType");
    }
    // A ScriptableObject (a game's balance data: weapons, prices, spawn tables) is an asset, never a scene object,
    // so FindObjectsByType has none of it; Resources.FindObjectsOfTypeAll has the loaded ones. Only for those
    // classes: for scene classes it would also return prefab assets, which a pin must not change.
    if (AMIl2cppArrayCount(array) == 0 && isScriptableObject(cls)) {
        void *resources = AMIl2cppClass("UnityEngine.CoreModule:UnityEngine.Resources");
        class_get_method_from_name_t *from_name = IL2CPP(class_get_method_from_name);
        void *all = resources && from_name ? from_name(resources, "FindObjectsOfTypeAll", 1) : NULL;
        if (all) {
            void *args[1] = {type};
            array = invoke(all, NULL, args, "Resources.FindObjectsOfTypeAll");
        }
    }
    if (array && count) *count = AMIl2cppArrayCount(array);
    return array;
}

int32_t AMIl2cppArrayCount(void *array) {
    return array ? *(int32_t *)((char *)array + 0x18) : 0;
}

void *AMIl2cppArrayAt(void *array, int32_t index) {
    if (!array || index < 0 || index >= AMIl2cppArrayCount(array)) return NULL;
    return ((void **)((char *)array + 0x20))[index];
}

bool AMIl2cppCall(void *obj, const char *cls, const char *method, int argc, void **argv, void **out) {
    void *klass = AMIl2cppClass(cls);
    runtime_invoke_t *run = IL2CPP(runtime_invoke);
    if (!klass || !run) return false;
    void *m = methodNamed(klass, cls, method, argc);
    if (!m) { logOnce([NSString stringWithFormat:@"method %s.%s/%d not found", cls, method, argc]); return false; }
    // Without JIT a hooked method's MethodInfo points at our replacement, and runtime_invoke would call it:
    // a pin reading SkillsManager.get_SkillPoints got the hook's 99, not the game's real 0, and never wrote.
    // So the call goes to the game's function for its duration (main thread, a few microseconds).
    void **fields = m;
    void *now = fields[0], *real = AMIl2cppOriginalOf(now);
    bool second = false;
    if (real) {
        fields[0] = real;
        if (fields[1] == now) { fields[1] = real; second = true; }  // Unity 2021.2+ virtualMethodPointer
    }
    void *exception = NULL;
    void *result = run(m, obj, argv, &exception);
    if (real) {
        fields[0] = now;
        if (second) fields[1] = now;
    }
    if (exception) {
        logOnce([NSString stringWithFormat:@"%s.%s threw", cls, method]);
        return false;
    }
    if (out) *out = result;
    return true;
}

// A new object of a class, its no-argument constructor run through AMIl2cppCall (an exception there is caught and
// logged, not a crash). NULL when the class or the constructor is missing.
typedef void *object_new_t(void *klass);
void *AMIl2cppNew(const char *cls) {
    void *klass = AMIl2cppClass(cls);
    object_new_t *make = IL2CPP(object_new);
    if (!klass || !make) return NULL;
    void *obj = make(klass);
    return obj && AMIl2cppCall(obj, cls, ".ctor", 0, NULL, NULL) ? obj : NULL;
}

void *AMIl2cppUnbox(void *boxed) {
    object_unbox_t *unbox = IL2CPP(object_unbox);
    return boxed && unbox ? unbox(boxed) : NULL;
}

NSString *AMIl2cppString(void *str) {
    string_chars_t *chars = IL2CPP(string_chars);
    string_length_t *length = IL2CPP(string_length);
    if (!str || !chars || !length) return nil;
    return [[NSString alloc] initWithCharacters:chars(str) length:(NSUInteger)length(str)];
}

void *AMIl2cppNewString(const char *utf8) {
    string_new_t *make = IL2CPP(string_new);
    return utf8 && make ? make(utf8) : NULL;
}

void AMIl2cppEvery(double seconds, void (^block)(void)) {
    static NSMutableArray *timers;
    if (!timers) timers = [NSMutableArray new];
    if (!(seconds >= 0.05)) seconds = 0.05;  // never a hot loop (also catches NaN)
    dispatch_source_t timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
    dispatch_source_set_timer(timer, DISPATCH_TIME_NOW, (uint64_t)(seconds * NSEC_PER_SEC), (uint64_t)(0.1 * NSEC_PER_SEC));
    dispatch_source_set_event_handler(timer, block);
    dispatch_resume(timer);
    [timers addObject:timer];
    // a switch or slider just changed in the menu (AMMenuDidChangeNotification): run now, not on the next tick
    [NSNotificationCenter.defaultCenter addObserverForName:@"AMMenuDidChange" object:nil
                                                     queue:NSOperationQueue.mainQueue
                                                usingBlock:^(NSNotification *note) { block(); }];
}

void AMIl2cppPauseGame(bool pause) {
    static const char *time = "UnityEngine.CoreModule:UnityEngine.Time";
    static float saved = 1;
    static bool paused;
    if (pause == paused) return;
    void *boxed = NULL;
    float *now = AMIl2cppCall(NULL, time, "get_timeScale", 0, NULL, &boxed) ? AMIl2cppUnbox(boxed) : NULL;
    if (!now) return;
    if (pause) {
        saved = *now;
        float zero = 0;
        void *argv[1] = {&zero};
        paused = AMIl2cppCall(NULL, time, "set_timeScale", 1, argv, NULL);
    } else {
        paused = false;
        if (*now != 0) return;  // the game set its own scale while the menu was open: keep it
        void *argv[1] = {&saved};
        AMIl2cppCall(NULL, time, "set_timeScale", 1, argv, NULL);
    }
}
