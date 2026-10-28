#include "AMDetectionProcess.h"
#include "AMDetectionHooks.h"
#include <errno.h>
static const AMDetectionProfile *am_process_profile;
static AMDetectionSpawnOriginal am_process_original;
static bool (*am_process_ready)(AMDetectionEntryID);
static _Thread_local unsigned am_process_depth;
bool AMDetectionConfigureProcess(const AMDetectionProfile *profile,AMDetectionSpawnOriginal original,bool (*ready)(AMDetectionEntryID)) {
    if (am_process_profile || !profile || !original || (unsigned)profile->mode>AM_DETECTION_FILTER ||
        profile->rules.spawn_count>64 || (profile->rules.spawn_count && !profile->rules.spawn_paths)) return false;
    am_process_original=original;am_process_ready=ready;am_process_profile=profile;return true;
}
int AMDetectionPosixSpawn(pid_t *pid,const char *path,const posix_spawn_file_actions_t *actions,
    const posix_spawnattr_t *attributes,char *const arguments[],char *const environment[]) {
    if (!am_process_original) return ENOSYS;
    if (am_process_depth) return am_process_original(pid,path,actions,attributes,arguments,environment);
    int saved=errno;AMDetectionRuleRef rule=AMDetectionSpawnRule(am_process_profile,path);
    bool block=rule.kind!=AM_DETECTION_RULE_NONE && am_process_profile->mode==AM_DETECTION_FILTER &&
        (!am_process_ready || am_process_ready(AM_DETECTION_ENTRY_POSIX_SPAWN));
    am_process_depth++;
    int code;
    if (block) {code=EACCES;errno=saved;}
    else code=am_process_original(pid,path,actions,attributes,arguments,environment);
    AMDetectionResult result=code==0 ? AM_DETECTION_RESULT_SUCCESS : (code==ENOENT || code==ENOTDIR) ? AM_DETECTION_RESULT_NEGATIVE : AM_DETECTION_RESULT_ERROR;
    AMDetectionRecordEntryOutcome(AM_DETECTION_ENTRY_POSIX_SPAWN,rule,block,__builtin_return_address(0),result);
    am_process_depth--;return code;
}
