#include "AMDetectionDiagnostics.h"
#include <stdatomic.h>

bool AMDetectionEventKeyEqual(const AMDetectionEventKey *a,const AMDetectionEventKey *b) {
    return a && b && a->entry==b->entry && a->image==b->image && a->rule.kind==b->rule.kind &&
        a->rule.index==b->rule.index && a->phase==b->phase;
}
#if AM_DETECTION_DIAGNOSTICS_ENABLED
static atomic_bool am_diag_enabled,am_diag_saturated;
static atomic_uint am_diag_phase,am_diag_attribution_dropped,am_diag_callsite_dropped,am_diag_allocation_failures,am_diag_operational_gaps;
static struct { atomic_uint calls,matched,filtered; } am_diag_entry_counts[AM_DETECTION_REGISTRY_ID_LIMIT];
/* state 0=empty, 1=initializing, 2=immutable identity published. */
static struct { atomic_uint state; AMDetectionEventKey key;
    atomic_uint calls,matched,filtered; } am_diag_attribution[AM_DETECTION_ATTRIBUTION_CAPACITY];
static struct { atomic_uint state; AMDetectionEventKey key; uint64_t token;
    atomic_uint results[AM_DETECTION_RESULT_COUNT]; } am_diag_callsites[AM_DETECTION_CALLSITE_CAPACITY];
