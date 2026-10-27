#include "AMDetectionRegistry.h"
#include <stdatomic.h>

static void *am_registry_originals[AM_DETECTION_REGISTRY_ID_LIMIT];
#define AM_ENTRY(id,name,module,abi,rule,requirements) \
    {id,name,module,abi,rule,requirements,module,&am_registry_originals[id],NULL,NULL,NULL}
/* Append-only wire IDs: 6/7 remain historical aggregates, never concrete hooks. */
static AMDetectionEntry am_registry_entries[]={
    AM_ENTRY(0,"access",AM_DETECTION_MODULE_FILES,AM_DETECTION_ABI_NATIVE,AM_DETECTION_RULE_PATH,5),
    AM_ENTRY(1,"stat",AM_DETECTION_MODULE_FILES,AM_DETECTION_ABI_NATIVE,AM_DETECTION_RULE_PATH,5),
    AM_ENTRY(2,"lstat",AM_DETECTION_MODULE_FILES,AM_DETECTION_ABI_NATIVE,AM_DETECTION_RULE_PATH,5),
    AM_ENTRY(3,"open",AM_DETECTION_MODULE_FILES,AM_DETECTION_ABI_NATIVE,AM_DETECTION_RULE_PATH,5),
    AM_ENTRY(4,"fopen",AM_DETECTION_MODULE_FILES,AM_DETECTION_ABI_NATIVE,AM_DETECTION_RULE_PATH,5),
    AM_ENTRY(5,"getenv",AM_DETECTION_MODULE_ENVIRONMENT,AM_DETECTION_ABI_NATIVE,AM_DETECTION_RULE_ENVIRONMENT,5),
    AM_ENTRY(6,"foundation",AM_DETECTION_MODULE_FOUNDATION,AM_DETECTION_ABI_AGGREGATE,AM_DETECTION_RULE_NONE,0),
    AM_ENTRY(7,"urls",AM_DETECTION_MODULE_URLS,AM_DETECTION_ABI_AGGREGATE,AM_DETECTION_RULE_NONE,0),
    AM_ENTRY(8,"foundation_exists",AM_DETECTION_MODULE_FOUNDATION,AM_DETECTION_ABI_OBJC,AM_DETECTION_RULE_PATH,6),
    AM_ENTRY(9,"foundation_attributes",AM_DETECTION_MODULE_FOUNDATION,AM_DETECTION_ABI_OBJC,AM_DETECTION_RULE_PATH,6),
    AM_ENTRY(10,"can_open_url",AM_DETECTION_MODULE_URLS,AM_DETECTION_ABI_OBJC,AM_DETECTION_RULE_SCHEME,6)
};
#undef AM_ENTRY
static bool am_registry_configured;
static AMDetectionMode am_registry_mode;
static bool am_registry_selected[AM_DETECTION_REGISTRY_ID_LIMIT];
/* Installation is serialized by the coordinator. Readers only access atomics. */
static struct { atomic_bool active; bool prepared,mode_set; AMDetectionMode mode;
    atomic_uint state,reason; } am_registry_modules[AM_DETECTION_MODULE_COUNT];

