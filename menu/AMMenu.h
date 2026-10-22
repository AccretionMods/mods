// Accretion Mods floating menu.
//
// Each mod registers its switches and sliders once, in %ctor, then calls -install.
// Values are saved in the app's own NSUserDefaults under "AM_<key>", so they survive
// relaunches and stay inside the app's sandbox.

#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

@interface AMMenu : NSObject

@property (class, readonly) AMMenu *shared;
@property (nonatomic, copy) NSString *title;

// Items added after this go under a section header with this title. Adding a title used before puts the
// next items back in that section (after what it already has), so related items can be added from anywhere.
- (void)addSection:(NSString *)title;

// Whether a switch or slider with this key is already in the menu (live pins add the ones a build lacks).
- (BOOL)hasItem:(NSString *)key;

+ (BOOL)boolForKey:(NSString *)key;
+ (float)floatForKey:(NSString *)key;

- (void)addSwitch:(NSString *)key
            title:(NSString *)title
     defaultValue:(BOOL)value
         onChange:(nullable void (^)(BOOL on))block;

- (void)addSlider:(NSString *)key
            title:(NSString *)title
              min:(float)min
              max:(float)max
     defaultValue:(float)value
         onChange:(nullable void (^)(float value))block;

// A row that opens a list page. `entries` is asked for the rows each time the page opens (so a mod can build
// them from the running game): dictionaries with "title" and optionally "symbol" (an SF Symbol name) and
// "detail". Tapping a row calls `block` with its index; the page stays open so several picks are easy.
- (void)addList:(NSString *)title
         symbol:(nullable NSString *)symbol
        entries:(NSArray<NSDictionary<NSString *, NSString *> *> *(^)(void))entries
         onPick:(void (^)(NSInteger index))block;

// Shows the floating button on every window that becomes key. Call once, after adding items.
- (void)install;

@end

// Live status of a switch while it is on: ok when the cheat reached the game this tick (found its object and
// read or wrote it, or a JIT hook ran), else why not ("no Enemy in the game right now", "needs JIT").
// The menu shows a glowing dot by each switch that is on, green working and red not, the reason under its
// title, and one dot on the floating button (red when any switch that is on is failing). A switch with no
// report for 15 s shows red. Cheap and thread-safe: call it every tick, or from a hook.
#ifdef __cplusplus
extern "C"
#endif
void AMMenuStatus(const char *key, bool ok, const char *_Nullable reason);

NS_ASSUME_NONNULL_END
