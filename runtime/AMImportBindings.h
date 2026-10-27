#ifndef AM_IMPORT_BINDINGS_H
#define AM_IMPORT_BINDINGS_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Fixed-width Mach-O wire layouts, shared by the parser and host fixtures. */
typedef struct { uint32_t magic,cputype,cpusubtype,filetype,ncmds,sizeofcmds,flags,reserved; } AMImportHeader;
typedef struct {
    uint32_t cmd,cmdsize; char segname[16]; uint64_t vmaddr,vmsize,fileoff,filesize;
    uint32_t maxprot,initprot,nsects,flags;
} AMImportSegment;
typedef struct {
    char sectname[16],segname[16]; uint64_t addr,size;
    uint32_t offset,align,reloff,nreloc,flags,reserved1,reserved2,reserved3;
} AMImportSection;
typedef struct { uint32_t cmd,cmdsize,symoff,nsyms,stroff,strsize; } AMImportSymtab;
typedef struct { uint32_t cmd,cmdsize,before[12],indirectsymoff,nindirectsyms,after[4]; } AMImportDysymtab;
typedef struct { uint32_t strx; uint8_t type,sect; uint16_t desc; uint64_t value; } AMImportSymbol;
typedef enum {
    AM_IMPORT_FAILURE_NONE, AM_IMPORT_FAILURE_ARCHITECTURE,
    AM_IMPORT_FAILURE_CHAINED_FIXUPS, AM_IMPORT_FAILURE_AUTHENTICATED,
    AM_IMPORT_FAILURE_CLASSIC_TABLES, AM_IMPORT_FAILURE_MALFORMED,
    AM_IMPORT_FAILURE_COUNT
} AMImportFailure;
typedef struct { size_t matched,applied,already_owned,conflicts,failed,unsupported; bool malformed; AMImportFailure reason; } AMImportBindResult;
const char *AMImportFailureName(AMImportFailure reason);
typedef struct { const char *symbol; void *expected; void *replacement; AMImportBindResult *result; } AMImportBinding;
typedef bool (*AMImportWriter)(void **slot,void *expected,void *replacement,void *context);
/* header_bytes bounds load commands. Segment mappings must belong to a dyld-validated,
   resolved loaded image. Chained formats 2/6 require retained classic indirect metadata;
   encoded chains are never traversed. Authenticated layouts remain unsupported. */
AMImportBindResult AMImportBindImage(const void *header,size_t header_bytes,intptr_t slide,
    const AMImportBinding *bindings,size_t count,AMImportWriter writer,void *context);
#endif
