#ifndef AM_DETECTION_IDENTITY_H
#define AM_DETECTION_IDENTITY_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#define AM_DETECTION_IDENTITY_MAX_RANGES 128
#define AM_DETECTION_IDENTITY_MAX_IMAGE (512u*1024u*1024u)
typedef struct { uint64_t offset,size; } AMDetectionExecutableRange;
typedef struct { unsigned char uuid[16],sha256[32]; uint32_t range_count;
    AMDetectionExecutableRange ranges[AM_DETECTION_IDENTITY_MAX_RANGES]; } AMDetectionImageIdentity;
typedef struct {
    FILE *(*open)(const char *,const char *);
    size_t (*read)(void *,size_t,size_t,FILE *);
    int (*seek)(FILE *,long,int);
    long (*tell)(FILE *);
    int (*close)(FILE *);
} AMDetectionIdentityIO;
bool AMDetectionIdentityMatches(const AMDetectionImageIdentity *,const AMDetectionImageIdentity *);
bool AMDetectionIdentityReadFile(const char *,const AMDetectionIdentityIO *,AMDetectionImageIdentity *);
#endif
