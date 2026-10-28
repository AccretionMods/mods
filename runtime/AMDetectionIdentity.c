#include "AMDetectionIdentity.h"
#include "AMImportBindings.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
typedef CC_SHA256_CTX AMIdentityHash;
static bool am_identity_hash_init(AMIdentityHash *c) { return CC_SHA256_Init(c)==1; }
static bool am_identity_hash_update(AMIdentityHash *c,const void *p,size_t n) { return CC_SHA256_Update(c,p,(CC_LONG)n)==1; }
static bool am_identity_hash_final(AMIdentityHash *c,unsigned char *out) { return CC_SHA256_Final(out,c)==1; }
static void am_identity_hash_free(AMIdentityHash *c) { (void)c; }
#else
#include <openssl/evp.h>
typedef EVP_MD_CTX *AMIdentityHash;
static bool am_identity_hash_init(AMIdentityHash *c) { *c=EVP_MD_CTX_new();return *c && EVP_DigestInit_ex(*c,EVP_sha256(),NULL)==1; }
static bool am_identity_hash_update(AMIdentityHash *c,const void *p,size_t n) { return EVP_DigestUpdate(*c,p,n)==1; }
static bool am_identity_hash_final(AMIdentityHash *c,unsigned char *out) { unsigned n=0;return EVP_DigestFinal_ex(*c,out,&n)==1 && n==32; }
static void am_identity_hash_free(AMIdentityHash *c) { EVP_MD_CTX_free(*c); }
#endif

