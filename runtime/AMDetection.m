// Included by SideloadCompat.m only for an explicitly generated profile.
#import "AMDetection.h"
#import <UIKit/UIKit.h>
#include <dlfcn.h>
#include <errno.h>
#include <objc/runtime.h>
#include <os/lock.h>
#include <mach-o/dyld.h>
#include "AMDetectionProfile.gen.h"
#include "AMDetectionPolicy.c"
#include "AMDetectionHooks.c"
#include "AMDetectionRegistry.c"

static AMImportBinding am_detection_bindings[AM_DETECTION_MAX_NATIVE_BINDINGS];
static AMImportBindResult am_detection_results[AM_DETECTION_REGISTRY_ID_LIMIT];
static size_t am_detection_binding_count;
static bool am_detection_started,am_detection_version_ok;
static NSTimeInterval am_detection_started_at;
static dispatch_source_t am_detection_report_timer;
static dispatch_source_t am_detection_phase_timer;
static NSString *am_detection_session,*am_detection_phase_request;
static NSString *const am_detection_phases[]={@"startup",@"login",@"menu",@"fight",@"results"};
static size_t am_detection_image_failures;
static size_t am_detection_image_failure_reasons[AM_IMPORT_FAILURE_COUNT];
static os_unfair_lock am_detection_lock=OS_UNFAIR_LOCK_INIT;
static char am_detection_app_path[4096],am_detection_executable[4096];
static BOOL am_detection_foundation_installed,am_detection_url_installed;
static _Thread_local unsigned am_detection_objc_depth;
static BOOL (*am_detection_file_exists)(id,SEL,NSString *);
static id (*am_detection_attributes)(id,SEL,NSString *,NSError **);
static BOOL (*am_detection_can_open)(id,SEL,NSURL *);
static bool am_detection_entry_installed[AM_DETECTION_REGISTRY_ID_LIMIT];

static bool am_detection_entry_enabled(AMDetectionEntryID id) {
    switch (id) {
        case AM_DETECTION_ENTRY_ACCESS: return AM_DETECTION_API_ACCESS;
        case AM_DETECTION_ENTRY_STAT: return AM_DETECTION_API_STAT;
        case AM_DETECTION_ENTRY_LSTAT: return AM_DETECTION_API_LSTAT;
        case AM_DETECTION_ENTRY_OPEN: return AM_DETECTION_API_OPEN;
        case AM_DETECTION_ENTRY_FOPEN: return AM_DETECTION_API_FOPEN;
        case AM_DETECTION_ENTRY_GETENV: return AM_DETECTION_API_GETENV;
        case AM_DETECTION_ENTRY_FOUNDATION_EXISTS: return AM_DETECTION_API_FOUNDATION_EXISTS;
        case AM_DETECTION_ENTRY_FOUNDATION_ATTRIBUTES: return AM_DETECTION_API_FOUNDATION_ATTRIBUTES;
        case AM_DETECTION_ENTRY_CAN_OPEN_URL: return AM_DETECTION_API_URLS;
        default: return false;
    }
}
static void *am_detection_native_replacement(AMDetectionEntryID id) {
    switch (id) {
        case AM_DETECTION_ENTRY_ACCESS: return (void *)AMDetectionAccess;
        case AM_DETECTION_ENTRY_STAT: return (void *)AMDetectionStat;
        case AM_DETECTION_ENTRY_LSTAT: return (void *)AMDetectionLstat;
        case AM_DETECTION_ENTRY_OPEN: return (void *)AMDetectionOpen;
        case AM_DETECTION_ENTRY_FOPEN: return (void *)AMDetectionFopen;
        case AM_DETECTION_ENTRY_GETENV: return (void *)AMDetectionGetenv;
        default: return NULL;
    }
}
static bool am_detection_module_ready(AMDetectionAPI api) {
    const AMDetectionEntry *entry=AMDetectionEntryByID((AMDetectionEntryID)api);
    return entry && AMDetectionModuleActive(entry->module);
}

