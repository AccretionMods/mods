// Accretion Mods floating menu.
//
// Each mod registers its switches and sliders once, in %ctor, then calls -install.
// Values are saved in the app's own NSUserDefaults under "AM_<key>", so they survive
// relaunches and stay inside the app's sandbox.
//
// The official mod menu template (owner, 2026-10-22): every mod uses this menu as it is; mods add items, never
// restyle it. The rules are in README "The mod menu (official template)". The look follows Accretion's design
// system (dark first, violet-black grounds, one outlined box per category, calm status colours); categories
// start expanded and long menus get a search field.

#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

@interface AMMenu : NSObject

@property (class, readonly) AMMenu *shared;
// The mod's name ("9Kings+"). The menu itself is titled with the game's own name, the one under its icon.
@property (nonatomic, copy) NSString *title;

// Called with YES as the menu starts to open and NO once it has closed (a Unity mod pauses the game with it:
// AMIl2cppPauseGame). Taps on the game are blocked from the button tap until the menu is fully up.
@property (nonatomic, copy, nullable) void (^onOpenChange)(BOOL open);

// Items added after this go under a section header with this title. Adding a title used before puts the
// next items back in that section (after what it already has), so related items can be added from anywhere.
- (void)addSection:(NSString *)title;

// Whether a switch or slider with this key is already in the menu (live pins add the ones a build lacks).
- (BOOL)hasItem:(NSString *)key;

+ (BOOL)boolForKey:(NSString *)key;
+ (float)floatForKey:(NSString *)key;

// Sets a switch or slider from outside the menu (am test turns switches on and off this way): saves the value,
// runs the item's onChange and logs the change. NO when no item has the key.
- (BOOL)setSwitch:(NSString *)key on:(BOOL)on;
- (BOOL)setSlider:(NSString *)key value:(float)value;
// The slider's range and default ({min, max, default}), or nil for an unknown key or a switch.
- (nullable NSArray<NSNumber *> *)sliderRange:(NSString *)key;

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
// them from the running game): dictionaries with "title" and optionally "symbol" (an SF Symbol name),
// "detail" and "group". Entries with a "group" (a quality tier: "Legendary", "Rare", ...) are shown one box per
// group, in the order they first appear, so list the best tier first; known tier names get their colour. Tapping a
// row calls `block` with its index in `entries`; the page stays open so several picks are easy.
- (void)addList:(NSString *)title
         symbol:(nullable NSString *)symbol
        entries:(NSArray<NSDictionary<NSString *, NSString *> *> *(^)(void))entries
         onPick:(void (^)(NSInteger index))block;

// Shows the floating button on every window that becomes key. Call once, after adding items.
- (void)install;

@end

// Live status of a switch while it is on: ok when the cheat reached the game this tick (found its object and
// read or wrote it, or a JIT hook ran), else why not ("no Enemy in the game right now", "needs JIT").
// The menu shows a dot by each switch that is on, green working and red not, the reason under its title, and a
// ring on the floating button (green all, yellow some, red none working). A switch with no report for 15 s
// shows red. Cheap and thread-safe: call it every tick, or from a hook.
#ifdef __cplusplus
extern "C"
#endif
void AMMenuStatus(const char *key, bool ok, const char *_Nullable reason);

// A short note shown under a slider's title, such as the game's own value ("Game 2.0"). NULL clears it.
#ifdef __cplusplus
extern "C"
#endif
void AMMenuNote(const char *key, const char *_Nullable note);

// What a list page's pick did, called from inside its onPick block: ok and a few words ("Added", "Load a vault
// first"). The row then lights up cyan with "✓ Added" or red with the reason. Without a call the row says "✓ Sent":
// the mod did not say whether the game took it.
#ifdef __cplusplus
extern "C"
#endif
void AMMenuPickResult(bool ok, const char *_Nullable text);

NS_ASSUME_NONNULL_END
