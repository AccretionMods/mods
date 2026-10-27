#ifndef AM_DETECTION_DIAGNOSTICS_H
#define AM_DETECTION_DIAGNOSTICS_H
#include "AMDetectionRegistry.h"
#ifndef AM_DETECTION_DIAGNOSTICS_ENABLED
#define AM_DETECTION_DIAGNOSTICS_ENABLED 1
#endif
#define AM_DETECTION_ATTRIBUTION_CAPACITY 4096
#define AM_DETECTION_CALLSITE_CAPACITY 4096
#define AM_DETECTION_UNKNOWN_IMAGE 64
typedef struct { uint32_t calls,matched,filtered; } AMDetectionCounters;
typedef enum { AM_DETECTION_PHASE_STARTUP, AM_DETECTION_PHASE_LOGIN, AM_DETECTION_PHASE_MENU,
    AM_DETECTION_PHASE_FIGHT, AM_DETECTION_PHASE_RESULTS, AM_DETECTION_PHASE_COUNT } AMDetectionPhase;
typedef enum { AM_DETECTION_RESULT_UNKNOWN, AM_DETECTION_RESULT_SUCCESS,
    AM_DETECTION_RESULT_NEGATIVE, AM_DETECTION_RESULT_ERROR, AM_DETECTION_RESULT_NOT_CALLED,
    AM_DETECTION_RESULT_COUNT } AMDetectionResult;
typedef struct { AMDetectionEntryID entry; uint16_t image; AMDetectionRuleRef rule;
    AMDetectionPhase phase; } AMDetectionEventKey;
typedef struct { AMDetectionEventKey key; AMDetectionCounters counts; } AMDetectionEventRow;
typedef struct { AMDetectionEventKey key; uint64_t token;
    uint32_t results[AM_DETECTION_RESULT_COUNT]; } AMDetectionEventCallsite;
typedef struct { uint32_t attribution_dropped,callsite_dropped,allocation_failures;
    bool saturated,incomplete; } AMDetectionCoverage;
/* Startup only. Quiet builds retain no diagnostic storage. */
void AMDetectionDiagnosticsConfigure(bool enabled);
bool AMDetectionEventKeyEqual(const AMDetectionEventKey *,const AMDetectionEventKey *);
void AMDetectionRecordEvent(AMDetectionEventKey,uint64_t token,bool filtered,AMDetectionResult);
void AMDetectionCountEntry(AMDetectionEntryID,bool matched,bool filtered);
AMDetectionCounters AMDetectionReadEntryCounters(AMDetectionEntryID);
size_t AMDetectionReadEvents(AMDetectionEventRow *,size_t capacity);
size_t AMDetectionReadEventCallsites(AMDetectionEventCallsite *,size_t capacity);
AMDetectionCoverage AMDetectionReadCoverage(void);
void AMDetectionRecordAllocationFailure(void);
bool AMDetectionSetPhase(AMDetectionPhase);
AMDetectionPhase AMDetectionCurrentPhase(void);
#endif
