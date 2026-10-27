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

static AMImportBinding am_detection_bindings[6];
static const bool am_detection_native_selected[6]={AM_DETECTION_API_ACCESS,AM_DETECTION_API_STAT,
    AM_DETECTION_API_LSTAT,AM_DETECTION_API_OPEN,AM_DETECTION_API_FOPEN,AM_DETECTION_API_GETENV};
static AMImportBindResult am_detection_results[6];
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
    bool block=match && am_detection_profile.mode==AM_DETECTION_FILTER && am_detection_foundation_installed;
    BOOL value=block ? NO : am_detection_file_exists(self,cmd,path);
    if (selected) AMDetectionRecordOutcome(AM_DETECTION_FOUNDATION,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_NEGATIVE);
    am_detection_objc_depth--; return value;
}
static id am_detection_attrs(id self,SEL cmd,NSString *path,NSError **error) {
    if (am_detection_objc_depth) return am_detection_attributes(self,cmd,path,error);
    am_detection_objc_depth++;
    bool selected=am_detection_caller_selected(__builtin_return_address(0));
    AMDetectionRuleRef rule=selected ? AMDetectionPathRule(&am_detection_profile,path.fileSystemRepresentation) : (AMDetectionRuleRef){0};
    bool match=rule.kind!=AM_DETECTION_RULE_NONE;
    bool block=match && am_detection_profile.mode==AM_DETECTION_FILTER && am_detection_foundation_installed;
    id value=nil;
    if (block) {
        if (error) *error=[NSError errorWithDomain:NSCocoaErrorDomain code:NSFileReadNoSuchFileError userInfo:nil];
    } else value=am_detection_attributes(self,cmd,path,error);
    if (selected) AMDetectionRecordOutcome(AM_DETECTION_FOUNDATION,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_NEGATIVE);
    am_detection_objc_depth--; return value;
}
static BOOL am_detection_url(id self,SEL cmd,NSURL *url) {
    if (am_detection_objc_depth) return am_detection_can_open(self,cmd,url);
    am_detection_objc_depth++;
    bool selected=am_detection_caller_selected(__builtin_return_address(0));
    AMDetectionRuleRef rule=selected ? AMDetectionSchemeRule(&am_detection_profile,url.scheme.UTF8String) : (AMDetectionRuleRef){0};
    bool match=rule.kind!=AM_DETECTION_RULE_NONE;
    bool block=match && am_detection_profile.mode==AM_DETECTION_FILTER;
    BOOL value=block ? NO : am_detection_can_open(self,cmd,url);
    if (selected) AMDetectionRecordOutcome(AM_DETECTION_URL,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_NEGATIVE);
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
void AMDetectionStart(void) {
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
    AMDetectionOriginals o={
        .access_fn=AM_DETECTION_API_ACCESS ? dlsym(RTLD_DEFAULT,"access") : NULL,
        .stat_fn=AM_DETECTION_API_STAT ? dlsym(RTLD_DEFAULT,"stat") : NULL,
        .lstat_fn=AM_DETECTION_API_LSTAT ? dlsym(RTLD_DEFAULT,"lstat") : NULL,
        .open_fn=AM_DETECTION_API_OPEN ? dlsym(RTLD_DEFAULT,"open") : NULL,
        .fopen_fn=AM_DETECTION_API_FOPEN ? dlsym(RTLD_DEFAULT,"fopen") : NULL,
        .getenv_fn=AM_DETECTION_API_GETENV ? dlsym(RTLD_DEFAULT,"getenv") : NULL};
    if (!AMDetectionConfigure(&am_detection_profile,&o)) { am_detection_version_ok=false; return; }
    AMDetectionSetOriginResolver(am_detection_origin);
    AMDetectionSetCallsiteResolver(am_detection_callsite);
    for (unsigned i=0;i<6;i++) AMDetectionSetAdapterReady((AMDetectionAPI)i,false);
    const char *names[]={"access","stat","lstat","open","fopen","getenv"};
    void *originals[]={(void *)o.access_fn,(void *)o.stat_fn,(void *)o.lstat_fn,(void *)o.open_fn,(void *)o.fopen_fn,(void *)o.getenv_fn};
    void *replacements[]={(void *)AMDetectionAccess,(void *)AMDetectionStat,(void *)AMDetectionLstat,(void *)AMDetectionOpen,(void *)AMDetectionFopen,(void *)AMDetectionGetenv};
    for (size_t i=0;i<6;i++) if (am_detection_native_selected[i] && originals[i])
        am_detection_bindings[am_detection_binding_count++]=(AMImportBinding){names[i],originals[i],replacements[i],&am_detection_results[i]};
    if (AM_DETECTION_API_FOUNDATION_EXISTS || AM_DETECTION_API_FOUNDATION_ATTRIBUTES) {
        BOOL a=!AM_DETECTION_API_FOUNDATION_EXISTS || am_detection_replace(NSFileManager.class,@selector(fileExistsAtPath:),(IMP)am_detection_exists,(IMP *)&am_detection_file_exists);
        BOOL b=!AM_DETECTION_API_FOUNDATION_ATTRIBUTES || am_detection_replace(NSFileManager.class,@selector(attributesOfItemAtPath:error:),(IMP)am_detection_attrs,(IMP *)&am_detection_attributes);
        am_detection_foundation_installed=a && b;
    }
    if (AM_DETECTION_API_URLS)
        am_detection_url_installed=am_detection_replace(UIApplication.class,@selector(canOpenURL:),(IMP)am_detection_url,(IMP *)&am_detection_can_open);
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
    AMImportBinding combined[16];
    if (count>10) return (AMImportBindResult){.malformed=true};
    memcpy(combined,compat,count*sizeof *combined);
    Dl_info image;
    bool selected=am_detection_version_ok && am_detection_profile.mode!=AM_DETECTION_OFF &&
        dladdr(header,&image) && am_detection_image_selected(image.dli_fname);
    if (selected) memcpy(combined+count,am_detection_bindings,am_detection_binding_count*sizeof *combined);
    size_t total=count+(selected ? am_detection_binding_count : 0);
    if (!total) return (AMImportBindResult){0};
    os_unfair_lock_lock(&am_detection_lock);
    if (selected) for (unsigned i=0;i<6;i++) AMDetectionSetAdapterReady((AMDetectionAPI)i,false);
    AMImportBindResult result=AMImportBindImage(header,bytes,slide,combined,total,writer,context);
    if (selected && (result.malformed || result.unsupported)) {
        if (am_detection_image_failures<SIZE_MAX) am_detection_image_failures++;
        if (result.reason>AM_IMPORT_FAILURE_NONE && result.reason<AM_IMPORT_FAILURE_COUNT &&
            am_detection_image_failure_reasons[result.reason]<SIZE_MAX) am_detection_image_failure_reasons[result.reason]++;
    }
    if (selected) for (unsigned i=0;i<6;i++) {
        AMImportBindResult s=am_detection_results[i];
        AMDetectionSetAdapterReady((AMDetectionAPI)i,!am_detection_image_failures && !s.conflicts && !s.failed && (s.applied || s.already_owned));
    }
    os_unfair_lock_unlock(&am_detection_lock);
    return result;
}
NSDictionary *AMDetectionReport(void) {
    static NSString *const states[]={@"disabled",@"observation-only",@"filtering",@"unsupported",@"failed"};
    static NSString *const names[]={@"access",@"stat",@"lstat",@"open",@"fopen",@"getenv",@"foundation",@"urls"};
    AMImportBindResult results[6]; size_t failures,reasons[AM_IMPORT_FAILURE_COUNT];
    os_unfair_lock_lock(&am_detection_lock);
    memcpy(results,am_detection_results,sizeof results); failures=am_detection_image_failures;
    memcpy(reasons,am_detection_image_failure_reasons,sizeof reasons);
    os_unfair_lock_unlock(&am_detection_lock);
    NSMutableDictionary *adapters=[NSMutableDictionary dictionary];
    for (size_t i=0;i<8;i++) {
        bool selected=i<6 ? am_detection_native_selected[i] : i==6 ? (AM_DETECTION_API_FOUNDATION_EXISTS || AM_DETECTION_API_FOUNDATION_ATTRIBUTES) : AM_DETECTION_API_URLS;
        size_t installed=i<6 ? results[i].applied+results[i].already_owned : i==6 ? am_detection_foundation_installed : am_detection_url_installed;
        size_t errors=i<6 ? failures+results[i].conflicts+results[i].failed : 0;
        AMDetectionState state=AMDetectionAdapterState(am_detection_profile.mode,selected,installed,errors,am_detection_version_ok);
        AMDetectionCounters counters=AMDetectionReadCounters((AMDetectionAPI)i);
        adapters[names[i]]=@{@"state":states[state],@"calls":@(counters.calls),@"matched":@(counters.matched),@"filtered":@(counters.filtered)};
    }
    adapters[@"image_concealment"]=@{@"state":@"unsupported"};
    adapters[@"class_concealment"]=@{@"state":@"unsupported"};
    NSMutableDictionary *failureReasons=[NSMutableDictionary dictionary];
    for (unsigned i=1;i<AM_IMPORT_FAILURE_COUNT;i++)
        failureReasons[@(AMImportFailureName((AMImportFailure)i))]=@(reasons[i]);
    AMDetectionAttribution rows[AM_DETECTION_ATTRIBUTION_CAPACITY];
    size_t rowCount=AMDetectionReadAttribution(rows,AM_DETECTION_ATTRIBUTION_CAPACITY);
    NSMutableArray *attributed=[NSMutableArray array];
    for (size_t i=0;i<rowCount;i++) {
        AMDetectionAttribution row=rows[i];
        const char *rule=row.rule.kind<=AM_DETECTION_RULE_ENVIRONMENT && row.rule.index<64 ? am_detection_rule_ids[row.rule.kind][row.rule.index] : NULL;
        NSString *image=row.image<AM_DETECTION_IMAGES_COUNT ? @(am_detection_image_ids[row.image]) : @"unattributed";
        [attributed addObject:@{@"image_id":image,@"phase":am_detection_phases[row.phase],@"api":names[row.api],
            @"rule_id":rule ? @(rule) : (id)NSNull.null,@"calls":@(row.counts.calls),
            @"matched":@(row.counts.matched),@"filtered":@(row.counts.filtered)}];
    }
    AMDetectionCallsite sites[AM_DETECTION_CALLSITE_CAPACITY];
    size_t siteCount=AMDetectionReadCallsites(sites,AM_DETECTION_CALLSITE_CAPACITY);
    NSMutableArray *siteRows=[NSMutableArray array];
    for (size_t i=0;i<siteCount;i++) {
        AMDetectionCallsite site=sites[i]; AMDetectionAttribution row=site.origin;
        const char *rule=am_detection_rule_ids[row.rule.kind][row.rule.index];
        NSString *image=row.image<AM_DETECTION_IMAGES_COUNT ? @(am_detection_image_ids[row.image]) : @"unattributed";
        NSString *token=site.token ? [NSString stringWithFormat:@"%016llx",(unsigned long long)site.token] : @"unavailable";
        [siteRows addObject:@{@"image_id":image,@"build_uuid":am_detection_build_uuid(row.image),@"caller_id":token,
            @"api":names[row.api],@"phase":am_detection_phases[row.phase],@"rule_id":rule ? @(rule) : (id)NSNull.null,
            @"results":@{@"unknown":@(site.results[AM_DETECTION_RESULT_UNKNOWN]),@"success":@(site.results[AM_DETECTION_RESULT_SUCCESS]),
                @"negative":@(site.results[AM_DETECTION_RESULT_NEGATIVE]),@"error":@(site.results[AM_DETECTION_RESULT_ERROR]),
                @"original_not_called":@(site.results[AM_DETECTION_RESULT_NOT_CALLED])}}];
    }
    return @{@"schema_version":@3,@"profile_mode":@(am_detection_profile.mode),@"profile_id":@AM_DETECTION_PROFILE_ID,
        @"callsite_id_algorithm":@"fnv1a64-uuid-return-offset-le-v1",@"callsites":siteRows,
        @"callsite_capacity":@AM_DETECTION_CALLSITE_CAPACITY,@"callsites_dropped":@(AMDetectionCallsitesDropped()),
        @"session_id":am_detection_session ?: @"",@"phase":am_detection_phases[AMDetectionCurrentPhase()],
        @"phase_request_id":am_detection_phase_request ?: @"",@"attribution":attributed,
        @"attribution_capacity":@AM_DETECTION_ATTRIBUTION_CAPACITY,@"attribution_dropped":@(AMDetectionAttributionDropped()),
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
