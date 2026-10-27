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

bool AMDetectionConfigure(const AMDetectionProfile *p,const AMDetectionOriginals *o) {
    if (profile || !p || !o || p->mode>AM_DETECTION_FILTER) return false;
    originals=*o;profile=p;
    AMDetectionDiagnosticsConfigure(p->mode!=AM_DETECTION_OFF);
    for (unsigned i=0;i<AM_DETECTION_API_COUNT;i++) atomic_store(&ready[i],true);
    return true;
}
void AMDetectionSetAdapterReady(AMDetectionAPI api,bool value) {
    if ((unsigned)api<AM_DETECTION_API_COUNT) atomic_store(&ready[api],value);
}
void AMDetectionSetReadinessResolver(bool (*resolver)(AMDetectionAPI)) { readiness_resolver=resolver; }
static bool am_detection_api_ready(AMDetectionAPI api) {
    return atomic_load(&ready[api]) && (!readiness_resolver || readiness_resolver(api));
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
void AMDetectionRecordEntryOutcome(AMDetectionEntryID entry,AMDetectionRuleRef rule,bool filtered,const void *caller,AMDetectionResult result) {
    if (!AM_DETECTION_DIAGNOSTICS_ENABLED || !profile || profile->mode==AM_DETECTION_OFF) return;
    int saved=errno;
    if ((unsigned)rule.kind>AM_DETECTION_RULE_ENVIRONMENT || rule.index>=64) rule=(AMDetectionRuleRef){0};
    unsigned image=origin_resolver ? origin_resolver(caller) : AM_DETECTION_UNKNOWN_IMAGE;
    if (image>AM_DETECTION_UNKNOWN_IMAGE) image=AM_DETECTION_UNKNOWN_IMAGE;
    uint64_t token=rule.kind!=AM_DETECTION_RULE_NONE && callsite_resolver ? callsite_resolver(caller) : 0;
    AMDetectionRecordEvent((AMDetectionEventKey){entry,(uint16_t)image,rule,AMDetectionCurrentPhase()},token,filtered,result);
    errno=saved;
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
static bool block_path(AMDetectionAPI api, const char *path) {
    AMDetectionRuleRef rule=AMDetectionPathRule(profile,path);
    bool match=rule.kind!=AM_DETECTION_RULE_NONE;
    bool block=profile && profile->mode==AM_DETECTION_FILTER && match && am_detection_api_ready(api);
    return block;
}
static AMDetectionResult file_result(bool success) {
    return success ? AM_DETECTION_RESULT_SUCCESS : (errno==ENOENT || errno==ENOTDIR) ? AM_DETECTION_RESULT_NEGATIVE : AM_DETECTION_RESULT_ERROR;
}
#define RECORD_PATH(api,p,block,success) AMDetectionRecordOutcome(api,AMDetectionPathRule(profile,p),block,__builtin_return_address(0),file_result(success))
#define MISSING() do { errno=ENOSYS; return -1; } while (0)
int AMDetectionAccess(const char *p,int mode) {
    if (!originals.access_fn) MISSING();
    if (depth) return originals.access_fn(p,mode);
    bool block=block_path(AM_DETECTION_ACCESS,p);
    depth++; int r; if (block) { errno=ENOENT; r=-1; } else r=originals.access_fn(p,mode);
    RECORD_PATH(AM_DETECTION_ACCESS,p,block,r==0); depth--; return r;
}
int AMDetectionStat(const char *p,struct stat *s) {
    if (!originals.stat_fn) MISSING();
    if (depth) return originals.stat_fn(p,s);
    bool block=block_path(AM_DETECTION_STAT,p);
    depth++; int r; if (block) { errno=ENOENT; r=-1; } else r=originals.stat_fn(p,s);
    RECORD_PATH(AM_DETECTION_STAT,p,block,r==0); depth--; return r;
}
int AMDetectionLstat(const char *p,struct stat *s) {
    if (!originals.lstat_fn) MISSING();
    if (depth) return originals.lstat_fn(p,s);
    bool block=block_path(AM_DETECTION_LSTAT,p);
    depth++; int r; if (block) { errno=ENOENT; r=-1; } else r=originals.lstat_fn(p,s);
    RECORD_PATH(AM_DETECTION_LSTAT,p,block,r==0); depth--; return r;
}
int AMDetectionOpen(const char *p,int flags,...) {
    if (!originals.open_fn) MISSING();
    bool has_mode=(flags&O_CREAT)!=0;
#ifdef O_TMPFILE
    has_mode=has_mode || (flags&O_TMPFILE)==O_TMPFILE;
#endif
    int mode=0; if (has_mode) { va_list args; va_start(args,flags); mode=va_arg(args,int); va_end(args); }
    if (depth) return has_mode ? originals.open_fn(p,flags,mode) : originals.open_fn(p,flags);
    bool block=block_path(AM_DETECTION_OPEN,p);
    depth++; int r; if (block) { errno=ENOENT; r=-1; } else r=has_mode ? originals.open_fn(p,flags,mode) : originals.open_fn(p,flags);
    RECORD_PATH(AM_DETECTION_OPEN,p,block,r>=0); depth--; return r;
}
FILE *AMDetectionFopen(const char *p,const char *mode) {
    if (!originals.fopen_fn) { errno=ENOSYS; return NULL; }
    if (depth) return originals.fopen_fn(p,mode);
    bool block=block_path(AM_DETECTION_FOPEN,p);
    depth++; FILE *r; if (block) { errno=ENOENT; r=NULL; } else r=originals.fopen_fn(p,mode);
    RECORD_PATH(AM_DETECTION_FOPEN,p,block,r!=NULL); depth--; return r;
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
