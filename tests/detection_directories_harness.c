#include "AMDetectionDirectories.h"
#include "AMDetectionHooks.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stddef.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static bool ready(AMDetectionEntryID entry) {(void)entry;return true;}
static DIR *fake_handle=(DIR *)(uintptr_t)0x1000;
static unsigned fake_position,closes;
static struct dirent fake_entry;
static bool concurrent_case,read_entered,release_read,close_started,other_done;
static pthread_mutex_t sync_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t sync_condition=PTHREAD_COND_INITIALIZER;
static DIR *other_handle=(DIR *)(uintptr_t)0x2000;
static DIR *fake_open(const char *path) {fake_position=0;return concurrent_case && strstr(path,"/B") ? other_handle : fake_handle;}
static struct dirent *fake_read(DIR *handle) {
    if (concurrent_case) {
        if (handle==fake_handle) {
            pthread_mutex_lock(&sync_lock);read_entered=true;pthread_cond_broadcast(&sync_condition);
            while (!release_read) pthread_cond_wait(&sync_condition,&sync_lock);
            pthread_mutex_unlock(&sync_lock);
        }
        return NULL;
    }
    assert(handle==fake_handle);return fake_position++ ? NULL : &fake_entry;
}
static int fake_close(DIR *handle) {(void)handle;if (concurrent_case) return 0;closes++;if (closes>1) {errno=EINVAL;return -1;}return 0;}
static void *blocked_reader(void *context) {(void)context;assert(!AMDetectionReaddir(fake_handle));return NULL;}
static void *waiting_closer(void *context) {
    (void)context;pthread_mutex_lock(&sync_lock);close_started=true;pthread_cond_broadcast(&sync_condition);pthread_mutex_unlock(&sync_lock);
    assert(!AMDetectionClosedir(fake_handle));return NULL;
}
static void *other_reader(void *context) {
    (void)context;assert(!AMDetectionReaddir(other_handle));
    pthread_mutex_lock(&sync_lock);other_done=true;pthread_cond_broadcast(&sync_condition);pthread_mutex_unlock(&sync_lock);return NULL;
}
static void *reopen(void *context) {
    (void)context;assert(!AMDetectionClosedir(fake_handle));
    assert(AMDetectionOpendir("/fixture/B")==fake_handle);return NULL;
}
int main(int argc,char **argv) {
    assert(argc==3);char hidden[4096],visible[4096],alias[4096],alias_child[4096];
    snprintf(hidden,sizeof hidden,"%s/hidden",argv[2]);snprintf(visible,sizeof visible,"%s/visible",argv[2]);
    int fd=open(hidden,O_CREAT|O_WRONLY,0600);assert(fd>=0);close(fd);
    fd=open(visible,O_CREAT|O_WRONLY,0600);assert(fd>=0);close(fd);
    snprintf(alias,sizeof alias,"%s/alias",argv[2]);snprintf(alias_child,sizeof alias_child,"%s/alias/hidden",argv[2]);
    bool reuse=!strcmp(argv[1],"reuse"),outcome=!strcmp(argv[1],"outcome");
    concurrent_case=!strcmp(argv[1],"concurrent");
    const char *paths[]={reuse || outcome ? "/fixture/A/hidden" : !strcmp(argv[1],"symlink") ? alias_child : hidden};
    AMDetectionProfile p={.mode=AM_DETECTION_FILTER,.rules={.paths=paths,.path_count=1}};
    AMDetectionOriginals files={.getcwd_fn=getcwd};assert(AMDetectionConfigure(&p,&files));
    AMDetectionDirectoryOriginals originals={opendir,readdir,closedir};
    if (reuse || outcome || concurrent_case) {
        originals=(AMDetectionDirectoryOriginals){fake_open,fake_read,fake_close};
        strcpy(fake_entry.d_name,"hidden");fake_entry.d_reclen=sizeof fake_entry;
    }
    assert(AMDetectionConfigureDirectories(&p,&originals,ready));
    if (!strcmp(argv[1],"stream")) {
        DIR *stream=AMDetectionOpendir(argv[2]);assert(stream);bool found=false;struct dirent *entry;
        errno=0;
        while ((entry=AMDetectionReaddir(stream))) {assert(strcmp(entry->d_name,"hidden"));if (!strcmp(entry->d_name,"visible")) found=true;}
        assert(found && errno==0);errno=E2BIG;assert(!AMDetectionReaddir(stream) && errno==E2BIG);
        assert(!AMDetectionClosedir(stream));
    } else if (!strcmp(argv[1],"capacity")) {
        DIR *streams[257];for (unsigned i=0;i<257;i++) {streams[i]=AMDetectionOpendir(argv[2]);assert(streams[i]);}
        bool found=false;struct dirent *entry;
        while ((entry=AMDetectionReaddir(streams[256]))) if (!strcmp(entry->d_name,"hidden")) found=true;
        assert(found && AMDetectionReadCoverage().incomplete && AMDetectionReadCoverage().operational_gaps);
        for (unsigned i=0;i<257;i++) assert(!AMDetectionClosedir(streams[i]));
    } else if (!strcmp(argv[1],"symlink")) {
        assert(!symlink(".",alias));DIR *stream=AMDetectionOpendir(alias);assert(stream);
        struct dirent *entry;bool found=false;
        while ((entry=AMDetectionReaddir(stream))) {assert(strcmp(entry->d_name,"hidden"));if (!strcmp(entry->d_name,"visible")) found=true;}
        assert(found && !AMDetectionReadCoverage().incomplete);assert(!AMDetectionClosedir(stream));
    } else if (!strcmp(argv[1],"normalization")) {
        char sub[4096],ambiguous[4096];snprintf(sub,sizeof sub,"%s/sub",argv[2]);snprintf(ambiguous,sizeof ambiguous,"%s/sub/..",argv[2]);
        assert(!mkdir(sub,0700));DIR *stream=AMDetectionOpendir(ambiguous);assert(stream);
        struct dirent *entry;bool found=false;
        while ((entry=AMDetectionReaddir(stream))) if (!strcmp(entry->d_name,"hidden")) found=true;
        assert(found && AMDetectionReadCoverage().incomplete);assert(!AMDetectionClosedir(stream));
    } else if (!strcmp(argv[1],"relative")) {
        char parent[4096];strcpy(parent,argv[2]);char *leaf=strrchr(parent,'/');assert(leaf && leaf[1]);
        *leaf=0;assert(!chdir(parent));DIR *stream=AMDetectionOpendir(leaf+1);assert(stream);
        struct dirent *entry;bool found=false;
        while ((entry=AMDetectionReaddir(stream))) if (!strcmp(entry->d_name,"hidden")) found=true;
        assert(found && AMDetectionReadCoverage().incomplete);assert(!AMDetectionClosedir(stream));
    } else if (concurrent_case) {
        assert(AMDetectionOpendir("/fixture/A")==fake_handle);assert(AMDetectionOpendir("/fixture/B")==other_handle);
        pthread_t reader,closer,other;assert(!pthread_create(&reader,NULL,blocked_reader,NULL));
        pthread_mutex_lock(&sync_lock);while (!read_entered) pthread_cond_wait(&sync_condition,&sync_lock);pthread_mutex_unlock(&sync_lock);
        assert(!pthread_create(&closer,NULL,waiting_closer,NULL));
        pthread_mutex_lock(&sync_lock);while (!close_started) pthread_cond_wait(&sync_condition,&sync_lock);pthread_mutex_unlock(&sync_lock);
        struct timespec settle={0,20000000};nanosleep(&settle,NULL);
        assert(!pthread_create(&other,NULL,other_reader,NULL));
        struct timespec deadline;assert(!clock_gettime(CLOCK_REALTIME,&deadline));deadline.tv_sec+=2;
        pthread_mutex_lock(&sync_lock);
        while (!other_done && pthread_cond_timedwait(&sync_condition,&sync_lock,&deadline)==0) {}
        bool progressed=other_done;release_read=true;pthread_cond_broadcast(&sync_condition);pthread_mutex_unlock(&sync_lock);
        assert(!pthread_join(reader,NULL));assert(!pthread_join(closer,NULL));assert(!pthread_join(other,NULL));
        assert(!AMDetectionClosedir(other_handle));assert(progressed);
    } else if (reuse) {
        assert(AMDetectionOpendir("/fixture/A")==fake_handle);
        pthread_t thread;assert(!pthread_create(&thread,NULL,reopen,NULL));assert(!pthread_join(thread,NULL));
        assert(AMDetectionReaddir(fake_handle)==&fake_entry);
        assert(AMDetectionClosedir(fake_handle)==-1 && errno==EINVAL);
        assert(AMDetectionClosedir(fake_handle)==-1 && errno==EINVAL && closes==3);
    } else if (outcome) {
        assert(AMDetectionOpendir("/fixture/A")==fake_handle);
        assert(!AMDetectionReaddir(fake_handle));
        AMDetectionEventCallsite sites[4];size_t n=AMDetectionReadEventCallsites(sites,4);
        assert(n==1 && sites[0].key.entry==AM_DETECTION_ENTRY_READDIR);
        assert(sites[0].results[AM_DETECTION_RESULT_SUCCESS]==1);
        assert(sites[0].results[AM_DETECTION_RESULT_NOT_CALLED]==0);
        assert(!AMDetectionClosedir(fake_handle));
    } else return 2;
    return 0;
}