size_t AMDetectionEntryCount(void) { return sizeof am_registry_entries/sizeof *am_registry_entries; }
const char *AMDetectionModuleName(AMDetectionModuleID module) {
    static const char *const names[]={"filesystem","environment","foundation","urls","directories",
        "writes","preferences","loader","objc","process","sdk"};
    return module<AM_DETECTION_MODULE_COUNT ? names[module] : "unknown";
}
const char *AMDetectionSupportReasonName(AMDetectionSupportReason reason) {
    static const char *const names[]={"none","not_selected","not_prepared","prepare_failed","publish_failed","fingerprint_mismatch","mode_off","fingerprint_unavailable"};
    return (unsigned)reason<sizeof names/sizeof *names ? names[reason] : "unknown";
}
const AMDetectionEntry *AMDetectionEntryAt(size_t index) {
    return index<AMDetectionEntryCount() ? &am_registry_entries[index] : NULL;
}
const AMDetectionEntry *AMDetectionEntryByID(AMDetectionEntryID id) {
    for (size_t i=0;i<AMDetectionEntryCount();i++) if (am_registry_entries[i].id==id) return &am_registry_entries[i];
    return NULL;
}
bool AMDetectionValidateSelection(const AMDetectionEntryID *ids,size_t count,size_t compat) {
    if ((!ids && count) || count>AM_DETECTION_MAX_ENTRYPOINTS || compat>AM_DETECTION_MAX_NATIVE_BINDINGS) return false;
    size_t native=0;
    for (size_t i=0;i<count;i++) {
        const AMDetectionEntry *entry=AMDetectionEntryByID(ids[i]);
        if (!entry || entry->abi==AM_DETECTION_ABI_AGGREGATE || entry->id>=AM_DETECTION_REGISTRY_ID_LIMIT) return false;
        for (size_t j=0;j<i;j++) if (ids[j]==ids[i]) return false;
        if (entry->abi==AM_DETECTION_ABI_NATIVE) native++;
    }
    return native<=AM_DETECTION_MAX_NATIVE_BINDINGS-compat;
}
bool AMDetectionSetInstaller(AMDetectionEntryID id,AMDetectionInstall prepare,AMDetectionInstall publish,void *context) {
    if (am_registry_configured || !prepare || !publish) return false;
    for (size_t i=0;i<AMDetectionEntryCount();i++) if (am_registry_entries[i].id==id) {
        if (am_registry_entries[i].abi==AM_DETECTION_ABI_AGGREGATE) return false;
        am_registry_entries[i].prepare=prepare; am_registry_entries[i].publish=publish;
        am_registry_entries[i].context=context; return true;
    }
    return false;
}
bool AMDetectionEntrySelected(AMDetectionEntryID id) {
    return am_registry_configured && id<AM_DETECTION_REGISTRY_ID_LIMIT && am_registry_selected[id];
}
bool AMDetectionSetModuleMode(AMDetectionModuleID module,AMDetectionMode mode) {
    if (am_registry_configured || module>=AM_DETECTION_MODULE_COUNT || (unsigned)mode>AM_DETECTION_FILTER) return false;
    am_registry_modules[module].mode=mode;am_registry_modules[module].mode_set=true;return true;
}
bool AMDetectionConfigureRegistry(const AMDetectionEntryID *ids,size_t count,size_t compat,AMDetectionMode mode) {
    if (am_registry_configured || (unsigned)mode>AM_DETECTION_FILTER || !AMDetectionValidateSelection(ids,count,compat)) return false;
    for (unsigned m=0;m<AM_DETECTION_MODULE_COUNT;m++)
        if (am_registry_modules[m].mode_set && am_registry_modules[m].mode>mode) return false;
    for (size_t i=0;i<count;i++) am_registry_selected[ids[i]]=true;
    am_registry_mode=mode;
    for (unsigned m=0;m<AM_DETECTION_MODULE_COUNT;m++) {
        if (!am_registry_modules[m].mode_set) am_registry_modules[m].mode=mode;
        am_registry_modules[m].mode=AMDetectionEffectiveMode(mode,am_registry_modules[m].mode);
        atomic_store(&am_registry_modules[m].state,AM_DETECTION_DISABLED);
        atomic_store(&am_registry_modules[m].reason,AM_DETECTION_REASON_NOT_SELECTED);
    }
    am_registry_configured=true; return true;
}
void AMDetectionInvalidateModule(AMDetectionModuleID module) {
    if (module>=AM_DETECTION_MODULE_COUNT) return;
    atomic_store_explicit(&am_registry_modules[module].active,false,memory_order_release);
    am_registry_modules[module].prepared=false;
    if (am_registry_configured && am_registry_modules[module].mode!=AM_DETECTION_OFF) {
        atomic_store(&am_registry_modules[module].state,AM_DETECTION_UNSUPPORTED);
        atomic_store(&am_registry_modules[module].reason,AM_DETECTION_REASON_NOT_PREPARED);
    }
}
void AMDetectionSetModuleUnsupported(AMDetectionModuleID module,AMDetectionSupportReason reason) {
    if (module>=AM_DETECTION_MODULE_COUNT || !am_registry_configured || am_registry_modules[module].mode==AM_DETECTION_OFF) return;
    AMDetectionInvalidateModule(module);
    atomic_store(&am_registry_modules[module].state,AM_DETECTION_UNSUPPORTED);
    atomic_store(&am_registry_modules[module].reason,(unsigned)reason<=AM_DETECTION_REASON_FINGERPRINT_UNAVAILABLE ? reason : AM_DETECTION_REASON_PREPARE_FAILED);
}
bool AMDetectionPrepareModule(AMDetectionModuleID module) {
    if (module>=AM_DETECTION_MODULE_COUNT || !am_registry_configured || am_registry_mode==AM_DETECTION_OFF) return false;
    if (am_registry_modules[module].mode==AM_DETECTION_OFF) {
        atomic_store(&am_registry_modules[module].state,AM_DETECTION_DISABLED);
        atomic_store(&am_registry_modules[module].reason,AM_DETECTION_REASON_MODE_OFF);return false;
    }
    AMDetectionInvalidateModule(module);
    bool any=false;
    for (size_t i=0;i<AMDetectionEntryCount();i++) {
        const AMDetectionEntry *entry=&am_registry_entries[i];
        if (entry->module!=module || !AMDetectionEntrySelected(entry->id)) continue;
        any=true;
        if (!entry->prepare || !entry->publish || !entry->prepare(entry,entry->context)) {
            atomic_store(&am_registry_modules[module].state,AM_DETECTION_UNSUPPORTED);
            atomic_store(&am_registry_modules[module].reason,AM_DETECTION_REASON_PREPARE_FAILED); return false;
        }
    }
    am_registry_modules[module].prepared=any;
    atomic_store(&am_registry_modules[module].state,any ? AM_DETECTION_UNSUPPORTED : AM_DETECTION_DISABLED);
    atomic_store(&am_registry_modules[module].reason,any ? AM_DETECTION_REASON_NOT_PREPARED : AM_DETECTION_REASON_NOT_SELECTED);
    return any;
}
bool AMDetectionPublishModule(AMDetectionModuleID module) {
    if (module>=AM_DETECTION_MODULE_COUNT || !am_registry_configured || !am_registry_modules[module].prepared) return false;
    /* A repeated publication is a fresh validation transaction, never a live partial update. */
    atomic_store_explicit(&am_registry_modules[module].active,false,memory_order_release);
    for (size_t i=0;i<AMDetectionEntryCount();i++) {
        const AMDetectionEntry *entry=&am_registry_entries[i];
        if (entry->module!=module || !AMDetectionEntrySelected(entry->id)) continue;
        if (!entry->publish(entry,entry->context)) {
            am_registry_modules[module].prepared=false;
            atomic_store(&am_registry_modules[module].state,AM_DETECTION_FAILED);
            atomic_store(&am_registry_modules[module].reason,AM_DETECTION_REASON_PUBLISH_FAILED); return false;
        }
    }
    atomic_store(&am_registry_modules[module].reason,AM_DETECTION_REASON_NONE);
    atomic_store(&am_registry_modules[module].state,am_registry_modules[module].mode==AM_DETECTION_FILTER ? AM_DETECTION_FILTERING : AM_DETECTION_OBSERVING);
    atomic_store_explicit(&am_registry_modules[module].active,am_registry_modules[module].mode==AM_DETECTION_FILTER,memory_order_release);
    return true;
}
bool AMDetectionModuleActive(AMDetectionModuleID module) {
    return module<AM_DETECTION_MODULE_COUNT && atomic_load_explicit(&am_registry_modules[module].active,memory_order_acquire);
}
AMDetectionModuleResult AMDetectionModuleStatus(AMDetectionModuleID module) {
    if (module>=AM_DETECTION_MODULE_COUNT) return (AMDetectionModuleResult){AM_DETECTION_UNSUPPORTED,AM_DETECTION_REASON_NOT_SELECTED};
    return (AMDetectionModuleResult){atomic_load(&am_registry_modules[module].state),atomic_load(&am_registry_modules[module].reason)};
}