static void am_diag_increment(atomic_uint *counter) {
    unsigned value=atomic_load_explicit(counter,memory_order_relaxed);
    while (value<UINT32_MAX) {
        if (atomic_compare_exchange_weak_explicit(counter,&value,value+1,memory_order_relaxed,memory_order_relaxed)) return;
    }
    atomic_store_explicit(&am_diag_saturated,true,memory_order_relaxed);
}
static uint32_t am_diag_hash(AMDetectionEventKey key) {
    uint32_t hash=2166136261u;
    const uint32_t values[]={key.entry,key.image,(unsigned)key.rule.kind,key.rule.index,(unsigned)key.phase};
    for (size_t i=0;i<sizeof values/sizeof *values;i++) { hash^=values[i];hash*=16777619u; }
    return hash;
}
void AMDetectionDiagnosticsConfigure(bool enabled) { atomic_store(&am_diag_enabled,enabled); }
void AMDetectionCountEntry(AMDetectionEntryID entry,bool matched,bool filtered) {
    if (!atomic_load_explicit(&am_diag_enabled,memory_order_relaxed) || entry>=AM_DETECTION_REGISTRY_ID_LIMIT) return;
    am_diag_increment(&am_diag_entry_counts[entry].calls);
    if (matched) am_diag_increment(&am_diag_entry_counts[entry].matched);
    if (filtered) am_diag_increment(&am_diag_entry_counts[entry].filtered);
}
AMDetectionCounters AMDetectionReadEntryCounters(AMDetectionEntryID entry) {
    if (entry>=AM_DETECTION_REGISTRY_ID_LIMIT) return (AMDetectionCounters){0};
    uint64_t calls=atomic_load(&am_diag_entry_counts[entry].calls),matched=atomic_load(&am_diag_entry_counts[entry].matched),
        filtered=atomic_load(&am_diag_entry_counts[entry].filtered);
    /* IDs 6/7 are wire-compatible aggregate readers, never concrete selectors. */
    unsigned first=entry==6 ? 8 : entry==7 ? 10 : 0,last=entry==6 ? 9 : 10;
    if (first) for (unsigned i=first;i<=last;i++) {
        calls+=atomic_load(&am_diag_entry_counts[i].calls);
        matched+=atomic_load(&am_diag_entry_counts[i].matched);
        filtered+=atomic_load(&am_diag_entry_counts[i].filtered);
    }
    if (calls>UINT32_MAX || matched>UINT32_MAX || filtered>UINT32_MAX) atomic_store(&am_diag_saturated,true);
    return (AMDetectionCounters){calls>UINT32_MAX ? UINT32_MAX : (uint32_t)calls,
        matched>UINT32_MAX ? UINT32_MAX : (uint32_t)matched,filtered>UINT32_MAX ? UINT32_MAX : (uint32_t)filtered};
}
static void am_diag_record_event(AMDetectionEventKey key,uint64_t token,bool filtered,AMDetectionResult result,bool original_called,bool count_entry) {
    if (!atomic_load_explicit(&am_diag_enabled,memory_order_relaxed)) return;
    if (key.entry>=AM_DETECTION_REGISTRY_ID_LIMIT || key.image>AM_DETECTION_UNKNOWN_IMAGE ||
        (unsigned)key.phase>=AM_DETECTION_PHASE_COUNT || (unsigned)key.rule.kind>255 || key.rule.index>=64) return;
    bool matched=key.rule.kind!=AM_DETECTION_RULE_NONE;
    if (count_entry) AMDetectionCountEntry(key.entry,matched,filtered);
    uint32_t hash=am_diag_hash(key); bool recorded=false;
    for (unsigned probe=0;probe<AM_DETECTION_ATTRIBUTION_CAPACITY;probe++) {
        unsigned i=(hash+probe)%AM_DETECTION_ATTRIBUTION_CAPACITY;
        unsigned state=atomic_load_explicit(&am_diag_attribution[i].state,memory_order_acquire);
        if (!state) {
            unsigned empty=0;
            if (atomic_compare_exchange_strong_explicit(&am_diag_attribution[i].state,&empty,1,memory_order_acquire,memory_order_relaxed)) {
                am_diag_attribution[i].key=key;
                atomic_store_explicit(&am_diag_attribution[i].state,2,memory_order_release);state=2;
            } else state=empty;
        }
        /* Do not duplicate an identity whose slot is still initializing. */
        if (state==1) break;
        if (state==2 && AMDetectionEventKeyEqual(&am_diag_attribution[i].key,&key)) {
            am_diag_increment(&am_diag_attribution[i].calls);
            if (matched) am_diag_increment(&am_diag_attribution[i].matched);
            if (filtered) am_diag_increment(&am_diag_attribution[i].filtered);
            recorded=true;break;
        }
    }
    if (!recorded) am_diag_increment(&am_diag_attribution_dropped);
    if (!matched) return;
    if ((unsigned)result>=AM_DETECTION_RESULT_COUNT) result=AM_DETECTION_RESULT_UNKNOWN;
    if (filtered && !original_called) result=AM_DETECTION_RESULT_NOT_CALLED;
    hash^=(uint32_t)token^(uint32_t)(token>>32);recorded=false;
    for (unsigned probe=0;probe<AM_DETECTION_CALLSITE_CAPACITY;probe++) {
        unsigned i=(hash+probe)%AM_DETECTION_CALLSITE_CAPACITY;
        unsigned state=atomic_load_explicit(&am_diag_callsites[i].state,memory_order_acquire);
        if (!state) {
            unsigned empty=0;
            if (atomic_compare_exchange_strong_explicit(&am_diag_callsites[i].state,&empty,1,memory_order_acquire,memory_order_relaxed)) {
                am_diag_callsites[i].key=key;am_diag_callsites[i].token=token;
                atomic_store_explicit(&am_diag_callsites[i].state,2,memory_order_release);state=2;
            } else state=empty;
        }
        if (state==1) break;
        if (state==2 && am_diag_callsites[i].token==token && AMDetectionEventKeyEqual(&am_diag_callsites[i].key,&key)) {
            am_diag_increment(&am_diag_callsites[i].results[result]);recorded=true;break;
        }
    }
    if (!recorded) am_diag_increment(&am_diag_callsite_dropped);
}
void AMDetectionRecordEvent(AMDetectionEventKey key,uint64_t token,bool filtered,AMDetectionResult result) {
    am_diag_record_event(key,token,filtered,result,false,true);
}
void AMDetectionRecordPostEvent(AMDetectionEventKey key,uint64_t token,bool filtered,AMDetectionResult result) {
    am_diag_record_event(key,token,filtered,result,true,true);
}
void AMDetectionRecordPostDetailEvent(AMDetectionEventKey key,uint64_t token,bool filtered,AMDetectionResult result) {
    am_diag_record_event(key,token,filtered,result,true,false);
}
size_t AMDetectionReadEvents(AMDetectionEventRow *out,size_t capacity) {
    if (!out) return 0;
    size_t count=0;
    for (size_t i=0;i<AM_DETECTION_ATTRIBUTION_CAPACITY && count<capacity;i++) {
        if (atomic_load_explicit(&am_diag_attribution[i].state,memory_order_acquire)!=2) continue;
        AMDetectionCounters counters={atomic_load(&am_diag_attribution[i].calls),
            atomic_load(&am_diag_attribution[i].matched),atomic_load(&am_diag_attribution[i].filtered)};
        if (counters.calls) out[count++]=(AMDetectionEventRow){am_diag_attribution[i].key,counters};
    }
    return count;
}
size_t AMDetectionReadEventCallsites(AMDetectionEventCallsite *out,size_t capacity) {
    if (!out) return 0;
    size_t count=0;
    for (size_t i=0;i<AM_DETECTION_CALLSITE_CAPACITY && count<capacity;i++) {
        if (atomic_load_explicit(&am_diag_callsites[i].state,memory_order_acquire)!=2) continue;
        AMDetectionEventCallsite row={.key=am_diag_callsites[i].key,.token=am_diag_callsites[i].token};
        bool any=false;
        for (unsigned j=0;j<AM_DETECTION_RESULT_COUNT;j++) { row.results[j]=atomic_load(&am_diag_callsites[i].results[j]);any|=row.results[j]!=0; }
        if (any) out[count++]=row;
    }
    return count;
}
AMDetectionCoverage AMDetectionReadCoverage(void) {
    AMDetectionCoverage c={.attribution_dropped=atomic_load(&am_diag_attribution_dropped),
        .callsite_dropped=atomic_load(&am_diag_callsite_dropped),.allocation_failures=atomic_load(&am_diag_allocation_failures),
        .saturated=atomic_load(&am_diag_saturated),.operational_gaps=atomic_load(&am_diag_operational_gaps)};
    c.incomplete=c.attribution_dropped || c.callsite_dropped || c.allocation_failures || c.operational_gaps || c.saturated;return c;
}
void AMDetectionRecordAllocationFailure(void) { if (atomic_load(&am_diag_enabled)) am_diag_increment(&am_diag_allocation_failures); }
void AMDetectionRecordCoverageGap(void) { if (atomic_load(&am_diag_enabled)) am_diag_increment(&am_diag_operational_gaps); }
bool AMDetectionSetPhase(AMDetectionPhase phase) {
    if (!atomic_load(&am_diag_enabled) || (unsigned)phase>=AM_DETECTION_PHASE_COUNT) return false;
    atomic_store(&am_diag_phase,(unsigned)phase);return true;
}
AMDetectionPhase AMDetectionCurrentPhase(void) { return (AMDetectionPhase)atomic_load(&am_diag_phase); }
#else
void AMDetectionDiagnosticsConfigure(bool enabled) { (void)enabled; }
void AMDetectionCountEntry(AMDetectionEntryID entry,bool matched,bool filtered) { (void)entry;(void)matched;(void)filtered; }
AMDetectionCounters AMDetectionReadEntryCounters(AMDetectionEntryID entry) { (void)entry;return (AMDetectionCounters){0}; }
void AMDetectionRecordEvent(AMDetectionEventKey key,uint64_t token,bool filtered,AMDetectionResult result) { (void)key;(void)token;(void)filtered;(void)result; }
void AMDetectionRecordPostEvent(AMDetectionEventKey key,uint64_t token,bool filtered,AMDetectionResult result) { (void)key;(void)token;(void)filtered;(void)result; }
void AMDetectionRecordPostDetailEvent(AMDetectionEventKey key,uint64_t token,bool filtered,AMDetectionResult result) { (void)key;(void)token;(void)filtered;(void)result; }
size_t AMDetectionReadEvents(AMDetectionEventRow *out,size_t capacity) { (void)out;(void)capacity;return 0; }
size_t AMDetectionReadEventCallsites(AMDetectionEventCallsite *out,size_t capacity) { (void)out;(void)capacity;return 0; }
AMDetectionCoverage AMDetectionReadCoverage(void) { return (AMDetectionCoverage){0}; }
void AMDetectionRecordAllocationFailure(void) {}
void AMDetectionRecordCoverageGap(void) {}
bool AMDetectionSetPhase(AMDetectionPhase phase) { (void)phase;return false; }
AMDetectionPhase AMDetectionCurrentPhase(void) { return AM_DETECTION_PHASE_STARTUP; }
#endif
