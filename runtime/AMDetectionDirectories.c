#ifndef __APPLE__
#ifndef _GNU_SOURCE
#define _GNU_SOURCE 1
#endif
#endif
#include "AMDetectionDirectories.h"
#include "AMDetectionHooks.h"
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stddef.h>
#include <string.h>

static const AMDetectionProfile *am_dir_profile;
static AMDetectionDirectoryOriginals am_dir_originals;
static bool (*am_dir_ready)(AMDetectionEntryID);
static _Thread_local unsigned am_dir_depth;
static pthread_mutex_t am_dir_map_lock=PTHREAD_MUTEX_INITIALIZER;
static struct AMDirectorySlot { DIR *handle; uint64_t generation; pthread_mutex_t lock;
    char parent[4096]; } am_dir_streams[AM_DETECTION_DIRECTORY_CAPACITY];

bool AMDetectionConfigureDirectories(const AMDetectionProfile *p,const AMDetectionDirectoryOriginals *o,bool (*ready)(AMDetectionEntryID)) {
    if (am_dir_profile || !p || !o || !o->opendir_fn || !o->readdir_fn || !o->closedir_fn || (unsigned)p->mode>AM_DETECTION_FILTER) return false;
    for (unsigned i=0;i<AM_DETECTION_DIRECTORY_CAPACITY;i++) if (pthread_mutex_init(&am_dir_streams[i].lock,NULL)) {
        for (unsigned j=0;j<i;j++) pthread_mutex_destroy(&am_dir_streams[j].lock);
        return false;
    }
    am_dir_originals=*o;am_dir_ready=ready;am_dir_profile=p;return true;
}
static bool am_dir_filter(AMDetectionEntryID entry) {
    return am_dir_profile && am_dir_profile->mode==AM_DETECTION_FILTER && (!am_dir_ready || am_dir_ready(entry));
}
/* Map lock precedes slot lock. Readers release the map before original I/O. */
static struct AMDirectorySlot *am_dir_lock_stream(DIR *handle) {
    pthread_mutex_lock(&am_dir_map_lock);
    struct AMDirectorySlot *found=NULL;
    for (unsigned i=0;i<AM_DETECTION_DIRECTORY_CAPACITY;i++) if (handle && am_dir_streams[i].handle==handle) {
        found=&am_dir_streams[i];pthread_mutex_lock(&found->lock);break;
    }
    pthread_mutex_unlock(&am_dir_map_lock);return found;
}
bool AMDetectionDirectoryShouldSkip(DIR *handle,const char *child) {
    struct AMDirectorySlot *slot=am_dir_lock_stream(handle);if (!slot) return false;
    bool skip=am_dir_filter(AM_DETECTION_ENTRY_READDIR) && AMDetectionChildRule(am_dir_profile,slot->parent,child).kind!=AM_DETECTION_RULE_NONE;
    pthread_mutex_unlock(&slot->lock);return skip;
}
DIR *AMDetectionOpendir(const char *path) {
    if (!am_dir_originals.opendir_fn) {errno=ENOSYS;return NULL;}
    if (am_dir_depth) return am_dir_originals.opendir_fn(path);
    am_dir_depth++;
    /* A pre-open cwd snapshot cannot prove which directory a relative stream opened. */
    char parent[4096];bool resolved=path && path[0]=='/' && AMDetectionResolvePath(AT_FDCWD,path,parent,sizeof parent);
    AMDetectionRuleRef rule=resolved ? AMDetectionPathRule(am_dir_profile,parent) : (AMDetectionRuleRef){0};
    bool block=rule.kind!=AM_DETECTION_RULE_NONE && am_dir_filter(AM_DETECTION_ENTRY_OPENDIR);
    DIR *handle=block ? (errno=ENOENT,(DIR *)NULL) : am_dir_originals.opendir_fn(path);
    int saved=errno;
    if (handle && am_dir_profile->mode!=AM_DETECTION_OFF) {
        bool tracked=false;
        if (resolved) {
            pthread_mutex_lock(&am_dir_map_lock);
            for (unsigned i=0;i<AM_DETECTION_DIRECTORY_CAPACITY;i++) if (!am_dir_streams[i].handle && am_dir_streams[i].generation<UINT64_MAX) {
                struct AMDirectorySlot *slot=&am_dir_streams[i];pthread_mutex_lock(&slot->lock);
                slot->generation++;memcpy(slot->parent,parent,strlen(parent)+1);slot->handle=handle;
                pthread_mutex_unlock(&slot->lock);tracked=true;break;
            }
            pthread_mutex_unlock(&am_dir_map_lock);
        }
        if (!tracked) AMDetectionRecordCoverageGap();
    }
    AMDetectionRecordEntryOutcome(AM_DETECTION_ENTRY_OPENDIR,rule,block,__builtin_return_address(0),
        handle ? AM_DETECTION_RESULT_SUCCESS : (saved==ENOENT || saved==ENOTDIR) ? AM_DETECTION_RESULT_NEGATIVE : AM_DETECTION_RESULT_ERROR);
    am_dir_depth--;errno=saved;return handle;
}
struct dirent *AMDetectionReaddir(DIR *handle) {
    if (!am_dir_originals.readdir_fn) {errno=ENOSYS;return NULL;}
    if (am_dir_depth) return am_dir_originals.readdir_fn(handle);
    am_dir_depth++;int prior=errno;
    struct AMDirectorySlot *slot=am_dir_lock_stream(handle);
    if (!slot) AMDetectionRecordCoverageGap();
    struct dirent *entry=NULL;
    bool matched_any=false,filtered_any=false;
    for (unsigned inspected=0;inspected<4096;inspected++) {
        errno=0;entry=am_dir_originals.readdir_fn(handle);int error=errno;
        AMDetectionResult result=entry ? AM_DETECTION_RESULT_SUCCESS : error ? AM_DETECTION_RESULT_ERROR : AM_DETECTION_RESULT_NEGATIVE;
        AMDetectionRuleRef rule={0};
        if (entry && slot) {
            size_t start=offsetof(struct dirent,d_name),length=entry->d_reclen>start ? entry->d_reclen-start : 0;
            if (length>sizeof entry->d_name) length=sizeof entry->d_name;
            if (length && strnlen(entry->d_name,length)<length) rule=AMDetectionChildRule(am_dir_profile,slot->parent,entry->d_name);
            else AMDetectionRecordCoverageGap();
        }
        bool block=entry && rule.kind!=AM_DETECTION_RULE_NONE && am_dir_filter(AM_DETECTION_ENTRY_READDIR);
        if (block && inspected==4095) {block=false;AMDetectionRecordCoverageGap();}
        matched_any|=rule.kind!=AM_DETECTION_RULE_NONE;filtered_any|=block;
        AMDetectionRecordEntryDetailOutcome(AM_DETECTION_ENTRY_READDIR,rule,block,__builtin_return_address(0),result);
        errno=error ? error : prior;
        if (!block) break;
    }
    int saved=errno;if (slot) pthread_mutex_unlock(&slot->lock);
    AMDetectionCountEntry(AM_DETECTION_ENTRY_READDIR,matched_any,filtered_any);
    am_dir_depth--;errno=saved;return entry;
}
int AMDetectionClosedir(DIR *handle) {
    if (!am_dir_originals.closedir_fn) {errno=ENOSYS;return -1;}
    if (am_dir_depth) return am_dir_originals.closedir_fn(handle);
    am_dir_depth++;
    pthread_mutex_lock(&am_dir_map_lock);struct AMDirectorySlot *slot=NULL;
    for (unsigned i=0;i<AM_DETECTION_DIRECTORY_CAPACITY;i++) if (handle && am_dir_streams[i].handle==handle) {
        slot=&am_dir_streams[i];pthread_mutex_lock(&slot->lock);slot->handle=NULL;memset(slot->parent,0,sizeof slot->parent);break;
    }
    pthread_mutex_unlock(&am_dir_map_lock);
    int value=am_dir_originals.closedir_fn(handle),saved=errno;
    if (slot) pthread_mutex_unlock(&slot->lock);
    AMDetectionRecordEntryPostOutcome(AM_DETECTION_ENTRY_CLOSEDIR,(AMDetectionRuleRef){0},false,__builtin_return_address(0),
        value==0 ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_ERROR);
    am_dir_depth--;errno=saved;return value;
}
