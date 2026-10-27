#ifndef AM_DETECTION_POLICY_H
#define AM_DETECTION_POLICY_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum { AM_DETECTION_OFF, AM_DETECTION_OBSERVE, AM_DETECTION_FILTER } AMDetectionMode;
AMDetectionMode AMDetectionEffectiveMode(AMDetectionMode global,AMDetectionMode module);
typedef enum { AM_DETECTION_DISABLED, AM_DETECTION_OBSERVING, AM_DETECTION_FILTERING,
    AM_DETECTION_UNSUPPORTED, AM_DETECTION_FAILED } AMDetectionState;
AMDetectionState AMDetectionAdapterState(AMDetectionMode mode, bool selected, size_t installed,
    size_t failures, bool supported);
typedef struct {
    const char *const *paths; size_t path_count;
    const char *const *path_prefixes; size_t prefix_count;
    const char *const *url_schemes; size_t scheme_count;
    const char *const *environment_names; size_t environment_count;
    const char *const *write_paths; size_t write_count;
} AMDetectionRules;
typedef struct { AMDetectionMode mode; AMDetectionRules rules; } AMDetectionProfile;
typedef enum { AM_DETECTION_RULE_NONE, AM_DETECTION_RULE_PATH, AM_DETECTION_RULE_PREFIX,
    AM_DETECTION_RULE_SCHEME, AM_DETECTION_RULE_ENVIRONMENT,
    AM_DETECTION_RULE_WRITE_PATH, AM_DETECTION_RULE_COUNT } AMDetectionRuleKind;
typedef struct { AMDetectionRuleKind kind; unsigned index; } AMDetectionRuleRef;
AMDetectionRuleRef AMDetectionPathRule(const AMDetectionProfile *,const char *);
AMDetectionRuleRef AMDetectionSchemeRule(const AMDetectionProfile *,const char *);
AMDetectionRuleRef AMDetectionEnvironmentRule(const AMDetectionProfile *,const char *);
AMDetectionRuleRef AMDetectionWriteRule(const AMDetectionProfile *,const char *);
bool AMDetectionPathValid(const char *);

bool AMDetectionMatchPath(const AMDetectionProfile *profile, const char *path);
bool AMDetectionMatchScheme(const AMDetectionProfile *profile, const char *scheme);
bool AMDetectionMatchEnvironment(const AMDetectionProfile *profile, const char *name);
#endif
