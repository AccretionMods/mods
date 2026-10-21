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

NS_ASSUME_NONNULL_END
