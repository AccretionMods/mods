#include "AMDetectionWrites.h"
#include <string.h>
bool AMDetectionExpandWriteProbe(const char *name,size_t length,char *out,size_t capacity) {
    static const char prefix[]="/private/";
    if (!name || !out || !length || length>4086 || capacity<=9+length ||
        memchr(name,0,length) || memchr(name,'/',length) || memchr(name,'\\',length) ||
        (length==1 && name[0]=='.') || (length==2 && name[0]=='.' && name[1]=='.')) return false;
    static const char *const roots[]={"var","etc","tmp","preboot"};
    for (size_t i=0;i<sizeof roots/sizeof *roots;i++) {
        size_t n=strlen(roots[i]);bool same=n==length;
        for (size_t j=0;same && j<n;j++) {
            unsigned char c=(unsigned char)name[j];if (c>='A' && c<='Z') c+=32;
            if (c!=(unsigned char)roots[i][j]) same=false;
        }
        if (same) return false;
    }
    memcpy(out,prefix,9);memcpy(out+9,name,length);out[9+length]=0;return true;
}
