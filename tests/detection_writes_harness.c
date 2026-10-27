#include "AMDetectionWrites.h"
#include <assert.h>
#include <string.h>
int main(void) {
    char out[4096],before[4096];memset(out,'Q',sizeof out);memcpy(before,out,sizeof out);
    assert(AMDetectionExpandWriteProbe("Game Name",9,out,sizeof out));assert(!strcmp(out,"/private/Game Name"));
    assert(AMDetectionExpandWriteProbe("Pok\303\251mon",8,out,sizeof out));assert(!strcmp(out,"/private/Pok\303\251mon"));
    const char *bad[]={"",".","..","../Game","Game/Child","Game\\Child","var","TMP"};
    for (unsigned i=0;i<sizeof bad/sizeof *bad;i++) assert(!AMDetectionExpandWriteProbe(bad[i],strlen(bad[i]),out,sizeof out));
    assert(!AMDetectionExpandWriteProbe("a\0b",3,out,sizeof out));
    memset(out,'Q',sizeof out);assert(!AMDetectionExpandWriteProbe("Game",4,out,8));assert(!memcmp(out,before,sizeof out));
    char large[4088];memset(large,'x',sizeof large);
    assert(AMDetectionExpandWriteProbe(large,4086,out,sizeof out));assert(strlen(out)==4095);
    assert(!AMDetectionExpandWriteProbe(large,4087,out,sizeof out));return 0;
}
