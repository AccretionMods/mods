#include "AMDetectionHooks.h"
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdatomic.h>
#ifndef AM_DETECTION_DIAGNOSTICS_ENABLED
#define AM_DETECTION_DIAGNOSTICS_ENABLED 1
#endif

static const AMDetectionProfile *profile;
static AMDetectionOriginals originals;
static _Thread_local unsigned depth;
static atomic_uint calls[AM_DETECTION_API_COUNT], matches[AM_DETECTION_API_COUNT], filtered[AM_DETECTION_API_COUNT];
static atomic_bool ready[AM_DETECTION_API_COUNT];
static unsigned (*origin_resolver)(const void *);
static uint64_t (*callsite_resolver)(const void *);
static atomic_uint callsites_dropped;
/* state 0=empty, 1=being initialized, 2=immutable identity published. */
static struct { atomic_uint state; unsigned key; uint64_t token;
    atomic_uint results[AM_DETECTION_RESULT_COUNT]; } callsites[AM_DETECTION_CALLSITE_CAPACITY];
static atomic_uint current_phase,attribution_dropped;
static struct { atomic_uint key,calls,matched,filtered; } attribution[AM_DETECTION_ATTRIBUTION_CAPACITY];
static void increment(atomic_uint *a) {
    unsigned v=atomic_load_explicit(a,memory_order_relaxed);
    while (v<UINT32_MAX && !atomic_compare_exchange_weak_explicit(a,&v,v+1,memory_order_relaxed,memory_order_relaxed)) {}
}
bool AMDetectionConfigure(const AMDetectionProfile *p, const AMDetectionOriginals *o) {
    if (profile || !p || !o || p->mode>AM_DETECTION_FILTER) return false;
    originals=*o; profile=p;
    for (unsigned i=0;i<AM_DETECTION_API_COUNT;i++) atomic_store(&ready[i],true);
    return true;
}
void AMDetectionSetAdapterReady(AMDetectionAPI api,bool value) {
    if ((unsigned)api<AM_DETECTION_API_COUNT) atomic_store(&ready[api],value);
}
void AMDetectionRecord(AMDetectionAPI api, bool match, bool block) {
    if (!AM_DETECTION_DIAGNOSTICS_ENABLED) return;
    if ((unsigned)api>=AM_DETECTION_API_COUNT || !profile || profile->mode==AM_DETECTION_OFF) return;
    increment(&calls[api]); if (match) increment(&matches[api]); if (block) increment(&filtered[api]);
}
AMDetectionCounters AMDetectionReadCounters(AMDetectionAPI api) {
    if ((unsigned)api>=AM_DETECTION_API_COUNT) return (AMDetectionCounters){0};
    return (AMDetectionCounters){atomic_load(&calls[api]),atomic_load(&matches[api]),atomic_load(&filtered[api])};
}
void AMDetectionSetOriginResolver(unsigned (*resolver)(const void *)) { origin_resolver=resolver; }
void AMDetectionSetCallsiteResolver(uint64_t (*resolver)(const void *)) { callsite_resolver=resolver; }
unsigned AMDetectionCallsitesDropped(void) { return atomic_load(&callsites_dropped); }
bool AMDetectionSetPhase(AMDetectionPhase phase) {
    if ((unsigned)phase>=AM_DETECTION_PHASE_COUNT) return false;
    atomic_store(&current_phase,(unsigned)phase); return true;
}
AMDetectionPhase AMDetectionCurrentPhase(void) { return (AMDetectionPhase)atomic_load(&current_phase); }
unsigned AMDetectionAttributionDropped(void) { return atomic_load(&attribution_dropped); }
void AMDetectionRecordOutcome(AMDetectionAPI api,AMDetectionRuleRef rule,bool block,const void *caller,AMDetectionResult result) {
    if (!AM_DETECTION_DIAGNOSTICS_ENABLED) return;
    if ((unsigned)api>=AM_DETECTION_API_COUNT || !profile || profile->mode==AM_DETECTION_OFF) return;
    int saved=errno;
    if ((unsigned)rule.kind>AM_DETECTION_RULE_ENVIRONMENT || rule.index>=64) rule=(AMDetectionRuleRef){0};
    bool match=rule.kind!=AM_DETECTION_RULE_NONE;
    AMDetectionRecord(api,match,block);
    unsigned image=origin_resolver ? origin_resolver(caller) : AM_DETECTION_UNKNOWN_IMAGE;
    if (image>AM_DETECTION_UNKNOWN_IMAGE) image=AM_DETECTION_UNKNOWN_IMAGE;
    unsigned phase=atomic_load(&current_phase);
    unsigned key=1+rule.index+((unsigned)rule.kind<<6)+((unsigned)api<<9)+(phase<<12)+(image<<15);
    if (match) {
        uint64_t token=callsite_resolver ? callsite_resolver(caller) : 0;
        if ((unsigned)result>=AM_DETECTION_RESULT_COUNT) result=AM_DETECTION_RESULT_UNKNOWN;
        if (block) result=AM_DETECTION_RESULT_NOT_CALLED;
        unsigned hash=(unsigned)(token^(token>>32)^key),probe; bool recorded=false;
        for (probe=0;probe<AM_DETECTION_CALLSITE_CAPACITY;probe++) {
            unsigned i=(hash+probe)%AM_DETECTION_CALLSITE_CAPACITY;
            unsigned state=atomic_load_explicit(&callsites[i].state,memory_order_acquire);
            if (!state) {
                unsigned empty=0;
                if (atomic_compare_exchange_strong_explicit(&callsites[i].state,&empty,1,memory_order_acquire,memory_order_relaxed)) {
                    callsites[i].key=key; callsites[i].token=token;
                    atomic_store_explicit(&callsites[i].state,2,memory_order_release); state=2;
                } else state=empty;
            }
            /* A busy slot may hold this same identity: drop explicitly rather than duplicate it. */
            if (state==1) break;
            if (state==2 && callsites[i].key==key && callsites[i].token==token) {
                increment(&callsites[i].results[result]); recorded=true; break;
            }
        }
        if (!recorded) increment(&callsites_dropped);
    }
    unsigned hash=key^(key>>16); hash*=0x7feb352du; hash^=hash>>15;
    for (unsigned probe=0;probe<AM_DETECTION_ATTRIBUTION_CAPACITY;probe++) {
        unsigned index=(hash+probe)%AM_DETECTION_ATTRIBUTION_CAPACITY;
        unsigned occupied=atomic_load_explicit(&attribution[index].key,memory_order_acquire);
        if (!occupied) {
            unsigned empty=0;
            if (atomic_compare_exchange_strong_explicit(&attribution[index].key,&empty,key,memory_order_release,memory_order_relaxed)) occupied=key;
            else occupied=empty;
        }
        if (occupied==key) {
            increment(&attribution[index].calls);
            if (match) increment(&attribution[index].matched);
            if (block) increment(&attribution[index].filtered);
            errno=saved; return;
        }
    }
    increment(&attribution_dropped); errno=saved;
}
void AMDetectionRecordRule(AMDetectionAPI api,AMDetectionRuleRef rule,bool block,const void *caller) {
    AMDetectionRecordOutcome(api,rule,block,caller,AM_DETECTION_RESULT_UNKNOWN);
}
size_t AMDetectionReadCallsites(AMDetectionCallsite *out,size_t capacity) {
    size_t count=0;
    if (!out) return 0;
    for (unsigned i=0;i<AM_DETECTION_CALLSITE_CAPACITY && count<capacity;i++) {
        if (atomic_load_explicit(&callsites[i].state,memory_order_acquire)!=2) continue;
        unsigned raw=callsites[i].key-1;
        AMDetectionCallsite row={.token=callsites[i].token,.origin={.api=(AMDetectionAPI)((raw>>9)&7),
            .image=(raw>>15)&127,.phase=(AMDetectionPhase)((raw>>12)&7),
            .rule={(AMDetectionRuleKind)((raw>>6)&7),raw&63}}};
        bool any=false;
        for (unsigned r=0;r<AM_DETECTION_RESULT_COUNT;r++) { row.results[r]=atomic_load(&callsites[i].results[r]); any|=row.results[r]!=0; }
        if (any) out[count++]=row;
    }
    return count;
}
size_t AMDetectionReadAttribution(AMDetectionAttribution *out,size_t capacity) {
    size_t count=0;
    if (!out) return 0;
    for (unsigned i=0;i<AM_DETECTION_ATTRIBUTION_CAPACITY && count<capacity;i++) {
        unsigned key=atomic_load_explicit(&attribution[i].key,memory_order_acquire);
        if (!key) continue;
        unsigned raw=key-1;
        AMDetectionCounters counters={atomic_load(&attribution[i].calls),atomic_load(&attribution[i].matched),atomic_load(&attribution[i].filtered)};
        if (!counters.calls) continue;
        out[count++]=(AMDetectionAttribution){.api=(AMDetectionAPI)((raw>>9)&7),.image=(raw>>15)&127,
            .phase=(AMDetectionPhase)((raw>>12)&7),.rule={(AMDetectionRuleKind)((raw>>6)&7),raw&63},.counts=counters};
    }
    return count;
}
static bool block_path(AMDetectionAPI api, const char *path) {
    AMDetectionRuleRef rule=AMDetectionPathRule(profile,path);
    bool match=rule.kind!=AM_DETECTION_RULE_NONE;
    bool block=profile && profile->mode==AM_DETECTION_FILTER && match && atomic_load(&ready[api]);
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
        bool block=profile && profile->mode==AM_DETECTION_FILTER && match && atomic_load(&ready[AM_DETECTION_GETENV]);
    depth++; char *r=block ? NULL : originals.getenv_fn(name);
    AMDetectionRecordOutcome(AM_DETECTION_GETENV,rule,block,__builtin_return_address(0),r ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_NEGATIVE);
    depth--; return r;
}