bool AMDetectionIdentityMatches(const AMDetectionImageIdentity *a,const AMDetectionImageIdentity *b) {
    if (!a || !b || !a->range_count || a->range_count>AM_DETECTION_IDENTITY_MAX_RANGES ||
        a->range_count!=b->range_count || memcmp(a->uuid,b->uuid,16) || memcmp(a->sha256,b->sha256,32)) return false;
    for (unsigned i=0;i<a->range_count;i++) if (a->ranges[i].offset!=b->ranges[i].offset || a->ranges[i].size!=b->ranges[i].size) return false;
    return true;
}
static bool am_identity_range(uint64_t limit,uint64_t offset,uint64_t size) { return offset<=limit && size<=limit-offset; }
static uint64_t am_identity_number(const unsigned char *p,unsigned n,bool big) {
    uint64_t value=0;
    for (unsigned i=0;i<n;i++) value=(value<<8)|p[big ? i : n-1-i];
    return value;
}
static void am_identity_le(unsigned char *out,uint64_t value,unsigned n) {
    for (unsigned i=0;i<n;i++) {out[i]=(unsigned char)value;value>>=8;}
}
static bool am_identity_read(const AMDetectionIdentityIO *io,FILE *file,uint64_t limit,uint64_t at,void *out,size_t n) {
    return am_identity_range(limit,at,n) && io->seek(file,(long)at,SEEK_SET)==0 && io->read(out,1,n,file)==n;
}
bool AMDetectionIdentityReadFile(const char *path,const AMDetectionIdentityIO *io,AMDetectionImageIdentity *out) {
    if (!path || !out || !io || !io->open || !io->read || !io->seek || !io->tell || !io->close) return false;
    int saved=errno;FILE *file=io->open(path,"rb");if (!file) {errno=saved;return false;}
    bool ok=false,hash_ready=false;unsigned char *commands=NULL;AMIdentityHash hash={0};
    AMDetectionImageIdentity identity={0};
    uint64_t slice=0,limit=0,total=0,signature_at=0,signature_size=0;bool uuid=false,signature=false;
    unsigned char first[32];AMImportHeader header;
    if (io->seek(file,0,SEEK_END)) goto done;
    long length=io->tell(file);
    if (length<32 || (uint64_t)length>AM_DETECTION_IDENTITY_MAX_IMAGE) goto done;
    total=limit=(uint64_t)length;
    if (!am_identity_read(io,file,total,0,first,sizeof first)) goto done;
    uint64_t magic=am_identity_number(first,4,true);
    if (magic==0xcafebabe || magic==0xcafebabf || magic==0xbebafeca || magic==0xbfbafeca) {
        bool big=magic==0xcafebabe || magic==0xcafebabf;
        unsigned stride=(magic==0xcafebabf || magic==0xbfbafeca) ? 32 : 20;
        uint64_t count=am_identity_number(first+4,4,big);bool found=false;
        if (count>64 || !am_identity_range(total,8,count*stride)) goto done;
        for (unsigned i=0;i<count;i++) {
            unsigned char arch[32];
            if (!am_identity_read(io,file,total,8+(uint64_t)i*stride,arch,stride)) goto done;
            if (am_identity_number(arch,4,big)!=0x100000c || (am_identity_number(arch+4,4,big)&0xffffff)==2) continue;
            slice=am_identity_number(arch+8,stride==32 ? 8 : 4,big);
            limit=am_identity_number(arch+(stride==32 ? 16 : 12),stride==32 ? 8 : 4,big);
            if (slice<8+count*stride || !am_identity_range(total,slice,limit) || limit<32) goto done;
            found=true;break;
        }
        if (!found) goto done;
    }
    if (!am_identity_read(io,file,total,slice,&header,sizeof header)) goto done;
    if (header.magic!=0xfeedfacf || header.cputype!=0x100000c || (header.cpusubtype&0xffffff)==2 ||
        header.ncmds>4096 || header.sizeofcmds>16*1024*1024 || !am_identity_range(limit,32,header.sizeofcmds)) goto done;
    commands=malloc(header.sizeofcmds ? header.sizeofcmds : 1);
    if (!commands || !am_identity_read(io,file,total,slice+32,commands,header.sizeofcmds)) goto done;
    size_t at=0;
    for (unsigned i=0;i<header.ncmds;i++) {
        uint32_t cmd,size;
        if (!am_identity_range(header.sizeofcmds,at,8)) goto done;
        memcpy(&cmd,commands+at,4);memcpy(&size,commands+at+4,4);
        if (size<8 || size%8 || !am_identity_range(header.sizeofcmds,at,size)) goto done;
        const unsigned char *p=commands+at;
        if (cmd==25) {
            AMImportSegment segment;
            if (size<sizeof segment) goto done;
            memcpy(&segment,p,sizeof segment);
            if (segment.nsects>(size-sizeof segment)/sizeof(AMImportSection) || segment.filesize>segment.vmsize ||
                !am_identity_range(limit,segment.fileoff,segment.filesize) || segment.vmsize>UINT64_MAX-segment.vmaddr) goto done;
            for (unsigned j=0;j<segment.nsects;j++) {
                AMImportSection section;memcpy(&section,p+sizeof segment+j*sizeof section,sizeof section);
                unsigned type=section.flags&255;
                if (!(segment.initprot&4) || type==1 || type==12 || type==18 || !section.size) continue;
                if (section.addr<segment.vmaddr || !am_identity_range(segment.vmsize,section.addr-segment.vmaddr,section.size) ||
                    section.offset<segment.fileoff || !am_identity_range(segment.filesize,section.offset-segment.fileoff,section.size) ||
                    !am_identity_range(limit,section.offset,section.size) || identity.range_count==AM_DETECTION_IDENTITY_MAX_RANGES) goto done;
                identity.ranges[identity.range_count++]=(AMDetectionExecutableRange){section.offset,section.size};
            }
        } else if (cmd==0x1b) {
            if (size!=24 || uuid) goto done;
            memcpy(identity.uuid,p+8,16);uuid=true;
        } else if (cmd==0x1d) {
            uint32_t offset,n;
            if (size!=16 || signature) goto done;
            memcpy(&offset,p+8,4);memcpy(&n,p+12,4);
            if (!am_identity_range(limit,offset,n)) goto done;
            signature_at=offset;signature_size=n;signature=true;
        } else if (cmd==0x21 || cmd==0x2c) {
            uint32_t cryptid;
            if (size<20) goto done;
            memcpy(&cryptid,p+16,4);if (cryptid) goto done;
        }
        at+=size;
    }
    if (at!=header.sizeofcmds || !uuid || !identity.range_count) goto done;
    for (unsigned i=1;i<identity.range_count;i++) {
        AMDetectionExecutableRange value=identity.ranges[i];unsigned j=i;
        while (j && identity.ranges[j-1].offset>value.offset) {identity.ranges[j]=identity.ranges[j-1];j--;}
        identity.ranges[j]=value;
    }
    if (!am_identity_hash_init(&hash)) {am_identity_hash_free(&hash);goto done;}
    hash_ready=true;
    static const unsigned char domain[]="AMExecutableRangesV1";
    unsigned char descriptor[16],chunk[65536];
    am_identity_le(descriptor,identity.range_count,4);
    if (!am_identity_hash_update(&hash,domain,sizeof domain) || !am_identity_hash_update(&hash,descriptor,4)) goto done;
    uint64_t previous=32+header.sizeofcmds;
    for (unsigned i=0;i<identity.range_count;i++) {
        AMDetectionExecutableRange r=identity.ranges[i];
        if (r.offset<previous || (signature && r.offset<signature_at+signature_size && signature_at<r.offset+r.size)) goto done;
        previous=r.offset+r.size;
        am_identity_le(descriptor,r.offset,8);am_identity_le(descriptor+8,r.size,8);
        if (!am_identity_hash_update(&hash,descriptor,16)) goto done;
        for (uint64_t offset=0;offset<r.size;) {
            size_t n=r.size-offset<sizeof chunk ? (size_t)(r.size-offset) : sizeof chunk;
            if (!am_identity_read(io,file,total,slice+r.offset+offset,chunk,n) || !am_identity_hash_update(&hash,chunk,n)) goto done;
            offset+=n;
        }
    }
    if (!am_identity_hash_final(&hash,identity.sha256)) goto done;
    ok=true;
done:
    if (hash_ready) am_identity_hash_free(&hash);
    free(commands);if (io->close(file)) ok=false;
    if (ok) *out=identity;
    errno=saved;return ok;
}
