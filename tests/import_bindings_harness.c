#include "AMImportBindings.h"
#include <assert.h>
#include <string.h>

static union { uint64_t align; unsigned char data[4096]; } storage;
static unsigned writes;
static bool fail_write;
static bool writer(void **slot,void *expected,void *replacement,void *context) {
    (void)context; writes++; if (fail_write || *slot!=expected) return false; *slot=replacement; return true;
}
static void original(void) {}
static void replacement(void) {}
static AMImportHeader *fixture(void) {
    memset(&storage,0,sizeof storage); writes=0; fail_write=false;
    unsigned char *bytes=storage.data;
    AMImportHeader *h=(void *)bytes;
    *h=(AMImportHeader){.magic=0xfeedfacf,.cputype=0x100000c,.ncmds=5,
        .sizeofcmds=sizeof(AMImportSegment)*3+sizeof(AMImportSection)+sizeof(AMImportSymtab)+sizeof(AMImportDysymtab)};
    AMImportSegment *text=(void *)(h+1);
    *text=(AMImportSegment){.cmd=0x19,.cmdsize=sizeof *text,.vmaddr=(uintptr_t)bytes,.vmsize=1024,.filesize=1024,.initprot=1};
    strcpy(text->segname,"__TEXT");
    AMImportSegment *data=(void *)((unsigned char *)text+text->cmdsize);
    *data=(AMImportSegment){.cmd=0x19,.cmdsize=sizeof *data+sizeof(AMImportSection),.vmaddr=(uintptr_t)bytes+1024,.vmsize=1024,.fileoff=1024,.filesize=1024,.initprot=3,.nsects=1};
    strcpy(data->segname,"__DATA");
    AMImportSection *sec=(void *)(data+1); *sec=(AMImportSection){.addr=(uintptr_t)bytes+1024,.size=8,.flags=6};
    AMImportSegment *link=(void *)((unsigned char *)data+data->cmdsize);
    *link=(AMImportSegment){.cmd=0x19,.cmdsize=sizeof *link,.vmaddr=(uintptr_t)bytes+2048,.vmsize=1024,.fileoff=2048,.filesize=1024,.initprot=1};
    strcpy(link->segname,"__LINKEDIT");
    AMImportSymtab *sym=(void *)(link+1); *sym=(AMImportSymtab){.cmd=2,.cmdsize=sizeof *sym,.symoff=2100,.nsyms=1,.stroff=2140,.strsize=32};
    AMImportDysymtab *ind=(void *)(sym+1); *ind=(AMImportDysymtab){.cmd=0xb,.cmdsize=sizeof *ind,.indirectsymoff=2200,.nindirectsyms=1};
    AMImportSymbol symbol={.strx=1}; memcpy(bytes+2100,&symbol,sizeof symbol);
    strcpy((char *)bytes+2141,"_access"); *(uint32_t *)(bytes+2200)=0;
    *(void **)(bytes+1024)=(void *)original;
    return h;
}
static AMImportBindResult bind(AMImportHeader *h,size_t bytes) {
    AMImportBinding b={"access",(void *)original,(void *)replacement,NULL};
    return AMImportBindImage(h,bytes,0,&b,1,writer,NULL);
}
/* A dyld-resolved non-authenticated image with retained indirect metadata. */
static AMImportHeader *chained_fixture(void) {
    AMImportHeader *h=fixture();
    uint32_t *command=(void *)(storage.data+sizeof *h+h->sizeofcmds);
    command[0]=0x80000034u; command[1]=16; command[2]=2300; command[3]=60;
    h->ncmds++; h->sizeofcmds+=16;
    uint32_t *payload=(void *)(storage.data+2300);
    payload[1]=28; /* starts_offset */
    payload[7]=1; payload[8]=8; /* one segment */
    unsigned char *segment=storage.data+2336;
    *(uint32_t *)segment=24;
    *(uint16_t *)(segment+4)=4096;
    *(uint16_t *)(segment+6)=6; /* DYLD_CHAINED_PTR_64_OFFSET */
    *(uint16_t *)(segment+20)=1;
    *(uint16_t *)(segment+22)=0xffff;
    return h;
}
int main(void) {
    AMImportHeader *h=fixture(); AMImportBindResult r=bind(h,1024);
    assert(r.applied==1 && !r.failed && !r.unsupported && writes==1 && r.reason==AM_IMPORT_FAILURE_NONE);
    r=bind(h,1024); assert(r.already_owned==1 && writes==1);
    h=fixture(); AMImportSegment *zero=(void *)(h+1); zero->vmaddr=0; zero->filesize=0; zero->initprot=0;
    r=bind(h,1024); assert(r.applied==1 && !r.malformed); /* unmapped __PAGEZERO */
    h=fixture(); *(void **)(storage.data+1024)=(void *)fixture;
    r=bind(h,1024); assert(r.conflicts==1 && writes==0);
    h=fixture(); fail_write=true; r=bind(h,1024); assert(r.failed==1 && writes==1 && *(void **)(storage.data+1024)==(void *)original);
    h=fixture(); r=bind(h,34); assert(r.malformed && writes==0 && r.reason==AM_IMPORT_FAILURE_MALFORMED);
    h=fixture(); ((AMImportSegment *)(h+1))->cmdsize=0; r=bind(h,1024); assert(r.malformed && writes==0);
    h=fixture(); h->sizeofcmds=0xffffffffu; r=bind(h,1024); assert(r.malformed && writes==0);
    h=fixture(); *(uint32_t *)(storage.data+2100)=90; r=bind(h,1024); assert(r.malformed && writes==0);
    h=fixture(); *(uint32_t *)(storage.data+2200)=99; r=bind(h,1024); assert(r.malformed && writes==0);
    h=fixture(); memset(storage.data+2140,'x',32); r=bind(h,1024); assert(r.malformed && writes==0);
    h=fixture(); AMImportSegment *data=(void *)((unsigned char *)(h+1)+sizeof(AMImportSegment));
    ((AMImportSection *)(data+1))->addr=0; r=bind(h,1024); assert(r.malformed && writes==0);
    h=fixture(); data=(void *)((unsigned char *)(h+1)+sizeof(AMImportSegment)); strcpy(data->segname,"__AUTH_CONST");
    r=bind(h,1024); assert(r.unsupported && writes==0 && r.reason==AM_IMPORT_FAILURE_AUTHENTICATED);
    h=chained_fixture(); r=bind(h,1024);
    assert(r.applied==1 && !r.unsupported && !r.malformed && writes==1);
    r=bind(h,1024); assert(r.already_owned==1 && writes==1);
    h=chained_fixture(); *(uint16_t *)(storage.data+2342)=2; r=bind(h,1024);
    assert(r.applied==1 && !r.unsupported && writes==1);
    h=chained_fixture(); *(void **)(storage.data+1024)=(void *)fixture; r=bind(h,1024);
    assert(r.conflicts==1 && writes==0);
    h=chained_fixture(); *(uint16_t *)(storage.data+2342)=12; r=bind(h,1024);
    assert(r.unsupported && writes==0 && r.reason==AM_IMPORT_FAILURE_AUTHENTICATED);
    h=chained_fixture(); *(uint16_t *)(storage.data+2342)=99; r=bind(h,1024);
    assert(r.unsupported && writes==0 && r.reason==AM_IMPORT_FAILURE_CHAINED_FIXUPS);
    assert(!strcmp(AMImportFailureName(r.reason),"chained_fixups"));
    h=chained_fixture(); *(uint32_t *)(storage.data+2304)=59; r=bind(h,1024);
    assert(r.malformed && writes==0);
    h=chained_fixture(); *(uint32_t *)(storage.data+2328)=0xffffffffu; r=bind(h,1024);
    assert(r.malformed && writes==0);
    h=chained_fixture(); *(uint32_t *)(storage.data+2332)=59; r=bind(h,1024);
    assert(r.malformed && writes==0);
    h=chained_fixture(); *(uint16_t *)(storage.data+2356)=4096; r=bind(h,1024);
    assert(r.malformed && writes==0);
    h=chained_fixture(); *(uint32_t *)(storage.data+2100)=90; r=bind(h,1024);
    assert(r.malformed && writes==0); /* chain support must not weaken classic validation */
    h=chained_fixture(); AMImportSegment *missing_chained=(void *)((unsigned char *)(h+1)+sizeof(AMImportSegment)*2+sizeof(AMImportSection));
    strcpy(missing_chained->segname,"__OTHER"); r=bind(h,1024);
    assert(r.unsupported && writes==0 && r.reason==AM_IMPORT_FAILURE_CLASSIC_TABLES);
    h=fixture(); h->cpusubtype=2; r=bind(h,1024);
    assert(r.unsupported && writes==0 && r.reason==AM_IMPORT_FAILURE_ARCHITECTURE);
    h=fixture(); ((AMImportSegment *)(h+1))->cmd=0; r=bind(h,1024);
    assert(r.applied==1 && r.reason==AM_IMPORT_FAILURE_NONE);
    h=fixture(); AMImportSegment *missing=(void *)((unsigned char *)(h+1)+sizeof(AMImportSegment)*2+sizeof(AMImportSection));
    strcpy(missing->segname,"__OTHER"); r=bind(h,1024);
    assert(r.unsupported && writes==0 && r.reason==AM_IMPORT_FAILURE_CLASSIC_TABLES);
    assert(!strcmp(AMImportFailureName(AM_IMPORT_FAILURE_COUNT),"unknown"));
    h=fixture(); AMImportSegment *link=(void *)((unsigned char *)(h+1)+sizeof(AMImportSegment)*2+sizeof(AMImportSection));
    link->filesize=16; r=bind(h,1024); assert(r.malformed && writes==0);
    return 0;
}
