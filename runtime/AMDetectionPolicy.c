#include "AMDetectionPolicy.h"
#include <string.h>

AMDetectionState AMDetectionAdapterState(AMDetectionMode mode, bool selected, size_t installed,
    size_t failures, bool supported) {
    if (!selected || mode==AM_DETECTION_OFF) return AM_DETECTION_DISABLED;
    if (failures) return AM_DETECTION_FAILED;
    if (!supported || !installed) return AM_DETECTION_UNSUPPORTED;
    return mode==AM_DETECTION_FILTER ? AM_DETECTION_FILTERING : AM_DETECTION_OBSERVING;
}

static size_t bounded_length(const char *s, size_t limit) {
    if (!s) return limit;
    size_t n=0; while (n<limit && s[n]) n++; return n;
}
static bool path_valid(const char *s) {
    size_t n=bounded_length(s,4096);
    if (!n || n==4096 || s[0]!='/') return false;
    for (size_t i=1; i<n; i++) {
        if (s[i]=='/' && s[i-1]=='/') return false;
        if (s[i]=='.' && s[i-1]=='/' &&
            (!s[i+1] || s[i+1]=='/' || (s[i+1]=='.' && (!s[i+2] || s[i+2]=='/')))) return false;
    }
    return true;
}
static unsigned char lower_ascii(unsigned char c) { return c>='A' && c<='Z' ? c+32 : c; }
static bool equal_name(const char *a, const char *b, bool ignore_case) {
    size_t n=bounded_length(a,256), m=bounded_length(b,256);
    if (!n || n==256 || n!=m) return false;
    for (size_t i=0;i<n;i++)
        if ((ignore_case ? lower_ascii((unsigned char)a[i]) : (unsigned char)a[i]) !=
            (ignore_case ? lower_ascii((unsigned char)b[i]) : (unsigned char)b[i])) return false;
    return true;
}
AMDetectionRuleRef AMDetectionPathRule(const AMDetectionProfile *p, const char *s) {
    if (!p || !path_valid(s)) return (AMDetectionRuleRef){0};
    for (size_t i=0;p->rules.paths && i<p->rules.path_count && i<64;i++)
        if (path_valid(p->rules.paths[i]) && !strcmp(s,p->rules.paths[i])) return (AMDetectionRuleRef){AM_DETECTION_RULE_PATH,(unsigned)i};
    for (size_t i=0;p->rules.path_prefixes && i<p->rules.prefix_count && i<64;i++) {
        const char *r=p->rules.path_prefixes[i];
        if (!path_valid(r)) continue;
        size_t n=strlen(r);
        if (n>1 && !strncmp(s,r,n) && (!s[n] || s[n]=='/')) return (AMDetectionRuleRef){AM_DETECTION_RULE_PREFIX,(unsigned)i};
    }
    return (AMDetectionRuleRef){0};
}
AMDetectionRuleRef AMDetectionSchemeRule(const AMDetectionProfile *p, const char *s) {
    if (!p) return (AMDetectionRuleRef){0};
    for (size_t i=0;p->rules.url_schemes && i<p->rules.scheme_count && i<64;i++)
        if (equal_name(s,p->rules.url_schemes[i],true)) return (AMDetectionRuleRef){AM_DETECTION_RULE_SCHEME,(unsigned)i};
    return (AMDetectionRuleRef){0};
}
AMDetectionRuleRef AMDetectionEnvironmentRule(const AMDetectionProfile *p, const char *s) {
    if (!p) return (AMDetectionRuleRef){0};
    for (size_t i=0;p->rules.environment_names && i<p->rules.environment_count && i<64;i++)
        if (equal_name(s,p->rules.environment_names[i],false)) return (AMDetectionRuleRef){AM_DETECTION_RULE_ENVIRONMENT,(unsigned)i};
    return (AMDetectionRuleRef){0};
}
bool AMDetectionMatchPath(const AMDetectionProfile *p,const char *s) { return AMDetectionPathRule(p,s).kind!=AM_DETECTION_RULE_NONE; }
bool AMDetectionMatchScheme(const AMDetectionProfile *p,const char *s) { return AMDetectionSchemeRule(p,s).kind!=AM_DETECTION_RULE_NONE; }
bool AMDetectionMatchEnvironment(const AMDetectionProfile *p,const char *s) { return AMDetectionEnvironmentRule(p,s).kind!=AM_DETECTION_RULE_NONE; }
