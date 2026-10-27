#include "AMDetectionProcess.h"
#include "AMDetectionHooks.h"
#include <assert.h>
#include <errno.h>
#include <string.h>
static unsigned calls;static bool active=true;
static const posix_spawn_file_actions_t *expected_actions;
static const posix_spawnattr_t *expected_attributes;
static char *const *expected_arguments,*const *expected_environment;
static bool ready(AMDetectionEntryID id) {(void)id;return active;}
static int original(pid_t *pid,const char *path,const posix_spawn_file_actions_t *actions,
    const posix_spawnattr_t *attributes,char *const arguments[],char *const environment[]) {
    assert(path && actions==expected_actions && attributes==expected_attributes);
    assert(arguments==expected_arguments && environment==expected_environment);
    calls++;if (pid) *pid=123;errno=EDOM;return EAGAIN;
}
int main(int argc,char **argv) {
    assert(argc==2);bool filter=!strcmp(argv[1],"filter");active=strcmp(argv[1],"inactive")!=0;
    const char *paths[]={"/fixture/probe"};
    AMDetectionProfile p={.mode=!strcmp(argv[1],"observe") ? AM_DETECTION_OBSERVE : !strcmp(argv[1],"off") ? AM_DETECTION_OFF : AM_DETECTION_FILTER,
        .rules={.spawn_paths=paths,.spawn_count=1}};
    AMDetectionOriginals files={0};assert(AMDetectionConfigure(&p,&files));
    assert(AMDetectionConfigureProcess(&p,original,ready));
    posix_spawn_file_actions_t actions;posix_spawnattr_t attributes;
    char *arguments[]={"fixture",NULL},*environment[]={"AM_SECRET=never-log",NULL};
    expected_actions=&actions;expected_attributes=&attributes;expected_arguments=arguments;expected_environment=environment;
    pid_t pid=987;errno=E2BIG;
    int code=AMDetectionPosixSpawn(&pid,paths[0],&actions,&attributes,arguments,environment);
    assert(code==(filter ? EACCES : EAGAIN));assert(pid==(filter ? 987 : 123));
    assert(errno==(filter ? E2BIG : EDOM) && calls==(filter ? 0u : 1u));
    errno=E2BIG;unsigned prior=calls;
    assert(AMDetectionPosixSpawn(&pid,"/fixture/ordinary",&actions,&attributes,arguments,environment)==EAGAIN);
    assert(pid==123 && errno==EDOM && calls==prior+1);
    if (filter) {errno=E2BIG;assert(AMDetectionPosixSpawn(NULL,paths[0],&actions,&attributes,arguments,environment)==EACCES && errno==E2BIG);}
    return 0;
}
