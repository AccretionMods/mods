#ifndef __APPLE__
#ifndef _GNU_SOURCE
#define _GNU_SOURCE 1
#endif
#endif
#include "AMDetectionFiles.h"
#include "AMDetectionHooks.h"
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <string.h>

static const AMDetectionProfile *am_file_profile;
static AMDetectionOriginals am_file_originals;
static bool (*am_file_ready)(AMDetectionEntryID);
static _Thread_local unsigned am_file_depth;

bool AMDetectionConfigureFiles(const AMDetectionProfile *p,const AMDetectionOriginals *o,bool (*ready)(AMDetectionEntryID)) {
    if (am_file_profile || !p || !o || (unsigned)p->mode>AM_DETECTION_FILTER) return false;
    const size_t counts[]={p->rules.path_count,p->rules.prefix_count,p->rules.environment_count,p->rules.allowlist_count,p->rules.scheme_count,p->rules.write_count,p->rules.spawn_count};
    const void *lists[]={p->rules.paths,p->rules.path_prefixes,p->rules.environment_names,p->rules.environment_allowlist,p->rules.url_schemes,p->rules.write_paths,p->rules.spawn_paths};
    for (size_t i=0;i<sizeof counts/sizeof *counts;i++) if (counts[i]>64 || (counts[i] && !lists[i])) return false;
    am_file_originals=*o;am_file_ready=ready;am_file_profile=p;return true;
}
bool AMDetectionResolvePath(int dirfd,const char *path,char *out,size_t capacity) {
    if (!path || !out || !capacity) return false;
    int saved=errno;bool ok=false;
    if (path[0]=='/') {
        if (AMDetectionPathValid(path) && strlen(path)<capacity) {memcpy(out,path,strlen(path)+1);ok=true;}
    } else {
        size_t n=strnlen(path,4096);char parent[4096]={0};
        if (!n || n==4096) goto done;
        bool resolved=false;
        if (dirfd==AT_FDCWD) resolved=am_file_originals.getcwd_fn && am_file_originals.getcwd_fn(parent,sizeof parent);
        else resolved=am_file_originals.directory_path_fn && am_file_originals.directory_path_fn(dirfd,parent,sizeof parent);
        if (!resolved || !AMDetectionPathValid(parent)) goto done;
        size_t size=strlen(parent),separator=size>1 ? 1 : 0;
        if (size+separator+n>=capacity) goto done;
        memcpy(out,parent,size);if (separator) out[size++]='/';memcpy(out+size,path,n+1);
        ok=AMDetectionPathValid(out);
    }
done:
    errno=saved;return ok;
}
static bool am_file_filter(AMDetectionEntryID entry,AMDetectionRuleRef rule) {
    return am_file_profile && am_file_profile->mode==AM_DETECTION_FILTER && rule.kind!=AM_DETECTION_RULE_NONE &&
        (!am_file_ready || am_file_ready(entry));
}
static AMDetectionResult am_file_result(bool success) {
    return success ? AM_DETECTION_RESULT_SUCCESS : (errno==ENOENT || errno==ENOTDIR) ? AM_DETECTION_RESULT_NEGATIVE : AM_DETECTION_RESULT_ERROR;
}
#define AM_FILE_RECORD(entry,rule,block,success) AMDetectionRecordEntryOutcome(entry,rule,block,__builtin_return_address(0),am_file_result(success))
#define AM_FILE_MISSING() do {errno=ENOSYS;return -1;} while (0)

