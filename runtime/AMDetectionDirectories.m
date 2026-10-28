/* Included by AMDetection.m only for explicitly selected Foundation directory APIs.
   Filtering remains unavailable until an Apple contract execution is recorded. */
static NSArray *am_detection_directory_result(NSArray *original,NSString *parent,bool urls,
    AMDetectionEntryID entry,const void *caller) {
    char resolved[4096];bool context=parent.isAbsolutePath && AMDetectionResolvePath(AT_FDCWD,parent.fileSystemRepresentation,resolved,sizeof resolved);
    if (!context) AMDetectionRecordCoverageGap();
    NSMutableArray *visible=[NSMutableArray arrayWithCapacity:original.count];
    bool matched=false,filtered=false;
    for (id item in original) {
        AMDetectionRuleRef rule={0};
        if (context && (urls ? [item isKindOfClass:NSURL.class] : [item isKindOfClass:NSString.class])) {
            if (urls) {
                NSURL *url=item;
                if (url.isFileURL) rule=AMDetectionPathRule(&am_detection_profile,url.path.fileSystemRepresentation);
            } else rule=AMDetectionChildRule(&am_detection_profile,resolved,[item fileSystemRepresentation]);
        } else AMDetectionRecordCoverageGap();
        bool match=rule.kind!=AM_DETECTION_RULE_NONE,block=match && AMDetectionModuleActive(AM_DETECTION_MODULE_DIRECTORIES);
        matched|=match;filtered|=block;
        AMDetectionRecordEntryDetailOutcome(entry,rule,block,caller,AM_DETECTION_RESULT_SUCCESS);
        if (!block) [visible addObject:item];
    }
    if (!original.count) AMDetectionRecordEntryDetailOutcome(entry,(AMDetectionRuleRef){0},false,caller,
        original ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_ERROR);
    AMDetectionCountEntry(entry,matched,filtered);
    return filtered ? [visible copy] : original;
}
static id am_detection_directory_paths(id self,SEL sel,NSString *path,NSError **error) {
    id (*original)(id,SEL,NSString *,NSError **)=*AMDetectionEntryByID(AM_DETECTION_ENTRY_DIRECTORY_PATHS)->original;
    if (am_detection_objc_depth || !am_detection_caller_selected(__builtin_return_address(0))) return original(self,sel,path,error);
    am_detection_objc_depth++;id value=original(self,sel,path,error);
    id result=am_detection_directory_result(value,path,false,AM_DETECTION_ENTRY_DIRECTORY_PATHS,__builtin_return_address(0));
    am_detection_objc_depth--;return result;
}
static id am_detection_directory_urls(id self,SEL sel,NSURL *url,NSArray *keys,NSDirectoryEnumerationOptions options,NSError **error) {
    id (*original)(id,SEL,NSURL *,NSArray *,NSDirectoryEnumerationOptions,NSError **)=*AMDetectionEntryByID(AM_DETECTION_ENTRY_DIRECTORY_URLS)->original;
    if (am_detection_objc_depth || !am_detection_caller_selected(__builtin_return_address(0))) return original(self,sel,url,keys,options,error);
    am_detection_objc_depth++;id value=original(self,sel,url,keys,options,error);
    id result=am_detection_directory_result(value,url.isFileURL ? url.path : nil,true,AM_DETECTION_ENTRY_DIRECTORY_URLS,__builtin_return_address(0));
    am_detection_objc_depth--;return result;
}
