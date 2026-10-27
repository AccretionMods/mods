#include "AMDetectionHooks.h"
#include "AMDetectionFiles.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

static int directory_fd=-1;
static const char *directory_path;
static bool active=true;
static bool readiness(AMDetectionAPI api) { (void)api;return active; }
static bool resolve_fd(int fd,char *out,size_t capacity) {
    if (fd!=directory_fd || strlen(directory_path)>=capacity) return false;
    strcpy(out,directory_path);return true;
}
static void make_file(const char *path) {
    int fd=open(path,O_WRONLY|O_CREAT|O_EXCL,0600);assert(fd>=0);
    assert(write(fd,"sample",6)==6);assert(!close(fd));
}
int main(int argc,char **argv) {
    assert(argc==3);directory_path=argv[2];
    char target[4096],link[4096],probe[4096],save[4096],visible[4096],sub[4096];
    assert(snprintf(target,sizeof target,"%s/target",directory_path)>0);
    snprintf(link,sizeof link,"%s/link",directory_path);snprintf(probe,sizeof probe,"%s/probe",directory_path);
    snprintf(save,sizeof save,"%s/save",directory_path);snprintf(visible,sizeof visible,"%s/visible",directory_path);
    snprintf(sub,sizeof sub,"%s/sub",directory_path);
    make_file(target);make_file(probe);assert(!symlink("target",link));assert(!symlink("target",visible));assert(!mkdir(sub,0700));
    directory_fd=open(directory_path,O_RDONLY);assert(directory_fd>=0);
    const char *paths[]={target,link},*write_paths[]={probe};
    AMDetectionProfile p={.mode=!strcmp(argv[1],"observe") ? AM_DETECTION_OBSERVE : AM_DETECTION_FILTER,
        .rules={.paths=paths,.path_count=2,.write_paths=write_paths,.write_count=1}};
    AMDetectionOriginals originals={.access_fn=access,.stat_fn=stat,.lstat_fn=lstat,.open_fn=open,
        .fopen_fn=fopen,.readlink_fn=readlink,.fstatat_fn=fstatat,.getcwd_fn=getcwd,.directory_path_fn=resolve_fd};
    if (!strcmp(argv[1],"invalid")) {
        p.rules.write_count=65;assert(!AMDetectionConfigure(&p,&originals));
        p.rules.write_count=1;assert(AMDetectionConfigure(&p,&originals));
        assert(!close(directory_fd));return 0;
    }
    assert(AMDetectionConfigure(&p,&originals));AMDetectionSetReadinessResolver(readiness);
    struct stat st;
    if (!strcmp(argv[1],"readlink")) {
        char out[32],before[32];memset(out,'Q',sizeof out);memcpy(before,out,sizeof out);
        errno=E2BIG;assert(AMDetectionReadlink(link,out,sizeof out)==-1 && errno==ENOENT);
        assert(!memcmp(out,before,sizeof out));
        active=false;errno=E2BIG;assert(AMDetectionReadlink(link,out,sizeof out)==6 && errno==E2BIG);
        assert(!memcmp(out,"target",6) && out[6]=='Q');
    } else if (!strcmp(argv[1],"relative")) {
        errno=0;assert(AMDetectionFstatat(directory_fd,"target",&st,0)==-1 && errno==ENOENT);
        assert(AMDetectionFstatat(directory_fd,"visible",&st,AT_SYMLINK_NOFOLLOW)==0 && S_ISLNK(st.st_mode));
        errno=0;assert(AMDetectionFstatat(-999,"target",&st,0)==-1 && errno==EBADF);
        errno=0;assert(AMDetectionFstatat(directory_fd,target,&st,0x40000000)==-1 && errno==EINVAL);
        assert(AMDetectionFstatat(directory_fd,"sub/../target",&st,0)==0 && S_ISREG(st.st_mode));
        char out[4096];assert(!AMDetectionResolvePath(-999,"target",out,sizeof out));
        assert(!AMDetectionResolvePath(directory_fd,"sub/../target",out,sizeof out));
        assert(!chdir(directory_path));
        assert(AMDetectionFstatat(AT_FDCWD,"target",&st,0)==-1 && errno==ENOENT);
    } else if (!strcmp(argv[1],"writes")) {
        int fd=AMDetectionOpen(probe,O_RDONLY);assert(fd>=0);close(fd);
        errno=0;assert(AMDetectionOpen(probe,O_WRONLY|O_TRUNC)==-1 && errno==EACCES);
        assert(AMDetectionOpen(probe,O_CREAT|O_RDONLY,0600)==-1 && errno==EACCES);
        FILE *stream=AMDetectionFopen(probe,"r");assert(stream);fclose(stream);
        assert(!AMDetectionFopen(probe,"r+") && errno==EACCES);
        assert(!AMDetectionFopen(probe,"w") && errno==EACCES);
        fd=AMDetectionOpen(save,O_WRONLY|O_CREAT|O_EXCL,0600);assert(fd>=0);close(fd);
        assert(!stat(save,&st) && (st.st_mode&0777)==0600);
        assert(!stat(probe,&st) && st.st_size==6);
#ifdef O_TMPFILE
        int direct=open(directory_path,O_TMPFILE|O_RDWR,0600),original_error=errno;
        int wrapped=AMDetectionOpen(directory_path,O_TMPFILE|O_RDWR,0600);
        if (direct>=0) {assert(wrapped>=0);assert(!fstat(wrapped,&st) && (st.st_mode&0777)==0600);close(direct);close(wrapped);}
        else assert(wrapped==-1 && errno==original_error);
#endif
        active=false;fd=AMDetectionOpen(probe,O_WRONLY|O_TRUNC);assert(fd>=0);close(fd);
        assert(!stat(probe,&st) && st.st_size==0);
    } else if (!strcmp(argv[1],"observe")) {
        char out[32];assert(AMDetectionReadlink(link,out,sizeof out)==6);
        int fd=AMDetectionOpen(probe,O_WRONLY|O_TRUNC);assert(fd>=0);close(fd);
        assert(!stat(probe,&st) && st.st_size==0);
        AMDetectionCounters c=AMDetectionReadEntryCounters(AM_DETECTION_ENTRY_READLINK);
        assert(c.calls==1 && c.matched==1 && c.filtered==0);
    } else return 2;
    assert(!close(directory_fd));return 0;
}