static unsigned am_detection_image_index(const char *path) {
    if (!path || !am_detection_app_path[0]) return AM_DETECTION_UNKNOWN_IMAGE;
    for (size_t i=0;i<AM_DETECTION_IMAGES_COUNT;i++) {
        const char *relative=am_detection_images[i];
        if (!strcmp(relative,"@executable")) { if (!strcmp(path,am_detection_executable)) return (unsigned)i; }
        else {
            size_t n=strlen(am_detection_app_path);
            if (!strncmp(path,am_detection_app_path,n) && path[n]=='/' && !strcmp(path+n+1,relative)) return (unsigned)i;
        }
    }
    return AM_DETECTION_UNKNOWN_IMAGE;
}
static bool am_detection_image_selected(const char *path) { return am_detection_image_index(path)!=AM_DETECTION_UNKNOWN_IMAGE; }
static unsigned am_detection_origin(const void *address) {
    Dl_info info;
    return address && dladdr(address,&info) ? am_detection_image_index(info.dli_fname) : AM_DETECTION_UNKNOWN_IMAGE;
}
static const uint8_t *am_detection_uuid(const struct mach_header *header) {
    if (!header || header->magic!=MH_MAGIC_64 || header->ncmds>4096 || header->sizeofcmds>1024*1024) return NULL;
    const uint8_t *at=(const uint8_t *)header+sizeof(struct mach_header_64),*end=at+header->sizeofcmds;
    for (unsigned i=0;i<header->ncmds;i++) {
        if ((size_t)(end-at)<sizeof(struct load_command)) return NULL;
        const struct load_command *cmd=(const void *)at;
        if (cmd->cmdsize<sizeof(*cmd) || cmd->cmdsize>(size_t)(end-at)) return NULL;
        if (cmd->cmd==LC_UUID && cmd->cmdsize==sizeof(struct uuid_command)) return ((const struct uuid_command *)cmd)->uuid;
        at+=cmd->cmdsize;
    }
    return NULL;
}
static uint64_t am_detection_callsite(const void *address) {
    Dl_info info;
    if (!address || !dladdr(address,&info) || am_detection_image_index(info.dli_fname)==AM_DETECTION_UNKNOWN_IMAGE || !info.dli_fbase) return 0;
    const uint8_t *uuid=am_detection_uuid(info.dli_fbase);
    if (!uuid || (uintptr_t)address<(uintptr_t)info.dli_fbase) return 0;
    uint64_t offset=(uintptr_t)address-(uintptr_t)info.dli_fbase,hash=UINT64_C(14695981039346656037);
    for (unsigned i=0;i<16;i++) { hash^=uuid[i]; hash*=UINT64_C(1099511628211); }
    for (unsigned i=0;i<8;i++) { hash^=(offset>>(8*i))&255; hash*=UINT64_C(1099511628211); }
    return hash ? hash : 1;
}
static NSString *am_detection_build_uuid(unsigned image) {
    if (image>=AM_DETECTION_IMAGES_COUNT) return @"unavailable";
    for (uint32_t i=0;i<_dyld_image_count();i++) if (am_detection_image_index(_dyld_get_image_name(i))==image) {
        const uint8_t *uuid=am_detection_uuid(_dyld_get_image_header(i));
        if (uuid) { char text[33]; for (unsigned j=0;j<16;j++) snprintf(text+2*j,3,"%02x",uuid[j]); return @(text); }
    }
    return @"unavailable";
}
static bool am_detection_caller_selected(const void *address) {
    return am_detection_origin(address)!=AM_DETECTION_UNKNOWN_IMAGE;
}
static BOOL am_detection_exists(id self,SEL cmd,NSString *path) {
    if (am_detection_objc_depth) return am_detection_file_exists(self,cmd,path);
    am_detection_objc_depth++;
    bool selected=am_detection_caller_selected(__builtin_return_address(0));
    AMDetectionRuleRef rule=selected ? AMDetectionPathRule(&am_detection_profile,path.fileSystemRepresentation) : (AMDetectionRuleRef){0};
    bool match=rule.kind!=AM_DETECTION_RULE_NONE;
    bool block=match && AMDetectionModuleActive(AM_DETECTION_MODULE_FOUNDATION);
    BOOL value=block ? NO : am_detection_file_exists(self,cmd,path);
    if (selected) AMDetectionRecordEntryOutcome(AM_DETECTION_ENTRY_FOUNDATION_EXISTS,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_NEGATIVE);
    am_detection_objc_depth--; return value;
}
static id am_detection_attrs(id self,SEL cmd,NSString *path,NSError **error) {
    if (am_detection_objc_depth) return am_detection_attributes(self,cmd,path,error);
    am_detection_objc_depth++;
    bool selected=am_detection_caller_selected(__builtin_return_address(0));
    AMDetectionRuleRef rule=selected ? AMDetectionPathRule(&am_detection_profile,path.fileSystemRepresentation) : (AMDetectionRuleRef){0};
    bool match=rule.kind!=AM_DETECTION_RULE_NONE;
    bool block=match && AMDetectionModuleActive(AM_DETECTION_MODULE_FOUNDATION);
    id value=nil;
    if (block) {
        if (error) *error=[NSError errorWithDomain:NSCocoaErrorDomain code:NSFileReadNoSuchFileError userInfo:nil];
    } else value=am_detection_attributes(self,cmd,path,error);
    if (selected) AMDetectionRecordEntryOutcome(AM_DETECTION_ENTRY_FOUNDATION_ATTRIBUTES,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_NEGATIVE);
    am_detection_objc_depth--; return value;
}
static BOOL am_detection_url(id self,SEL cmd,NSURL *url) {
    if (am_detection_objc_depth) return am_detection_can_open(self,cmd,url);
    am_detection_objc_depth++;
    bool selected=am_detection_caller_selected(__builtin_return_address(0));
    AMDetectionRuleRef rule=selected ? AMDetectionSchemeRule(&am_detection_profile,url.scheme.UTF8String) : (AMDetectionRuleRef){0};
    bool match=rule.kind!=AM_DETECTION_RULE_NONE;
    bool block=match && AMDetectionModuleActive(AM_DETECTION_MODULE_URLS);
    BOOL value=block ? NO : am_detection_can_open(self,cmd,url);
    if (selected) AMDetectionRecordEntryOutcome(AM_DETECTION_ENTRY_CAN_OPEN_URL,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_NEGATIVE);
    am_detection_objc_depth--; return value;
}
static BOOL am_detection_replace(Class cls,SEL sel,IMP replacement,IMP *original) {
    Method m=class_getInstanceMethod(cls,sel);
    if (!m) return NO;
    IMP current=method_getImplementation(m);
    Dl_info info;
    // Do not replace a method already owned by another injected library.
    if (!dladdr((const void *)current,&info) || !info.dli_fname || strncmp(info.dli_fname,"/System/Library/",16)) return NO;
    *original=current;
    if (class_addMethod(cls,sel,replacement,method_getTypeEncoding(m))) return YES;
    IMP prior=method_setImplementation(m,replacement);
    if (prior==current) return YES;
    method_setImplementation(m,prior); return NO;
}
static bool am_detection_objc_target(AMDetectionEntryID id,Class *cls,SEL *sel,IMP *replacement) {
    switch (id) {
        case AM_DETECTION_ENTRY_FOUNDATION_EXISTS:
            *cls=NSFileManager.class; *sel=@selector(fileExistsAtPath:); *replacement=(IMP)am_detection_exists; return true;
        case AM_DETECTION_ENTRY_FOUNDATION_ATTRIBUTES:
            *cls=NSFileManager.class; *sel=@selector(attributesOfItemAtPath:error:); *replacement=(IMP)am_detection_attrs; return true;
        case AM_DETECTION_ENTRY_CAN_OPEN_URL:
            *cls=UIApplication.class; *sel=@selector(canOpenURL:); *replacement=(IMP)am_detection_url; return true;
        default: return false;
    }
}
static bool am_detection_prepare_entry(const AMDetectionEntry *entry,void *context) {
    (void)context;
    if (entry->abi==AM_DETECTION_ABI_NATIVE) {
        if (!*entry->original) *entry->original=dlsym(RTLD_DEFAULT,entry->name);
        return *entry->original && am_detection_native_replacement(entry->id);
    }
    Class cls; SEL sel; IMP replacement;
    if (!am_detection_objc_target(entry->id,&cls,&sel,&replacement)) return false;
    Method method=class_getInstanceMethod(cls,sel);
    if (!method) return false;
    IMP current=method_getImplementation(method); Dl_info owner;
    if (am_detection_entry_installed[entry->id]) return current==replacement;
    if (!dladdr((const void *)current,&owner) || !owner.dli_fname || strncmp(owner.dli_fname,"/System/Library/",16)) return false;
    *entry->original=(void *)current;
    switch (entry->id) {
        case AM_DETECTION_ENTRY_FOUNDATION_EXISTS: am_detection_file_exists=(void *)current; break;
        case AM_DETECTION_ENTRY_FOUNDATION_ATTRIBUTES: am_detection_attributes=(void *)current; break;
        case AM_DETECTION_ENTRY_CAN_OPEN_URL: am_detection_can_open=(void *)current; break;
        default: return false;
    }
    return true;
}
static bool am_detection_publish_entry(const AMDetectionEntry *entry,void *context) {
    (void)context;
    if (entry->abi==AM_DETECTION_ABI_NATIVE) {
        AMImportBindResult result=am_detection_results[entry->id];
        return !am_detection_image_failures && !result.conflicts && !result.failed &&
            !result.unsupported && (result.applied || result.already_owned);
    }
    Class cls; SEL sel; IMP replacement;
    if (!am_detection_objc_target(entry->id,&cls,&sel,&replacement)) return false;
    Method method=class_getInstanceMethod(cls,sel);
    if (!method) return false;
    if (am_detection_entry_installed[entry->id]) return method_getImplementation(method)==replacement;
    if ((void *)method_getImplementation(method)!=*entry->original) return false;
    IMP original=NULL;
    bool installed=am_detection_replace(cls,sel,replacement,&original);
    am_detection_entry_installed[entry->id]=installed;
    return installed && (void *)original==*entry->original;
}
static void am_detection_read_phase(void) {
    NSString *path=[NSHomeDirectory() stringByAppendingPathComponent:@"Documents/am_detection_phase.json"];
    FILE *file=fopen(path.fileSystemRepresentation,"rb");
    if (!file) return;
    unsigned char bytes[1025]; size_t count=fread(bytes,1,sizeof bytes,file); bool failed=ferror(file); fclose(file);
    if (failed || !count || count>1024) return;
    id value=[NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:bytes length:count] options:0 error:nil];
    if (![value isKindOfClass:NSDictionary.class] || [value count]!=3) return;
    id session=value[@"session_id"],request=value[@"request_id"],phase=value[@"phase"];
    if (![session isKindOfClass:NSString.class] || ![session isEqualToString:am_detection_session] ||
        ![request isKindOfClass:NSString.class] || [request length]!=32 ||
        [request rangeOfCharacterFromSet:[[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet]].location!=NSNotFound ||
        ![phase isKindOfClass:NSString.class] || [request isEqualToString:am_detection_phase_request]) return;
    for (unsigned i=0;i<AM_DETECTION_PHASE_COUNT;i++) if ([phase isEqualToString:am_detection_phases[i]]) {
        AMDetectionSetPhase((AMDetectionPhase)i); am_detection_phase_request=[request copy]; AMDetectionWriteReport(); return;
    }
}
void AMDetectionStart(size_t compatibility_bindings) {
    if (am_detection_started) return;
    am_detection_started=true;
    am_detection_session=[[NSUUID.UUID.UUIDString stringByReplacingOccurrencesOfString:@"-" withString:@""] lowercaseString];
    am_detection_phase_request=@"";
    am_detection_started_at=NSProcessInfo.processInfo.systemUptime;
    NSString *base=[NSBundle.mainBundle.infoDictionary[@"CFBundleShortVersionString"] componentsSeparatedByString:@"-mod"].firstObject;
    am_detection_version_ok=[base isEqualToString:@AM_DETECTION_GAME_VERSION];
    if (!am_detection_version_ok || am_detection_profile.mode==AM_DETECTION_OFF) return;
    const char *bundle=NSBundle.mainBundle.bundlePath.fileSystemRepresentation;
    const char *exe=NSBundle.mainBundle.executablePath.fileSystemRepresentation;
    if (!bundle || !exe || strlen(bundle)>=sizeof am_detection_app_path || strlen(exe)>=sizeof am_detection_executable) {
        am_detection_version_ok=false; return;
    }
    memcpy(am_detection_app_path,bundle,strlen(bundle)+1); memcpy(am_detection_executable,exe,strlen(exe)+1);
    AMDetectionEntryID selected[AM_DETECTION_MAX_ENTRYPOINTS]; size_t selected_count=0;
    for (size_t i=0;i<AMDetectionEntryCount();i++) {
        const AMDetectionEntry *entry=AMDetectionEntryAt(i);
        if (!am_detection_entry_enabled(entry->id)) continue;
        if (selected_count==AM_DETECTION_MAX_ENTRYPOINTS ||
            !AMDetectionSetInstaller(entry->id,am_detection_prepare_entry,am_detection_publish_entry,NULL)) {
            am_detection_version_ok=false; return;
        }
        selected[selected_count++]=entry->id;
    }
    if (AM_DETECTION_IMAGES_COUNT>AM_DETECTION_MAX_CALLER_IMAGES ||
        !AMDetectionConfigureRegistry(selected,selected_count,compatibility_bindings,am_detection_profile.mode)) {
        am_detection_version_ok=false; return;
    }
    for (unsigned m=0;m<AM_DETECTION_MODULE_COUNT;m++) AMDetectionPrepareModule((AMDetectionModuleID)m);
    AMDetectionOriginals o={
        .access_fn=*AMDetectionEntryByID(AM_DETECTION_ENTRY_ACCESS)->original,
        .stat_fn=*AMDetectionEntryByID(AM_DETECTION_ENTRY_STAT)->original,
        .lstat_fn=*AMDetectionEntryByID(AM_DETECTION_ENTRY_LSTAT)->original,
        .open_fn=*AMDetectionEntryByID(AM_DETECTION_ENTRY_OPEN)->original,
        .fopen_fn=*AMDetectionEntryByID(AM_DETECTION_ENTRY_FOPEN)->original,
        .getenv_fn=*AMDetectionEntryByID(AM_DETECTION_ENTRY_GETENV)->original};
    if (!AMDetectionConfigure(&am_detection_profile,&o)) { am_detection_version_ok=false; return; }
    AMDetectionSetOriginResolver(am_detection_origin);
    AMDetectionSetCallsiteResolver(am_detection_callsite);
    AMDetectionSetReadinessResolver(am_detection_module_ready);
    for (size_t i=0;i<AMDetectionEntryCount();i++) {
        const AMDetectionEntry *entry=AMDetectionEntryAt(i);
        if (entry->abi!=AM_DETECTION_ABI_NATIVE || !AMDetectionEntrySelected(entry->id) || !*entry->original) continue;
        am_detection_bindings[am_detection_binding_count++]=(AMImportBinding){entry->name,*entry->original,
            am_detection_native_replacement(entry->id),&am_detection_results[entry->id]};
    }
    am_detection_foundation_installed=AMDetectionPublishModule(AM_DETECTION_MODULE_FOUNDATION);
    am_detection_url_installed=AMDetectionPublishModule(AM_DETECTION_MODULE_URLS);
    // One bounded file, refreshed while this opted-in diagnostic session runs.
    if (!AM_DETECTION_DIAGNOSTICS_ENABLED) return;
    am_detection_report_timer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,dispatch_get_main_queue());
    dispatch_source_set_timer(am_detection_report_timer,dispatch_time(DISPATCH_TIME_NOW,30*NSEC_PER_SEC),15*NSEC_PER_SEC,NSEC_PER_SEC);
    dispatch_source_set_event_handler(am_detection_report_timer,^{ AMDetectionWriteReport(); });
    dispatch_resume(am_detection_report_timer);
    am_detection_phase_timer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,dispatch_get_main_queue());
    dispatch_source_set_timer(am_detection_phase_timer,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC),NSEC_PER_SEC,NSEC_PER_SEC/10);
    dispatch_source_set_event_handler(am_detection_phase_timer,^{ am_detection_read_phase(); });
    dispatch_resume(am_detection_phase_timer);
}
AMImportBindResult AMDetectionBindImage(const void *header,size_t bytes,intptr_t slide,
    const AMImportBinding *compat,size_t count,AMImportWriter writer,void *context) {
    AMImportBinding combined[AM_DETECTION_MAX_NATIVE_BINDINGS];
    if (count>AM_DETECTION_MAX_NATIVE_BINDINGS || (!compat && count)) return (AMImportBindResult){.malformed=true};
    Dl_info image;
    bool selected=am_detection_version_ok && am_detection_profile.mode!=AM_DETECTION_OFF &&
        dladdr(header,&image) && am_detection_image_selected(image.dli_fname);
    if (selected && am_detection_binding_count>AM_DETECTION_MAX_NATIVE_BINDINGS-count) return (AMImportBindResult){.malformed=true};
    if (count) memcpy(combined,compat,count*sizeof *combined);
    if (selected && am_detection_binding_count) memcpy(combined+count,am_detection_bindings,am_detection_binding_count*sizeof *combined);
    size_t total=count+(selected ? am_detection_binding_count : 0);
    if (!total) return (AMImportBindResult){0};
    os_unfair_lock_lock(&am_detection_lock);
    bool native_modules[AM_DETECTION_MODULE_COUNT]={0};
    if (selected) for (size_t i=0;i<AMDetectionEntryCount();i++) {
        const AMDetectionEntry *entry=AMDetectionEntryAt(i);
        if (entry->abi==AM_DETECTION_ABI_NATIVE && AMDetectionEntrySelected(entry->id)) native_modules[entry->module]=true;
    }
    for (unsigned m=0;m<AM_DETECTION_MODULE_COUNT;m++) if (native_modules[m]) AMDetectionInvalidateModule((AMDetectionModuleID)m);
    AMImportBindResult result=AMImportBindImage(header,bytes,slide,combined,total,writer,context);
    if (selected && (result.malformed || result.unsupported)) {
        if (am_detection_image_failures<SIZE_MAX) am_detection_image_failures++;
        if (result.reason>AM_IMPORT_FAILURE_NONE && result.reason<AM_IMPORT_FAILURE_COUNT &&
            am_detection_image_failure_reasons[result.reason]<SIZE_MAX) am_detection_image_failure_reasons[result.reason]++;
    }
    for (unsigned m=0;m<AM_DETECTION_MODULE_COUNT;m++) if (native_modules[m] && AMDetectionPrepareModule((AMDetectionModuleID)m))
        AMDetectionPublishModule((AMDetectionModuleID)m);
    os_unfair_lock_unlock(&am_detection_lock);
    return result;
}
NSDictionary *AMDetectionReport(void) {
    if (!AM_DETECTION_DIAGNOSTICS_ENABLED) return nil;
#if defined(__aarch64__)
    bool arm64=true;
#else
    bool arm64=false;
#endif
    static NSString *const states[]={@"disabled",@"observing",@"filtering",@"unsupported",@"failed"};
    size_t failures,reasons[AM_IMPORT_FAILURE_COUNT];
    os_unfair_lock_lock(&am_detection_lock);
    failures=am_detection_image_failures;memcpy(reasons,am_detection_image_failure_reasons,sizeof reasons);
    os_unfair_lock_unlock(&am_detection_lock);
    NSMutableDictionary *adapters=[NSMutableDictionary dictionary];
    NSMutableArray *entries=[NSMutableArray array];
    for (size_t i=0;i<AMDetectionEntryCount();i++) {
        const AMDetectionEntry *entry=AMDetectionEntryAt(i);
        AMDetectionModuleResult status=AMDetectionModuleStatus(entry->module);
        bool selected=am_detection_entry_enabled(entry->id);
        if (entry->abi==AM_DETECTION_ABI_AGGREGATE) {
            for (size_t j=0;j<AMDetectionEntryCount();j++) {
                const AMDetectionEntry *child=AMDetectionEntryAt(j);
                if (child->module==entry->module && am_detection_entry_enabled(child->id)) selected=true;
            }
        }
        if (!selected || am_detection_profile.mode==AM_DETECTION_OFF) status=(AMDetectionModuleResult){AM_DETECTION_DISABLED,AM_DETECTION_REASON_NOT_SELECTED};
        else if (!am_detection_version_ok) status=(AMDetectionModuleResult){AM_DETECTION_UNSUPPORTED,AM_DETECTION_REASON_PREPARE_FAILED};
        AMDetectionCounters counters=AMDetectionReadEntryCounters(entry->id);
        NSDictionary *value=@{@"entry_id":@(entry->id),@"name":@(entry->name),@"module":@(AMDetectionModuleName(entry->module)),
            @"state":states[status.state],@"reason":@(AMDetectionSupportReasonName(status.reason)),
            @"calls":@(counters.calls),@"matched":@(counters.matched),@"filtered":@(counters.filtered),
            @"aggregate":@(entry->abi==AM_DETECTION_ABI_AGGREGATE),
            @"evidence":@{@"implemented":@YES,@"statically_supported":@"not_recorded",
                @"host_contract_tested":@"not_recorded",@"arm64_built":@(arm64),
                @"device_compatible":@"not_verified",@"positive_artifact_filtering":@"not_verified"}};
        adapters[@(entry->name)]=value;
        if (entry->abi!=AM_DETECTION_ABI_AGGREGATE) [entries addObject:value];
    }
    adapters[@"image_concealment"]=@{@"state":@"unsupported"};
    adapters[@"class_concealment"]=@{@"state":@"unsupported"};
    NSMutableDictionary *failureReasons=[NSMutableDictionary dictionary];
    for (unsigned i=1;i<AM_IMPORT_FAILURE_COUNT;i++) failureReasons[@(AMImportFailureName((AMImportFailure)i))]=@(reasons[i]);
    NSMutableArray *attributed=[NSMutableArray array],*siteRows=[NSMutableArray array];
    AMDetectionEventRow *rows=calloc(AM_DETECTION_ATTRIBUTION_CAPACITY,sizeof *rows);
    if (!rows) AMDetectionRecordAllocationFailure();
    size_t rowCount=AMDetectionReadEvents(rows,AM_DETECTION_ATTRIBUTION_CAPACITY);
    for (size_t i=0;i<rowCount;i++) {
        AMDetectionEventRow row=rows[i];const AMDetectionEntry *entry=AMDetectionEntryByID(row.key.entry);
        if (!entry) continue;
        const char *rule=row.key.rule.kind<=AM_DETECTION_RULE_ENVIRONMENT && row.key.rule.index<64 ? am_detection_rule_ids[row.key.rule.kind][row.key.rule.index] : NULL;
        NSString *image=row.key.image<AM_DETECTION_IMAGES_COUNT ? @(am_detection_image_ids[row.key.image]) : @"unattributed";
        [attributed addObject:@{@"entry_id":@(entry->id),@"module":@(AMDetectionModuleName(entry->module)),
            @"image_id":image,@"phase":am_detection_phases[row.key.phase],@"api":@(entry->name),
            @"rule_id":rule ? @(rule) : (id)NSNull.null,@"calls":@(row.counts.calls),
            @"matched":@(row.counts.matched),@"filtered":@(row.counts.filtered)}];
    }
    free(rows);
    AMDetectionEventCallsite *sites=calloc(AM_DETECTION_CALLSITE_CAPACITY,sizeof *sites);
    if (!sites) AMDetectionRecordAllocationFailure();
    size_t siteCount=AMDetectionReadEventCallsites(sites,AM_DETECTION_CALLSITE_CAPACITY);
    for (size_t i=0;i<siteCount;i++) {
        AMDetectionEventCallsite site=sites[i];AMDetectionEventKey key=site.key;
        const AMDetectionEntry *entry=AMDetectionEntryByID(key.entry);if (!entry) continue;
        const char *rule=key.rule.kind<=AM_DETECTION_RULE_ENVIRONMENT && key.rule.index<64 ? am_detection_rule_ids[key.rule.kind][key.rule.index] : NULL;
        NSString *image=key.image<AM_DETECTION_IMAGES_COUNT ? @(am_detection_image_ids[key.image]) : @"unattributed";
        NSString *token=site.token ? [NSString stringWithFormat:@"%016llx",(unsigned long long)site.token] : @"unavailable";
        [siteRows addObject:@{@"entry_id":@(entry->id),@"module":@(AMDetectionModuleName(entry->module)),
            @"image_id":image,@"build_uuid":am_detection_build_uuid(key.image),@"caller_id":token,
            @"api":@(entry->name),@"phase":am_detection_phases[key.phase],@"rule_id":rule ? @(rule) : (id)NSNull.null,
            @"results":@{@"unknown":@(site.results[AM_DETECTION_RESULT_UNKNOWN]),@"success":@(site.results[AM_DETECTION_RESULT_SUCCESS]),
                @"negative":@(site.results[AM_DETECTION_RESULT_NEGATIVE]),@"error":@(site.results[AM_DETECTION_RESULT_ERROR]),
                @"original_not_called":@(site.results[AM_DETECTION_RESULT_NOT_CALLED])}}];
    }
    free(sites);
    AMDetectionCoverage coverage=AMDetectionReadCoverage();
    return @{@"schema_version":@4,@"profile_mode":@(am_detection_profile.mode),@"profile_id":@AM_DETECTION_PROFILE_ID,
        @"callsite_id_algorithm":@"fnv1a64-uuid-return-offset-le-v1",@"callsites":siteRows,@"entries":entries,
        @"coverage":@{@"incomplete":@(coverage.incomplete),@"saturated":@(coverage.saturated),
            @"allocation_failures":@(coverage.allocation_failures)},
        @"callsite_capacity":@AM_DETECTION_CALLSITE_CAPACITY,@"callsites_dropped":@(coverage.callsite_dropped),
        @"session_id":am_detection_session ?: @"",@"phase":am_detection_phases[AMDetectionCurrentPhase()],
        @"phase_request_id":am_detection_phase_request ?: @"",@"attribution":attributed,
        @"attribution_capacity":@AM_DETECTION_ATTRIBUTION_CAPACITY,@"attribution_dropped":@(coverage.attribution_dropped),
        @"elapsed_ms":@((NSProcessInfo.processInfo.systemUptime-am_detection_started_at)*1000.0),@"version_match":@(am_detection_version_ok),
        @"image_layout_failures":@(failures),@"image_layout_failure_reasons":failureReasons,
        @"loaded_image_count":@(_dyld_image_count()),@"adapters":adapters};
}
BOOL AMDetectionWriteReport(void) {
    if (!AM_DETECTION_DIAGNOSTICS_ENABLED) return NO;
    NSData *data=[NSJSONSerialization dataWithJSONObject:AMDetectionReport() options:0 error:nil];
    NSString *path=[NSHomeDirectory() stringByAppendingPathComponent:@"Documents/am_detection.json"];
    return data && [data writeToFile:path options:NSDataWritingAtomic error:nil];
}
