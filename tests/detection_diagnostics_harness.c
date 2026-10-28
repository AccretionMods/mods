/* Include the real implementation to arrange collisions/saturation without a test API. */
#include "AMDetectionDiagnostics.c"
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <string.h>

static AMDetectionEventRow rows[4096];
static AMDetectionEventCallsite sites[4096];
static AMDetectionEventKey key(unsigned entry,unsigned image,unsigned index) {
    return (AMDetectionEventKey){.entry=(AMDetectionEntryID)entry,.image=(uint16_t)image,
        .rule={AM_DETECTION_RULE_PATH,index},.phase=AM_DETECTION_PHASE_FIGHT};
}
static void *writer(void *context) {
    (void)context;
    for (unsigned i=0;i<10000;i++) AMDetectionRecordEvent(key(9,64,0),17,false,AM_DETECTION_RESULT_SUCCESS);
    return NULL;
}
int main(int argc,char **argv) {
    assert(argc==2); AMDetectionDiagnosticsConfigure(true);
    if (!strcmp(argv[1],"identity")) {
        unsigned ids[]={1,9,17};
        for (unsigned i=0;i<3;i++) {
            AMDetectionEventKey k=key(ids[i],64,0);
            /* Force callsite hashes to collide while full identities differ. */
            AMDetectionRecordEvent(k,am_diag_hash(k),false,AM_DETECTION_RESULT_SUCCESS);
        }
        AMDetectionRecordEvent(key(1,0,0),123,false,AM_DETECTION_RESULT_NEGATIVE);
        assert(AMDetectionReadEvents(rows,4096)==4);
        assert(AMDetectionReadEventCallsites(sites,4096)==4);
        bool found[3]={0}; bool zero=false;
        for (unsigned i=0;i<4;i++) {
            assert(rows[i].counts.calls==1);
            if (rows[i].key.image==0) { assert(rows[i].key.entry==1);zero=true; }
            else for (unsigned j=0;j<3;j++) if (rows[i].key.entry==ids[j]) found[j]=true;
        }
        assert(zero && found[0] && found[1] && found[2]);
        assert(AMDetectionReadEntryCounters(6).calls==1); /* historical Foundation aggregate */
        assert(AMDetectionReadEntryCounters(9).calls==1); /* concrete selector remains distinct */
        assert(!AMDetectionReadCoverage().incomplete);
    } else if (!strcmp(argv[1],"capacity")) {
        for (unsigned i=0;i<4096;i++) AMDetectionRecordEvent(key(1,i/64,i%64),i+1,false,AM_DETECTION_RESULT_NEGATIVE);
        assert(AMDetectionReadEvents(rows,4096)==4096);
        assert(AMDetectionReadEventCallsites(sites,4096)==4096);
        AMDetectionRecordEvent(key(9,64,0),8192,false,AM_DETECTION_RESULT_SUCCESS);
        AMDetectionCoverage c=AMDetectionReadCoverage();
        assert(c.attribution_dropped==1 && c.callsite_dropped==1 && c.incomplete);
        assert(c.allocation_failures==0);
        /* A new caller with existing attribution drops only the callsite row. */
        AMDetectionRecordEvent(key(1,0,0),16384,false,AM_DETECTION_RESULT_SUCCESS);
        c=AMDetectionReadCoverage(); assert(c.attribution_dropped==1 && c.callsite_dropped==2);
    } else if (!strcmp(argv[1],"saturation")) {
        atomic_uint value=UINT32_MAX-1;
        am_diag_increment(&value); am_diag_increment(&value);
        assert(atomic_load(&value)==UINT32_MAX);
        AMDetectionRecordAllocationFailure();
        AMDetectionCoverage c=AMDetectionReadCoverage();
        assert(c.saturated && c.allocation_failures==1 && c.incomplete);
    } else if (!strcmp(argv[1],"threads")) {
        pthread_t threads[4];
        for (unsigned i=0;i<4;i++) assert(!pthread_create(&threads[i],NULL,writer,NULL));
        for (unsigned i=0;i<4;i++) assert(!pthread_join(threads[i],NULL));
        assert(AMDetectionReadEntryCounters(9).calls==40000);
        assert(AMDetectionReadEvents(rows,4096)==1 && rows[0].key.entry==9 && rows[0].key.image==64);
        assert(rows[0].counts.calls+AMDetectionReadCoverage().attribution_dropped==40000);
        assert(AMDetectionReadEventCallsites(sites,4096)==1);
        assert(sites[0].results[AM_DETECTION_RESULT_SUCCESS]+AMDetectionReadCoverage().callsite_dropped==40000);
    } else return 2;
    errno=E2BIG; AMDetectionRecordEvent(key(1,0,0),1,true,AM_DETECTION_RESULT_SUCCESS); assert(errno==E2BIG);
    return 0;
}
