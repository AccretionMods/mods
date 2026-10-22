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

#define IL2CPP(name) ((__typeof__(name##_t) *)dlsym(RTLD_DEFAULT, "il2cpp_" #name))
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

static void *field(const char *cls, const char *name) {
    static NSMutableDictionary<NSString *, NSValue *> *cache;
    NSString *key = lookupKey(cls, name, -1);
    NSValue *hit = cache[key];
    if (hit) return hit.pointerValue;
    void *klass = AMIl2cppClass(cls);
    class_get_field_from_name_t *from_name = IL2CPP(class_get_field_from_name);
    if (!klass || !from_name) return NULL;
    void *f = from_name(klass, name);
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

void *AMIl2cppStaticObject(const char *cls, const char *name) {
    void *f = field(cls, name);
    field_static_get_value_t *get = IL2CPP(field_static_get_value);
    if (!f || !get) return NULL;
    if (!fieldIsStatic(f)) { logOnce([NSString stringWithFormat:@"%s.%s is not a static field", cls, name]); return NULL; }
    // the value is copied with the field's own size: room for a struct, not just a pointer
    char value[64] = {0};
    get(f, value);
    return *(void **)value;
}

void *AMIl2cppField(void *obj, const char *cls, const char *name) {
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
    void *klass = AMIl2cppClass(cls);
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
