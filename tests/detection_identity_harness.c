#include "AMDetectionIdentity.h"
#include <assert.h>
#include <errno.h>
#include <string.h>

int main(int argc,char **argv) {
    assert(argc==2);
    const AMDetectionIdentityIO io={fopen,fread,fseek,ftell,fclose};
    AMDetectionImageIdentity identity={0},before=identity;
    errno=E2BIG;
    bool ok=AMDetectionIdentityReadFile(argv[1],&io,&identity);
    assert(errno==E2BIG);
    if (!ok) {assert(!memcmp(&before,&identity,sizeof identity));return 1;}
    assert(AMDetectionIdentityMatches(&identity,&identity));
    AMDetectionImageIdentity changed=identity;changed.sha256[0]^=1;assert(!AMDetectionIdentityMatches(&identity,&changed));
    changed=identity;changed.uuid[0]^=1;assert(!AMDetectionIdentityMatches(&identity,&changed));
    changed=identity;changed.ranges[0].size++;assert(!AMDetectionIdentityMatches(&identity,&changed));
    changed=identity;changed.range_count=129;assert(!AMDetectionIdentityMatches(&changed,&identity));
    for (unsigned i=0;i<16;i++) printf("%02x",identity.uuid[i]);
    putchar(':');for (unsigned i=0;i<32;i++) printf("%02x",identity.sha256[i]);
    printf(":%u",identity.range_count);
    for (unsigned i=0;i<identity.range_count;i++) printf(":%llu,%llu",(unsigned long long)identity.ranges[i].offset,(unsigned long long)identity.ranges[i].size);
    putchar('\n');return 0;
}
