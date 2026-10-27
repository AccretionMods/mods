#include "AMImportBindings.h"
#include <string.h>

const char *AMImportFailureName(AMImportFailure reason) {
    static const char *const names[]={"none","architecture","chained_fixups","authenticated_segments","classic_tables_missing","malformed_layout"};
    return (unsigned)reason<AM_IMPORT_FAILURE_COUNT ? names[reason] : "unknown";
}

static bool amib_address(uint64_t base,intptr_t slide,uintptr_t *out) {
    if (base>UINTPTR_MAX) return false;
    if (slide>=0) { if (base>UINTPTR_MAX-(uintptr_t)slide) return false; *out=(uintptr_t)base+(uintptr_t)slide; }
    else { uintptr_t sub=(uintptr_t)(-(slide+1))+1; if (base<sub) return false; *out=(uintptr_t)base-sub; }
    return *out!=0;
}
static bool amib_range(uint64_t start,uint64_t size,uint64_t at,uint64_t length) {
    return at>=start && at-start<=size && length<=size-(at-start);
}
static const void *amib_file(const AMImportSegment *link,uint64_t offset,uint64_t length,intptr_t slide) {
    uintptr_t base;
    if (!amib_range(link->fileoff,link->filesize,offset,length) ||
        offset-link->fileoff>link->vmsize || length>link->vmsize-(offset-link->fileoff) ||
        !amib_address(link->vmaddr,slide,&base) || offset-link->fileoff>UINTPTR_MAX-base ||
        length>UINTPTR_MAX-(base+(uintptr_t)(offset-link->fileoff))) return NULL;
    return (const void *)(base+(uintptr_t)(offset-link->fileoff));
}
/* Only use retained classic import slots in dyld-resolved, non-authenticated images.
   This checks chain encoding; it never walks or rewrites encoded chains. */
