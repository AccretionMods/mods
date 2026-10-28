#ifndef AM_DETECTION_FILES_H
#define AM_DETECTION_FILES_H
#include "AMDetectionRegistry.h"
#include <stdio.h>
#include <sys/stat.h>
#include <sys/types.h>

typedef struct {
    int (*access_fn)(const char *,int);
    int (*stat_fn)(const char *,struct stat *);
    int (*lstat_fn)(const char *,struct stat *);
    int (*open_fn)(const char *,int,...);
    FILE *(*fopen_fn)(const char *,const char *);
    char *(*getenv_fn)(const char *);
    ssize_t (*readlink_fn)(const char *,char *,size_t);
    int (*fstatat_fn)(int,const char *,struct stat *,int);
    char *(*getcwd_fn)(char *,size_t);
    bool (*directory_path_fn)(int,char *,size_t);
} AMDetectionOriginals;

bool AMDetectionConfigureFiles(const AMDetectionProfile *,const AMDetectionOriginals *,bool (*ready)(AMDetectionEntryID));
bool AMDetectionResolvePath(int,const char *,char *,size_t);
int AMDetectionAccess(const char *,int);
int AMDetectionStat(const char *,struct stat *);
int AMDetectionLstat(const char *,struct stat *);
int AMDetectionOpen(const char *,int,...);
FILE *AMDetectionFopen(const char *,const char *);
ssize_t AMDetectionReadlink(const char *,char *,size_t);
int AMDetectionFstatat(int,const char *,struct stat *,int);
#endif
