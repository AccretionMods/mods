/* Host contract executable. Not an installed app or a device/game bypass test. */
#import <Foundation/Foundation.h>
#include "AMDetectionHooks.h"
#include <assert.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#ifdef NDEBUG
#error Contract assertions must remain enabled
#endif

static AMDetectionProfile am_detection_profile;
static _Thread_local unsigned am_detection_objc_depth;
static bool fixture_selected=true;
static NSArray *last_original,*last_keys;
static NSError *last_error;
static NSDirectoryEnumerationOptions last_options;
static bool last_error_was_null;
static unsigned original_calls;
static bool am_detection_caller_selected(const void *caller) {(void)caller;return fixture_selected;}
#include "AMDetectionDirectories.m"
#include "AMDetectionWrites.m"

static unsigned write_calls;
static BOOL last_atomic;
static NSStringEncoding last_encoding;
static NSDataWritingOptions last_write_options;
static BOOL original_string_path(id value,SEL sel,NSString *path,BOOL atomic,NSStringEncoding encoding,NSError **error) {
    (void)sel;write_calls++;last_atomic=atomic;last_encoding=encoding;
    return [value writeToFile:path atomically:atomic encoding:encoding error:error];
}
static BOOL original_string_url(id value,SEL sel,NSURL *url,BOOL atomic,NSStringEncoding encoding,NSError **error) {
    (void)sel;write_calls++;last_atomic=atomic;last_encoding=encoding;
    return [value writeToURL:url atomically:atomic encoding:encoding error:error];
}
static BOOL original_data_path(id value,SEL sel,NSString *path,NSDataWritingOptions options,NSError **error) {
    (void)sel;write_calls++;last_write_options=options;return [value writeToFile:path options:options error:error];
}
static BOOL original_data_url(id value,SEL sel,NSURL *url,NSDataWritingOptions options,NSError **error) {
    (void)sel;write_calls++;last_write_options=options;return [value writeToURL:url options:options error:error];
}
static void verify_writes(NSString *root,bool observing) {
    NSString *probe=[root stringByAppendingPathComponent:@"probe"],*save=[root stringByAppendingPathComponent:@"save"];
    NSURL *probeURL=[NSURL fileURLWithPath:probe],*saveURL=[NSURL fileURLWithPath:save];
    NSString *value=@"payload";NSData *data=[value dataUsingEncoding:NSUTF8StringEncoding];NSError *error=nil;
    unsigned before=write_calls;
    assert(am_detection_string_write_path(value,@selector(writeToFile:atomically:encoding:error:),probe,YES,NSUTF8StringEncoding,&error)==observing);
    if (!observing) assert([error.domain isEqualToString:NSCocoaErrorDomain] && error.code==NSFileWriteNoPermissionError);
    assert(am_detection_string_write_url(value,@selector(writeToURL:atomically:encoding:error:),probeURL,NO,NSUTF8StringEncoding,NULL)==observing);
    assert(am_detection_data_write_path(data,@selector(writeToFile:options:error:),probe,NSDataWritingAtomic,&error)==observing);
    assert(am_detection_data_write_url(data,@selector(writeToURL:options:error:),probeURL,0,NULL)==observing);
    assert(write_calls==before+(observing ? 4u : 0u));
    assert([NSFileManager.defaultManager fileExistsAtPath:probe]==observing);
    assert(am_detection_string_write_path(value,@selector(writeToFile:atomically:encoding:error:),save,YES,NSUTF8StringEncoding,&error));
    assert(last_atomic && last_encoding==NSUTF8StringEncoding);
    assert(am_detection_string_write_url(value,@selector(writeToURL:atomically:encoding:error:),saveURL,NO,NSUTF8StringEncoding,NULL));
    assert(!last_atomic && last_encoding==NSUTF8StringEncoding);
    assert(am_detection_data_write_path(data,@selector(writeToFile:options:error:),save,NSDataWritingAtomic,&error));assert(last_write_options==NSDataWritingAtomic);
    assert(am_detection_data_write_url(data,@selector(writeToURL:options:error:),saveURL,0,NULL));assert(last_write_options==0);
    assert([[NSData dataWithContentsOfFile:save] isEqualToData:data]);
    fixture_selected=false;before=write_calls;
    assert(am_detection_data_write_path(data,@selector(writeToFile:options:error:),probe,0,NULL));assert(write_calls==before+1);
    fixture_selected=true;assert(am_detection_objc_depth==0);
}