static AMImportFailure amib_chain_encoding(const unsigned char *payload,size_t size) {
    uint32_t version,starts,count;
    if (!payload || size<28) return AM_IMPORT_FAILURE_MALFORMED;
    memcpy(&version,payload,4); memcpy(&starts,payload+4,4);
    if (version) return AM_IMPORT_FAILURE_CHAINED_FIXUPS;
    if (starts<28 || starts>size || size-starts<4) return AM_IMPORT_FAILURE_MALFORMED;
    memcpy(&count,payload+starts,4);
    if (count>128 || count>(size-starts-4)/4) return AM_IMPORT_FAILURE_MALFORMED;
    bool found=false;
    for (uint32_t i=0;i<count;i++) {
        uint32_t offset,length; uint16_t page_size,format,pages;
        memcpy(&offset,payload+starts+4+i*4,4);
        if (!offset) continue;
        if (offset<4+count*4 || offset>size-starts || size-starts-offset<22) return AM_IMPORT_FAILURE_MALFORMED;
        const unsigned char *segment=payload+starts+offset;
        memcpy(&length,segment,4); memcpy(&page_size,segment+4,2);
        memcpy(&format,segment+6,2); memcpy(&pages,segment+20,2);
        if (length<22 || length>size-starts-offset || pages>(length-22)/2 ||
            (page_size!=4096 && page_size!=16384)) return AM_IMPORT_FAILURE_MALFORMED;
        if (format==1 || format==7 || format==9 || format==10 || format==12 || format==13 || format==14)
            return AM_IMPORT_FAILURE_AUTHENTICATED;
        if (format!=2 && format!=6) return AM_IMPORT_FAILURE_CHAINED_FIXUPS;
        found=true;
    }
    return found ? AM_IMPORT_FAILURE_NONE : AM_IMPORT_FAILURE_CHAINED_FIXUPS;
}
AMImportBindResult AMImportBindImage(const void *raw,size_t header_bytes,intptr_t slide,
    const AMImportBinding *bindings,size_t count,AMImportWriter writer,void *context) {
    AMImportBindResult r={0};
    if (!raw || header_bytes<sizeof(AMImportHeader) || !writer || !bindings || !count || count>64) goto malformed;
    AMImportHeader h; memcpy(&h,raw,sizeof h);
    if (h.magic!=0xfeedfacf || h.cputype!=0x100000c || (h.cpusubtype&0xffffff)==2) { r.unsupported=1; r.reason=AM_IMPORT_FAILURE_ARCHITECTURE; return r; }
    if (h.ncmds>4096 || h.sizeofcmds>16*1024*1024 || h.sizeofcmds>header_bytes-sizeof h || h.ncmds>h.sizeofcmds/8) goto malformed;
    const unsigned char *bytes=raw;
    const AMImportSegment *segments[128],*link=NULL; size_t nsegments=0;
    const AMImportSymtab *sym=NULL; const AMImportDysymtab *dysym=NULL;
    const uint32_t *chained=NULL;
    size_t at=sizeof h,end=at+h.sizeofcmds;
    for (uint32_t i=0;i<h.ncmds;i++) {
        uint32_t cmd,size;
        if (at>end || end-at<8) goto malformed;
        memcpy(&cmd,bytes+at,4); memcpy(&size,bytes+at+4,4);
        if (size<8 || size%8 || size>end-at) goto malformed;
        if (cmd==0x80000034u) {
            if (size!=16 || chained) goto malformed;
            chained=(const void *)(bytes+at);
        }
        if (cmd==0x19) {
            if (size<sizeof(AMImportSegment) || nsegments==128) goto malformed;
            const AMImportSegment *s=(const void *)(bytes+at); uintptr_t address;
            if (!s->initprot && !s->filesize && !s->nsects) { at+=size; continue; } /* __PAGEZERO */
            if (s->nsects>(size-sizeof *s)/sizeof(AMImportSection) || s->filesize>s->vmsize ||
                !amib_address(s->vmaddr,slide,&address) || s->vmsize>UINTPTR_MAX-address) goto malformed;
            segments[nsegments++]=s;
            if (!memcmp(s->segname,"__LINKEDIT\0",11)) { if (link) goto malformed; link=s; }
            if (!memcmp(s->segname,"__AUTH",6)) { r.unsupported=1; r.reason=AM_IMPORT_FAILURE_AUTHENTICATED; return r; }
        } else if (cmd==2) {
            if (size<sizeof(AMImportSymtab) || sym) goto malformed;
            sym=(const void *)(bytes+at);
        } else if (cmd==0xb) {
            if (size<sizeof(AMImportDysymtab) || dysym) goto malformed;
            dysym=(const void *)(bytes+at);
        }
        at+=size;
    }
    if (at!=end) goto malformed;
    if (!link || !sym || !dysym || !(link->initprot&1) || (chained && !dysym->nindirectsyms)) {
        r.unsupported=1; r.reason=AM_IMPORT_FAILURE_CLASSIC_TABLES; return r;
    }
    if (chained) {
        const unsigned char *payload=amib_file(link,chained[2],chained[3],slide);
        r.reason=amib_chain_encoding(payload,chained[3]);
        if (r.reason==AM_IMPORT_FAILURE_MALFORMED) goto malformed;
        if (r.reason!=AM_IMPORT_FAILURE_NONE) { r.unsupported=1; return r; }
    }
    const unsigned char *symbols=amib_file(link,sym->symoff,(uint64_t)sym->nsyms*sizeof(AMImportSymbol),slide);
    const char *strings=amib_file(link,sym->stroff,sym->strsize,slide);
    const unsigned char *indirect=amib_file(link,dysym->indirectsymoff,(uint64_t)dysym->nindirectsyms*4,slide);
    if (!symbols || !strings || !indirect) goto malformed;
    /* Validate the complete relevant layout before the first possible write. */
    for (unsigned pass=0;pass<2;pass++) for (size_t sidx=0;sidx<nsegments;sidx++) {
        const AMImportSegment *s=segments[sidx];
        const AMImportSection *sections=(const void *)(s+1);
        for (uint32_t j=0;j<s->nsects;j++) {
            const AMImportSection *sec=&sections[j]; uint32_t kind=sec->flags&0xff;
            if (kind!=6 && kind!=7) continue;
            if (!(s->initprot&1) || !amib_range(s->vmaddr,s->vmsize,sec->addr,sec->size) || sec->size%sizeof(void *) || sec->addr%sizeof(void *)) goto malformed;
            size_t slots=(size_t)(sec->size/sizeof(void *)); uintptr_t address;
            if (!amib_address(sec->addr,slide,&address) || sec->reserved1>dysym->nindirectsyms || slots>dysym->nindirectsyms-sec->reserved1) goto malformed;
            for (size_t k=0;k<slots;k++) {
                uint32_t index; memcpy(&index,indirect+((size_t)sec->reserved1+k)*4,4);
                if (index&0xc0000000u) continue;
                if (index>=sym->nsyms) goto malformed;
                AMImportSymbol symbol; memcpy(&symbol,symbols+(size_t)index*sizeof symbol,sizeof symbol);
                if (symbol.strx>=sym->strsize) goto malformed;
                const char *name=strings+symbol.strx;
                if (!memchr(name,0,sym->strsize-symbol.strx)) goto malformed;
                if (!pass || *name!='_') continue;
                for (size_t b=0;b<count;b++) {
                    if (!bindings[b].symbol || !bindings[b].replacement || strcmp(name+1,bindings[b].symbol)) continue;
                    r.matched++;
                    AMImportBindResult *detail=bindings[b].result;
                    if (detail) detail->matched++;
                    void **slot=(void **)(address+k*sizeof(void *)); void *current;
                    memcpy(&current,slot,sizeof current);
                    if (current==bindings[b].replacement) { r.already_owned++; if (detail) detail->already_owned++; }
                    else if (bindings[b].expected && current!=bindings[b].expected) { r.conflicts++; if (detail) detail->conflicts++; }
                    else if (writer(slot,current,bindings[b].replacement,context)) { r.applied++; if (detail) detail->applied++; }
                    else { r.failed++; if (detail) detail->failed++; }
                    break;
                }
            }
        }
    }
    return r;
malformed:
    r.malformed=true; r.reason=AM_IMPORT_FAILURE_MALFORMED; return r;
}
