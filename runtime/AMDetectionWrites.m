/* Typed Foundation write boundaries. Apple contract verification gates installation. */
static BOOL am_detection_write_denied(NSError **error) {
    if (error) *error=[NSError errorWithDomain:NSCocoaErrorDomain code:NSFileWriteNoPermissionError userInfo:nil];
    return NO;
}
static BOOL am_detection_string_write_path(id self,SEL sel,NSString *path,BOOL atomic,NSStringEncoding encoding,NSError **error) {
    BOOL (*original)(id,SEL,NSString *,BOOL,NSStringEncoding,NSError **)=*AMDetectionEntryByID(AM_DETECTION_ENTRY_STRING_WRITE_PATH)->original;
    if (am_detection_objc_depth || !am_detection_caller_selected(__builtin_return_address(0))) return original(self,sel,path,atomic,encoding,error);
    am_detection_objc_depth++;
    AMDetectionRuleRef rule=AMDetectionWriteRule(&am_detection_profile,path.fileSystemRepresentation);
    bool block=rule.kind!=AM_DETECTION_RULE_NONE && AMDetectionModuleActive(AM_DETECTION_MODULE_WRITES);
    BOOL value=block ? am_detection_write_denied(error) : original(self,sel,path,atomic,encoding,error);
    AMDetectionRecordEntryOutcome(AM_DETECTION_ENTRY_STRING_WRITE_PATH,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_ERROR);
    am_detection_objc_depth--;return value;
}
static BOOL am_detection_string_write_url(id self,SEL sel,NSURL *url,BOOL atomic,NSStringEncoding encoding,NSError **error) {
    BOOL (*original)(id,SEL,NSURL *,BOOL,NSStringEncoding,NSError **)=*AMDetectionEntryByID(AM_DETECTION_ENTRY_STRING_WRITE_URL)->original;
    if (am_detection_objc_depth || !am_detection_caller_selected(__builtin_return_address(0))) return original(self,sel,url,atomic,encoding,error);
    am_detection_objc_depth++;
    AMDetectionRuleRef rule=AMDetectionWriteRule(&am_detection_profile,url.isFileURL ? url.path.fileSystemRepresentation : NULL);
    bool block=rule.kind!=AM_DETECTION_RULE_NONE && AMDetectionModuleActive(AM_DETECTION_MODULE_WRITES);
    BOOL value=block ? am_detection_write_denied(error) : original(self,sel,url,atomic,encoding,error);
    AMDetectionRecordEntryOutcome(AM_DETECTION_ENTRY_STRING_WRITE_URL,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_ERROR);
    am_detection_objc_depth--;return value;
}
static BOOL am_detection_data_write_path(id self,SEL sel,NSString *path,NSDataWritingOptions options,NSError **error) {
    BOOL (*original)(id,SEL,NSString *,NSDataWritingOptions,NSError **)=*AMDetectionEntryByID(AM_DETECTION_ENTRY_DATA_WRITE_PATH)->original;
    if (am_detection_objc_depth || !am_detection_caller_selected(__builtin_return_address(0))) return original(self,sel,path,options,error);
    am_detection_objc_depth++;
    AMDetectionRuleRef rule=AMDetectionWriteRule(&am_detection_profile,path.fileSystemRepresentation);
    bool block=rule.kind!=AM_DETECTION_RULE_NONE && AMDetectionModuleActive(AM_DETECTION_MODULE_WRITES);
    BOOL value=block ? am_detection_write_denied(error) : original(self,sel,path,options,error);
    AMDetectionRecordEntryOutcome(AM_DETECTION_ENTRY_DATA_WRITE_PATH,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_ERROR);
    am_detection_objc_depth--;return value;
}
static BOOL am_detection_data_write_url(id self,SEL sel,NSURL *url,NSDataWritingOptions options,NSError **error) {
    BOOL (*original)(id,SEL,NSURL *,NSDataWritingOptions,NSError **)=*AMDetectionEntryByID(AM_DETECTION_ENTRY_DATA_WRITE_URL)->original;
    if (am_detection_objc_depth || !am_detection_caller_selected(__builtin_return_address(0))) return original(self,sel,url,options,error);
    am_detection_objc_depth++;
    AMDetectionRuleRef rule=AMDetectionWriteRule(&am_detection_profile,url.isFileURL ? url.path.fileSystemRepresentation : NULL);
    bool block=rule.kind!=AM_DETECTION_RULE_NONE && AMDetectionModuleActive(AM_DETECTION_MODULE_WRITES);
    BOOL value=block ? am_detection_write_denied(error) : original(self,sel,url,options,error);
    AMDetectionRecordEntryOutcome(AM_DETECTION_ENTRY_DATA_WRITE_URL,rule,block,__builtin_return_address(0),value ? AM_DETECTION_RESULT_SUCCESS : AM_DETECTION_RESULT_ERROR);
    am_detection_objc_depth--;return value;
}
