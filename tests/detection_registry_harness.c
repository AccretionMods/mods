#include "AMDetectionRegistry.h"
#include "AMDetectionHooks.h"
#include <assert.h>
#include <errno.h>
#include <string.h>

static unsigned prepared,published;
static AMDetectionEntryID fail_prepare=UINT16_MAX,fail_publish=UINT16_MAX;
static bool prepare(const AMDetectionEntry *entry,void *context) {
    assert(context==&prepared); prepared++;
    assert(!AMDetectionModuleActive(entry->module));
    return entry->id!=fail_prepare;
}
static bool publish(const AMDetectionEntry *entry,void *context) {
    assert(context==&prepared); published++;
    assert(!AMDetectionModuleActive(entry->module));
    return entry->id!=fail_publish;
}
static void install(AMDetectionEntryID id) {
    assert(AMDetectionSetInstaller(id,prepare,publish,&prepared));
}
static int original_stat(const char *path,struct stat *out) {
    (void)path; (void)out; errno=E2BIG; return 0;
}
static bool module_ready(AMDetectionAPI api) {
    return api==AM_DETECTION_STAT && AMDetectionModuleActive(AM_DETECTION_MODULE_FILES);
}
int main(int argc,char **argv) {
    assert(argc==2);
    if (!strcmp(argv[1],"identity")) {
        assert(!strcmp(AMDetectionEntryByID(1)->name,"stat"));
        assert(!strcmp(AMDetectionEntryByID(8)->name,"foundation_exists"));
        assert(!strcmp(AMDetectionEntryByID(9)->name,"foundation_attributes"));
        assert(!strcmp(AMDetectionEntryByID(10)->name,"can_open_url"));
        for (size_t i=0;i<AMDetectionEntryCount();i++) {
            const AMDetectionEntry *a=AMDetectionEntryAt(i);
            assert(a && a->original);
            for (size_t j=0;j<i;j++) {
                const AMDetectionEntry *b=AMDetectionEntryAt(j);
                assert(a->id!=b->id && strcmp(a->name,b->name));
            }
        }
        AMDetectionEntryID aggregate=6;
        assert(!AMDetectionValidateSelection(&aggregate,1,0));
        assert(!AMDetectionEntryByID(UINT16_MAX));
    } else if (!strcmp(argv[1],"limits")) {
        AMDetectionEntryID ids[65]={1};
        assert(AMDetectionValidateSelection(ids,1,63));
        assert(!AMDetectionValidateSelection(ids,1,64));
        assert(!AMDetectionValidateSelection(ids,65,0));
        assert(!AMDetectionValidateSelection(ids,1,SIZE_MAX));
        ids[1]=1; assert(!AMDetectionValidateSelection(ids,2,0));
        ids[0]=UINT16_MAX; assert(!AMDetectionValidateSelection(ids,1,0));
        assert(!AMDetectionValidateSelection(NULL,1,0));
        assert(AMDetectionValidateSelection(NULL,0,64));
        assert(!prepared && !published);
    } else if (!strcmp(argv[1],"transaction") || !strcmp(argv[1],"conflict")) {
        AMDetectionEntryID ids[]={0,1}; install(0); install(1);
        assert(AMDetectionConfigureRegistry(ids,2,4,AM_DETECTION_FILTER));
        assert(!AMDetectionSetInstaller(0,prepare,publish,&prepared));
        assert(!AMDetectionPublishModule(AM_DETECTION_MODULE_FILES));
        fail_prepare=1;
        assert(!AMDetectionPrepareModule(AM_DETECTION_MODULE_FILES));
        assert(!published && !AMDetectionModuleActive(AM_DETECTION_MODULE_FILES));
        fail_prepare=UINT16_MAX;
        assert(AMDetectionPrepareModule(AM_DETECTION_MODULE_FILES));
        fail_publish=1;
        assert(!AMDetectionPublishModule(AM_DETECTION_MODULE_FILES));
        assert(published==2 && !AMDetectionModuleActive(AM_DETECTION_MODULE_FILES));
        assert(AMDetectionModuleStatus(AM_DETECTION_MODULE_FILES).state==AM_DETECTION_FAILED);
        if (!strcmp(argv[1],"conflict")) return 0;
        fail_publish=UINT16_MAX;
        assert(AMDetectionPrepareModule(AM_DETECTION_MODULE_FILES));
        assert(AMDetectionPublishModule(AM_DETECTION_MODULE_FILES));
        assert(AMDetectionModuleActive(AM_DETECTION_MODULE_FILES));
        AMDetectionInvalidateModule(AM_DETECTION_MODULE_FILES);
        assert(!AMDetectionModuleActive(AM_DETECTION_MODULE_FILES));
        assert(AMDetectionModuleStatus(AM_DETECTION_MODULE_FILES).state!=AM_DETECTION_FILTERING);
        assert(!AMDetectionPublishModule(AM_DETECTION_MODULE_FILES));
    } else if (!strcmp(argv[1],"off")) {
        AMDetectionEntryID id=1; install(id);
        assert(AMDetectionConfigureRegistry(&id,1,4,AM_DETECTION_OFF));
        assert(!AMDetectionPrepareModule(AM_DETECTION_MODULE_FILES));
        assert(!AMDetectionPublishModule(AM_DETECTION_MODULE_FILES));
        assert(!prepared && !published);
        assert(AMDetectionModuleStatus(AM_DETECTION_MODULE_FILES).state==AM_DETECTION_DISABLED);
    } else if (!strcmp(argv[1],"invalid")) {
        AMDetectionEntryID id=1; install(id);
        assert(!AMDetectionConfigureRegistry(&id,1,64,AM_DETECTION_FILTER));
        assert(!AMDetectionPrepareModule(AM_DETECTION_MODULE_FILES));
        assert(!prepared && !published);
    } else if (!strcmp(argv[1],"gate")) {
        AMDetectionEntryID id=1; install(id);
        const char *paths[]={"/exact/probe"};
        AMDetectionProfile profile={.mode=AM_DETECTION_FILTER,.rules={.paths=paths,.path_count=1}};
        AMDetectionOriginals originals={.stat_fn=original_stat}; struct stat out;
        assert(AMDetectionConfigureRegistry(&id,1,4,AM_DETECTION_FILTER));
        assert(AMDetectionConfigure(&profile,&originals));
        AMDetectionSetReadinessResolver(module_ready);
        assert(AMDetectionStat(paths[0],&out)==0 && errno==E2BIG);
        assert(AMDetectionPrepareModule(AM_DETECTION_MODULE_FILES));
        assert(AMDetectionStat(paths[0],&out)==0 && errno==E2BIG);
        assert(AMDetectionPublishModule(AM_DETECTION_MODULE_FILES));
        assert(AMDetectionStat(paths[0],&out)==-1 && errno==ENOENT);
        AMDetectionInvalidateModule(AM_DETECTION_MODULE_FILES);
        assert(AMDetectionStat(paths[0],&out)==0 && errno==E2BIG);
    } else return 2;
    return 0;
}
