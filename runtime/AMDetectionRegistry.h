#ifndef AM_DETECTION_REGISTRY_H
#define AM_DETECTION_REGISTRY_H
#include "AMDetectionPolicy.h"

#define AM_DETECTION_MAX_ENTRYPOINTS 64
#define AM_DETECTION_MAX_CALLER_IMAGES 64
#define AM_DETECTION_MAX_NATIVE_BINDINGS 64
#define AM_DETECTION_REGISTRY_ID_LIMIT 256

typedef uint16_t AMDetectionEntryID;
typedef uint8_t AMDetectionModuleID;
enum {
    AM_DETECTION_MODULE_FILES, AM_DETECTION_MODULE_ENVIRONMENT,
    AM_DETECTION_MODULE_FOUNDATION, AM_DETECTION_MODULE_URLS,
    AM_DETECTION_MODULE_DIRECTORIES, AM_DETECTION_MODULE_WRITES,
    AM_DETECTION_MODULE_PREFERENCES, AM_DETECTION_MODULE_LOADER,
    AM_DETECTION_MODULE_OBJC, AM_DETECTION_MODULE_PROCESS,
    AM_DETECTION_MODULE_SDK, AM_DETECTION_MODULE_COUNT
};
enum {
    AM_DETECTION_ENTRY_ACCESS=0, AM_DETECTION_ENTRY_STAT=1,
    AM_DETECTION_ENTRY_LSTAT=2, AM_DETECTION_ENTRY_OPEN=3,
    AM_DETECTION_ENTRY_FOPEN=4, AM_DETECTION_ENTRY_GETENV=5,
    AM_DETECTION_ENTRY_FOUNDATION_AGGREGATE=6, AM_DETECTION_ENTRY_URL_AGGREGATE=7,
    AM_DETECTION_ENTRY_FOUNDATION_EXISTS=8, AM_DETECTION_ENTRY_FOUNDATION_ATTRIBUTES=9,
    AM_DETECTION_ENTRY_CAN_OPEN_URL=10
};
typedef enum { AM_DETECTION_ABI_NATIVE, AM_DETECTION_ABI_OBJC, AM_DETECTION_ABI_AGGREGATE } AMDetectionABI;
enum { AM_DETECTION_REQUIRE_IMPORT=1, AM_DETECTION_REQUIRE_OBJC=2,
    AM_DETECTION_REQUIRE_ORIGINAL_VIEW=4 };
typedef enum { AM_DETECTION_REASON_NONE, AM_DETECTION_REASON_NOT_SELECTED,
    AM_DETECTION_REASON_NOT_PREPARED, AM_DETECTION_REASON_PREPARE_FAILED,
    AM_DETECTION_REASON_PUBLISH_FAILED } AMDetectionSupportReason;
typedef struct AMDetectionEntry AMDetectionEntry;
typedef bool (*AMDetectionInstall)(const AMDetectionEntry *,void *);
struct AMDetectionEntry {
    AMDetectionEntryID id;
    const char *name;
    AMDetectionModuleID module;
    AMDetectionABI abi;
    AMDetectionRuleKind rule_kind;
    unsigned requirements;
    unsigned required_group;
    void **original;
    AMDetectionInstall prepare,publish;
    void *context;
};
typedef struct { AMDetectionState state; AMDetectionSupportReason reason; } AMDetectionModuleResult;

const AMDetectionEntry *AMDetectionEntryByID(AMDetectionEntryID);
const AMDetectionEntry *AMDetectionEntryAt(size_t);
size_t AMDetectionEntryCount(void);
bool AMDetectionValidateSelection(const AMDetectionEntryID *,size_t,size_t compatibility_bindings);
/* Startup-only registration/configuration, before any adapter is published. */
bool AMDetectionSetInstaller(AMDetectionEntryID,AMDetectionInstall,AMDetectionInstall,void *);
bool AMDetectionConfigureRegistry(const AMDetectionEntryID *,size_t,size_t,AMDetectionMode);
bool AMDetectionEntrySelected(AMDetectionEntryID);
bool AMDetectionPrepareModule(AMDetectionModuleID);
bool AMDetectionPublishModule(AMDetectionModuleID);
void AMDetectionInvalidateModule(AMDetectionModuleID);
bool AMDetectionModuleActive(AMDetectionModuleID);
AMDetectionModuleResult AMDetectionModuleStatus(AMDetectionModuleID);
const char *AMDetectionModuleName(AMDetectionModuleID);
const char *AMDetectionSupportReasonName(AMDetectionSupportReason);
#endif
