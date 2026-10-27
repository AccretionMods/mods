#pragma once
#import <Foundation/Foundation.h>
#include "AMImportBindings.h"
#if defined(AM_DETECTION_PROBE)
#define AM_DETECTION_EXPORT __attribute__((visibility("default")))
#else
#define AM_DETECTION_EXPORT
#endif
void AMDetectionStart(size_t compatibility_bindings);
AMImportBindResult AMDetectionBindImage(const void *,size_t,intptr_t,const AMImportBinding *,size_t,AMImportWriter,void *);
AM_DETECTION_EXPORT NSDictionary *AMDetectionReport(void);
AM_DETECTION_EXPORT BOOL AMDetectionWriteReport(void);
