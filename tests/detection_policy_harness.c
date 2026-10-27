#include "AMDetectionHooks.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static unsigned image_value=2;
static unsigned origin(const void *caller) { (void)caller; errno=EDOM; return image_value; }
static uint64_t token_value=123;
static uint64_t callsite(const void *caller) { (void)caller; errno=EDOM; return token_value; }
static FILE *fake_fopen(const char *path,const char *mode) {
    (void)path;
    if (!strcmp(mode,"success")) { errno=E2BIG; return (FILE *)(uintptr_t)1; }
    errno=!strcmp(mode,"missing") ? ENOENT : EACCES; return NULL;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    assert(AMDetectionEffectiveMode(AM_DETECTION_FILTER,AM_DETECTION_OBSERVE)==AM_DETECTION_OBSERVE);
    assert(AMDetectionEffectiveMode(AM_DETECTION_OFF,AM_DETECTION_FILTER)==AM_DETECTION_OFF);
    assert(AMDetectionEffectiveMode(AM_DETECTION_OBSERVE,AM_DETECTION_FILTER)==AM_DETECTION_OBSERVE);
    assert(AMDetectionEffectiveMode((AMDetectionMode)99,AM_DETECTION_FILTER)==AM_DETECTION_OFF);
    assert(AMDetectionAdapterState(AM_DETECTION_FILTER,true,0,0,false)==AM_DETECTION_UNSUPPORTED);
    assert(AMDetectionAdapterState(AM_DETECTION_FILTER,true,1,1,true)==AM_DETECTION_FAILED);
    assert(AMDetectionAdapterState(AM_DETECTION_FILTER,true,1,0,true)==AM_DETECTION_FILTERING);
    assert(AMDetectionAdapterState(AM_DETECTION_OBSERVE,true,1,0,true)==AM_DETECTION_OBSERVING);
    assert(AMDetectionAdapterState(AM_DETECTION_OFF,true,1,0,true)==AM_DETECTION_DISABLED);
    const char *paths[] = {"/am-test/exact"};
    const char *prefixes[] = {"/opt/test"};
    const char *schemes[] = {"cydia"};
    const char *envs[] = {"AM_PROBE_ENV"};
    AMDetectionProfile p = {.mode = AM_DETECTION_FILTER,
        .rules = {.paths = paths, .path_count = 1, .path_prefixes = prefixes, .prefix_count = 1,
                  .url_schemes = schemes, .scheme_count = 1, .environment_names = envs, .environment_count = 1}};
    if (!strcmp(argv[1],"outcomes")) {
        p.mode=AM_DETECTION_OBSERVE;
        AMDetectionOriginals original={.fopen_fn=fake_fopen};
        assert(AMDetectionConfigure(&p,&original));
        AMDetectionSetOriginResolver(origin); AMDetectionSetCallsiteResolver(callsite);
        assert(AMDetectionFopen(paths[0],"success")== (FILE *)(uintptr_t)1 && errno==E2BIG);
        assert(!AMDetectionFopen(paths[0],"missing") && errno==ENOENT);
        assert(!AMDetectionFopen(paths[0],"denied") && errno==EACCES);
        token_value=456;
        assert(!AMDetectionFopen(paths[0],"missing") && errno==ENOENT);
        assert(!AMDetectionFopen("/ordinary","missing"));
        AMDetectionCallsite rows[AM_DETECTION_CALLSITE_CAPACITY];
        size_t n=AMDetectionReadCallsites(rows,AM_DETECTION_CALLSITE_CAPACITY);
        assert(n==2);
        bool first=false,second=false;
        for (size_t i=0;i<n;i++) {
            assert(rows[i].origin.image==2 && rows[i].origin.rule.kind==AM_DETECTION_RULE_PATH);
            if (rows[i].token==123) {
                assert(rows[i].results[AM_DETECTION_RESULT_SUCCESS]==1);
                assert(rows[i].results[AM_DETECTION_RESULT_NEGATIVE]==1);
                assert(rows[i].results[AM_DETECTION_RESULT_ERROR]==1); first=true;
            } else if (rows[i].token==456) {
                assert(rows[i].results[AM_DETECTION_RESULT_NEGATIVE]==1); second=true;
            } else assert(false);
        }
        assert(first && second);
        for (unsigned i=0;i<AM_DETECTION_CALLSITE_CAPACITY+1;i++) {
            token_value=1000+i; AMDetectionFopen(paths[0],"missing");
        }
        assert(AMDetectionReadCallsites(rows,AM_DETECTION_CALLSITE_CAPACITY)==AM_DETECTION_CALLSITE_CAPACITY);
        assert(AMDetectionCallsitesDropped()>0);
        return 0;
    }
    if (!strcmp(argv[1], "paths")) {
        assert(AMDetectionMatchPath(&p, "/am-test/exact"));
        assert(!AMDetectionMatchPath(&p, "/am-test/exact-more"));
        assert(AMDetectionMatchPath(&p, "/opt/test"));
        assert(AMDetectionMatchPath(&p, "/opt/test/a"));
        assert(!AMDetectionMatchPath(&p, "/opt/testing/a"));
        const char *pass[] = {NULL, "", "relative", "/opt/test/../a", "/opt/test/./a", "/opt//test/a"};
        for (size_t i = 0; i < sizeof pass / sizeof *pass; i++) assert(!AMDetectionMatchPath(&p, pass[i]));
        char longpath[5000]; memset(longpath, 'x', sizeof longpath); longpath[0]='/'; longpath[4999]=0;
        assert(!AMDetectionMatchPath(&p, longpath));
        return 0;
    }
    if (!strcmp(argv[1], "names")) {
        assert(AMDetectionMatchScheme(&p, "CyDiA"));
        assert(!AMDetectionMatchScheme(&p, "cydias"));
        assert(!AMDetectionMatchScheme(&p, NULL));
        assert(AMDetectionMatchEnvironment(&p, "AM_PROBE_ENV"));
        assert(!AMDetectionMatchEnvironment(&p, "AM_PROBE_ENV_EXTRA"));
        assert(!AMDetectionMatchEnvironment(&p, "am_probe_env"));
        return 0;
    }
    char filename[] = "/tmp/am-detection-test-XXXXXX";
    int fd = mkstemp(filename); assert(fd >= 0); assert(close(fd) == 0);
    const char *filepaths[] = {filename}; p.rules.paths=filepaths;
    p.mode = !strcmp(argv[1], "off") ? AM_DETECTION_OFF : !strcmp(argv[1], "observe") ? AM_DETECTION_OBSERVE : AM_DETECTION_FILTER;
    AMDetectionOriginals orig = {.access_fn=access, .stat_fn=stat, .lstat_fn=lstat, .open_fn=open, .fopen_fn=fopen, .getenv_fn=getenv};
    assert(AMDetectionConfigure(&p, &orig));
    assert(!AMDetectionConfigure(&p, &orig)); /* immutable after startup */
    AMDetectionSetOriginResolver(origin);
    assert(AMDetectionCurrentPhase()==AM_DETECTION_PHASE_STARTUP);
    assert(!AMDetectionSetPhase(AM_DETECTION_PHASE_COUNT));
    assert(AMDetectionSetPhase(AM_DETECTION_PHASE_LOGIN)==(p.mode!=AM_DETECTION_OFF));
    assert(setenv("AM_PROBE_ENV", "probe-value", 1) == 0);
    struct stat st, before; memset(&st, 0x51, sizeof st); before=st;
    if (p.mode == AM_DETECTION_FILTER) {
        errno=0; assert(AMDetectionAccess(filename,F_OK)==-1 && errno==ENOENT);
        errno=0; assert(AMDetectionStat(filename,&st)==-1 && errno==ENOENT && !memcmp(&st,&before,sizeof st));
        errno=0; assert(AMDetectionLstat(filename,&st)==-1 && errno==ENOENT);
        errno=0; assert(AMDetectionOpen(filename,O_RDONLY)==-1 && errno==ENOENT);
        errno=0; assert(AMDetectionFopen(filename,"r")==NULL && errno==ENOENT);
        errno=E2BIG; assert(AMDetectionGetenv("AM_PROBE_ENV")==NULL && errno==E2BIG);
    } else {
        errno=E2BIG; assert(AMDetectionAccess(filename,F_OK)==0 && errno==E2BIG);
        assert(AMDetectionStat(filename,&st)==0);
        assert(AMDetectionLstat(filename,&st)==0);
        fd=AMDetectionOpen(filename,O_RDONLY); assert(fd>=0); close(fd);
        FILE *fp=AMDetectionFopen(filename,"r"); assert(fp); fclose(fp);
        errno=E2BIG; assert(!strcmp(AMDetectionGetenv("AM_PROBE_ENV"),"probe-value") && errno==E2BIG);
    }
    char ordinary[]="/tmp/am-ordinary-test-XXXXXX";
    fd=mkstemp(ordinary); assert(fd>=0); close(fd); unlink(ordinary);
    fd=AMDetectionOpen(ordinary,O_WRONLY|O_CREAT|O_EXCL,0600); assert(fd>=0); close(fd);
    assert(stat(ordinary,&st)==0 && (st.st_mode&0777)==0600);
    assert(AMDetectionAccess(ordinary,F_OK)==0);
    FILE *fp=AMDetectionFopen(ordinary,"r"); assert(fp); fclose(fp);
    AMDetectionCounters c=AMDetectionReadCounters(AM_DETECTION_ACCESS);
    assert(c.filtered == (p.mode == AM_DETECTION_FILTER ? 1u : 0u));
    assert(c.calls == (p.mode == AM_DETECTION_OFF ? 0u : 2u));
    AMDetectionAttribution rows[AM_DETECTION_ATTRIBUTION_CAPACITY];
    size_t n=AMDetectionReadAttribution(rows,AM_DETECTION_ATTRIBUTION_CAPACITY);
    AMDetectionCallsite sites[AM_DETECTION_CALLSITE_CAPACITY];
    size_t site_count=AMDetectionReadCallsites(sites,AM_DETECTION_CALLSITE_CAPACITY);
    if (p.mode==AM_DETECTION_OFF) assert(n==0 && site_count==0);
    else {
        assert(site_count>0);
        for (size_t i=0;i<site_count;i++) {
            assert(sites[i].results[AM_DETECTION_RESULT_UNKNOWN]==0);
            assert(sites[i].results[p.mode==AM_DETECTION_FILTER ? AM_DETECTION_RESULT_NOT_CALLED : AM_DETECTION_RESULT_SUCCESS]==1);
        }
        bool found=false;
        for (size_t i=0;i<n;i++) if (rows[i].api==AM_DETECTION_ACCESS && rows[i].rule.kind==AM_DETECTION_RULE_PATH) {
            assert(rows[i].image==2 && rows[i].phase==AM_DETECTION_PHASE_LOGIN && rows[i].rule.index==0);
            assert(rows[i].counts.calls==1 && rows[i].counts.matched==1);
            found=true;
        }
        assert(found);
        assert(AMDetectionSetPhase(AM_DETECTION_PHASE_FIGHT));
        AMDetectionAccess(filename,F_OK);
        n=AMDetectionReadAttribution(rows,AM_DETECTION_ATTRIBUTION_CAPACITY);
        bool fight=false;
        for (size_t i=0;i<n;i++) if (rows[i].phase==AM_DETECTION_PHASE_FIGHT && rows[i].rule.kind==AM_DETECTION_RULE_PATH) fight=true;
        assert(fight);
        for (image_value=2;image_value<4;image_value++) for (unsigned phase=0;phase<AM_DETECTION_PHASE_COUNT;phase++) {
            AMDetectionSetPhase((AMDetectionPhase)phase);
            for (unsigned index=0;index<64;index++) for (unsigned api=0;api<AM_DETECTION_API_COUNT;api++)
                AMDetectionRecordRule((AMDetectionAPI)api,(AMDetectionRuleRef){AM_DETECTION_RULE_PATH,index},false,NULL);
        }
        assert(AMDetectionReadAttribution(rows,AM_DETECTION_ATTRIBUTION_CAPACITY)==AM_DETECTION_ATTRIBUTION_CAPACITY);
        assert(AMDetectionAttributionDropped()>0); /* no silent truncation */
    }
    AMDetectionSetAdapterReady(AM_DETECTION_ACCESS,false);
    assert(AMDetectionAccess(filename,F_OK)==0); /* failed/unavailable installation must pass through */
    unlink(filename); unlink(ordinary); return 0;
}
