#ifndef AM_DETECTION_PROCESS_H
#define AM_DETECTION_PROCESS_H
#include "AMDetectionRegistry.h"
#include <spawn.h>
typedef int (*AMDetectionSpawnOriginal)(pid_t *,const char *,const posix_spawn_file_actions_t *,
    const posix_spawnattr_t *,char *const [],char *const []);
bool AMDetectionConfigureProcess(const AMDetectionProfile *,AMDetectionSpawnOriginal,bool (*ready)(AMDetectionEntryID));
int AMDetectionPosixSpawn(pid_t *,const char *,const posix_spawn_file_actions_t *,
    const posix_spawnattr_t *,char *const [],char *const []);
#endif
