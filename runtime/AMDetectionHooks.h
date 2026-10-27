#ifndef AM_DETECTION_HOOKS_H
#define AM_DETECTION_HOOKS_H
#include "AMDetectionPolicy.h"
#include <stdio.h>
#include <sys/stat.h>

typedef enum {
    AM_DETECTION_ACCESS, AM_DETECTION_STAT, AM_DETECTION_LSTAT, AM_DETECTION_OPEN,
    AM_DETECTION_FOPEN, AM_DETECTION_GETENV, AM_DETECTION_FOUNDATION, AM_DETECTION_URL,
    AM_DETECTION_API_COUNT
} AMDetectionAPI;
typedef struct { uint32_t calls, matched, filtered; } AMDetectionCounters;
typedef enum { AM_DETECTION_PHASE_STARTUP, AM_DETECTION_PHASE_LOGIN, AM_DETECTION_PHASE_MENU,
    AM_DETECTION_PHASE_FIGHT, AM_DETECTION_PHASE_RESULTS, AM_DETECTION_PHASE_COUNT } AMDetectionPhase;
#define AM_DETECTION_ATTRIBUTION_CAPACITY 512
#define AM_DETECTION_UNKNOWN_IMAGE 64
typedef struct { AMDetectionAPI api; unsigned image; AMDetectionPhase phase;
    AMDetectionRuleRef rule; AMDetectionCounters counts; } AMDetectionAttribution;
void AMDetectionSetOriginResolver(unsigned (*resolver)(const void *));
bool AMDetectionSetPhase(AMDetectionPhase);
AMDetectionPhase AMDetectionCurrentPhase(void);
void AMDetectionRecordRule(AMDetectionAPI,AMDetectionRuleRef,bool filtered,const void *caller);
size_t AMDetectionReadAttribution(AMDetectionAttribution *,size_t capacity);
unsigned AMDetectionAttributionDropped(void);
#define AM_DETECTION_CALLSITE_CAPACITY 128
typedef enum { AM_DETECTION_RESULT_UNKNOWN, AM_DETECTION_RESULT_SUCCESS,
    AM_DETECTION_RESULT_NEGATIVE, AM_DETECTION_RESULT_ERROR, AM_DETECTION_RESULT_NOT_CALLED,
    AM_DETECTION_RESULT_COUNT } AMDetectionResult;
typedef struct { AMDetectionAttribution origin; uint64_t token;
    uint32_t results[AM_DETECTION_RESULT_COUNT]; } AMDetectionCallsite;
void AMDetectionSetCallsiteResolver(uint64_t (*resolver)(const void *));
void AMDetectionRecordOutcome(AMDetectionAPI,AMDetectionRuleRef,bool,const void *,AMDetectionResult);
size_t AMDetectionReadCallsites(AMDetectionCallsite *,size_t);
unsigned AMDetectionCallsitesDropped(void);
typedef struct {
    int (*access_fn)(const char *,int);
    int (*stat_fn)(const char *,struct stat *);
    int (*lstat_fn)(const char *,struct stat *);
    int (*open_fn)(const char *,int,...);
    FILE *(*fopen_fn)(const char *,const char *);
    char *(*getenv_fn)(const char *);
} AMDetectionOriginals;
/* Startup only, before hooks are published. Profile/original storage must outlive the hooks. */
bool AMDetectionConfigure(const AMDetectionProfile *, const AMDetectionOriginals *);
void AMDetectionSetAdapterReady(AMDetectionAPI, bool ready);
void AMDetectionSetReadinessResolver(bool (*resolver)(AMDetectionAPI));
void AMDetectionRecord(AMDetectionAPI, bool matched, bool filtered);
AMDetectionCounters AMDetectionReadCounters(AMDetectionAPI);
int AMDetectionAccess(const char *,int);
int AMDetectionStat(const char *,struct stat *);
int AMDetectionLstat(const char *,struct stat *);
int AMDetectionOpen(const char *,int,...);
FILE *AMDetectionFopen(const char *,const char *);
char *AMDetectionGetenv(const char *);
#endif
