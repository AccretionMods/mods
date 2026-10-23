// Hidden names: the game's class, method and field names never sit in the built .dylib as plain text.
// scripts/embed_hooks.py and scripts/gen_tweaks.py write them XOR-encoded, and scripts/hide_strings.py turns
// AMS("Name") in a hand-written Tweak.x into AMS_BYTES(n, ...) at build time (the source keeps the plain name).
// Each is decoded once, in memory, the first time it is used. The key is read through a volatile so the
// compiler can't fold the decoding back into plain text. This slows a casual look, nothing more.
#ifndef AM_HIDE_H
#define AM_HIDE_H

#include <stddef.h>

__attribute__((unused)) static volatile unsigned char am_hide_key = 0xA7;

// out may be in itself (decoding in place); out[n] becomes the terminating 0
static inline void am_unhide(char *out, const unsigned char *in, size_t n) {
    unsigned char k = am_hide_key;
    for (size_t i = 0; i < n; i++) out[i] = (char)(in[i] ^ (unsigned char)(k + 31 * i));
    out[n] = 0;
}

#define AMS(s) (s)  // a name hide_strings.py encodes at build time; plain until then
#define AMS_BYTES(n, ...) ({                                                  \
    static char am_s[(n) + 1];                                                \
    static int am_done;                                                       \
    if (!am_done) {                                                           \
        static const unsigned char am_e[(n) + 1] = {__VA_ARGS__};             \
        am_unhide(am_s, am_e, (n));                                           \
        am_done = 1;                                                          \
    }                                                                         \
    (const char *)am_s;                                                       \
})

#endif