int AMDetectionAccess(const char *path,int mode) {
    if (!am_file_originals.access_fn) AM_FILE_MISSING();
    if (am_file_depth) return am_file_originals.access_fn(path,mode);
    AMDetectionRuleRef rule=AMDetectionPathRule(am_file_profile,path);bool block=am_file_filter(AM_DETECTION_ENTRY_ACCESS,rule);
    am_file_depth++;int value=block ? (errno=ENOENT,-1) : am_file_originals.access_fn(path,mode);
    AM_FILE_RECORD(AM_DETECTION_ENTRY_ACCESS,rule,block,value==0);am_file_depth--;return value;
}
int AMDetectionStat(const char *path,struct stat *out) {
    if (!am_file_originals.stat_fn) AM_FILE_MISSING();
    if (am_file_depth) return am_file_originals.stat_fn(path,out);
    AMDetectionRuleRef rule=AMDetectionPathRule(am_file_profile,path);bool block=am_file_filter(AM_DETECTION_ENTRY_STAT,rule);
    am_file_depth++;int value=block ? (errno=ENOENT,-1) : am_file_originals.stat_fn(path,out);
    AM_FILE_RECORD(AM_DETECTION_ENTRY_STAT,rule,block,value==0);am_file_depth--;return value;
}
int AMDetectionLstat(const char *path,struct stat *out) {
    if (!am_file_originals.lstat_fn) AM_FILE_MISSING();
    if (am_file_depth) return am_file_originals.lstat_fn(path,out);
    AMDetectionRuleRef rule=AMDetectionPathRule(am_file_profile,path);bool block=am_file_filter(AM_DETECTION_ENTRY_LSTAT,rule);
    am_file_depth++;int value=block ? (errno=ENOENT,-1) : am_file_originals.lstat_fn(path,out);
    AM_FILE_RECORD(AM_DETECTION_ENTRY_LSTAT,rule,block,value==0);am_file_depth--;return value;
}
static AMDetectionRuleRef am_file_open_rule(const char *path,bool writing) {
    if (writing && am_file_profile && am_file_profile->rules.write_count) {
        char resolved[4096];
        if (AMDetectionResolvePath(AT_FDCWD,path,resolved,sizeof resolved)) {
            AMDetectionRuleRef write=AMDetectionWriteRule(am_file_profile,resolved);
            if (write.kind!=AM_DETECTION_RULE_NONE) return write;
        }
    }
    return AMDetectionPathRule(am_file_profile,path);
}
int AMDetectionOpen(const char *path,int flags,...) {
    if (!am_file_originals.open_fn) AM_FILE_MISSING();
    bool has_mode=(flags&O_CREAT)!=0;
#ifdef O_TMPFILE
    has_mode=has_mode || (flags&O_TMPFILE)==O_TMPFILE;
#endif
    int mode=0;if (has_mode) {va_list args;va_start(args,flags);mode=va_arg(args,int);va_end(args);}
    if (am_file_depth) return has_mode ? am_file_originals.open_fn(path,flags,mode) : am_file_originals.open_fn(path,flags);
    bool writing=(flags&O_ACCMODE)!=O_RDONLY || (flags&(O_CREAT|O_TRUNC|O_APPEND));
    AMDetectionRuleRef rule=am_file_open_rule(path,writing);bool block=am_file_filter(AM_DETECTION_ENTRY_OPEN,rule);
    am_file_depth++;int value;
    if (block) {errno=rule.kind==AM_DETECTION_RULE_WRITE_PATH ? EACCES : ENOENT;value=-1;}
    else value=has_mode ? am_file_originals.open_fn(path,flags,mode) : am_file_originals.open_fn(path,flags);
    AM_FILE_RECORD(AM_DETECTION_ENTRY_OPEN,rule,block,value>=0);am_file_depth--;return value;
}
static bool am_file_mode_writes(const char *mode) {
    if (!mode || (mode[0]!='r' && mode[0]!='w' && mode[0]!='a')) return false;
    bool plus=false,binary=false,exclusive=false,cloexec=false;
    for (unsigned i=1;i<8;i++) {
        if (!mode[i]) return mode[0]!='r' || plus;
        if (mode[i]=='+' && !plus) plus=true;
        else if (mode[i]=='b' && !binary) binary=true;
        else if (mode[i]=='x' && mode[0]=='w' && !exclusive) exclusive=true;
        else if (mode[i]=='e' && !cloexec) cloexec=true;
        else return false;
    }
    return false;
}
FILE *AMDetectionFopen(const char *path,const char *mode) {
    if (!am_file_originals.fopen_fn) {errno=ENOSYS;return NULL;}
    if (am_file_depth) return am_file_originals.fopen_fn(path,mode);
    AMDetectionRuleRef rule=am_file_open_rule(path,am_file_mode_writes(mode));bool block=am_file_filter(AM_DETECTION_ENTRY_FOPEN,rule);
    am_file_depth++;FILE *value;
    if (block) {errno=rule.kind==AM_DETECTION_RULE_WRITE_PATH ? EACCES : ENOENT;value=NULL;}
    else value=am_file_originals.fopen_fn(path,mode);
    AM_FILE_RECORD(AM_DETECTION_ENTRY_FOPEN,rule,block,value!=NULL);am_file_depth--;return value;
}
ssize_t AMDetectionReadlink(const char *path,char *out,size_t capacity) {
    if (!am_file_originals.readlink_fn) AM_FILE_MISSING();
    if (am_file_depth) return am_file_originals.readlink_fn(path,out,capacity);
    char resolved[4096];AMDetectionRuleRef rule={0};
    if (capacity && AMDetectionResolvePath(AT_FDCWD,path,resolved,sizeof resolved)) rule=AMDetectionPathRule(am_file_profile,resolved);
    bool block=am_file_filter(AM_DETECTION_ENTRY_READLINK,rule);
    am_file_depth++;ssize_t value=block ? (errno=ENOENT,-1) : am_file_originals.readlink_fn(path,out,capacity);
    AM_FILE_RECORD(AM_DETECTION_ENTRY_READLINK,rule,block,value>=0);am_file_depth--;return value;
}
int AMDetectionFstatat(int dirfd,const char *path,struct stat *out,int flags) {
    if (!am_file_originals.fstatat_fn) AM_FILE_MISSING();
    if (am_file_depth) return am_file_originals.fstatat_fn(dirfd,path,out,flags);
    unsigned known=AT_SYMLINK_NOFOLLOW;
#ifdef AT_EMPTY_PATH
    known|=AT_EMPTY_PATH;
#endif
#ifdef AT_NO_AUTOMOUNT
    known|=AT_NO_AUTOMOUNT;
#endif
    char resolved[4096];AMDetectionRuleRef rule={0};
    if (!((unsigned)flags&~known) && AMDetectionResolvePath(dirfd,path,resolved,sizeof resolved)) rule=AMDetectionPathRule(am_file_profile,resolved);
    bool block=am_file_filter(AM_DETECTION_ENTRY_FSTATAT,rule);
    am_file_depth++;int value=block ? (errno=ENOENT,-1) : am_file_originals.fstatat_fn(dirfd,path,out,flags);
    AM_FILE_RECORD(AM_DETECTION_ENTRY_FSTATAT,rule,block,value==0);am_file_depth--;return value;
}
#undef AM_FILE_RECORD
#undef AM_FILE_MISSING
