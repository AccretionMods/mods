#ifndef __APPLE__
#ifndef _GNU_SOURCE
#define _GNU_SOURCE 1
#endif
#endif
#include "AMDetectionHooks.h"
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdlib.h>
/* Shared include model: existing game Makefiles still compile one source. */
#include "AMDetectionDiagnostics.c"

static const AMDetectionProfile *profile;
static AMDetectionOriginals originals;
static _Thread_local unsigned depth;
static atomic_bool ready[AM_DETECTION_API_COUNT];
static bool (*readiness_resolver)(AMDetectionAPI);
static unsigned (*origin_resolver)(const void *);
static uint64_t (*callsite_resolver)(const void *);
static bool am_detection_api_ready(AMDetectionEntryID);

bool AMDetectionConfigure(const AMDetectionProfile *p,const AMDetectionOriginals *o) {
    if (profile || !p || !o || p->mode>AM_DETECTION_FILTER) return false;
    if (!AMDetectionConfigureFiles(p,o,am_detection_api_ready)) return false;
    originals=*o;profile=p;
    AMDetectionDiagnosticsConfigure(p->mode!=AM_DETECTION_OFF);
    for (unsigned i=0;i<AM_DETECTION_API_COUNT;i++) atomic_store(&ready[i],true);
    return true;
}
void AMDetectionSetAdapterReady(AMDetectionAPI api,bool value) {
    if ((unsigned)api<AM_DETECTION_API_COUNT) atomic_store(&ready[api],value);
}
void AMDetectionSetReadinessResolver(bool (*resolver)(AMDetectionAPI)) { readiness_resolver=resolver; }
static bool am_detection_api_ready(AMDetectionEntryID entry) {
    if (entry<AM_DETECTION_API_COUNT && !atomic_load(&ready[entry])) return false;
    return !readiness_resolver || readiness_resolver((AMDetectionAPI)entry);
}
void AMDetectionSetOriginResolver(unsigned (*resolver)(const void *)) { origin_resolver=resolver; }
void AMDetectionSetCallsiteResolver(uint64_t (*resolver)(const void *)) { callsite_resolver=resolver; }
void AMDetectionRecord(AMDetectionAPI api,bool matched,bool filtered) {
    if ((unsigned)api<AM_DETECTION_API_COUNT && profile && profile->mode!=AM_DETECTION_OFF)
        AMDetectionCountEntry((AMDetectionEntryID)api,matched,filtered);
}
AMDetectionCounters AMDetectionReadCounters(AMDetectionAPI api) {
    return (unsigned)api<AM_DETECTION_API_COUNT ? AMDetectionReadEntryCounters((AMDetectionEntryID)api) : (AMDetectionCounters){0};
}
unsigned AMDetectionCallsitesDropped(void) { return AMDetectionReadCoverage().callsite_dropped; }
unsigned AMDetectionAttributionDropped(void) { return AMDetectionReadCoverage().attribution_dropped; }
static void am_detection_record_entry(AMDetectionEntryID entry,AMDetectionRuleRef rule,bool filtered,const void *caller,AMDetectionResult result,bool original_called,bool count_entry) {
    if (!AM_DETECTION_DIAGNOSTICS_ENABLED || !profile || profile->mode==AM_DETECTION_OFF) return;
    int saved=errno;
    if ((unsigned)rule.kind>=AM_DETECTION_RULE_COUNT || rule.index>=64) rule=(AMDetectionRuleRef){0};
    unsigned image=origin_resolver ? origin_resolver(caller) : AM_DETECTION_UNKNOWN_IMAGE;
    if (image>AM_DETECTION_UNKNOWN_IMAGE) image=AM_DETECTION_UNKNOWN_IMAGE;
    uint64_t token=rule.kind!=AM_DETECTION_RULE_NONE && callsite_resolver ? callsite_resolver(caller) : 0;
    AMDetectionEventKey key={entry,(uint16_t)image,rule,AMDetectionCurrentPhase()};
    if (!count_entry) AMDetectionRecordPostDetailEvent(key,token,filtered,result);
    else if (original_called) AMDetectionRecordPostEvent(key,token,filtered,result);
    else AMDetectionRecordEvent(key,token,filtered,result);
    errno=saved;
}
void AMDetectionRecordEntryOutcome(AMDetectionEntryID entry,AMDetectionRuleRef rule,bool filtered,const void *caller,AMDetectionResult result) {
    am_detection_record_entry(entry,rule,filtered,caller,result,false,true);
}
void AMDetectionRecordEntryPostOutcome(AMDetectionEntryID entry,AMDetectionRuleRef rule,bool filtered,const void *caller,AMDetectionResult result) {
    am_detection_record_entry(entry,rule,filtered,caller,result,true,true);
}
void AMDetectionRecordEntryDetailOutcome(AMDetectionEntryID entry,AMDetectionRuleRef rule,bool filtered,const void *caller,AMDetectionResult result) {
    am_detection_record_entry(entry,rule,filtered,caller,result,true,false);
}
void AMDetectionRecordOutcome(AMDetectionAPI api,AMDetectionRuleRef rule,bool filtered,const void *caller,AMDetectionResult result) {
    if ((unsigned)api<AM_DETECTION_API_COUNT) AMDetectionRecordEntryOutcome((AMDetectionEntryID)api,rule,filtered,caller,result);
}
void AMDetectionRecordRule(AMDetectionAPI api,AMDetectionRuleRef rule,bool filtered,const void *caller) {
    AMDetectionRecordOutcome(api,rule,filtered,caller,AM_DETECTION_RESULT_UNKNOWN);
}
static AMDetectionAPI am_detection_legacy_api(AMDetectionEntryID entry) {
    if (entry==AM_DETECTION_ENTRY_FOUNDATION_EXISTS || entry==AM_DETECTION_ENTRY_FOUNDATION_ATTRIBUTES) return AM_DETECTION_FOUNDATION;
    if (entry==AM_DETECTION_ENTRY_CAN_OPEN_URL) return AM_DETECTION_URL;
    return entry<AM_DETECTION_API_COUNT ? (AMDetectionAPI)entry : AM_DETECTION_API_COUNT;
}
size_t AMDetectionReadAttribution(AMDetectionAttribution *out,size_t capacity) {
    if (!AM_DETECTION_DIAGNOSTICS_ENABLED || !out || !capacity) return 0;
    AMDetectionEventRow *rows=calloc(AM_DETECTION_ATTRIBUTION_CAPACITY,sizeof *rows);
    if (!rows) { AMDetectionRecordAllocationFailure();return 0; }
    size_t n=AMDetectionReadEvents(rows,AM_DETECTION_ATTRIBUTION_CAPACITY),count=0;
    for (size_t i=0;i<n && count<capacity;i++) {
        AMDetectionAPI api=am_detection_legacy_api(rows[i].key.entry);
        if (api==AM_DETECTION_API_COUNT) continue;
        out[count++]=(AMDetectionAttribution){api,rows[i].key.image,rows[i].key.phase,rows[i].key.rule,rows[i].counts};
    }
    free(rows);return count;
}
size_t AMDetectionReadCallsites(AMDetectionCallsite *out,size_t capacity) {
    if (!AM_DETECTION_DIAGNOSTICS_ENABLED || !out || !capacity) return 0;
    AMDetectionEventCallsite *rows=calloc(AM_DETECTION_CALLSITE_CAPACITY,sizeof *rows);
    if (!rows) { AMDetectionRecordAllocationFailure();return 0; }
    size_t n=AMDetectionReadEventCallsites(rows,AM_DETECTION_CALLSITE_CAPACITY),count=0;
    for (size_t i=0;i<n && count<capacity;i++) {
        AMDetectionAPI api=am_detection_legacy_api(rows[i].key.entry);
        if (api==AM_DETECTION_API_COUNT) continue;
        AMDetectionCallsite row={.origin={.api=api,.image=rows[i].key.image,.phase=rows[i].key.phase,.rule=rows[i].key.rule},.token=rows[i].token};
        for (unsigned j=0;j<AM_DETECTION_RESULT_COUNT;j++) row.results[j]=rows[i].results[j];
        out[count++]=row;
    }
    free(rows);return count;
}
char *AMDetectionGetenv(const char *name) {
    if (!originals.getenv_fn) return NULL;
    if (depth) return originals.getenv_fn(name);
        AMDetectionRuleRef rule=AMDetectionEnvironmentRule(profile,name);
        bool match=rule.kind!=AM_DETECTION_RULE_NONE;
        bool block=profile && profile->mode==AM_DETECTION_FILTER && match && am_detection_api_ready(AM_DETECTION_GETENV);
    depth++; char *r=block ? NULL : originals.getenv_fn(name);
    AMDetectionRecordOutcome(AM_DETECTION_GETENV,rule,block,__builtin_return_address(0),r ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_NEGATIVE);
    depth--; return r;
}

#include "AMDetectionFiles.c"