static id original_paths(id manager,SEL selector,NSString *path,NSError **error) {
    (void)selector;original_calls++;
    last_original=[manager contentsOfDirectoryAtPath:path error:error];
    last_error=error ? *error : nil;last_error_was_null=error==NULL;return last_original;
}
static id original_urls(id manager,SEL selector,NSURL *url,NSArray *keys,NSDirectoryEnumerationOptions options,NSError **error) {
    (void)selector;original_calls++;last_keys=keys;last_options=options;
    last_original=[manager contentsOfDirectoryAtURL:url includingPropertiesForKeys:keys options:options error:error];
    last_error=error ? *error : nil;last_error_was_null=error==NULL;return last_original;
}
static bool prepare(const AMDetectionEntry *entry,void *context) {(void)context;return *entry->original!=NULL;}
static bool publish(const AMDetectionEntry *entry,void *context) {(void)entry;(void)context;return true;}

int AMDirectoryContractFixtureRun(const char *directory,bool observing) {
    @autoreleasepool {
        NSFileManager *manager=NSFileManager.defaultManager;
        NSString *root=[NSURL fileURLWithPath:@(directory)].URLByResolvingSymlinksInPath.path;
        NSError *error=nil;
        assert([manager createDirectoryAtPath:root withIntermediateDirectories:YES attributes:nil error:&error]);
        NSString *hidden=[root stringByAppendingPathComponent:@"hidden"],*visible=[root stringByAppendingPathComponent:@"visible"];
        assert([[@"hidden" dataUsingEncoding:NSUTF8StringEncoding] writeToFile:hidden options:0 error:&error]);
        assert([[@"visible" dataUsingEncoding:NSUTF8StringEncoding] writeToFile:visible options:0 error:&error]);
        char *owned_path=strdup(hidden.fileSystemRepresentation);assert(owned_path);
        NSString *probe=[root stringByAppendingPathComponent:@"probe"];
        char *owned_probe=strdup(probe.fileSystemRepresentation);assert(owned_probe);
        const char *paths[]={owned_path},*write_paths[]={owned_probe};
        am_detection_profile=(AMDetectionProfile){.mode=observing ? AM_DETECTION_OBSERVE : AM_DETECTION_FILTER,
            .rules={.paths=paths,.path_count=1,.write_paths=write_paths,.write_count=1}};
        const AMDetectionOriginals originals={.getcwd_fn=getcwd};
        assert(AMDetectionConfigure(&am_detection_profile,&originals));
        AMDetectionEntryID ids[]={AM_DETECTION_ENTRY_DIRECTORY_PATHS,AM_DETECTION_ENTRY_DIRECTORY_URLS,
            AM_DETECTION_ENTRY_STRING_WRITE_PATH,AM_DETECTION_ENTRY_STRING_WRITE_URL,
            AM_DETECTION_ENTRY_DATA_WRITE_PATH,AM_DETECTION_ENTRY_DATA_WRITE_URL};
        *AMDetectionEntryByID(ids[0])->original=(void *)original_paths;
        *AMDetectionEntryByID(ids[1])->original=(void *)original_urls;
        *AMDetectionEntryByID(ids[2])->original=(void *)original_string_path;
        *AMDetectionEntryByID(ids[3])->original=(void *)original_string_url;
        *AMDetectionEntryByID(ids[4])->original=(void *)original_data_path;
        *AMDetectionEntryByID(ids[5])->original=(void *)original_data_url;
        for (unsigned i=0;i<6;i++) assert(AMDetectionSetInstaller(ids[i],prepare,publish,NULL));
        assert(AMDetectionConfigureRegistry(ids,6,0,am_detection_profile.mode));
        assert(AMDetectionPrepareModule(AM_DETECTION_MODULE_DIRECTORIES));
        assert(AMDetectionPublishModule(AM_DETECTION_MODULE_DIRECTORIES));
        assert(AMDetectionPrepareModule(AM_DETECTION_MODULE_WRITES));assert(AMDetectionPublishModule(AM_DETECTION_MODULE_WRITES));

        error=[NSError errorWithDomain:@"fixture" code:1 userInfo:nil];
        unsigned before=original_calls;
        NSArray *names=am_detection_directory_paths(manager,@selector(contentsOfDirectoryAtPath:error:),root,&error);
        assert(original_calls==before+1 && error==last_error);
        assert([names containsObject:@"visible"] && [names containsObject:@"hidden"]==observing);
        assert(names.count==(observing ? 2u : 1u));
        for (id item in names) assert([last_original indexOfObjectIdenticalTo:item]!=NSNotFound);
        if (observing) assert(names==last_original);
        assert(am_detection_directory_paths(manager,@selector(contentsOfDirectoryAtPath:error:),root,NULL));
        assert(last_error_was_null);

        NSArray *keys=@[NSURLFileSizeKey,NSURLIsRegularFileKey];
        NSDirectoryEnumerationOptions options=NSDirectoryEnumerationSkipsSubdirectoryDescendants;
        NSArray *urls=am_detection_directory_urls(manager,
            @selector(contentsOfDirectoryAtURL:includingPropertiesForKeys:options:error:),
            [NSURL fileURLWithPath:root],keys,options,NULL);
        assert(last_keys==keys && last_options==options && last_error_was_null);
        assert(urls.count==(observing ? 2u : 1u));
        for (NSURL *item in urls) {
            assert([last_original indexOfObjectIdenticalTo:item]!=NSNotFound);
            NSNumber *size=nil;assert([item getResourceValue:&size forKey:NSURLFileSizeKey error:NULL]);
            assert(size.unsignedIntegerValue==([item.lastPathComponent isEqualToString:@"visible"] ? 7u : 6u));
        }

        NSString *missing=[root stringByAppendingPathComponent:@"missing"];
        NSError *control=nil,*actual=nil;
        assert(![manager contentsOfDirectoryAtPath:missing error:&control]);
        assert(!am_detection_directory_paths(manager,@selector(contentsOfDirectoryAtPath:error:),missing,&actual));
        assert([actual.domain isEqualToString:control.domain] && actual.code==control.code);
        assert(!am_detection_directory_paths(manager,@selector(contentsOfDirectoryAtPath:error:),missing,NULL));

        char cwd[4096];assert(getcwd(cwd,sizeof cwd));assert(!chdir(root.fileSystemRepresentation));
        NSArray *relative=am_detection_directory_paths(manager,@selector(contentsOfDirectoryAtPath:error:),@".",NULL);
        assert(relative==last_original && [relative containsObject:@"hidden"]);
        assert(!chdir(cwd));

        fixture_selected=false;before=original_calls;
        AMDetectionCounters counters=AMDetectionReadEntryCounters(AM_DETECTION_ENTRY_DIRECTORY_PATHS);
        NSArray *unselected=am_detection_directory_paths(manager,@selector(contentsOfDirectoryAtPath:error:),root,NULL);
        assert(unselected==last_original && [unselected containsObject:@"hidden"] && original_calls==before+1);
        assert(AMDetectionReadEntryCounters(AM_DETECTION_ENTRY_DIRECTORY_PATHS).calls==counters.calls);
        /* Enumerators remain unsupported; this fixture never substitutes a proxy. */
        assert(!AMDetectionEntryByID(AM_DETECTION_ENTRY_ENUMERATOR_PATHS)->implemented);
        assert(!AMDetectionEntryByID(AM_DETECTION_ENTRY_ENUMERATOR_URLS)->implemented);
        fixture_selected=true;verify_writes(root,observing);
        free(owned_path);free(owned_probe);
    }
    return 0;
}
#ifndef AM_DIRECTORY_CONTRACT_NO_MAIN
int main(int argc,char **argv) {
    assert(argc==3 && (!strcmp(argv[1],"filter") || !strcmp(argv[1],"observe")));
    int result=AMDirectoryContractFixtureRun(argv[2],!strcmp(argv[1],"observe"));
    if (!result) puts("PASS foundation-file-contract-v2");
    return result;
}
#endif
