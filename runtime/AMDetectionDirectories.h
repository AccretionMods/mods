#ifndef AM_DETECTION_DIRECTORIES_H
#define AM_DETECTION_DIRECTORIES_H
#include "AMDetectionRegistry.h"
#include <dirent.h>
#define AM_DETECTION_DIRECTORY_CAPACITY 256
typedef struct { DIR *(*opendir_fn)(const char *); struct dirent *(*readdir_fn)(DIR *);
    int (*closedir_fn)(DIR *); } AMDetectionDirectoryOriginals;
bool AMDetectionConfigureDirectories(const AMDetectionProfile *,const AMDetectionDirectoryOriginals *,bool (*ready)(AMDetectionEntryID));
DIR *AMDetectionOpendir(const char *);
struct dirent *AMDetectionReaddir(DIR *);
int AMDetectionClosedir(DIR *);
bool AMDetectionDirectoryShouldSkip(DIR *,const char *child);
#endif
