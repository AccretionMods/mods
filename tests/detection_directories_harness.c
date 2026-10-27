#include "AMDetectionDirectories.h"
#include "AMDetectionHooks.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>

static bool ready(AMDetectionEntryID entry) {(void)entry;return true;}
static DIR *fake_handle=(DIR *)(uintptr_t)0x1000;
static unsigned fake_position,closes;
static struct dirent fake_entry;
static DIR *fake_open(const char *path) {(void)path;fake_position=0;return fake_handle;}
static struct dirent *fake_read(DIR *handle) {assert(handle==fake_handle);return fake_position++ ? NULL : &fake_entry;}
static int fake_close(DIR *handle) {(void)handle;closes++;if (closes>1) {errno=EINVAL;return -1;}return 0;}
static void *reopen(void *context) {
    (void)context;assert(!AMDetectionClosedir(fake_handle));
    assert(AMDetectionOpendir("/fixture/B")==fake_handle);return NULL;
}
int main(int argc,char **argv) {
    assert(argc==3);char hidden[4096],visible[4096];
    snprintf(hidden,sizeof hidden,"%s/hidden",argv[2]);snprintf(visible,sizeof visible,"%s/visible",argv[2]);
    int fd=open(hidden,O_CREAT|O_WRONLY,0600);assert(fd>=0);close(fd);
    fd=open(visible,O_CREAT|O_WRONLY,0600);assert(fd>=0);close(fd);
    bool reuse=!strcmp(argv[1],"reuse"),outcome=!strcmp(argv[1],"outcome");
    const char *paths[]={reuse || outcome ? "/fixture/A/hidden" : hidden};
    AMDetectionProfile p={.mode=AM_DETECTION_FILTER,.rules={.paths=paths,.path_count=1}};
    AMDetectionOriginals files={.getcwd_fn=getcwd};assert(AMDetectionConfigure(&p,&files));
    AMDetectionDirectoryOriginals originals={opendir,readdir,closedir};
    if (reuse || outcome) {
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
    } else if (!strcmp(argv[1],"relative")) {
        char parent[4096];strcpy(parent,argv[2]);char *leaf=strrchr(parent,'/');assert(leaf && leaf[1]);
        *leaf=0;assert(!chdir(parent));DIR *stream=AMDetectionOpendir(leaf+1);assert(stream);
        struct dirent *entry;bool found=false;
        while ((entry=AMDetectionReaddir(stream))) if (!strcmp(entry->d_name,"hidden")) found=true;
        assert(found && AMDetectionReadCoverage().incomplete);assert(!AMDetectionClosedir(stream));
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
