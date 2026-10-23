#import "AMMenu.h"
#import <os/lock.h>
#import <os/log.h>
#import <dlfcn.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <CommonCrypto/CommonDigest.h>

static NSString *AMKey(NSString *key) {
    return [@"AM_" stringByAppendingString:key];
}

#pragma mark - Accretion tokens

// From design/tokens.json in the store repo (dark, light). Dark first: the menu follows the phone's setting.
static UIColor *AMTok(uint32_t dark, uint32_t light) {
    UIColor *(^rgb)(uint32_t) = ^UIColor *(uint32_t c) {
        return [UIColor colorWithRed:((c >> 16) & 0xFF) / 255.0 green:((c >> 8) & 0xFF) / 255.0 blue:(c & 0xFF) / 255.0 alpha:1];
    };
    UIColor *d = rgb(dark), *l = rgb(light);
    return [UIColor colorWithDynamicProvider:^UIColor *(UITraitCollection *t) {
        return t.userInterfaceStyle == UIUserInterfaceStyleLight ? l : d;
    }];
}
#define AM_BG AMTok(0x07070D, 0xF7F6FC)
#define AM_SURFACE AMTok(0x0F0E1A, 0xFFFFFF)
#define AM_RAISED AMTok(0x17152A, 0xF0EEF9)
#define AM_LINE AMTok(0x262145, 0xDFDAF2)
#define AM_FG AMTok(0xECEAFF, 0x15122B)
#define AM_MUTED AMTok(0x8B87B3, 0x625E85)
#define AM_ACCENT AMTok(0x8B5CF6, 0x6D3FE0)
#define AM_ACCENT_TEXT AMTok(0xA07AF7, 0x6A3BDF)
#define AM_ACCENT_FILL AMTok(0x7C4DEE, 0x6D3FE0)
#define AM_GOOD AMTok(0x34D399, 0x066D4A)
#define AM_WARN AMTok(0xF59E0B, 0x8C5100)
#define AM_BAD AMTok(0xF87171, 0xAA390B)
#define AM_ACCENT2 AMTok(0x22D3EE, 0x0E8FB0)
#define AM_LINE_STRONG AMTok(0x6458AF, 0x8F7ED2)

static UIColor *AMResolved(UIColor *c, UITraitCollection *t) {
    return [c resolvedColorWithTraitCollection:t];
}

static UIFont *AMMono(CGFloat size, UIFontWeight weight) {
    return [UIFont monospacedSystemFontOfSize:size weight:weight];
}

// Uppercase mono label with wide tracking (the design system's `label`).
static NSAttributedString *AMLabel(NSString *text, UIColor *color) {
    return [[NSAttributedString alloc] initWithString:text.uppercaseString
                                           attributes:@{NSFontAttributeName: AMMono(11, UIFontWeightMedium),
                                                        NSForegroundColorAttributeName: color,
                                                        NSKernAttributeName: @1.2}];
}

#pragma mark - Live status and notes

// What each switch last reported (AMMenuStatus) and each slider's note (AMMenuNote). Fixed tables: hooks may
// report from any thread and often. 512 entries: 9 Kings alone has 194 switches.
typedef struct {
    char key[48];
    bool ok;
    char reason[160];
    CFAbsoluteTime at;
} AMStatusEntry;
static AMStatusEntry sStatus[512];
static int sStatusCount;
typedef struct {
    char key[48];
    char note[64];
} AMNoteEntry;
static AMNoteEntry sNotes[512];
static int sNoteCount;
static os_unfair_lock sStatusLock = OS_UNFAIR_LOCK_INIT;
static const CFAbsoluteTime AMStatusStale = 15;  // seconds without a report before a switch that is on shows red

void AMMenuStatus(const char *key, bool ok, const char *reason) {
    if (!key) return;
    os_unfair_lock_lock(&sStatusLock);
    AMStatusEntry *e = NULL;
    for (int i = 0; i < sStatusCount; i++) {
        if (!strcmp(sStatus[i].key, key)) { e = &sStatus[i]; break; }
    }
    if (!e && sStatusCount < (int)(sizeof sStatus / sizeof sStatus[0])) {
        e = &sStatus[sStatusCount++];
        strlcpy(e->key, key, sizeof e->key);
    }
    bool changed = false;
    char line[220] = "";
    if (e) {
        const char *why = ok ? "" : (reason ?: "not reaching the game");
        // a switch back on after a quiet spell (it was off) announces itself again, even with the same state
        BOOL quiet = e->at != 0 && CFAbsoluteTimeGetCurrent() - e->at > AMStatusStale;
        changed = e->at == 0 || quiet || e->ok != ok || strcmp(e->reason, why) != 0;
        e->ok = ok;
        strlcpy(e->reason, why, sizeof e->reason);
        e->at = CFAbsoluteTimeGetCurrent();
        if (changed) snprintf(line, sizeof line, "status %s %s%s", key, ok ? "ok" : "waiting: ", why);
    }
    os_unfair_lock_unlock(&sStatusLock);
    // Mod Studio and am test read these from the device log to show each switch's state live
    if (changed) os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", line);
}

static BOOL sPickReported, sPickOK;
static NSString *sPickText;

void AMMenuPickResult(bool ok, const char *text) {  // main thread, inside a list page's onPick
    sPickReported = YES;
    sPickOK = ok;
    sPickText = text && text[0] ? @(text) : nil;
}

void AMMenuNote(const char *key, const char *note) {
    if (!key) return;
    os_unfair_lock_lock(&sStatusLock);
    AMNoteEntry *e = NULL;
    for (int i = 0; i < sNoteCount; i++) {
        if (!strcmp(sNotes[i].key, key)) { e = &sNotes[i]; break; }
    }
    if (!e && sNoteCount < (int)(sizeof sNotes / sizeof sNotes[0])) {
        e = &sNotes[sNoteCount++];
        strlcpy(e->key, key, sizeof e->key);
    }
    if (e) strlcpy(e->note, note ?: "", sizeof e->note);
    os_unfair_lock_unlock(&sStatusLock);
}

static NSString *AMNoteFor(NSString *key) {
    NSString *text = nil;
    os_unfair_lock_lock(&sStatusLock);
    for (int i = 0; i < sNoteCount; i++) {
        if (!strcmp(sNotes[i].key, key.UTF8String)) { if (sNotes[i].note[0]) text = @(sNotes[i].note); break; }
    }
    os_unfair_lock_unlock(&sStatusLock);
    return text;
}

// Whether a switch's cheat is working now, and the line shown under its title. While another switch is working
// the save is clearly loaded, so a "load a save" line (the generated default, or no report yet) becomes what is
// true then: the thing it acts on isn't in the game yet (2026-10-23: the child's switches said "load a save first"
// in a loaded save).
static BOOL AMStatusFor(NSString *key, NSString **line) {
    BOOL ok = NO, loaded = NO, waiting = YES;
    NSString *text = @"Waiting for the game to load";
    CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
    os_unfair_lock_lock(&sStatusLock);
    for (int i = 0; i < sStatusCount; i++) {
        BOOL fresh = now - sStatus[i].at <= AMStatusStale;
        if (strcmp(sStatus[i].key, key.UTF8String)) {
            if (fresh && sStatus[i].ok) loaded = YES;
            continue;
        }
        if (!fresh) {
            text = @"Stopped: no report for 15 s";
            waiting = NO;
        } else if (sStatus[i].ok) {
            ok = YES;
            text = @"Working";
            waiting = NO;
        } else {
            text = @(sStatus[i].reason);
            waiting = strcmp(sStatus[i].reason, "Waiting for the game: load a save first") == 0;
        }
    }
    os_unfair_lock_unlock(&sStatusLock);
    if (!ok && waiting && loaded) text = @"Not in your game yet: it shows up later in play, or this game version doesn't have it";
    if (line) *line = text;
    return ok;
}

// The menu's Display category: whether the status dot and ring pulse, and whether switches show their toggle.
// Kept in the game's NSUserDefaults with the switches; both on until turned off.
static NSString *const AMDisplaySection = @"Display";
static NSString *const AMPulseKey = @"AM__pulse";
static NSString *const AMTogglesKey = @"AM__toggles";

static BOOL AMDisplayOn(NSString *key) {
    id value = [NSUserDefaults.standardUserDefaults objectForKey:key];
    return value ? [value boolValue] : YES;
}

static BOOL AMPulseAllowed(void) {
    return !UIAccessibilityIsReduceMotionEnabled() && AMDisplayOn(AMPulseKey);
}

static void AMPulse(CALayer *layer, CGFloat from, CGFloat to, CFTimeInterval duration) {
    [layer removeAllAnimations];
    if (!AMPulseAllowed()) return;
    CABasicAnimation *fade = [CABasicAnimation animationWithKeyPath:@"opacity"];
    fade.fromValue = @(from);
    fade.toValue = @(to);
    fade.duration = duration / 2;
    fade.autoreverses = YES;
    fade.repeatCount = HUGE_VALF;
    fade.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
    [layer addAnimation:fade forKey:@"pulse"];
}

// A switch: a small outline pill, no checkmark. Off: a `line-strong` edge and a `muted` knob at the left. On: the
// knob moves right and edge and knob turn cyan (`accent-2`) with a soft glow, or red (`bad`) while the switch is on
// but not reaching the game. The whole row is the tap target (AMPanel's didSelectRow), so the pill stays small.
@interface AMLineToggle : UIView
@property (nonatomic, readonly) BOOL on, bad;
- (void)setOn:(BOOL)on bad:(BOOL)bad animated:(BOOL)animated;
@end

@implementation AMLineToggle {
    CALayer *_knob;
}

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectMake(0, 0, 34, 20)])) {
        self.userInteractionEnabled = NO;
        self.layer.cornerRadius = 10;
        self.layer.borderWidth = 1;
        _knob = [CALayer layer];
        _knob.cornerRadius = 6;
        _knob.shadowOffset = CGSizeZero;
        _knob.shadowRadius = 3;
        [self.layer addSublayer:_knob];
        [self setOn:NO bad:NO animated:NO];
    }
    return self;
}

- (CGSize)intrinsicContentSize { return CGSizeMake(34, 20); }

- (void)setOn:(BOOL)on bad:(BOOL)bad animated:(BOOL)animated {
    _on = on;
    _bad = on && bad;
    [CATransaction begin];
    [CATransaction setDisableActions:!animated];
    [CATransaction setAnimationDuration:0.18];
    [self applyColors];
    _knob.frame = CGRectMake(on ? 18 : 4, 4, 12, 12);
    [CATransaction commit];
}

- (void)applyColors {
    UITraitCollection *t = self.traitCollection;
    UIColor *tint = !self.on ? AMResolved(AM_LINE_STRONG, t) : self.bad ? AMResolved(AM_BAD, t) : AMResolved(AM_ACCENT2, t);
    self.layer.borderColor = tint.CGColor;
    self.layer.backgroundColor = self.on ? [tint colorWithAlphaComponent:0.12].CGColor : UIColor.clearColor.CGColor;
    _knob.backgroundColor = (self.on ? tint : AMResolved(AM_MUTED, t)).CGColor;
    _knob.shadowColor = tint.CGColor;
    _knob.shadowOpacity = self.on && !self.bad ? 0.9 : 0;
}

- (void)traitCollectionDidChange:(UITraitCollection *)previous {
    [super traitCollectionDidChange:previous];
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    [self applyColors];
    [CATransaction commit];
}

@end

// The status dot by a switch that is on: `good` steady when working, `bad` pulsing every 2.4 s when not
// (the design system's live dot). Still with Reduce Motion.
@interface AMStatusDot : UIView
@property (nonatomic) BOOL ok;
@end

@implementation AMStatusDot

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectMake(0, 0, 8, 8)])) {
        self.userInteractionEnabled = NO;
        self.layer.cornerRadius = 4;
        [self setOk:NO];
    }
    return self;
}

- (void)setOk:(BOOL)ok {
    _ok = ok;
    self.backgroundColor = ok ? AM_GOOD : AM_BAD;
    BOOL want = !ok && self.window && AMPulseAllowed();  // a red dot holds still when Pulsing dot is off
    BOOL has = [self.layer animationForKey:@"pulse"] != nil;
    if (want && !has) AMPulse(self.layer, 1, 0.35, 2.4);
    else if (!want && has) [self.layer removeAllAnimations];
}

- (void)didMoveToWindow {
    [super didMoveToWindow];
    [self setOk:self.ok];
}

@end

// A plain text button in the title bar (iOS 26 would draw Done as a checkmark in a glass circle).
static UIBarButtonItem *AMBarText(NSString *title, id target, SEL action) {
    UIBarButtonItem *b = [[UIBarButtonItem alloc] initWithTitle:title style:UIBarButtonItemStylePlain target:target action:action];
    if ([b respondsToSelector:NSSelectorFromString(@"setHidesSharedBackground:")]) [b setValue:@YES forKey:@"hidesSharedBackground"];
    return b;
}

@interface AMItem : NSObject
@property (nonatomic, copy) NSString *key;
@property (nonatomic, copy) NSString *title;
@property (nonatomic) BOOL isSlider;
@property (nonatomic) float min, max, defaultValue;
@property (nonatomic, copy) void (^onSwitch)(BOOL);
@property (nonatomic, copy) void (^onSlider)(float);
// list rows
@property (nonatomic) BOOL isList;
@property (nonatomic, copy) NSString *symbol;
@property (nonatomic, copy) NSArray<NSDictionary<NSString *, NSString *> *> *(^entries)(void);
@property (nonatomic, copy) void (^onPick)(NSInteger);
@property (nonatomic, copy) NSString *section;  // the menu group it belongs to ("" before any addSection)
@property (nonatomic) BOOL display;  // one of the menu's own Display settings: never counted as a cheat that is on
// an action row (Cloud saves): runs on tap, then says what happened on the row (✓ text in cyan, or red)
@property (nonatomic, copy) void (^onAction)(void (^done)(BOOL ok, NSString *text));
@property (nonatomic, copy) NSString *label;  // the action row's right side before it runs ("›")
@property (nonatomic, copy) NSString *(^liveLabel)(void);  // or a right side that changes (Last saved: …)
@property (nonatomic) BOOL quiet;  // a muted title (Unlink)
@end

@implementation AMItem
@end

static BOOL AMItemOn(AMItem *item) {
    if (item.isList || item.display || item.onAction) return NO;
    if (item.isSlider) return [NSUserDefaults.standardUserDefaults floatForKey:AMKey(item.key)] != item.defaultValue;
    return [NSUserDefaults.standardUserDefaults boolForKey:AMKey(item.key)];
}

// A slider is on while its value is not its default. Off keeps the value it had (AM_<key>__last), so its toggle
// brings it back; the slider then shows that value dimmed.
static NSString *AMLastKey(AMItem *item) { return [AMKey(item.key) stringByAppendingString:@"__last"]; }

static void AMSliderSet(AMItem *item, float value) {
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    float now = [d floatForKey:AMKey(item.key)];
    if (value == item.defaultValue && now != item.defaultValue) [d setFloat:now forKey:AMLastKey(item)];
    if (value != item.defaultValue) [d setFloat:value forKey:AMLastKey(item)];
    [d setFloat:value forKey:AMKey(item.key)];
    if (item.onSlider) item.onSlider(value);
}

// what the slider shows: its value while on, the kept value (or the default) while off
static float AMSliderShown(AMItem *item) {
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    if (AMItemOn(item)) return [d floatForKey:AMKey(item.key)];
    return [d objectForKey:AMLastKey(item)] ? [d floatForKey:AMLastKey(item)] : item.defaultValue;
}

static void AMStyleNav(UINavigationController *nav) {
    UINavigationBarAppearance *a = [UINavigationBarAppearance new];
    [a configureWithOpaqueBackground];
    a.backgroundColor = AM_BG;
    a.shadowColor = AM_LINE;
    a.titleTextAttributes = @{NSForegroundColorAttributeName: AM_FG,
                              NSFontAttributeName: [UIFont systemFontOfSize:17 weight:UIFontWeightSemibold]};
    nav.navigationBar.standardAppearance = a;
    nav.navigationBar.scrollEdgeAppearance = a;
    nav.navigationBar.compactAppearance = a;
    nav.navigationBar.tintColor = AM_ACCENT_TEXT;
    nav.view.tintColor = AM_ACCENT;
}

#pragma mark - List page

@interface AMListPanel : UITableViewController <UITextFieldDelegate>
@property (nonatomic, strong) AMItem *item;
@property (nonatomic, copy) NSArray<NSDictionary<NSString *, NSString *> *> *entries;
- (instancetype)initWithItem:(AMItem *)item;
@end


#pragma mark - Backdrop and panels (the store's live-index look)

static BOOL AMStill(void) {
    return UIAccessibilityIsReduceMotionEnabled() || NSProcessInfo.processInfo.lowPowerModeEnabled;
}

// The Backdrop: `bg` and two soft lights (violet, cyan) drifting over 40 s.
// Core Animation moves the lights on the render server: no timer, nothing redrawn per frame. Still with
// Reduce Motion or Low Power Mode.
@interface AMBackdrop : UIView
@end

@implementation AMBackdrop {
    CAGradientLayer *_lights[2];
    CGSize _laidOut;
}

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectZero])) {
        self.userInteractionEnabled = NO;
        self.backgroundColor = AM_BG;
        for (int i = 0; i < 2; i++) {
            _lights[i] = [CAGradientLayer layer];
            _lights[i].type = kCAGradientLayerRadial;
            _lights[i].startPoint = CGPointMake(0.5, 0.5);
            _lights[i].endPoint = CGPointMake(1, 1);
            [self.layer addSublayer:_lights[i]];
        }
        [self applyColors];
    }
    return self;
}

- (void)applyColors {
    BOOL light = self.traitCollection.userInterfaceStyle == UIUserInterfaceStyleLight;
    CGFloat glow = light ? 0.09 : 0.16;
    UIColor *a = AMResolved(AM_ACCENT, self.traitCollection), *b = AMResolved(AM_ACCENT2, self.traitCollection);
    _lights[0].colors = @[(id)[a colorWithAlphaComponent:glow].CGColor, (id)[a colorWithAlphaComponent:0].CGColor];
    _lights[1].colors = @[(id)[b colorWithAlphaComponent:glow].CGColor, (id)[b colorWithAlphaComponent:0].CGColor];
}

- (void)traitCollectionDidChange:(UITraitCollection *)previous {
    [super traitCollectionDidChange:previous];
    [self applyColors];
}

- (void)layoutSubviews {
    [super layoutSubviews];
    CGSize size = self.bounds.size;
    if (CGSizeEqualToSize(size, _laidOut)) return;
    _laidOut = size;
    CGFloat d = MAX(size.width, size.height) * 0.9;
    CGPoint from[2] = {CGPointMake(size.width * 0.15, size.height * 0.12), CGPointMake(size.width * 0.9, size.height * 0.75)};
    CGPoint to[2] = {CGPointMake(size.width * 0.6, size.height * 0.35), CGPointMake(size.width * 0.3, size.height * 0.95)};
    for (int i = 0; i < 2; i++) {
        _lights[i].bounds = CGRectMake(0, 0, d, d);
        _lights[i].position = from[i];
        [_lights[i] removeAllAnimations];
        if (AMStill()) continue;
        CABasicAnimation *drift = [CABasicAnimation animationWithKeyPath:@"position"];
        drift.fromValue = [NSValue valueWithCGPoint:from[i]];
        drift.toValue = [NSValue valueWithCGPoint:to[i]];
        drift.duration = i ? 52 : 40;
        drift.autoreverses = YES;
        drift.repeatCount = HUGE_VALF;
        drift.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
        [_lights[i] addAnimation:drift forKey:@"drift"];
    }
}

@end

// Panels: every category is one bordered box (`surface`, a 1 px `line` edge, 12 pt corners) with its label just
// above it. A table can't border a whole section, so each row draws its own slice: the top (rounded corners, top
// edge), a middle (the two sides), the bottom, or a whole box (a category of one). No lines between rows.
static const CGFloat AMSide = 16;   // a panel's distance from the screen edge
static const CGFloat AMMaxWidth = 640;  // panels never grow wider: landscape centres them like a reading column

// The side inset for a given width: 16 pt, or more so the panels stay at most AMMaxWidth wide.
static CGFloat AMInset(CGFloat width) {
    return MAX(AMSide, floor((width - AMMaxWidth) / 2));
}
static const CGFloat AMPad = 12;    // inside a panel
static const CGFloat AMDotColumn = 16;  // titles start after the status dot's column, on or off
static const CGFloat AMRadius = 12;
static const CGFloat AMCardAlpha = 0.6;  // the box lets the drifting lights through a little

typedef NS_ENUM(NSInteger, AMPiece) { AMPieceTop, AMPieceMiddle, AMPieceBottom, AMPieceWhole };

@interface AMPanelPiece : UIView
- (void)flash:(UIColor *)color;
@property (nonatomic) AMPiece piece;
@property (nonatomic) BOOL rule;  // a hairline at the top: under the header, between rows
@end

@implementation AMPanelPiece {
    CAShapeLayer *_fill, *_edge, *_hair;
}

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectZero])) {
        self.userInteractionEnabled = NO;
        _fill = [CAShapeLayer layer];
        _edge = [CAShapeLayer layer];
        _edge.fillColor = UIColor.clearColor.CGColor;
        _edge.lineWidth = 1;
        _hair = [CAShapeLayer layer];
        _hair.lineWidth = 1 / UIScreen.mainScreen.scale;
        for (CALayer *l in @[_fill, _hair, _edge]) [self.layer addSublayer:l];
        [self applyColors];
    }
    return self;
}

- (void)setPiece:(AMPiece)piece { _piece = piece; [self setNeedsLayout]; }
- (void)setRule:(BOOL)rule { _rule = rule; [self setNeedsLayout]; }

- (void)applyColors {
    _fill.fillColor = [AMResolved(AM_SURFACE, self.traitCollection) colorWithAlphaComponent:AMCardAlpha].CGColor;
    _edge.strokeColor = AMResolved(AM_LINE, self.traitCollection).CGColor;
    _hair.strokeColor = [AMResolved(AM_FG, self.traitCollection) colorWithAlphaComponent:0.07].CGColor;  // barely there
}

// A pick's confirmation: the slice glows in `color`, then fades back to `surface` over 1.4 s.
- (void)flash:(UIColor *)color {
    UIColor *base = [AMResolved(AM_SURFACE, self.traitCollection) colorWithAlphaComponent:AMCardAlpha];
    UIColor *tint = [AMResolved(color, self.traitCollection) colorWithAlphaComponent:0.22];
    CABasicAnimation *glow = [CABasicAnimation animationWithKeyPath:@"fillColor"];
    glow.fromValue = (id)tint.CGColor;
    glow.toValue = (id)base.CGColor;
    glow.duration = 1.4;
    glow.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseIn];
    [_fill addAnimation:glow forKey:@"flash"];
}

- (void)traitCollectionDidChange:(UITraitCollection *)previous {
    [super traitCollectionDidChange:previous];
    [self applyColors];
}

- (void)layoutSubviews {
    [super layoutSubviews];
    BOOL top = self.piece == AMPieceTop || self.piece == AMPieceWhole;
    BOOL bottom = self.piece == AMPieceBottom || self.piece == AMPieceWhole;
    CGRect b = self.bounds;
    CGFloat side = AMInset(b.size.width);
    CGFloat x0 = side + 0.5, x1 = b.size.width - side - 0.5, R = AMRadius;
    CGFloat y0 = top ? 0.5 : 0, y1 = bottom ? b.size.height - 0.5 : b.size.height;
    UIRectCorner corners = (top ? UIRectCornerTopLeft | UIRectCornerTopRight : 0) | (bottom ? UIRectCornerBottomLeft | UIRectCornerBottomRight : 0);
    _fill.path = [UIBezierPath bezierPathWithRoundedRect:CGRectMake(x0, y0, x1 - x0, y1 - y0) byRoundingCorners:corners
                                             cornerRadii:CGSizeMake(R, R)].CGPath;
    UIBezierPath *e = [UIBezierPath bezierPath];
    if (bottom) {  // right side's lower end, round the bottom, up the left side
        [e moveToPoint:CGPointMake(x1, y1 - R)];
        [e addArcWithCenter:CGPointMake(x1 - R, y1 - R) radius:R startAngle:0 endAngle:M_PI_2 clockwise:YES];
        [e addLineToPoint:CGPointMake(x0 + R, y1)];
        [e addArcWithCenter:CGPointMake(x0 + R, y1 - R) radius:R startAngle:M_PI_2 endAngle:M_PI clockwise:YES];
    } else {
        [e moveToPoint:CGPointMake(x0, y1)];
    }
    [e addLineToPoint:CGPointMake(x0, top ? y0 + R : y0)];
    if (top) {  // round the top, then down the right side
        [e addArcWithCenter:CGPointMake(x0 + R, y0 + R) radius:R startAngle:M_PI endAngle:3 * M_PI_2 clockwise:YES];
        [e addLineToPoint:CGPointMake(x1 - R, y0)];
        [e addArcWithCenter:CGPointMake(x1 - R, y0 + R) radius:R startAngle:3 * M_PI_2 endAngle:2 * M_PI clockwise:YES];
    } else {
        [e moveToPoint:CGPointMake(x1, y0)];
    }
    [e addLineToPoint:CGPointMake(x1, bottom ? y1 - R : y1)];
    _edge.path = e.CGPath;
    UIBezierPath *h = [UIBezierPath bezierPath];
    if (self.rule && !top) {  // edge to edge inside the box
        [h moveToPoint:CGPointMake(x0 + 0.5, 0.25)];
        [h addLineToPoint:CGPointMake(x1 - 0.5, 0.25)];
    }
    _hair.path = h.CGPath;
}

@end

#pragma mark - Search

// The only thing above the panels: a search field (long menus), edged in `line-strong` like the store's fields.
static UITextField *AMSearchField(NSString *placeholder) {
    UITextField *field = [UITextField new];
    field.backgroundColor = AM_SURFACE;
    field.textColor = AM_FG;
    field.tintColor = AM_ACCENT_TEXT;
    field.font = [UIFont systemFontOfSize:15];
    field.layer.cornerRadius = 10;
    field.layer.borderWidth = 1;
    field.layer.borderColor = AMResolved(AM_LINE_STRONG, UITraitCollection.currentTraitCollection).CGColor;
    field.clearButtonMode = UITextFieldViewModeWhileEditing;
    field.returnKeyType = UIReturnKeyDone;
    field.autocorrectionType = UITextAutocorrectionTypeNo;
    field.attributedPlaceholder = [[NSAttributedString alloc]
        initWithString:placeholder attributes:@{NSForegroundColorAttributeName: AM_MUTED}];
    UIImageView *glass = [[UIImageView alloc] initWithImage:[UIImage systemImageNamed:@"magnifyingglass"]];
    glass.tintColor = AM_MUTED;
    glass.contentMode = UIViewContentModeCenter;
    glass.frame = CGRectMake(0, 0, 36, 36);
    field.leftView = glass;
    field.leftViewMode = UITextFieldViewModeAlways;
    return field;
}

// The search field's frame: its own width (the sheet's, never the screen's), and a live edge: violet into cyan
// with a light travelling around it every 6 s (a conic gradient turning under a stroke-shaped mask; Core Animation,
// no timers; still with Reduce Motion or Low Power Mode).
@interface AMSearchHeader : UIView
@property (nonatomic, strong) UITextField *field;
@end

@implementation AMSearchHeader {
    CALayer *_edge;
    CAGradientLayer *_spin;
    CAShapeLayer *_mask;
}

- (instancetype)initWithField:(UITextField *)field {
    if ((self = [super initWithFrame:CGRectMake(0, 0, 320, 50)])) {
        _field = field;
        field.layer.borderWidth = 0;
        [self addSubview:field];
        _edge = [CALayer layer];
        _spin = [CAGradientLayer layer];
        _spin.type = kCAGradientLayerConic;
        _spin.startPoint = CGPointMake(0.5, 0.5);
        _spin.endPoint = CGPointMake(0.5, 0);
        [_edge addSublayer:_spin];
        _mask = [CAShapeLayer layer];
        _mask.fillColor = UIColor.clearColor.CGColor;
        _mask.strokeColor = UIColor.blackColor.CGColor;
        _mask.lineWidth = 1.5;
        _edge.mask = _mask;
        [self.layer addSublayer:_edge];
        [self applyColors];
    }
    return self;
}

- (void)applyColors {
    UITraitCollection *t = self.traitCollection;
    UIColor *a = AMResolved(AM_ACCENT, t), *b = AMResolved(AM_ACCENT2, t);
    UIColor *glint = [b colorWithAlphaComponent:1];
    CGFloat h, sat, br, al;
    if ([b getHue:&h saturation:&sat brightness:&br alpha:&al]) glint = [UIColor colorWithHue:h saturation:sat * 0.3 brightness:1 alpha:1];
    _spin.colors = @[(id)a.CGColor, (id)b.CGColor, (id)glint.CGColor, (id)b.CGColor, (id)a.CGColor, (id)a.CGColor];
    _spin.locations = @[@0, @0.18, @0.25, @0.32, @0.5, @1];
}

- (void)traitCollectionDidChange:(UITraitCollection *)previous {
    [super traitCollectionDidChange:previous];
    [self applyColors];
}

- (void)layoutSubviews {
    [super layoutSubviews];
    CGFloat side = AMInset(self.bounds.size.width);
    CGRect r = CGRectMake(side, 10, self.bounds.size.width - 2 * side, 36);
    self.field.frame = r;
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    _edge.frame = CGRectInset(r, -1, -1);
    CGRect local = CGRectInset(_edge.bounds, 0.75, 0.75);
    _mask.frame = _edge.bounds;
    _mask.path = [UIBezierPath bezierPathWithRoundedRect:local cornerRadius:10.75].CGPath;
    CGFloat d = hypot(_edge.bounds.size.width, _edge.bounds.size.height);
    _spin.bounds = CGRectMake(0, 0, d, d);
    _spin.position = CGPointMake(CGRectGetMidX(_edge.bounds), CGRectGetMidY(_edge.bounds));
    [CATransaction commit];
    if (AMStill()) { [_spin removeAllAnimations]; return; }
    if ([_spin animationForKey:@"spin"]) return;
    CABasicAnimation *spin = [CABasicAnimation animationWithKeyPath:@"transform.rotation.z"];
    spin.fromValue = @0;
    spin.toValue = @(2 * M_PI);
    spin.duration = 6;
    spin.repeatCount = HUGE_VALF;
    [_spin addAnimation:spin forKey:@"spin"];
}

- (void)didMoveToWindow {
    [super didMoveToWindow];
    [self setNeedsLayout];  // iOS drops layer animations off screen: start it again
}

@end

// A category's moving edge: the light that runs around the search field, around the category's whole box (violet
// into cyan with a bright glint, the rest of the outline in `line`), once every 6 s. The rows each draw their own
// slice of the box, so this sits over them as one view per open category (AMCardEdges places them). Core Animation
// only; still with Reduce Motion or Low Power Mode.
@interface AMCardEdge : UIView
@end

@implementation AMCardEdge {
    CAGradientLayer *_spin;
    CAShapeLayer *_mask;
@public
    CGFloat _radius;  // 0: the category box's corners; the Log in capsule sets half its height
}

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectZero])) {
        self.userInteractionEnabled = NO;
        self.layer.zPosition = 5;  // over the rows' own outline
        _spin = [CAGradientLayer layer];
        _spin.type = kCAGradientLayerConic;
        _spin.startPoint = CGPointMake(0.5, 0.5);
        _spin.endPoint = CGPointMake(0.5, 0);
        [self.layer addSublayer:_spin];
        _mask = [CAShapeLayer layer];
        _mask.fillColor = UIColor.clearColor.CGColor;
        _mask.strokeColor = UIColor.blackColor.CGColor;
        _mask.lineWidth = 1;
        self.layer.mask = _mask;
        [self applyColors];
    }
    return self;
}

- (void)applyColors {
    UITraitCollection *t = self.traitCollection;
    UIColor *line = AMResolved(AM_LINE, t), *a = AMResolved(AM_ACCENT, t), *b = AMResolved(AM_ACCENT2, t);
    UIColor *glint = b;
    CGFloat h, sat, br, al;
    if ([b getHue:&h saturation:&sat brightness:&br alpha:&al]) glint = [UIColor colorWithHue:h saturation:sat * 0.3 brightness:1 alpha:1];
    _spin.colors = @[(id)line.CGColor, (id)a.CGColor, (id)b.CGColor, (id)glint.CGColor, (id)b.CGColor, (id)line.CGColor, (id)line.CGColor];
    _spin.locations = @[@0, @0.11, @0.22, @0.26, @0.31, @0.44, @1];
}

- (void)traitCollectionDidChange:(UITraitCollection *)previous {
    [super traitCollectionDidChange:previous];
    [self applyColors];
}

- (void)layoutSubviews {
    [super layoutSubviews];
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    _mask.frame = self.bounds;
    _mask.path = [UIBezierPath bezierPathWithRoundedRect:CGRectInset(self.bounds, 0.5, 0.5) cornerRadius:_radius ?: AMRadius].CGPath;
    CGFloat d = hypot(self.bounds.size.width, self.bounds.size.height);
    _spin.bounds = CGRectMake(0, 0, d, d);
    _spin.position = CGPointMake(CGRectGetMidX(self.bounds), CGRectGetMidY(self.bounds));
    [CATransaction commit];
    if (AMStill()) { [_spin removeAllAnimations]; return; }
    if ([_spin animationForKey:@"spin"]) return;
    CABasicAnimation *spin = [CABasicAnimation animationWithKeyPath:@"transform.rotation.z"];
    spin.fromValue = @0;
    spin.toValue = @(2 * M_PI);
    spin.duration = 6;
    spin.repeatCount = HUGE_VALF;
    [_spin addAnimation:spin forKey:@"spin"];
}

- (void)didMoveToWindow {
    [super didMoveToWindow];
    [self setNeedsLayout];  // iOS drops layer animations off screen: start it again
}

@end

// Keeps one AMCardEdge around each open category's rows in a table, following its content as rows come, go,
// grow (a status line) and fold.
@interface AMCardEdges : NSObject
- (instancetype)initWithTable:(UITableView *)table;
- (void)layout;
@end

@implementation AMCardEdges {
    UITableView *_table;
    NSMutableArray<AMCardEdge *> *_edges;
    BOOL _queued;
}

- (instancetype)initWithTable:(UITableView *)table {
    if ((self = [super init])) {
        _table = table;
        _edges = [NSMutableArray new];
        [table addObserver:self forKeyPath:@"contentSize" options:0 context:NULL];
    }
    return self;
}

- (void)dealloc {
    [_table removeObserver:self forKeyPath:@"contentSize"];
}

- (void)observeValueForKeyPath:(NSString *)keyPath ofObject:(id)object change:(NSDictionary *)change context:(void *)context {
    if (_queued) return;
    _queued = YES;
    dispatch_async(dispatch_get_main_queue(), ^{ self->_queued = NO; [self layout]; });
}

- (void)layout {
    UITableView *table = _table;
    CGFloat width = table.bounds.size.width, side = AMInset(width);
    NSUInteger used = 0;
    for (NSInteger s = 0; s < table.numberOfSections; s++) {
        NSInteger rows = [table numberOfRowsInSection:s];
        if (!rows) continue;  // a folded category is just its label
        CGRect first = [table rectForRowAtIndexPath:[NSIndexPath indexPathForRow:0 inSection:s]];
        CGRect last = [table rectForRowAtIndexPath:[NSIndexPath indexPathForRow:rows - 1 inSection:s]];
        if (used == _edges.count) [_edges addObject:[AMCardEdge new]];
        AMCardEdge *edge = _edges[used++];
        if (edge.superview != table) [table addSubview:edge];
        CGRect frame = CGRectMake(side, CGRectGetMinY(first), width - 2 * side, CGRectGetMaxY(last) - CGRectGetMinY(first));
        if (!CGRectEqualToRect(edge.frame, frame)) edge.frame = frame;
    }
    while (_edges.count > used) {
        [_edges.lastObject removeFromSuperview];
        [_edges removeLastObject];
    }
}

@end

// The one motif, everywhere the same: a hairline in violet into cyan with a soft light travelling along it
// every 6 s. Under the title bar here; the search edge and the idle floating button use the same colours.
@interface AMFlowLine : UIView
@end

@implementation AMFlowLine {
    CAGradientLayer *_g;
}

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectZero])) {
        self.userInteractionEnabled = NO;
        self.clipsToBounds = YES;
        _g = [CAGradientLayer layer];
        _g.startPoint = CGPointMake(0, 0.5);
        _g.endPoint = CGPointMake(1, 0.5);
        [self.layer addSublayer:_g];
        [self applyColors];
    }
    return self;
}

- (void)applyColors {
    UIColor *a = AMResolved(AM_ACCENT, self.traitCollection), *b = AMResolved(AM_ACCENT2, self.traitCollection);
    UIColor *glint = b;
    CGFloat h, sat, br, al;
    if ([b getHue:&h saturation:&sat brightness:&br alpha:&al]) glint = [UIColor colorWithHue:h saturation:sat * 0.3 brightness:1 alpha:1];
    // three widths of gradient: violet, cyan, the light, cyan, violet; it slides one width per loop
    _g.colors = @[(id)a.CGColor, (id)b.CGColor, (id)glint.CGColor, (id)b.CGColor, (id)a.CGColor, (id)b.CGColor, (id)a.CGColor];
}

- (void)traitCollectionDidChange:(UITraitCollection *)previous {
    [super traitCollectionDidChange:previous];
    [self applyColors];
}

- (void)layoutSubviews {
    [super layoutSubviews];
    CGFloat w = self.bounds.size.width;
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    _g.frame = CGRectMake(-2 * w, 0, 3 * w, self.bounds.size.height);
    [CATransaction commit];
    [_g removeAllAnimations];
    if (AMStill() || w == 0) return;
    CABasicAnimation *flow = [CABasicAnimation animationWithKeyPath:@"position.x"];
    flow.fromValue = @(-0.5 * w);
    flow.toValue = @(1.5 * w);
    flow.duration = 6;
    flow.repeatCount = HUGE_VALF;
    [_g addAnimation:flow forKey:@"flow"];
}

- (void)didMoveToWindow {
    [super didMoveToWindow];
    [self setNeedsLayout];
}

@end

// Accretion's mark, small and alive: a bright star inside two tilted rings (violet into pink, as on the app
// icon), a light running round the rings and the star breathing. Core Animation only; still with Reduce Motion.
@interface AMLogo : UIView
@end

@implementation AMLogo {
    CAShapeLayer *_rings[2], *_glints[2];
    CALayer *_star;
}

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectMake(0, 0, 22, 22)])) {
        self.userInteractionEnabled = NO;
        self.isAccessibilityElement = NO;
        UIColor *colors[2] = {[UIColor colorWithRed:0.55 green:0.36 blue:0.96 alpha:1], [UIColor colorWithRed:0.93 green:0.36 blue:0.62 alpha:1]};
        CGFloat tilts[2] = {-0.45, 0.35};
        for (int i = 0; i < 2; i++) {
            CAShapeLayer *ring = [CAShapeLayer layer];
            ring.frame = self.bounds;
            ring.path = [UIBezierPath bezierPathWithOvalInRect:CGRectMake(1, 7 - i, 20, 8 + 2 * i)].CGPath;
            ring.fillColor = UIColor.clearColor.CGColor;
            ring.strokeColor = [colors[i] colorWithAlphaComponent:0.85].CGColor;
            ring.lineWidth = 1.4;
            ring.affineTransform = CGAffineTransformMakeRotation(tilts[i]);
            [self.layer addSublayer:ring];
            _rings[i] = ring;
            CAShapeLayer *glint = [CAShapeLayer layer];  // the light that runs round the ring
            glint.frame = self.bounds;
            glint.path = ring.path;
            glint.fillColor = UIColor.clearColor.CGColor;
            glint.strokeColor = [UIColor colorWithRed:1 green:0.95 blue:0.86 alpha:1].CGColor;
            glint.lineWidth = 1.6;
            glint.lineCap = kCALineCapRound;
            glint.strokeStart = 0;
            glint.strokeEnd = 0.12;
            glint.affineTransform = ring.affineTransform;
            [self.layer addSublayer:glint];
            _glints[i] = glint;
        }
        _star = [CALayer layer];
        _star.frame = CGRectMake(8, 8, 6, 6);
        _star.cornerRadius = 3;
        _star.backgroundColor = [UIColor colorWithRed:1 green:0.97 blue:0.9 alpha:1].CGColor;
        _star.shadowColor = [UIColor colorWithRed:1 green:0.85 blue:0.7 alpha:1].CGColor;
        _star.shadowOffset = CGSizeZero;
        _star.shadowRadius = 3;
        _star.shadowOpacity = 0.9;
        [self.layer addSublayer:_star];
    }
    return self;
}

- (CGSize)intrinsicContentSize { return CGSizeMake(22, 22); }

- (void)didMoveToWindow {
    [super didMoveToWindow];
    for (int i = 0; i < 2; i++) [_glints[i] removeAllAnimations];
    [_star removeAllAnimations];
    if (!self.window || AMStill()) { for (int i = 0; i < 2; i++) _glints[i].hidden = YES; return; }
    for (int i = 0; i < 2; i++) {
        _glints[i].hidden = NO;
        CABasicAnimation *start = [CABasicAnimation animationWithKeyPath:@"strokeStart"];
        start.fromValue = @0;
        start.toValue = @0.88;
        CABasicAnimation *end = [CABasicAnimation animationWithKeyPath:@"strokeEnd"];
        end.fromValue = @0.12;
        end.toValue = @1;
        CAAnimationGroup *run = [CAAnimationGroup animation];
        run.animations = @[start, end];
        run.duration = i ? 4.4 : 3.2;
        run.repeatCount = HUGE_VALF;
        [_glints[i] addAnimation:run forKey:@"run"];
    }
    CABasicAnimation *breathe = [CABasicAnimation animationWithKeyPath:@"shadowRadius"];
    breathe.fromValue = @2;
    breathe.toValue = @5;
    breathe.duration = 1.2;
    breathe.autoreverses = YES;
    breathe.repeatCount = HUGE_VALF;
    [_star addAnimation:breathe forKey:@"breathe"];
}

@end

#pragma mark - Rows

// Every row sits in its category's panel: a slice of the border as its background, content inside the panel's padding.
@interface AMRowCell : UITableViewCell
- (void)setPiece:(AMPiece)piece rule:(BOOL)rule;
@end

@implementation AMRowCell

- (instancetype)initWithStyle:(UITableViewCellStyle)style reuseIdentifier:(NSString *)reuseIdentifier {
    if ((self = [super initWithStyle:UITableViewCellStyleDefault reuseIdentifier:reuseIdentifier])) {
        self.backgroundColor = UIColor.clearColor;
        self.selectionStyle = UITableViewCellSelectionStyleNone;
        self.backgroundView = [AMPanelPiece new];
        self.contentView.preservesSuperviewLayoutMargins = NO;
        self.contentView.insetsLayoutMarginsFromSafeArea = NO;
        self.contentView.layoutMargins = UIEdgeInsetsMake(5, AMSide + AMPad, 5, AMSide + AMPad);
    }
    return self;
}

// the content follows the panel's inset (wider in landscape, see AMInset)
- (void)layoutSubviews {
    CGFloat side = AMInset(self.bounds.size.width) + AMPad;
    UIEdgeInsets m = self.contentView.layoutMargins;
    if (fabs(m.left - side) > 0.5) self.contentView.layoutMargins = UIEdgeInsetsMake(m.top, side, m.bottom, side);
    [super layoutSubviews];
}

- (void)setPiece:(AMPiece)piece rule:(BOOL)rule {
    AMPanelPiece *bg = (AMPanelPiece *)self.backgroundView;
    bg.piece = piece;
    bg.rule = rule;
}

@end

// A switch: the status dot (while on), the title, the line toggle; under the title, in `bad`, what to do while it
// is on and not working ("Start a fight to see it work").
@interface AMSwitchCell : AMRowCell
@property (nonatomic, strong) AMStatusDot *dot;
@property (nonatomic, strong) UILabel *title, *detail;
@property (nonatomic, strong) AMLineToggle *toggle;
@property (nonatomic, copy, nullable) NSString *reason;  // set while the switch is on and not working
- (void)showToggle:(BOOL)show;
@end

@implementation AMSwitchCell {
    NSArray<NSLayoutConstraint *> *_byToggle, *_byEdge;  // where the text ends: before the toggle, or at the edge
}

- (instancetype)initWithStyle:(UITableViewCellStyle)style reuseIdentifier:(NSString *)reuseIdentifier {
    if ((self = [super initWithStyle:style reuseIdentifier:reuseIdentifier])) {
        _dot = [AMStatusDot new];
        _title = [UILabel new];
        _title.font = [UIFont systemFontOfSize:13];
        _title.textColor = AM_FG;
        _title.numberOfLines = 0;
        _detail = [UILabel new];
        _detail.font = [UIFont systemFontOfSize:11];
        _detail.textColor = AM_BAD;
        _detail.numberOfLines = 0;
        for (UILabel *l in @[_title, _detail]) {  // long titles wrap; they never push the toggle out
            [l setContentCompressionResistancePriority:UILayoutPriorityDefaultLow forAxis:UILayoutConstraintAxisHorizontal];
        }
        _toggle = [AMLineToggle new];
        for (UIView *v in @[_dot, _title, _detail, _toggle]) {
            v.translatesAutoresizingMaskIntoConstraints = NO;
            [self.contentView addSubview:v];
        }
        UILayoutGuide *m = self.contentView.layoutMarginsGuide;
        [NSLayoutConstraint activateConstraints:@[
            [_dot.widthAnchor constraintEqualToConstant:8],
            [_dot.heightAnchor constraintEqualToConstant:8],
            [_dot.leadingAnchor constraintEqualToAnchor:m.leadingAnchor],
            [_dot.centerYAnchor constraintEqualToAnchor:_title.firstBaselineAnchor constant:-4.5],
            [_title.topAnchor constraintEqualToAnchor:m.topAnchor constant:2],
            [_title.leadingAnchor constraintEqualToAnchor:m.leadingAnchor constant:AMDotColumn],
            [_detail.topAnchor constraintEqualToAnchor:_title.bottomAnchor],
            [_detail.leadingAnchor constraintEqualToAnchor:_title.leadingAnchor],
            [_detail.bottomAnchor constraintEqualToAnchor:m.bottomAnchor constant:-2],
            [_toggle.widthAnchor constraintEqualToConstant:34],
            [_toggle.heightAnchor constraintEqualToConstant:20],
            [_toggle.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [_toggle.centerYAnchor constraintEqualToAnchor:self.contentView.centerYAnchor],
            [self.contentView.heightAnchor constraintGreaterThanOrEqualToConstant:30],
        ]];
        _byToggle = @[[_title.trailingAnchor constraintLessThanOrEqualToAnchor:_toggle.leadingAnchor constant:-12],
                      [_detail.trailingAnchor constraintLessThanOrEqualToAnchor:_toggle.leadingAnchor constant:-12]];
        _byEdge = @[[_title.trailingAnchor constraintLessThanOrEqualToAnchor:m.trailingAnchor],
                    [_detail.trailingAnchor constraintLessThanOrEqualToAnchor:m.trailingAnchor]];
        [NSLayoutConstraint activateConstraints:_byToggle];
        self.isAccessibilityElement = YES;
        self.accessibilityTraits = UIAccessibilityTraitButton;
    }
    return self;
}

- (void)showToggle:(BOOL)show {
    if (self.toggle.hidden == !show) return;
    self.toggle.hidden = !show;
    [NSLayoutConstraint deactivateConstraints:show ? _byEdge : _byToggle];
    [NSLayoutConstraint activateConstraints:show ? _byToggle : _byEdge];
}

- (NSString *)accessibilityLabel { return self.title.text; }

- (NSString *)accessibilityValue {
    if (!self.toggle.on) return @"Off";
    return self.reason.length ? [@"On, not working: " stringByAppendingString:self.reason] : @"On";
}

@end

// A slider: the title on its own line (never squeezed), the value in mono on the right (accent while on) and the
// line toggle after it, the game's own value under the title, the slider full width below (dimmed while off).
// Tapping the row turns it off and on; dragging turns it on.
@interface AMSliderCell : AMRowCell
@property (nonatomic, strong) UILabel *title, *value, *note;
@property (nonatomic, strong) UISlider *slider;
@property (nonatomic, strong) AMLineToggle *toggle;
- (void)showToggle:(BOOL)show;
@end

@implementation AMSliderCell {
    NSLayoutConstraint *_byToggle, *_byEdge;  // where the value ends: before the toggle, or at the edge
}

- (void)showToggle:(BOOL)show {
    if (self.toggle.hidden == !show) return;
    self.toggle.hidden = !show;
    (show ? _byEdge : _byToggle).active = NO;
    (show ? _byToggle : _byEdge).active = YES;
}


- (instancetype)initWithStyle:(UITableViewCellStyle)style reuseIdentifier:(NSString *)reuseIdentifier {
    if ((self = [super initWithStyle:style reuseIdentifier:reuseIdentifier])) {
        _title = [UILabel new];
        _title.font = [UIFont systemFontOfSize:13];
        _title.textColor = AM_FG;
        _title.numberOfLines = 0;
        _value = [UILabel new];
        _value.font = AMMono(12.5, UIFontWeightSemibold);
        _value.textAlignment = NSTextAlignmentRight;
        [_value setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];
        [_value setContentCompressionResistancePriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];
        _note = [UILabel new];
        _note.font = AMMono(11, UIFontWeightRegular);
        _note.textColor = AM_MUTED;
        _slider = [UISlider new];
        _slider.minimumTrackTintColor = AM_ACCENT;
        _slider.maximumTrackTintColor = AM_LINE;  // `raised` vanished on the panel's surface
        _toggle = [AMLineToggle new];
        for (UIView *v in @[_title, _value, _note, _slider, _toggle]) {
            v.translatesAutoresizingMaskIntoConstraints = NO;
            [self.contentView addSubview:v];
        }
        UILayoutGuide *m = self.contentView.layoutMarginsGuide;
        _byToggle = [_value.trailingAnchor constraintEqualToAnchor:_toggle.leadingAnchor constant:-10];
        _byEdge = [_value.trailingAnchor constraintEqualToAnchor:m.trailingAnchor];
        [NSLayoutConstraint activateConstraints:@[
            [_title.topAnchor constraintEqualToAnchor:m.topAnchor constant:2],
            [_title.leadingAnchor constraintEqualToAnchor:m.leadingAnchor constant:AMDotColumn],
            [_title.trailingAnchor constraintLessThanOrEqualToAnchor:_value.leadingAnchor constant:-12],
            _byToggle,
            [_toggle.widthAnchor constraintEqualToConstant:34],
            [_toggle.heightAnchor constraintEqualToConstant:20],
            [_toggle.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [_toggle.centerYAnchor constraintEqualToAnchor:_value.centerYAnchor],
            [_value.firstBaselineAnchor constraintEqualToAnchor:_title.firstBaselineAnchor],
            [_note.topAnchor constraintEqualToAnchor:_title.bottomAnchor constant:2],
            [_note.leadingAnchor constraintEqualToAnchor:_title.leadingAnchor],
            [_note.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [_slider.topAnchor constraintEqualToAnchor:_note.bottomAnchor constant:2],
            [_slider.leadingAnchor constraintEqualToAnchor:_title.leadingAnchor],
            [_slider.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [_slider.bottomAnchor constraintEqualToAnchor:m.bottomAnchor],
        ]];
    }
    return self;
}

@end

// A row that opens a list page: its symbol, the title, a chevron.
@interface AMListCell : AMRowCell
@property (nonatomic, strong) UIImageView *icon;
@property (nonatomic, strong) UILabel *title;
@end

@implementation AMListCell

- (instancetype)initWithStyle:(UITableViewCellStyle)style reuseIdentifier:(NSString *)reuseIdentifier {
    if ((self = [super initWithStyle:style reuseIdentifier:reuseIdentifier])) {
        _icon = [UIImageView new];
        _icon.tintColor = AM_ACCENT_TEXT;
        _icon.contentMode = UIViewContentModeScaleAspectFit;
        _title = [UILabel new];
        _title.font = [UIFont systemFontOfSize:13];
        _title.textColor = AM_FG;
        _title.numberOfLines = 0;
        UILabel *chevron = [UILabel new];
        chevron.text = @"›";
        chevron.font = [UIFont systemFontOfSize:20 weight:UIFontWeightLight];
        chevron.textColor = AM_MUTED;
        for (UIView *v in @[_icon, _title, chevron]) {
            v.translatesAutoresizingMaskIntoConstraints = NO;
            [self.contentView addSubview:v];
        }
        UILayoutGuide *m = self.contentView.layoutMarginsGuide;
        [NSLayoutConstraint activateConstraints:@[
            [_icon.leadingAnchor constraintEqualToAnchor:m.leadingAnchor],
            [_icon.centerYAnchor constraintEqualToAnchor:self.contentView.centerYAnchor],
            [_icon.widthAnchor constraintEqualToConstant:18],
            [_icon.heightAnchor constraintEqualToConstant:18],
            [_title.leadingAnchor constraintEqualToAnchor:_icon.trailingAnchor constant:10],
            [_title.topAnchor constraintEqualToAnchor:m.topAnchor],
            [_title.bottomAnchor constraintEqualToAnchor:m.bottomAnchor],
            [_title.trailingAnchor constraintLessThanOrEqualToAnchor:chevron.leadingAnchor constant:-12],
            [chevron.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [chevron.centerYAnchor constraintEqualToAnchor:self.contentView.centerYAnchor],
        ]];
        self.selectionStyle = UITableViewCellSelectionStyleNone;
    }
    return self;
}

@end

// An action row (Cloud saves): the title, and at the right what it will do or did ("›", "✓ Backed up · 2 MB").
@interface AMActionCell : AMRowCell
@property (nonatomic, strong) UILabel *title, *right;
@end

@implementation AMActionCell

- (instancetype)initWithStyle:(UITableViewCellStyle)style reuseIdentifier:(NSString *)reuseIdentifier {
    if ((self = [super initWithStyle:style reuseIdentifier:reuseIdentifier])) {
        _title = [UILabel new];
        _title.font = [UIFont systemFontOfSize:13];
        _title.numberOfLines = 0;
        [_title setContentCompressionResistancePriority:UILayoutPriorityDefaultLow forAxis:UILayoutConstraintAxisHorizontal];
        _right = [UILabel new];
        _right.font = [UIFont systemFontOfSize:12 weight:UIFontWeightSemibold];
        _right.textAlignment = NSTextAlignmentRight;
        _right.numberOfLines = 2;
        for (UIView *v in @[_title, _right]) {
            v.translatesAutoresizingMaskIntoConstraints = NO;
            [self.contentView addSubview:v];
        }
        UILayoutGuide *m = self.contentView.layoutMarginsGuide;
        [NSLayoutConstraint activateConstraints:@[
            [_title.leadingAnchor constraintEqualToAnchor:m.leadingAnchor constant:AMDotColumn],
            [_title.topAnchor constraintEqualToAnchor:m.topAnchor constant:2],
            [_title.bottomAnchor constraintEqualToAnchor:m.bottomAnchor constant:-2],
            [_title.trailingAnchor constraintLessThanOrEqualToAnchor:_right.leadingAnchor constant:-10],
            [_right.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [_right.centerYAnchor constraintEqualToAnchor:self.contentView.centerYAnchor],
            [_right.widthAnchor constraintLessThanOrEqualToAnchor:self.contentView.widthAnchor multiplier:0.55],
            [self.contentView.heightAnchor constraintGreaterThanOrEqualToConstant:30],
        ]];
        self.isAccessibilityElement = YES;
        self.accessibilityTraits = UIAccessibilityTraitButton;
    }
    return self;
}

- (NSString *)accessibilityLabel { return self.title.text; }
- (NSString *)accessibilityValue { return self.right.text; }

@end

// The Log in capsule under the title (or, once linked, who is signed in): opens Accretion's sign-in in a browser
// inside the game; the moving cyan edge runs around it like around the category boxes.
@interface AMLoginBar : UIView
@property (nonatomic, strong) UIButton *button;
@property (nonatomic, strong) UILabel *who;
- (void)show:(nullable NSString *)account busy:(BOOL)busy;
@end

@implementation AMLoginBar {
    AMCardEdge *_edge;
}

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectMake(0, 0, 320, 52)])) {
        _button = [UIButton buttonWithType:UIButtonTypeSystem];
        _button.layer.cornerRadius = 17;
        _button.backgroundColor = [AM_SURFACE colorWithAlphaComponent:AMCardAlpha];
        _button.accessibilityHint = @"Opens Accretion's sign-in to back up your saves";
        _edge = [AMCardEdge new];
        _edge->_radius = 17;
        _edge.layer.zPosition = 0;
        _who = [UILabel new];
        _who.textAlignment = NSTextAlignmentCenter;
        _who.font = AMMono(11, UIFontWeightRegular);
        _who.textColor = AM_MUTED;
        for (UIView *v in @[_button, _who]) [self addSubview:v];
        [_button addSubview:_edge];
        [self show:nil busy:NO];
    }
    return self;
}

- (void)show:(NSString *)account busy:(BOOL)busy {
    self.button.hidden = account.length > 0;
    self.who.hidden = account.length == 0;
    self.who.text = account.length ? [@"☁ Cloud saves · " stringByAppendingString:account] : nil;
    NSString *title = busy ? @"Opening sign-in…" : @"Log in  ·  back up your saves";
    [self.button setAttributedTitle:[[NSAttributedString alloc] initWithString:title attributes:@{
        NSFontAttributeName: [UIFont systemFontOfSize:14 weight:UIFontWeightSemibold], NSForegroundColorAttributeName: AM_FG}]
                           forState:UIControlStateNormal];
    self.button.enabled = !busy;
}

- (void)layoutSubviews {
    [super layoutSubviews];
    CGFloat w = MIN(self.bounds.size.width - 2 * AMSide, 300);
    self.button.frame = CGRectMake((self.bounds.size.width - w) / 2, 10, w, 34);
    _edge.frame = self.button.bounds;
    self.who.frame = CGRectMake(AMSide, 14, self.bounds.size.width - 2 * AMSide, 24);
}

@end

// A category's header: a line above its box, the label at the left, "N on" (and the count with ▾ / › when the
// menu folds) at the right, then Off in plain text while something in it is on. A folded category is that line.
@interface AMSectionHead : UITableViewHeaderFooterView
@property (nonatomic, strong) UILabel *label, *right;
@property (nonatomic, strong) UIControl *tap;
@property (nonatomic, strong) UIButton *off;  // turns the category's switches off; shown while one is on
@end

@implementation AMSectionHead

- (instancetype)initWithReuseIdentifier:(NSString *)reuseIdentifier {
    if ((self = [super initWithReuseIdentifier:reuseIdentifier])) {
        self.backgroundView = [UIView new];  // clear: no grey band behind the label
        _label = [UILabel new];
        _right = [UILabel new];
        _right.textAlignment = NSTextAlignmentRight;
        _tap = [UIControl new];
        _off = [UIButton buttonWithType:UIButtonTypeSystem];
        [_off setAttributedTitle:AMLabel(@"Off", AM_ACCENT_TEXT) forState:UIControlStateNormal];
        _off.contentHorizontalAlignment = UIControlContentHorizontalAlignmentRight;
        _off.accessibilityLabel = @"Turn this category off";
        for (UIView *v in @[_label, _right, _tap, _off]) [self.contentView addSubview:v];  // off above tap
    }
    return self;
}

- (void)layoutSubviews {
    [super layoutSubviews];
    CGFloat w = self.contentView.bounds.size.width, x = AMInset(w) + 4, inner = w - 2 * x;
    CGFloat y = self.contentView.bounds.size.height - 21;  // the line sits just above the box
    self.label.frame = CGRectMake(x, y, inner * 0.6, 16);
    self.right.frame = CGRectMake(x + inner * 0.4, y, inner * 0.6, 16);
    self.tap.frame = self.contentView.bounds;
    if (!self.off.hidden) {
        self.off.frame = CGRectMake(x + inner - 40, y - 6, 40, 28);
        CGRect r = self.right.frame;
        r.size.width -= 50;  // the count moves left of Off
        self.right.frame = r;
    }
}

@end

#pragma mark - Cloud saves

// Cloud saves: the game's save, backed up to the owner's Accretion account and restored from it (any device).
// A mod links once: Link asks the store's `saves` function for a one-time code, the owner approves it signed in at
// getaccretion.app/#/link (or in the Accretion app), and the menu gets a token for this game only (no password in
// the game). The token lives in Library/Accretion/cloud.json, which a backup never includes.
//
// A backup is a zip (stored, not compressed: the menu needs no library) of the game's saved preferences
// (prefs.plist, without the menu's own AM__ settings), Documents/ and Library/Application Support/ (Unity's
// persistentDataPath and friends), with accretion.json saying what it is. A restore keeps a backup of the current
// save first, stages the chosen one in Library/Accretion/restore/, and closes the game; on the next launch,
// before the game reads anything, AMCloudApplyRestore puts it in place.
static NSString *const AMCloudSection = @"Cloud saves";
static NSString *const AMCloudAPI = @"https://bbcjpinhvlxocjxlyplt.supabase.co/functions/v1/saves?op=";
static NSString *const AMCloudAutoKey = @"AM__cloudAuto";  // the Display-style setting "Back up when the menu opens"
static const NSUInteger AMCloudMaxBytes = 50 * 1024 * 1024;

static NSURL *AMCloudDir(void) {
    NSURL *lib = [NSFileManager.defaultManager URLsForDirectory:NSLibraryDirectory inDomains:NSUserDomainMask].firstObject;
    NSURL *dir = [lib URLByAppendingPathComponent:@"Accretion" isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:dir withIntermediateDirectories:YES attributes:nil error:nil];
    return dir;
}

static NSMutableDictionary *AMCloudState(void) {
    NSData *data = [NSData dataWithContentsOfURL:[AMCloudDir() URLByAppendingPathComponent:@"cloud.json"]];
    id json = data ? [NSJSONSerialization JSONObjectWithData:data options:NSJSONReadingMutableContainers error:nil] : nil;
    return [json isKindOfClass:NSMutableDictionary.class] ? json : [NSMutableDictionary new];
}

static void AMCloudSave(NSDictionary *state) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:state options:0 error:nil];
    [data writeToURL:[AMCloudDir() URLByAppendingPathComponent:@"cloud.json"] options:NSDataWritingAtomic error:nil];
}

static BOOL AMCloudLinked(void) { return [AMCloudState()[@"token"] length] > 0; }

// The game's id without a re-signing team suffix (io.x.game.ABCDE12345 -> io.x.game), so every device agrees.
static NSString *AMCloudBundle(void) {
    NSString *b = NSBundle.mainBundle.bundleIdentifier ?: @"";
    NSRange r = [b rangeOfString:@"\\.[A-Z0-9]{10}$" options:NSRegularExpressionSearch];
    return r.location != NSNotFound ? [b substringToIndex:r.location] : b;
}

static NSString *AMCloudGame(void) {
    NSDictionary *info = NSBundle.mainBundle.infoDictionary;
    return info[@"CFBundleDisplayName"] ?: info[@"CFBundleName"] ?: AMCloudBundle();
}

static NSString *AMCloudDevice(void) { return UIDevice.currentDevice.name ?: UIDevice.currentDevice.model; }

// "10.1-mod1.0.26" -> game 10.1, mod 1.0.26
static void AMCloudVersions(NSString **game, NSString **mod) {
    NSString *v = NSBundle.mainBundle.infoDictionary[@"CFBundleShortVersionString"] ?: @"";
    NSRange r = [v rangeOfString:@"-mod"];
    *game = r.location != NSNotFound ? [v substringToIndex:r.location] : v;
    *mod = r.location != NSNotFound ? [v substringFromIndex:NSMaxRange(r)] : @"";
}

static NSString *AMCloudSize(long long bytes) {
    return [NSByteCountFormatter stringFromByteCount:bytes countStyle:NSByteCountFormatterCountStyleFile];
}

// One call to the saves function: JSON in and out, on the main queue. err is a plain sentence.
static void AMCloudCall(NSString *op, NSDictionary *body, void (^done)(NSDictionary *json, NSString *err)) {
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[AMCloudAPI stringByAppendingString:op]]];
    req.HTTPMethod = @"POST";
    req.timeoutInterval = 30;
    [req setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    NSString *token = AMCloudState()[@"token"];
    if (token.length) [req setValue:token forHTTPHeaderField:@"x-save-token"];
    req.HTTPBody = [NSJSONSerialization dataWithJSONObject:body ?: @{} options:0 error:nil];
    [[NSURLSession.sharedSession dataTaskWithRequest:req completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        id json = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        NSInteger status = [(NSHTTPURLResponse *)response statusCode];
        NSString *err = error ? @"No connection to Accretion"
                      : ![json isKindOfClass:NSDictionary.class] ? @"Accretion did not answer as expected"
                      : json[@"error"] ? [json[@"error"] description]
                      : status >= 400 ? [NSString stringWithFormat:@"Accretion answered %ld", (long)status] : nil;
        if (status == 401) {  // unlinked from the site: forget the token here too
            NSMutableDictionary *state = AMCloudState();
            [state removeObjectForKey:@"token"];
            AMCloudSave(state);
        }
        dispatch_async(dispatch_get_main_queue(), ^{ done(err ? nil : json, err); });
    }] resume];
}

static void AMCloudTransfer(NSString *method, NSString *url, NSData *body, void (^done)(NSData *data, NSString *err)) {
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:url]];
    req.HTTPMethod = method;
    req.timeoutInterval = 120;
    if (body) [req setValue:@"application/zip" forHTTPHeaderField:@"Content-Type"];
    void (^finish)(NSData *, NSURLResponse *, NSError *) = ^(NSData *data, NSURLResponse *response, NSError *error) {
        NSInteger status = [(NSHTTPURLResponse *)response statusCode];
        NSString *err = error ? @"The transfer stopped: check the connection" : status >= 300 ? [NSString stringWithFormat:@"Storage answered %ld", (long)status] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{ done(err ? nil : data, err); });
    };
    if (body) [[NSURLSession.sharedSession uploadTaskWithRequest:req fromData:body completionHandler:finish] resume];
    else [[NSURLSession.sharedSession dataTaskWithRequest:req completionHandler:finish] resume];
}

#pragma mark Zip (stored entries only)

static uint32_t AMCrc32(const uint8_t *bytes, NSUInteger length) {
    static uint32_t table[256];
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
    });
    uint32_t c = 0xFFFFFFFFu;
    for (NSUInteger i = 0; i < length; i++) c = table[(c ^ bytes[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

static void AMPut16(NSMutableData *d, uint16_t v) { uint8_t b[2] = {v & 0xFF, v >> 8}; [d appendBytes:b length:2]; }
static void AMPut32(NSMutableData *d, uint32_t v) { uint8_t b[4] = {v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, v >> 24}; [d appendBytes:b length:4]; }
static uint16_t AMGet16(const uint8_t *p) { return p[0] | p[1] << 8; }
static uint32_t AMGet32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

// A zip of {path: data}, every entry stored (method 0), UTF-8 names.
static NSData *AMZip(NSDictionary<NSString *, NSData *> *files) {
    NSMutableData *out = [NSMutableData new], *central = [NSMutableData new];
    for (NSString *name in [files.allKeys sortedArrayUsingSelector:@selector(compare:)]) {
        NSData *data = files[name], *n = [name dataUsingEncoding:NSUTF8StringEncoding];
        uint32_t crc = AMCrc32(data.bytes, data.length), offset = (uint32_t)out.length;
        AMPut32(out, 0x04034b50); AMPut16(out, 20); AMPut16(out, 0x0800); AMPut16(out, 0); AMPut16(out, 0); AMPut16(out, 0);
        AMPut32(out, crc); AMPut32(out, (uint32_t)data.length); AMPut32(out, (uint32_t)data.length);
        AMPut16(out, (uint16_t)n.length); AMPut16(out, 0);
        [out appendData:n]; [out appendData:data];
        AMPut32(central, 0x02014b50); AMPut16(central, 20); AMPut16(central, 20); AMPut16(central, 0x0800); AMPut16(central, 0);
        AMPut16(central, 0); AMPut16(central, 0); AMPut32(central, crc); AMPut32(central, (uint32_t)data.length);
        AMPut32(central, (uint32_t)data.length); AMPut16(central, (uint16_t)n.length); AMPut16(central, 0); AMPut16(central, 0);
        AMPut16(central, 0); AMPut16(central, 0); AMPut32(central, 0); AMPut32(central, offset);
        [central appendData:n];
    }
    uint32_t start = (uint32_t)out.length;
    [out appendData:central];
    AMPut32(out, 0x06054b50); AMPut16(out, 0); AMPut16(out, 0); AMPut16(out, (uint16_t)files.count); AMPut16(out, (uint16_t)files.count);
    AMPut32(out, (uint32_t)central.length); AMPut32(out, start); AMPut16(out, 0);
    return out;
}

// {path: data} from a zip made by AMZip (stored entries; anything else is refused). nil when it is not one.
static NSDictionary<NSString *, NSData *> *AMUnzip(NSData *zip) {
    const uint8_t *p = zip.bytes;
    NSUInteger len = zip.length, at = 0;
    NSMutableDictionary *files = [NSMutableDictionary new];
    while (at + 30 <= len && AMGet32(p + at) == 0x04034b50) {
        uint16_t method = AMGet16(p + at + 8), nameLen = AMGet16(p + at + 26), extra = AMGet16(p + at + 28);
        uint32_t crc = AMGet32(p + at + 14), size = AMGet32(p + at + 18);
        NSUInteger start = at + 30 + nameLen + extra;
        if (method != 0 || start + size > len) return nil;
        NSString *name = [[NSString alloc] initWithBytes:p + at + 30 length:nameLen encoding:NSUTF8StringEncoding];
        NSData *data = [zip subdataWithRange:NSMakeRange(start, size)];
        if (!name || AMCrc32(data.bytes, data.length) != crc) return nil;
        files[name] = data;
        at = start + size;
    }
    return files.count ? files : nil;
}

#pragma mark Backup and restore

static NSURL *AMCloudContainer(void) { return [NSURL fileURLWithPath:NSHomeDirectory() isDirectory:YES]; }

// Every file under these container folders, by its path from the container ("Documents/save.dat").
static void AMCloudCollect(NSString *folder, NSMutableDictionary *files, NSUInteger *total) {
    NSURL *root = [AMCloudContainer() URLByAppendingPathComponent:folder isDirectory:YES];
    NSDirectoryEnumerator *walk = [NSFileManager.defaultManager enumeratorAtURL:root includingPropertiesForKeys:@[NSURLIsRegularFileKey]
                                                                       options:0 errorHandler:nil];
    // the enumerator answers /private/var/... for a /var/... home: compare both resolved, or the names come out
    // cut in the wrong place ("62A7A39/Documents/save0.json", the first backups)
    NSString *base = root.URLByResolvingSymlinksInPath.path;
    for (NSURL *url in walk) {
        NSNumber *regular = nil;
        [url getResourceValue:&regular forKey:NSURLIsRegularFileKey error:nil];
        if (!regular.boolValue) continue;
        NSString *path = url.URLByResolvingSymlinksInPath.path;
        if (![path hasPrefix:[base stringByAppendingString:@"/"]]) continue;
        NSString *rel = [folder stringByAppendingPathComponent:[path substringFromIndex:base.length + 1]];
        NSData *data = [NSData dataWithContentsOfURL:url options:NSDataReadingMappedIfSafe error:nil];
        if (!data || *total + data.length > AMCloudMaxBytes) continue;
        *total += data.length;
        files[rel] = data;
    }
}

// The save as a zip, or nil with why; *hash fingerprints the save itself (not when or where it was packed), so an
// unchanged save is not uploaded again.
static NSData *AMCloudPack(NSString **why, NSString **hash) {
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    [d synchronize];
    NSMutableDictionary *prefs = [[d persistentDomainForName:NSBundle.mainBundle.bundleIdentifier] mutableCopy] ?: [NSMutableDictionary new];
    for (NSString *key in prefs.allKeys) if ([key hasPrefix:@"AM__"]) [prefs removeObjectForKey:key];  // the menu's own settings
    NSMutableDictionary *files = [NSMutableDictionary new];
    NSUInteger total = 0;
    NSData *plist = [NSPropertyListSerialization dataWithPropertyList:prefs format:NSPropertyListBinaryFormat_v1_0 options:0 error:nil];
    if (plist) { files[@"prefs.plist"] = plist; total += plist.length; }
    AMCloudCollect(@"Documents", files, &total);
    AMCloudCollect(@"Library/Application Support", files, &total);
    if (files.count <= 1 && prefs.count == 0) { if (why) *why = @"There is no save yet: play a little first"; return nil; }
    if (hash) {
        NSData *contents = AMZip(files);  // stored entries with no dates: the same save gives the same bytes
        unsigned char digest[CC_SHA256_DIGEST_LENGTH];
        CC_SHA256(contents.bytes, (CC_LONG)contents.length, digest);
        NSMutableString *hex = [NSMutableString new];
        for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) [hex appendFormat:@"%02x", digest[i]];
        *hash = hex;
    }
    NSString *game, *mod;
    AMCloudVersions(&game, &mod);
    NSDictionary *about = @{@"game": AMCloudGame(), @"bundle": AMCloudBundle(), @"game_version": game, @"mod_version": mod,
                            @"device": AMCloudDevice(), @"created": @((long long)NSDate.date.timeIntervalSince1970), @"format": @1};
    files[@"accretion.json"] = [NSJSONSerialization dataWithJSONObject:about options:NSJSONWritingPrettyPrinted error:nil];
    return AMZip(files);
}

// Back up now: pack, ask for a place, upload, confirm. text: "Backed up · 2.3 MB" or why not.
// "Last saved: 5 min ago" on the Back up now row (or "Saving…" while a backup runs); the open panel redraws it
// every second
static BOOL AMCloudSaving;

static NSString *AMCloudLastText(void) {
    if (AMCloudSaving) return @"Saving…";
    NSNumber *last = AMCloudState()[@"last_backup"];
    if (!last) return @"Not saved yet";
    long s = (long)MAX(0, NSDate.date.timeIntervalSince1970 - last.doubleValue);
    NSString *ago = s < 60 ? @"a few seconds ago" : s < 3600 ? [NSString stringWithFormat:@"%ld min ago", s / 60]
        : s < 86400 ? [NSString stringWithFormat:@"%ld h ago", s / 3600] : [NSString stringWithFormat:@"%ld d ago", s / 86400];
    return [@"Last saved: " stringByAppendingString:ago];
}

static void AMCloudBackup(void (^reply)(BOOL ok, NSString *text)) {
    AMCloudSaving = YES;
    void (^done)(BOOL, NSString *) = ^(BOOL ok, NSString *text) {
        AMCloudSaving = NO;
        reply(ok, text);
    };
    // reading and zipping the save takes a moment (a 2 MB save is tens of milliseconds): off the main thread, so
    // the menu never stutters (Back up now, and the backup when the menu opens)
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *why = nil, *hash = nil;
        NSData *zip = AMCloudPack(&why, &hash);
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!zip) { done(NO, why); return; }
            if ([AMCloudState()[@"last_hash"] isEqual:hash]) {  // the cloud copy is this save: it counts as saved now
                NSMutableDictionary *state = AMCloudState();
                state[@"last_backup"] = @((long long)NSDate.date.timeIntervalSince1970);
                AMCloudSave(state);
                done(YES, @"Already backed up");
                return;
            }
            NSString *game, *mod;
            AMCloudVersions(&game, &mod);
            AMCloudCall(@"backup-start", @{@"size": @(zip.length), @"mod_version": mod, @"game_version": game}, ^(NSDictionary *json, NSString *err) {
                if (err) { done(NO, err); return; }
                AMCloudTransfer(@"PUT", json[@"upload_url"], zip, ^(NSData *data, NSString *err2) {
                    if (err2) { done(NO, err2); return; }
                    AMCloudCall(@"backup-finish", @{@"id": json[@"id"]}, ^(NSDictionary *fin, NSString *err3) {
                        if (err3) { done(NO, err3); return; }
                        NSMutableDictionary *state = AMCloudState();
                        state[@"last_backup"] = @((long long)NSDate.date.timeIntervalSince1970);
                        state[@"last_hash"] = hash;
                        AMCloudSave(state);
                        done(YES, [@"Backed up · " stringByAppendingString:AMCloudSize(zip.length)]);
                    });
                });
            });
        });
    });
}

// Stage a downloaded backup for the next launch. NO with why when the file is not a save of this game.
static BOOL AMCloudStage(NSData *zip, NSString **why) {
    NSDictionary *files = AMUnzip(zip);
    NSDictionary *about = files[@"accretion.json"] ? [NSJSONSerialization JSONObjectWithData:files[@"accretion.json"] options:0 error:nil] : nil;
    if (!files || ![about isKindOfClass:NSDictionary.class]) { *why = @"That backup could not be read"; return NO; }
    if (![about[@"bundle"] isEqual:AMCloudBundle()]) { *why = @"That backup is from another game"; return NO; }
    NSURL *stage = [AMCloudDir() URLByAppendingPathComponent:@"restore" isDirectory:YES];
    [NSFileManager.defaultManager removeItemAtURL:stage error:nil];
    for (NSString *original in files) {
        if ([original containsString:@".."] || [original hasPrefix:@"/"]) continue;  // stay inside the stage
        // the first backups named files "62A7A39/Documents/save0.json": keep what follows the stray piece
        NSString *name = original;
        for (NSString *root in @[@"Documents/", @"Library/"]) {
            NSRange r = [name rangeOfString:[@"/" stringByAppendingString:root]];
            if (![name hasPrefix:root] && r.location != NSNotFound) { name = [name substringFromIndex:r.location + 1]; break; }
        }
        NSURL *to = [stage URLByAppendingPathComponent:name];
        [NSFileManager.defaultManager createDirectoryAtURL:to.URLByDeletingLastPathComponent withIntermediateDirectories:YES attributes:nil error:nil];
        if (![files[original] writeToURL:to options:NSDataWritingAtomic error:nil]) { *why = @"There is no room on the phone to restore it"; return NO; }
    }
    return YES;
}

// At launch, before the game reads its save: a staged restore goes in place (prefs, Documents, Application Support),
// keeping the menu's own settings, then the stage is removed.
__attribute__((constructor)) static void AMCloudApplyRestore(void) {
    @autoreleasepool {
        NSFileManager *fm = NSFileManager.defaultManager;
        NSURL *lib = [fm URLsForDirectory:NSLibraryDirectory inDomains:NSUserDomainMask].firstObject;
        NSURL *stage = [lib URLByAppendingPathComponent:@"Accretion/restore" isDirectory:YES];
        if (![fm fileExistsAtPath:[stage URLByAppendingPathComponent:@"accretion.json"].path]) return;
        NSURL *home = AMCloudContainer();
        for (NSString *folder in @[@"Documents", @"Library/Application Support"]) {
            NSURL *from = [stage URLByAppendingPathComponent:folder], *to = [home URLByAppendingPathComponent:folder];
            if (![fm fileExistsAtPath:from.path]) continue;
            for (NSURL *old in [fm contentsOfDirectoryAtURL:to includingPropertiesForKeys:nil options:0 error:nil]) [fm removeItemAtURL:old error:nil];
            [fm createDirectoryAtURL:to withIntermediateDirectories:YES attributes:nil error:nil];
            for (NSURL *item in [fm contentsOfDirectoryAtURL:from includingPropertiesForKeys:nil options:0 error:nil])
                [fm moveItemAtURL:item toURL:[to URLByAppendingPathComponent:item.lastPathComponent] error:nil];
        }
        NSData *plist = [NSData dataWithContentsOfURL:[stage URLByAppendingPathComponent:@"prefs.plist"]];
        NSDictionary *prefs = plist ? [NSPropertyListSerialization propertyListWithData:plist options:0 format:nil error:nil] : nil;
        if ([prefs isKindOfClass:NSDictionary.class]) {
            NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
            NSString *domain = NSBundle.mainBundle.bundleIdentifier;
            NSMutableDictionary *merged = [prefs mutableCopy];
            [[d persistentDomainForName:domain] enumerateKeysAndObjectsUsingBlock:^(NSString *key, id value, BOOL *stop) {
                if ([key hasPrefix:@"AM__"]) merged[key] = value;  // the menu's own settings stay this device's
            }];
            [d setPersistentDomain:merged forName:domain];
            [d synchronize];
        }
        [fm removeItemAtURL:stage error:nil];
        os_log(OS_LOG_DEFAULT, "[AccretionMods] cloud save restored");
    }
}

// Restore: keep a backup of the current save first, then download, stage, and close the game.
// Restore: download the chosen backup first (so nothing can push it out), keep a backup of the current save, then
// stage the chosen one and close the game.
static void AMCloudRestore(NSString *backupId, void (^done)(BOOL ok, NSString *text)) {
    AMCloudCall(@"restore", @{@"id": backupId}, ^(NSDictionary *json, NSString *err) {
        if (err) { done(NO, err); return; }
        AMCloudTransfer(@"GET", json[@"download_url"], nil, ^(NSData *zip, NSString *err2) {
            if (err2) { done(NO, err2); return; }
            AMCloudBackup(^(BOOL keptOk, NSString *keptText) {
                // with no save yet there is nothing to keep; any other failure stops the restore (the current save stays)
                if (!keptOk && ![keptText hasPrefix:@"There is no save yet"]) { done(NO, [@"Your current save could not be kept: " stringByAppendingString:keptText]); return; }
                dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{  // unzip and write off the main thread
                    NSString *why = nil;
                    BOOL staged = AMCloudStage(zip, &why);
                    dispatch_async(dispatch_get_main_queue(), ^{
                        if (!staged) { done(NO, why); return; }
                        NSMutableDictionary *state = AMCloudState();  // the restored save is the one to back up from now on
                        state[@"last_backup"] = @((long long)NSDate.date.timeIntervalSince1970);
                        [state removeObjectForKey:@"last_hash"];
                        AMCloudSave(state);
                        done(YES, @"Restored: the game closes; open it again");
                    });
                });
            });
        });
    });
}

static NSString *AMCloudWhen(NSString *iso) {
    NSISO8601DateFormatter *in = [NSISO8601DateFormatter new];
    in.formatOptions = NSISO8601DateFormatWithInternetDateTime | NSISO8601DateFormatWithFractionalSeconds;
    NSDate *date = [in dateFromString:iso];
    if (!date) { in.formatOptions = NSISO8601DateFormatWithInternetDateTime; date = [in dateFromString:iso]; }
    if (!date) return iso ?: @"";
    NSDateFormatter *out = [NSDateFormatter new];
    out.doesRelativeDateFormatting = YES;
    out.dateStyle = NSDateFormatterMediumStyle;
    out.timeStyle = NSDateFormatterShortStyle;
    return [out stringFromDate:date];
}

#pragma mark - Panel

@interface AMPanel : UITableViewController <UITextFieldDelegate>
@property (nonatomic, strong) AMCardEdges *edges;
@property (nonatomic, strong) NSArray<AMItem *> *cheats;  // the mod's own items, as registered
@property (nonatomic, strong) AMLoginBar *login;
@property (nonatomic, strong, nullable) UIViewController *browser;  // the sign-in page while it is open (fallback)
@property (nonatomic, strong, nullable) id authSession;  // ASWebAuthenticationSession while the sign-in sheet is open
@property (nonatomic, strong, nullable) NSTimer *linkTimer;
@property (nonatomic, strong) NSArray<AMItem *> *items;
@property (nonatomic, strong) NSArray<NSString *> *allTitles;            // categories in the order first added
@property (nonatomic, strong) NSArray<NSArray<NSNumber *> *> *allRows;   // indexes into items
@property (nonatomic, strong) NSArray<NSString *> *shownTitles;          // after the search filter
@property (nonatomic, strong) NSArray<NSArray<NSNumber *> *> *shownRows;
@property (nonatomic, copy) NSString *query;
@property (nonatomic) BOOL folds;  // long menus fold their categories
@property (nonatomic, strong, nullable) UITextField *search;
@property (nonatomic, strong) UILabel *note;
@property (nonatomic, strong) UIButton *more;  // "More at getaccretion.app", under the note
@property (nonatomic, strong, nullable) NSTimer *statusTimer;
@property (nonatomic, copy, nullable) void (^onClose)(void);  // the whole menu went away (Done or swipe down)
@end

static NSString *const AMFoldedSections = @"AM__foldedSections";  // every category starts open; these were folded
static NSString *const AMSite = @"https://getaccretion.app";

// The menu's title: the game's own name as the phone shows it under its icon, the same rule for every mod. The
// mod's title (with "+") only when the app has no name.
static NSString *AMGameName(NSString *fallback) {
    NSDictionary *info = NSBundle.mainBundle.infoDictionary;
    for (NSString *key in @[@"CFBundleDisplayName", @"CFBundleName"]) {
        NSString *name = [info[key] isKindOfClass:NSString.class] ? info[key] : nil;
        name = [name stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if (name.length) return name;
    }
    return fallback;
}

@implementation AMPanel

- (instancetype)initWithItems:(NSArray<AMItem *> *)items title:(NSString *)title {
    if ((self = [super initWithStyle:UITableViewStyleGrouped])) {
        _cheats = items;
        _query = @"";
        self.title = title;
        [self regroup];
    }
    return self;
}

// The menu's own rows after the mod's: Cloud saves (once linked), then Display.
- (void)regroup {
    NSUInteger cheats = self.cheats.count;
    NSMutableArray<AMItem *> *all = [self.cheats mutableCopy];
    [all addObjectsFromArray:[self cloudItems]];
    for (NSArray<NSString *> *d in @[@[@"_pulse", @"Pulsing dot"], @[@"_toggles", @"Toggles"]]) {
        AMItem *item = [AMItem new];  // AMKey(@"_pulse") is AMPulseKey
        item.key = d[0];
        item.title = d[1];
        item.section = AMDisplaySection;
        item.display = YES;
        [all addObject:item];
    }
    NSArray<AMItem *> *items = all;
    {
        _items = items;
        NSMutableArray<NSString *> *titles = [NSMutableArray new];
        NSMutableDictionary<NSString *, NSMutableArray<NSNumber *> *> *rows = [NSMutableDictionary new];
        [items enumerateObjectsUsingBlock:^(AMItem *item, NSUInteger i, BOOL *stop) {
            NSString *t = item.section ?: @"";
            if (!rows[t]) { rows[t] = [NSMutableArray new]; [titles addObject:t]; }
            [rows[t] addObject:@(i)];
        }];
        _allTitles = titles;
        NSMutableArray *ordered = [NSMutableArray new];
        for (NSString *t in titles) [ordered addObject:rows[t]];
        _allRows = ordered;
        _folds = cheats > 30 && titles.count > 2;
        [self filter];
    }
}

- (NSArray<AMItem *> *)cloudItems {
    if (!AMCloudLinked()) return @[];  // the Log in capsule under the title stands in for them
    __weak typeof(self) weak = self;
    AMItem *backup = [AMItem new];
    backup.title = @"Back up now";
    backup.onAction = ^(void (^done)(BOOL, NSString *)) { AMCloudBackup(done); };
    backup.liveLabel = ^NSString *{ return AMCloudLastText(); };
    AMItem *restore = [AMItem new];
    restore.title = @"Restore a backup";
    restore.label = @"›";
    restore.onAction = ^(void (^done)(BOOL, NSString *)) { [weak showBackups:done]; };
    AMItem *autoBackup = [AMItem new];
    autoBackup.key = @"_cloudAuto";  // AMKey(@"_cloudAuto") is AMCloudAutoKey
    autoBackup.title = @"Back up when the menu opens";
    autoBackup.display = YES;
    AMItem *unlink = [AMItem new];
    unlink.title = @"Unlink this device";
    unlink.quiet = YES;
    unlink.onAction = ^(void (^done)(BOOL, NSString *)) { [weak unlink:done]; };
    for (AMItem *item in @[backup, restore, autoBackup, unlink]) item.section = AMCloudSection;
    return @[backup, restore, autoBackup, unlink];
}

// Cloud saves changed (linked, unlinked): rows and the capsule follow.
- (void)cloudChanged {
    [self regroup];
    [self.login show:AMCloudState()[@"account"] busy:NO];
    [self.tableView reloadData];
}

// Log in: a one-time code from Accretion, its page opened inside the game (the code already filled in); once the
// owner signs in and links there, the menu notices, closes the page and shows Cloud saves.
- (void)logIn {
    [self.login show:nil busy:YES];
    AMCloudCall(@"link-start", @{@"bundle": AMCloudBundle(), @"game": AMCloudGame(), @"device": AMCloudDevice()}, ^(NSDictionary *json, NSString *err) {
        [self.login show:nil busy:NO];
        if (err) { [self say:err title:@"Could not start the sign-in"]; return; }
        // return=accretionmods: the page sends accretionmods://linked once it has linked, which closes the sheet
        NSURL *url = [NSURL URLWithString:[json[@"approve_url"] stringByAppendingString:@"&return=accretionmods"]];
        [self openBrowser:url code:json[@"code"]];
        [self pollLink:json[@"code"] until:[NSDate dateWithTimeIntervalSinceNow:600]];
    });
}

// iOS's sign-in sheet (ASWebAuthenticationSession): it shares Safari's sign-in, so the owner is often signed in
// already, and it closes by itself when the page answers accretionmods://linked. Its framework is loaded when
// needed (no mod's Makefile lists it); the plain in-app browser is the fallback.
- (void)openBrowser:(NSURL *)url code:(NSString *)code {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        dlopen("/System/Library/Frameworks/AuthenticationServices.framework/AuthenticationServices", RTLD_LAZY);
        dlopen("/System/Library/Frameworks/SafariServices.framework/SafariServices", RTLD_LAZY);
        Protocol *provider = objc_getProtocol("ASWebAuthenticationPresentationContextProviding");
        if (provider) class_addProtocol(AMPanel.class, provider);
    });
    Class auth = NSClassFromString(@"ASWebAuthenticationSession");
    if (auth) {
        __weak typeof(self) weak = self;
        void (^finished)(NSURL *, NSError *) = ^(NSURL *callback, NSError *error) {
            dispatch_async(dispatch_get_main_queue(), ^{
                weak.authSession = nil;
                [weak pollLink:code until:[NSDate dateWithTimeIntervalSinceNow:60]];  // closed: one last look
            });
        };
        id session = ((id (*)(id, SEL, NSURL *, NSString *, id))objc_msgSend)([auth alloc],
            @selector(initWithURL:callbackURLScheme:completionHandler:), url, @"accretionmods", finished);
        [session setValue:self forKey:@"presentationContextProvider"];
        [session setValue:@NO forKey:@"prefersEphemeralWebBrowserSession"];
        self.authSession = session;
        if (((BOOL (*)(id, SEL))objc_msgSend)(session, @selector(start))) return;
        self.authSession = nil;
    }
    Class safari = NSClassFromString(@"SFSafariViewController");
    if (!safari) { [UIApplication.sharedApplication openURL:url options:@{} completionHandler:nil]; return; }
    UIViewController *page = [(id)[safari alloc] initWithURL:url];
    page.modalPresentationStyle = UIModalPresentationPageSheet;
    self.browser = page;
    [self presentViewController:page animated:YES completion:nil];
}

- (UIWindow *)presentationAnchorForWebAuthenticationSession:(id)session {
    return self.view.window;
}

- (void)pollLink:(NSString *)code until:(NSDate *)end {
    [self.linkTimer invalidate];
    __weak typeof(self) weak = self;
    self.linkTimer = [NSTimer scheduledTimerWithTimeInterval:2 repeats:YES block:^(NSTimer *timer) {
        typeof(self) me = weak;
        if (!me || AMCloudLinked() || NSDate.date.timeIntervalSince1970 > end.timeIntervalSince1970) { [timer invalidate]; return; }
        AMCloudCall(@"link-poll", @{@"code": code}, ^(NSDictionary *json, NSString *err) {
            if (err || [json[@"status"] isEqual:@"waiting"]) return;
            [timer invalidate];
            if (![json[@"status"] isEqual:@"linked"]) { [me say:@"The code expired: tap Log in again" title:@"Not linked"]; return; }
            NSMutableDictionary *state = AMCloudState();
            state[@"token"] = json[@"token"];
            state[@"account"] = json[@"account"];
            AMCloudSave(state);
            [[[UINotificationFeedbackGenerator alloc] init] notificationOccurred:UINotificationFeedbackTypeSuccess];
            if (me.authSession) ((void (*)(id, SEL))objc_msgSend)(me.authSession, @selector(cancel));
            me.authSession = nil;
            if (me.browser.presentingViewController) [me.browser dismissViewControllerAnimated:YES completion:nil];
            me.browser = nil;
            [me cloudChanged];
        });
    }];
}

- (void)say:(NSString *)message title:(NSString *)title {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:title message:message preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleCancel handler:nil]];
    [(self.presentedViewController ?: self) presentViewController:alert animated:YES completion:nil];
}

- (void)unlink:(void (^)(BOOL, NSString *))done {
    UIAlertController *ask = [UIAlertController alertControllerWithTitle:@"Unlink this device?"
        message:@"Your backups stay in your Accretion account. Log in again to use them." preferredStyle:UIAlertControllerStyleAlert];
    [ask addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:^(UIAlertAction *a) { done(YES, nil); }]];
    [ask addAction:[UIAlertAction actionWithTitle:@"Unlink" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *a) {
        AMCloudCall(@"unlink", @{}, ^(NSDictionary *json, NSString *err) {
            NSMutableDictionary *state = AMCloudState();  // forgotten here even when Accretion could not be reached
            [state removeObjectForKey:@"token"];
            [state removeObjectForKey:@"account"];
            AMCloudSave(state);
            done(YES, nil);
            [self cloudChanged];
        });
    }]];
    [self presentViewController:ask animated:YES completion:nil];
}

// Restore a backup: the account's backups of this game, newest first, as a list page; a pick asks first.
- (void)showBackups:(void (^)(BOOL, NSString *))done {
    AMCloudCall(@"list", @{}, ^(NSDictionary *json, NSString *err) {
        if (err) { done(NO, err); return; }
        NSArray *backups = json[@"backups"];
        if (![backups isKindOfClass:NSArray.class] || !backups.count) { done(NO, @"No backups yet: Back up now first"); return; }
        done(YES, nil);
        NSMutableArray *entries = [NSMutableArray new], *ids = [NSMutableArray new];
        // one list, newest first (Accretion sends them so), the device on each row: after a reinstall the same
        // phone links as a new device, so "this device" first hid the save that mattered
        for (NSDictionary *b in backups) {
            NSString *detail = [NSString stringWithFormat:@"%@ · mod %@ · %@", b[@"device"] ?: @"device",
                                b[@"mod_version"] ?: @"?", AMCloudSize([b[@"size"] longLongValue])];
            [entries addObject:@{@"title": AMCloudWhen(b[@"created_at"]), @"detail": detail}];
            [ids addObject:b[@"id"]];
        }
        AMItem *list = [AMItem new];
        list.title = @"Restore a backup";
        list.isList = YES;
        list.entries = ^{ return (NSArray *)entries; };
        __weak typeof(self) weak = self;
        list.onPick = ^(NSInteger index) {
            AMMenuPickResult(true, "Asking first");
            [weak confirmRestore:ids[index] when:entries[index][@"title"]];
        };
        [self.navigationController pushViewController:[[AMListPanel alloc] initWithItem:list] animated:YES];
    });
}

- (void)confirmRestore:(NSString *)backupId when:(NSString *)when {
    UIViewController *top = self.navigationController.topViewController ?: self;
    UIAlertController *ask = [UIAlertController alertControllerWithTitle:[@"Restore the backup from " stringByAppendingString:when]
        message:@"Your current save is backed up first. Then the game closes: open it again to play from the backup."
        preferredStyle:UIAlertControllerStyleAlert];
    [ask addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:nil]];
    [ask addAction:[UIAlertAction actionWithTitle:@"Restore" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        UIAlertController *wait = [UIAlertController alertControllerWithTitle:@"Restoring…" message:nil preferredStyle:UIAlertControllerStyleAlert];
        [top presentViewController:wait animated:YES completion:nil];
        AMCloudRestore(backupId, ^(BOOL ok, NSString *text) {
            [wait dismissViewControllerAnimated:YES completion:^{
                UIAlertController *end = [UIAlertController alertControllerWithTitle:ok ? @"Restored" : @"Not restored"
                    message:ok ? @"The game closes now. Open it again to play from the backup." : text preferredStyle:UIAlertControllerStyleAlert];
                [end addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:^(UIAlertAction *b) {
                    if (ok) exit(0);  // the staged save goes in place at the next launch (AMCloudApplyRestore)
                }]];
                [top presentViewController:end animated:YES completion:nil];
            }];
        });
    }]];
    [top presentViewController:ask animated:YES completion:nil];
}

- (BOOL)isOpen:(NSString *)section {
    if (!self.folds || self.query.length || !section.length || [section isEqualToString:AMDisplaySection] ||
        [section isEqualToString:AMCloudSection]) return YES;
    return ![[NSUserDefaults.standardUserDefaults arrayForKey:AMFoldedSections] containsObject:section];
}

- (void)toggle:(NSString *)section {
    NSMutableArray *folded = [[NSUserDefaults.standardUserDefaults arrayForKey:AMFoldedSections] mutableCopy] ?: [NSMutableArray new];
    if ([folded containsObject:section]) [folded removeObject:section];
    else [folded addObject:section];
    [NSUserDefaults.standardUserDefaults setObject:folded forKey:AMFoldedSections];
    NSUInteger s = [self.shownTitles indexOfObject:section];
    if (s == NSNotFound) return;
    [UIView performWithoutAnimation:^{
        [self.tableView reloadSections:[NSIndexSet indexSetWithIndex:s] withRowAnimation:UITableViewRowAnimationNone];
    }];
}

- (void)filter {
    NSString *q = [self.query stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
    NSMutableArray *titles = [NSMutableArray new], *rows = [NSMutableArray new];
    NSStringCompareOptions opts = NSCaseInsensitiveSearch | NSDiacriticInsensitiveSearch;
    for (NSUInteger s = 0; s < self.allTitles.count; s++) {
        NSString *t = self.allTitles[s];
        BOOL sectionHit = q.length && [t rangeOfString:q options:opts].location != NSNotFound;
        NSMutableArray *hits = [NSMutableArray new];
        for (NSNumber *n in self.allRows[s]) {
            if (!q.length || sectionHit || [self.items[n.integerValue].title rangeOfString:q options:opts].location != NSNotFound) [hits addObject:n];
        }
        if (hits.count) { [titles addObject:t]; [rows addObject:hits]; }
    }
    self.shownTitles = titles;
    self.shownRows = rows;
}

- (void)searchChanged:(UITextField *)field {
    NSString *q = field.text ?: @"";
    if ([q isEqualToString:self.query]) return;
    self.query = q;
    [self filter];
    [self.tableView reloadData];
    [self showNote];
}

- (BOOL)textFieldShouldReturn:(UITextField *)field {
    [field resignFirstResponder];
    return YES;
}

- (NSInteger)flatIndexAt:(NSIndexPath *)path {
    return self.shownRows[path.section][path.row].integerValue;
}

- (nullable NSIndexPath *)pathOfFlat:(NSInteger)flat {
    for (NSUInteger s = 0; s < self.shownRows.count; s++) {
        if (![self isOpen:self.shownTitles[s]]) continue;
        NSUInteger r = [self.shownRows[s] indexOfObject:@(flat)];
        if (r != NSNotFound) return [NSIndexPath indexPathForRow:r inSection:s];
    }
    return nil;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    UITableView *table = self.tableView;
    table.backgroundColor = AM_BG;
    table.backgroundView = [AMBackdrop new];  // fixed behind the panels while they scroll
    table.separatorStyle = UITableViewCellSeparatorStyleNone;
    table.sectionHeaderTopPadding = 0;
    table.estimatedRowHeight = 30;
    table.estimatedSectionHeaderHeight = 32;
    table.keyboardDismissMode = UIScrollViewKeyboardDismissModeOnDrag;
    self.navigationItem.rightBarButtonItem = AMBarText(@"Done", self, @selector(close));
    // the title with our mark beside it
    UILabel *name = [UILabel new];
    name.text = self.title;
    name.font = [UIFont systemFontOfSize:17 weight:UIFontWeightSemibold];
    name.textColor = AM_FG;
    UIStackView *titleView = [[UIStackView alloc] initWithArrangedSubviews:@[[AMLogo new], name]];
    titleView.spacing = 8;
    titleView.alignment = UIStackViewAlignmentCenter;
    self.navigationItem.titleView = titleView;
    [self refreshAllOff];
    if (self.cheats.count > 12) {
        self.search = AMSearchField([NSString stringWithFormat:@"Search %lu switches", (unsigned long)self.cheats.count]);
        self.search.delegate = self;
        [self.search addTarget:self action:@selector(searchChanged:) forControlEvents:UIControlEventEditingChanged];
    }
    self.login = [AMLoginBar new];
    [self.login.button addTarget:self action:@selector(logIn) forControlEvents:UIControlEventTouchUpInside];
    [self.login show:AMCloudState()[@"account"] busy:NO];
    UIView *head = [[UIView alloc] initWithFrame:CGRectMake(0, 0, 320, self.login.bounds.size.height + (self.search ? 50 : 0))];
    self.login.frame = CGRectMake(0, 0, 320, self.login.bounds.size.height);
    self.login.autoresizingMask = UIViewAutoresizingFlexibleWidth;
    [head addSubview:self.login];
    if (self.search) {
        AMSearchHeader *field = [[AMSearchHeader alloc] initWithField:self.search];
        field.frame = CGRectMake(0, self.login.bounds.size.height - 6, 320, 50);
        field.autoresizingMask = UIViewAutoresizingFlexibleWidth;
        [head addSubview:field];
    }
    table.tableHeaderView = head;
    UIView *foot = [[UIView alloc] initWithFrame:CGRectMake(0, 0, UIScreen.mainScreen.bounds.size.width, 112)];
    self.note = [[UILabel alloc] initWithFrame:CGRectInset(foot.bounds, AMSide + 4, 10)];
    self.note.autoresizingMask = UIViewAutoresizingFlexibleWidth;
    self.note.numberOfLines = 0;
    self.note.font = AMMono(11, UIFontWeightRegular);
    self.note.textColor = AM_MUTED;
    [foot addSubview:self.note];
    self.more = [UIButton buttonWithType:UIButtonTypeSystem];
    NSMutableAttributedString *more = [[NSMutableAttributedString alloc] initWithString:@"More at "
        attributes:@{NSFontAttributeName: AMMono(11, UIFontWeightRegular), NSForegroundColorAttributeName: AM_MUTED}];
    [more appendAttributedString:[[NSAttributedString alloc] initWithString:@"getaccretion.app ›"
        attributes:@{NSFontAttributeName: AMMono(11, UIFontWeightMedium), NSForegroundColorAttributeName: AM_ACCENT_TEXT}]];
    [self.more setAttributedTitle:more forState:UIControlStateNormal];
    self.more.contentHorizontalAlignment = UIControlContentHorizontalAlignmentLeft;
    self.more.accessibilityLabel = @"More at getaccretion.app";
    [self.more addTarget:self action:@selector(openSite) forControlEvents:UIControlEventTouchUpInside];
    [foot addSubview:self.more];
    table.tableFooterView = foot;
    [self showNote];
}

// The header and footer take the table's own width (the sheet can be narrower than the screen).
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    if (!self.edges) self.edges = [[AMCardEdges alloc] initWithTable:self.tableView];
    [self.edges layout];
    CGFloat width = self.tableView.bounds.size.width;
    for (UIView *v in @[self.tableView.tableHeaderView ?: [UIView new], self.tableView.tableFooterView ?: [UIView new]]) {
        if (!v.superview || fabs(v.frame.size.width - width) < 0.5) continue;
        CGRect f = v.frame;
        f.size.width = width;
        v.frame = f;
        if (v == self.tableView.tableHeaderView) self.tableView.tableHeaderView = v;
        else self.tableView.tableFooterView = v;
    }
    CGFloat side = AMInset(width) + 4;
    CGFloat footH = self.note.superview.bounds.size.height;
    self.note.frame = CGRectMake(side, 8, MAX(width - 2 * side, 0), footH - 46);
    self.more.frame = CGRectMake(side, footH - 36, MAX(width - 2 * side, 0), 28);
}

// The flowing hairline under the title bar (the bar's own shadow line is cleared for it).
- (void)addFlowLine {
    UINavigationBar *bar = self.navigationController.navigationBar;
    if (!bar || [bar viewWithTag:9301]) return;
    for (UINavigationBarAppearance *a in @[bar.standardAppearance, bar.scrollEdgeAppearance ?: bar.standardAppearance]) a.shadowColor = UIColor.clearColor;
    AMFlowLine *line = [AMFlowLine new];
    line.tag = 9301;
    line.alpha = 0.85;
    line.frame = CGRectMake(0, bar.bounds.size.height - 1, bar.bounds.size.width, 1);
    line.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleTopMargin;
    [bar addSubview:line];
}

- (void)showNote {
    self.note.text = self.shownTitles.count
        ? @"Made by Accretion. Settings stay on this device. A dot by a switch that is on: green when it reaches the game, red with what to do."
        : [NSString stringWithFormat:@"No switch matches “%@”.", self.query];
}

- (void)openSite {
    [UIApplication.sharedApplication openURL:[NSURL URLWithString:AMSite] options:@{} completionHandler:nil];
}

- (void)close {
    [self dismissViewControllerAnimated:YES completion:nil];
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView {
    return self.shownTitles.count;
}

- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {
    return [self isOpen:self.shownTitles[section]] ? self.shownRows[section].count : 0;
}

- (UIView *)tableView:(UITableView *)tableView viewForHeaderInSection:(NSInteger)section {
    if (!self.shownTitles[section].length) return nil;
    AMSectionHead *head = [tableView dequeueReusableHeaderFooterViewWithIdentifier:@"head"];
    if (!head) {
        head = [[AMSectionHead alloc] initWithReuseIdentifier:@"head"];
        [head.tap addTarget:self action:@selector(headerTapped:) forControlEvents:UIControlEventTouchUpInside];
        [head.off addTarget:self action:@selector(categoryOff:) forControlEvents:UIControlEventTouchUpInside];
    }
    [self configureHeader:head section:section];
    return head;
}

- (void)configureHeader:(AMSectionHead *)head section:(NSInteger)section {
    NSString *title = self.shownTitles[section];
    BOOL open = [self isOpen:title];
    head.label.attributedText = AMLabel(title, AM_MUTED);
    head.tap.accessibilityIdentifier = title;
    head.tap.userInteractionEnabled = self.folds && !self.query.length && ![title isEqualToString:AMDisplaySection] &&
                                     ![title isEqualToString:AMCloudSection];
    NSUInteger on = 0;
    for (NSNumber *n in self.shownRows[section]) if (AMItemOn(self.items[n.integerValue])) on++;
    NSMutableAttributedString *r = [NSMutableAttributedString new];
    if (on) [r appendAttributedString:AMLabel([NSString stringWithFormat:@"%lu on", (unsigned long)on], AM_GOOD)];
    if (head.tap.userInteractionEnabled) {
        NSString *tail = [NSString stringWithFormat:@"%@%lu  %@", on ? @" · " : @"", (unsigned long)self.shownRows[section].count, open ? @"▾" : @"›"];
        [r appendAttributedString:AMLabel(tail, AM_MUTED)];
    }
    if ([title isEqualToString:AMCloudSection] && [AMCloudState()[@"account"] length])
        r = [[NSMutableAttributedString alloc] initWithString:AMCloudState()[@"account"]
                                                   attributes:@{NSFontAttributeName: AMMono(11, UIFontWeightRegular), NSForegroundColorAttributeName: AM_MUTED}];
    head.right.attributedText = r;
    head.off.hidden = on == 0;
    head.off.accessibilityIdentifier = title;
    [head setNeedsLayout];
    head.tap.isAccessibilityElement = YES;
    head.tap.accessibilityTraits = UIAccessibilityTraitButton;
    head.tap.accessibilityLabel = [NSString stringWithFormat:@"%@, %lu switches, %lu on, %@", title,
                                   (unsigned long)self.shownRows[section].count, (unsigned long)on, open ? @"open" : @"folded"];
}

- (CGFloat)tableView:(UITableView *)tableView heightForHeaderInSection:(NSInteger)section {
    return self.shownTitles[section].length ? 32 : CGFLOAT_MIN;
}

- (CGFloat)tableView:(UITableView *)tableView heightForFooterInSection:(NSInteger)section {
    return 6;  // the gap between boxes (the next label adds its own)
}

- (UIView *)tableView:(UITableView *)tableView viewForFooterInSection:(NSInteger)section {
    return [UIView new];
}

// Off: every switch of these items off and every slider back to its default (restorable pins put the game's own
// values back as their switch goes off).
- (void)turnOff:(NSArray<NSNumber *> *)flats {
    NSUInteger count = 0;
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    for (NSNumber *n in flats) {
        AMItem *item = self.items[n.integerValue];
        if (!AMItemOn(item)) continue;
        count++;
        if (item.isSlider) {
            AMSliderSet(item, item.defaultValue);  // keeps its value for its toggle
        } else {
            [d setBool:NO forKey:AMKey(item.key)];
            if (item.onSwitch) item.onSwitch(NO);
            os_log(OS_LOG_DEFAULT, "[AccretionMods] status %{public}@ off", item.key);
        }
    }
    if (!count) return;
    [[[UINotificationFeedbackGenerator alloc] init] notificationOccurred:UINotificationFeedbackTypeSuccess];
    [self.tableView reloadData];
    [self refreshAllOff];
}

- (void)categoryOff:(UIButton *)button {
    NSUInteger s = [self.allTitles indexOfObject:button.accessibilityIdentifier ?: @""];
    if (s != NSNotFound) [self turnOff:self.allRows[s]];
}

- (void)allOff {
    NSMutableArray *all = [NSMutableArray new];
    for (NSUInteger i = 0; i < self.items.count; i++) [all addObject:@(i)];
    [self turnOff:all];
}

// "All off" at the top left, while anything is on
- (void)refreshAllOff {
    BOOL any = NO;
    for (AMItem *item in self.items) if (AMItemOn(item)) { any = YES; break; }
    if (any == (self.navigationItem.leftBarButtonItem != nil)) return;
    self.navigationItem.leftBarButtonItem = any
        ? AMBarText(@"All off", self, @selector(allOff))
        : nil;
}

- (void)headerTapped:(UIControl *)tap {
    if (tap.accessibilityIdentifier) [self toggle:tap.accessibilityIdentifier];
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    NSInteger flat = [self flatIndexAt:indexPath];
    AMItem *item = self.items[flat];
    AMRowCell *cell;
    if (item.isSlider) {
        AMSliderCell *row = [tableView dequeueReusableCellWithIdentifier:@"slider"];
        if (!row) {
            row = [[AMSliderCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:@"slider"];
            [row.slider addTarget:self action:@selector(sliderChanged:) forControlEvents:UIControlEventValueChanged];
            [row.slider addTarget:self action:@selector(sliderReleased:) forControlEvents:UIControlEventTouchUpInside | UIControlEventTouchUpOutside];
        }
        row.title.text = item.title;
        row.slider.minimumValue = item.min;
        row.slider.maximumValue = item.max;
        row.slider.value = AMSliderShown(item);
        row.slider.tag = flat;
        row.note.text = AMNoteFor(item.key).lowercaseString;
        [self showValueOf:item inRow:row];
        cell = row;
    } else if (item.isList) {
        AMListCell *row = [tableView dequeueReusableCellWithIdentifier:@"list"] ?: [[AMListCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:@"list"];
        row.title.text = item.title;
        row.icon.image = item.symbol ? [UIImage systemImageNamed:item.symbol] : nil;
        cell = row;
    } else if (item.onAction) {
        AMActionCell *row = [tableView dequeueReusableCellWithIdentifier:@"action"]
            ?: [[AMActionCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:@"action"];
        row.title.text = item.title;
        row.title.textColor = item.quiet ? AM_MUTED : AM_FG;
        row.right.text = item.liveLabel ? item.liveLabel() : item.label;
        row.right.textColor = AM_MUTED;
        cell = row;
    } else {
        AMSwitchCell *row = [tableView dequeueReusableCellWithIdentifier:@"switch"]
            ?: [[AMSwitchCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:@"switch"];
        row.title.text = item.title;
        [self showStatusOf:item inRow:row animated:NO];
        cell = row;
    }
    // which slice of the category's box this row draws
    BOOL last = indexPath.row == (NSInteger)self.shownRows[indexPath.section].count - 1;
    BOOL first = indexPath.row == 0;
    [cell setPiece:last ? (first ? AMPieceWhole : AMPieceBottom) : (first ? AMPieceTop : AMPieceMiddle) rule:!first];
    return cell;
}

- (void)showValueOf:(AMItem *)item inRow:(AMSliderCell *)row {
    float v = row.slider.value;
    BOOL on = AMItemOn(item), toggles = AMDisplayOn(AMTogglesKey);
    row.value.text = fabsf(v) >= 100 || v == roundf(v) ? [NSString stringWithFormat:@"%.0f", v] : [NSString stringWithFormat:@"%.1f", v];
    row.value.textColor = on ? AM_ACCENT_TEXT : AM_MUTED;
    row.title.textColor = toggles || on ? AM_FG : AM_MUTED;
    row.slider.alpha = on ? 1 : 0.45;
    [row showToggle:toggles];
    if (row.toggle.on != on) [row.toggle setOn:on bad:NO animated:YES];
}

- (void)showStatusOf:(AMItem *)item inRow:(AMSwitchCell *)row animated:(BOOL)animated {
    if (item.display) {  // a setting of the menu: its toggle always shows, no dot, no status
        BOOL on = AMDisplayOn(AMKey(item.key));
        row.reason = nil;
        row.dot.hidden = YES;
        row.detail.text = nil;
        row.title.textColor = AM_FG;
        [row showToggle:YES];
        if (row.toggle.on != on || row.toggle.bad) [row.toggle setOn:on bad:NO animated:animated];
        return;
    }
    BOOL on = [NSUserDefaults.standardUserDefaults boolForKey:AMKey(item.key)];
    BOOL toggles = AMDisplayOn(AMTogglesKey);
    [row showToggle:toggles];
    row.title.textColor = toggles || on ? AM_FG : AM_MUTED;  // without toggles, bright text and the dot say "on"
    NSString *line = nil;
    BOOL ok = on && AMStatusFor(item.key, &line);
    row.reason = on && !ok ? line : nil;
    row.dot.hidden = !on;
    if (on) row.dot.ok = ok;
    row.detail.text = row.reason;  // the green dot says "working" on its own; a line only when something needs doing
    if (row.toggle.on != on || row.toggle.bad != (on && !ok)) [row.toggle setOn:on bad:!ok animated:animated];
}

- (void)refreshStatus {
    BOOL resize = NO;
    for (UITableViewCell *cell in self.tableView.visibleCells) {
        NSIndexPath *path = [self.tableView indexPathForCell:cell];
        if (!path) continue;
        AMItem *item = self.items[[self flatIndexAt:path]];
        if ([cell isKindOfClass:AMSliderCell.class]) {
            AMSliderCell *row = (AMSliderCell *)cell;
            BOOL had = row.note.text.length > 0;
            row.note.text = AMNoteFor(item.key).lowercaseString;
            if (had != (row.note.text.length > 0)) resize = YES;
        } else if ([cell isKindOfClass:AMSwitchCell.class]) {
            AMSwitchCell *row = (AMSwitchCell *)cell;
            BOOL had = row.detail.text.length > 0;
            [self showStatusOf:item inRow:row animated:YES];
            if (had != (row.detail.text.length > 0)) resize = YES;
        } else if ([cell isKindOfClass:AMActionCell.class] && item.liveLabel) {
            AMActionCell *row = (AMActionCell *)cell;  // not over a fresh outcome (✓ Backed up)
            if (![row.right.text hasPrefix:@"✓"] && ![row.right.text hasPrefix:@"✕"] && ![row.right.text isEqual:@"…"]) row.right.text = item.liveLabel();
        }
    }
    if (resize) {
        [UIView performWithoutAnimation:^{ [self.tableView beginUpdates]; [self.tableView endUpdates]; }];
    }
    [self refreshAllOff];
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    [self addFlowLine];
    [self.statusTimer invalidate];
    __weak typeof(self) weak = self;
    self.statusTimer = [NSTimer scheduledTimerWithTimeInterval:1 repeats:YES block:^(NSTimer *t) { [weak refreshStatus]; }];
}

- (void)viewWillDisappear:(BOOL)animated {
    [super viewWillDisappear:animated];
    [self.statusTimer invalidate];
    self.statusTimer = nil;
}

- (void)viewDidDisappear:(BOOL)animated {
    [super viewDidDisappear:animated];
    // not when a list page is pushed on top: only when the menu itself is dismissed
    if ((self.navigationController.isBeingDismissed || self.isBeingDismissed) && self.onClose) self.onClose();
}

- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    NSInteger flat = [self flatIndexAt:indexPath];
    AMItem *item = self.items[flat];
    [tableView deselectRowAtIndexPath:indexPath animated:YES];
    if (item.isList) [self.navigationController pushViewController:[[AMListPanel alloc] initWithItem:item] animated:YES];
    else if (item.onAction) [self runAction:item at:indexPath];
    else if (item.isSlider) [self flipSlider:flat];
    else [self flip:flat];
}

// A slider row's tap: off puts the default back (the game's own value) and keeps the value; on brings it back.
- (void)flipSlider:(NSInteger)flat {
    AMItem *item = self.items[flat];
    BOOL on = !AMItemOn(item);
    float kept = AMSliderShown(item);
    if (on && kept == item.defaultValue) on = NO;  // nothing kept yet: drag to pick a value
    else AMSliderSet(item, on ? kept : item.defaultValue);
    NSIndexPath *path = [self pathOfFlat:flat];
    AMSliderCell *row = path ? (AMSliderCell *)[self.tableView cellForRowAtIndexPath:path] : nil;
    if ([row isKindOfClass:AMSliderCell.class]) { row.slider.value = AMSliderShown(item); [self showValueOf:item inRow:row]; }
    [self confirm:on row:row];
    [self refreshHeaderOf:flat];
    [self refreshAllOff];
}

// An action row: "…" while it runs, then the outcome on the row with the glow and haptic of a pick.
- (void)runAction:(AMItem *)item at:(NSIndexPath *)path {
    AMActionCell *row = (AMActionCell *)[self.tableView cellForRowAtIndexPath:path];
    if ([row isKindOfClass:AMActionCell.class]) { row.right.text = @"…"; row.right.textColor = AM_MUTED; }
    item.onAction(^(BOOL ok, NSString *text) {
        AMActionCell *now = (AMActionCell *)[self.tableView cellForRowAtIndexPath:path];
        if (![now isKindOfClass:AMActionCell.class] || ![now.title.text isEqual:item.title]) return;
        now.right.text = text.length ? [NSString stringWithFormat:@"%@ %@", ok ? @"✓" : @"✕", text] : item.label;
        now.right.textColor = ok ? AM_ACCENT2 : AM_BAD;
        if (text.length) {
            [[[UINotificationFeedbackGenerator alloc] init] notificationOccurred:ok ? UINotificationFeedbackTypeSuccess : UINotificationFeedbackTypeError];
            if ([now.backgroundView isKindOfClass:AMPanelPiece.class]) [(AMPanelPiece *)now.backgroundView flash:ok ? AM_ACCENT2 : AM_BAD];
        }
    });
}

// The "N on" in a category's header follows its switches.
- (void)refreshHeaderOf:(NSInteger)flat {
    NSUInteger s = [self.shownTitles indexOfObject:self.items[flat].section ?: @""];
    if (s == NSNotFound) return;
    AMSectionHead *head = (AMSectionHead *)[self.tableView headerViewForSection:s];
    if ([head isKindOfClass:AMSectionHead.class]) [self configureHeader:head section:s];
}

// A tap's confirmation, like a list pick's: the row glows (cyan when the switch went on, the line colour when it
// went off) with a success or light haptic. Whether the cheat then reaches the game is the dot's job.
- (void)confirm:(BOOL)on row:(nullable UITableViewCell *)row {
    if (on) [[[UINotificationFeedbackGenerator alloc] init] notificationOccurred:UINotificationFeedbackTypeSuccess];
    else [[[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleLight] impactOccurred];
    if ([row.backgroundView isKindOfClass:AMPanelPiece.class]) [(AMPanelPiece *)row.backgroundView flash:on ? AM_ACCENT2 : AM_LINE_STRONG];
}

- (void)flip:(NSInteger)flat {
    AMItem *item = self.items[flat];
    if (item.display) {
        BOOL on = !AMDisplayOn(AMKey(item.key));
        [NSUserDefaults.standardUserDefaults setBool:on forKey:AMKey(item.key)];
        [self.tableView reloadData];  // every row follows the new setting
        [self.tableView layoutIfNeeded];  // so the row exists to glow
        NSIndexPath *path = [self pathOfFlat:flat];
        [self confirm:on row:path ? [self.tableView cellForRowAtIndexPath:path] : nil];
        return;
    }
    BOOL on = ![NSUserDefaults.standardUserDefaults boolForKey:AMKey(item.key)];
    [NSUserDefaults.standardUserDefaults setBool:on forKey:AMKey(item.key)];
    if (item.onSwitch) item.onSwitch(on);
    if (!on) os_log(OS_LOG_DEFAULT, "[AccretionMods] status %{public}@ off", item.key);
    NSIndexPath *path = [self pathOfFlat:flat];
    AMSwitchCell *row = path ? (AMSwitchCell *)[self.tableView cellForRowAtIndexPath:path] : nil;
    if ([row isKindOfClass:AMSwitchCell.class]) {
        BOOL had = row.detail.text.length > 0;
        [self showStatusOf:item inRow:row animated:YES];
        if (had != (row.detail.text.length > 0))  // only when the red line came or went: re-measuring every row stutters
            [UIView performWithoutAnimation:^{ [self.tableView beginUpdates]; [self.tableView endUpdates]; }];
    }
    [self confirm:on row:row];
    [self refreshHeaderOf:flat];
    [self refreshAllOff];
}

// Letting go of a slider confirms like a switch: cyan when it is off its default (on), the line colour back at it.
- (void)sliderReleased:(UISlider *)s {
    NSIndexPath *path = [self pathOfFlat:s.tag];
    [self confirm:AMItemOn(self.items[s.tag]) row:path ? [self.tableView cellForRowAtIndexPath:path] : nil];
}

// Dragging sets the value and turns the slider on; within 3% of the range from the default it snaps to the
// default (off), with a tick, so off is easy to reach by hand.
- (void)sliderChanged:(UISlider *)s {
    AMItem *item = self.items[s.tag];
    float v = s.value;
    BOOL snap = fabsf(v - item.defaultValue) <= (item.max - item.min) * 0.03f;
    if (snap) {
        v = item.defaultValue;
        s.value = v;
    }
    BOOL was = AMItemOn(item);
    if (snap && !was) return;  // already off: nothing changes
    if (snap) [[UISelectionFeedbackGenerator new] selectionChanged];
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    [d setFloat:v forKey:AMKey(item.key)];
    if (!snap) [d setFloat:v forKey:AMLastKey(item)];  // a snap keeps the value it was dragged from
    if (item.onSlider) item.onSlider(v);
    NSIndexPath *path = [self pathOfFlat:s.tag];
    AMSliderCell *row = path ? (AMSliderCell *)[self.tableView cellForRowAtIndexPath:path] : nil;
    if ([row isKindOfClass:AMSliderCell.class]) [self showValueOf:item inRow:row];
    [self refreshHeaderOf:s.tag];
    if (was != !snap) [self refreshAllOff];
}

@end

#pragma mark - List page

// A tier's colour, from its name: the usual game rarities (Legendary, Epic, Rare, Uncommon or Normal); anything
// else, and Common, in `muted`.
static UIColor *AMTierColor(NSString *group) {
    NSString *g = group.lowercaseString;
    if ([g containsString:@"legend"] || [g containsString:@"mythic"]) return AMTok(0xF5B83D, 0x8C5A00);
    if ([g containsString:@"epic"]) return AMTok(0xE879F9, 0xA21CAF);
    if ([g containsString:@"rare"]) return AMTok(0xB48CFF, 0x6D3FE0);
    if ([g containsString:@"uncommon"] || [g containsString:@"normal"]) return AMTok(0x7FD4E6, 0x0E7490);
    return AM_MUTED;
}

// A list page's row: its symbol (when it has one), the title, the detail in mono at the right.
@interface AMEntryCell : AMRowCell
@property (nonatomic, strong) UIImageView *icon;
@property (nonatomic, strong) UILabel *title, *detail;
- (void)setSymbol:(nullable NSString *)symbol;
@end

@implementation AMEntryCell {
    NSLayoutConstraint *_afterIcon, *_atEdge;
}

- (instancetype)initWithStyle:(UITableViewCellStyle)style reuseIdentifier:(NSString *)reuseIdentifier {
    if ((self = [super initWithStyle:style reuseIdentifier:reuseIdentifier])) {
        _icon = [UIImageView new];
        _icon.tintColor = AM_ACCENT_TEXT;
        _icon.contentMode = UIViewContentModeScaleAspectFit;
        _title = [UILabel new];
        _title.font = [UIFont systemFontOfSize:13];
        _title.textColor = AM_FG;
        _title.numberOfLines = 0;
        [_title setContentCompressionResistancePriority:UILayoutPriorityDefaultLow forAxis:UILayoutConstraintAxisHorizontal];
        _detail = [UILabel new];
        _detail.font = AMMono(11, UIFontWeightRegular);
        _detail.textColor = AM_MUTED;
        _detail.textAlignment = NSTextAlignmentRight;
        [_detail setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];
        [_detail setContentCompressionResistancePriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];
        for (UIView *v in @[_icon, _title, _detail]) {
            v.translatesAutoresizingMaskIntoConstraints = NO;
            [self.contentView addSubview:v];
        }
        UILayoutGuide *m = self.contentView.layoutMarginsGuide;
        _afterIcon = [_title.leadingAnchor constraintEqualToAnchor:_icon.trailingAnchor constant:10];
        _atEdge = [_title.leadingAnchor constraintEqualToAnchor:m.leadingAnchor];
        [NSLayoutConstraint activateConstraints:@[
            [_icon.leadingAnchor constraintEqualToAnchor:m.leadingAnchor],
            [_icon.centerYAnchor constraintEqualToAnchor:self.contentView.centerYAnchor],
            [_icon.widthAnchor constraintEqualToConstant:16],
            [_icon.heightAnchor constraintEqualToConstant:16],
            [_title.topAnchor constraintEqualToAnchor:m.topAnchor constant:2],
            [_title.bottomAnchor constraintEqualToAnchor:m.bottomAnchor constant:-2],
            [_title.trailingAnchor constraintLessThanOrEqualToAnchor:_detail.leadingAnchor constant:-10],
            [_detail.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [_detail.centerYAnchor constraintEqualToAnchor:self.contentView.centerYAnchor],
            [self.contentView.heightAnchor constraintGreaterThanOrEqualToConstant:30],
            _atEdge,
        ]];
    }
    return self;
}

- (void)setSymbol:(NSString *)symbol {
    self.icon.image = symbol ? [UIImage systemImageNamed:symbol] : nil;
    self.icon.hidden = !self.icon.image;
    _afterIcon.active = !self.icon.hidden;
    _atEdge.active = self.icon.hidden;
}

@end

// A list page (a mod's addList): the menu's look, its entries in boxes. Entries that name a "group" (a quality
// tier such as Legendary or Rare) get one box per group, in the order the mod lists them (best first), the label
// in the tier's colour with the count; every group starts open and folds when its label is tapped. A search
// field above long lists. A pick calls the mod with the entry's index in its own list; the page stays open.
@implementation AMListPanel {
    AMCardEdges *_edges;
    NSArray<NSString *> *_groups;              // shown groups ("" for entries with none)
    NSArray<NSArray<NSNumber *> *> *_rows;     // indexes into entries, per shown group
    NSMutableSet<NSString *> *_folded;
    NSString *_query;
    UILabel *_note;
}

- (instancetype)initWithItem:(AMItem *)item {
    if ((self = [super initWithStyle:UITableViewStyleGrouped])) {
        _item = item;
        _query = @"";
        self.title = item.title;
    }
    return self;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    UITableView *table = self.tableView;
    table.backgroundColor = AM_BG;
    table.backgroundView = [AMBackdrop new];
    table.separatorStyle = UITableViewCellSeparatorStyleNone;
    table.sectionHeaderTopPadding = 0;
    table.estimatedRowHeight = 30;
    table.keyboardDismissMode = UIScrollViewKeyboardDismissModeOnDrag;
    table.tableHeaderView = [[UIView alloc] initWithFrame:CGRectMake(0, 0, 1, 8)];
    UIView *foot = [[UIView alloc] initWithFrame:CGRectMake(0, 0, UIScreen.mainScreen.bounds.size.width, 52)];
    _note = [UILabel new];
    _note.numberOfLines = 0;
    _note.font = AMMono(11, UIFontWeightRegular);
    _note.textColor = AM_MUTED;
    [foot addSubview:_note];
    table.tableFooterView = foot;
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    self.entries = self.item.entries ? self.item.entries() : @[];
    if (!_folded) _folded = [NSMutableSet new];  // every group starts open
    if (self.entries.count > 20 && ![self.tableView.tableHeaderView isKindOfClass:AMSearchHeader.class]) {
        UITextField *field = AMSearchField([NSString stringWithFormat:@"Search %lu", (unsigned long)self.entries.count]);
        field.delegate = self;
        [field addTarget:self action:@selector(searchChanged:) forControlEvents:UIControlEventEditingChanged];
        self.tableView.tableHeaderView = [[AMSearchHeader alloc] initWithField:field];
    }
    [self filter];
    [self.tableView reloadData];
}

// The header and footer take the table's own width (the sheet can be narrower than the screen).
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    if (!_edges) _edges = [[AMCardEdges alloc] initWithTable:self.tableView];
    [_edges layout];
    CGFloat width = self.tableView.bounds.size.width;
    UIView *head = self.tableView.tableHeaderView;
    if (head && fabs(head.frame.size.width - width) > 0.5) {
        CGRect f = head.frame;
        f.size.width = width;
        head.frame = f;
        self.tableView.tableHeaderView = head;
    }
    CGFloat side = AMInset(width) + 4;
    _note.frame = CGRectMake(side, 8, MAX(width - 2 * side, 0), 36);
}

- (void)filter {
    NSString *q = [_query stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceCharacterSet];
    NSStringCompareOptions opts = NSCaseInsensitiveSearch | NSDiacriticInsensitiveSearch;
    NSMutableArray<NSString *> *groups = [NSMutableArray new];
    NSMutableDictionary<NSString *, NSMutableArray<NSNumber *> *> *rows = [NSMutableDictionary new];
    [self.entries enumerateObjectsUsingBlock:^(NSDictionary *e, NSUInteger i, BOOL *stop) {
        if (q.length && [e[@"title"] ?: @"" rangeOfString:q options:opts].location == NSNotFound) return;
        NSString *g = e[@"group"] ?: @"";
        if (!rows[g]) { rows[g] = [NSMutableArray new]; [groups addObject:g]; }
        [rows[g] addObject:@(i)];
    }];
    _groups = groups;
    NSMutableArray *ordered = [NSMutableArray new];
    for (NSString *g in groups) [ordered addObject:rows[g]];
    _rows = ordered;
    _note.text = !self.entries.count ? @"Nothing to list yet. Open this again once the game has loaded."
               : !groups.count       ? [NSString stringWithFormat:@"Nothing matches “%@”.", q]
                                     : [NSString stringWithFormat:@"%lu entries. Tap one to apply it.", (unsigned long)self.entries.count];
}

- (void)searchChanged:(UITextField *)field {
    _query = field.text ?: @"";
    [self filter];
    [self.tableView reloadData];
}

- (BOOL)textFieldShouldReturn:(UITextField *)field {
    [field resignFirstResponder];
    return YES;
}

- (BOOL)isOpen:(NSInteger)section {
    return _query.length || ![_folded containsObject:_groups[section]];
}

- (BOOL)named {  // groups get a header unless the list has none
    return _groups.count > 1 || (_groups.count == 1 && _groups[0].length);
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView {
    return _groups.count;
}

- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {
    return [self isOpen:section] ? _rows[section].count : 0;
}

- (CGFloat)tableView:(UITableView *)tableView heightForHeaderInSection:(NSInteger)section {
    return self.named ? 32 : CGFLOAT_MIN;
}

- (CGFloat)tableView:(UITableView *)tableView heightForFooterInSection:(NSInteger)section {
    return 6;
}

- (UIView *)tableView:(UITableView *)tableView viewForFooterInSection:(NSInteger)section {
    return [UIView new];
}

- (UIView *)tableView:(UITableView *)tableView viewForHeaderInSection:(NSInteger)section {
    if (!self.named) return nil;
    AMSectionHead *head = [tableView dequeueReusableHeaderFooterViewWithIdentifier:@"tier"];
    if (!head) {
        head = [[AMSectionHead alloc] initWithReuseIdentifier:@"tier"];
        [head.tap addTarget:self action:@selector(headerTapped:) forControlEvents:UIControlEventTouchUpInside];
    }
    NSString *g = _groups[section];
    BOOL open = [self isOpen:section];
    head.label.attributedText = AMLabel(g.length ? g : @"Other", AMTierColor(g));
    head.right.attributedText = AMLabel([NSString stringWithFormat:@"%lu  %@", (unsigned long)_rows[section].count,
                                         _query.length ? @"" : open ? @"▾" : @"›"], AM_MUTED);
    head.off.hidden = YES;
    head.tap.accessibilityIdentifier = g;
    head.tap.userInteractionEnabled = !_query.length;
    head.tap.isAccessibilityElement = YES;
    head.tap.accessibilityTraits = UIAccessibilityTraitButton;
    head.tap.accessibilityLabel = [NSString stringWithFormat:@"%@, %lu entries, %@", g, (unsigned long)_rows[section].count,
                                   open ? @"open" : @"folded"];
    [head setNeedsLayout];
    return head;
}

- (void)headerTapped:(UIControl *)tap {
    NSString *g = tap.accessibilityIdentifier ?: @"";
    if ([_folded containsObject:g]) [_folded removeObject:g];
    else [_folded addObject:g];
    NSUInteger s = [_groups indexOfObject:g];
    if (s == NSNotFound) return;
    [UIView performWithoutAnimation:^{
        [self.tableView reloadSections:[NSIndexSet indexSetWithIndex:s] withRowAnimation:UITableViewRowAnimationNone];
    }];
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    NSDictionary *e = self.entries[_rows[indexPath.section][indexPath.row].integerValue];
    AMEntryCell *cell = [tableView dequeueReusableCellWithIdentifier:@"entry"]
        ?: [[AMEntryCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:@"entry"];
    cell.title.text = e[@"title"];
    cell.title.textColor = AM_FG;
    cell.detail.text = e[@"detail"];
    cell.detail.textColor = AM_MUTED;
    cell.accessibilityValue = nil;
    [cell setSymbol:e[@"symbol"]];
    BOOL last = indexPath.row == (NSInteger)_rows[indexPath.section].count - 1, first = indexPath.row == 0;
    [cell setPiece:last ? (first ? AMPieceWhole : AMPieceBottom) : (first ? AMPieceTop : AMPieceMiddle) rule:!first];
    return cell;
}

// A pick: the mod's onPick runs, then the row confirms what happened (AMMenuPickResult): it glows cyan and its
// detail reads "✓ Added" for 1.6 s, or red with the reason when the game did not take it, with a matching haptic.
- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    [tableView deselectRowAtIndexPath:indexPath animated:YES];
    NSInteger index = _rows[indexPath.section][indexPath.row].integerValue;
    sPickReported = NO;
    sPickText = nil;
    if (self.item.onPick) self.item.onPick(index);
    BOOL ok = sPickReported ? sPickOK : YES;
    NSString *text = sPickText ?: (!sPickReported ? @"Sent" : ok ? @"Added" : @"Not added");
    [[[UINotificationFeedbackGenerator alloc] init]
        notificationOccurred:ok ? UINotificationFeedbackTypeSuccess : UINotificationFeedbackTypeError];
    AMEntryCell *cell = (AMEntryCell *)[tableView cellForRowAtIndexPath:indexPath];
    if (![cell isKindOfClass:AMEntryCell.class]) return;
    UIColor *color = ok ? AM_ACCENT2 : AM_BAD;
    [(AMPanelPiece *)cell.backgroundView flash:color];
    NSString *detail = self.entries[index][@"detail"];
    cell.detail.text = [NSString stringWithFormat:@"%@ %@", ok ? @"✓" : @"✕", text];
    cell.detail.textColor = color;
    cell.accessibilityValue = cell.detail.text;
    UIAccessibilityPostNotification(UIAccessibilityAnnouncementNotification, cell.detail.text);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.6 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        if (![[tableView indexPathForCell:cell] isEqual:indexPath]) return;  // reused for another row meanwhile
        [UIView transitionWithView:cell.detail duration:0.3 options:UIViewAnimationOptionTransitionCrossDissolve animations:^{
            cell.detail.text = detail;
            cell.detail.textColor = AM_MUTED;
        } completion:nil];
        cell.accessibilityValue = nil;
    });
}

@end

#pragma mark - Floating button

typedef NS_ENUM(NSInteger, AMRingState) { AMRingNone, AMRingGood, AMRingSome, AMRingBad };

@interface AMButton : UIButton
@property (nonatomic, strong) CAShapeLayer *ring;
@property (nonatomic, strong) CALayer *idle;  // the brand edge while no switch is on: violet into cyan, turning
@property (nonatomic) AMRingState ringState;
@property (nonatomic) BOOL menuOpen;  // hidden while the menu is open
@property (nonatomic, strong, nullable) NSTimer *idleTimer;
- (void)wake;
- (void)restorePosition;
@end

static NSString *const AMButtonSpot = @"AM__buttonSpot";  // where the button was left, as fractions of the screen

@implementation AMButton

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectMake(16, 120, 44, 44)])) {
        self.backgroundColor = [AM_SURFACE colorWithAlphaComponent:0.92];
        self.layer.cornerRadius = 22;
        self.layer.borderWidth = 1;
        self.layer.borderColor = AMTok(0x262145, 0x262145).CGColor;  // `line`, the dark value: the button sits on the game
        self.layer.zPosition = 9999;
        self.accessibilityLabel = @"Mod menu";
        UIImage *icon = [UIImage systemImageNamed:@"slider.horizontal.3"
                                withConfiguration:[UIImageSymbolConfiguration configurationWithPointSize:17 weight:UIImageSymbolWeightMedium]];
        [self setImage:icon forState:UIControlStateNormal];
        self.tintColor = AMTok(0xA07AF7, 0xA07AF7);  // `accent-text`, dark
        [self addGestureRecognizer:[[UIPanGestureRecognizer alloc] initWithTarget:self action:@selector(drag:)]];
        [self addTarget:self action:@selector(wake) forControlEvents:UIControlEventTouchDown];
        [self addTarget:self action:@selector(pressStart) forControlEvents:UIControlEventTouchDown | UIControlEventTouchDragEnter];
        [self addTarget:self action:@selector(pressEnd) forControlEvents:UIControlEventTouchUpInside | UIControlEventTouchUpOutside | UIControlEventTouchCancel | UIControlEventTouchDragExit];
        // the status ring just outside the button: green all working, yellow some, red none (AMMenu updateBadge)
        _ring = [CAShapeLayer layer];
        _ring.path = [UIBezierPath bezierPathWithOvalInRect:CGRectInset(self.bounds, -3, -3)].CGPath;
        _ring.fillColor = UIColor.clearColor.CGColor;
        _ring.lineWidth = 2;
        _ring.hidden = YES;
        [self.layer addSublayer:_ring];
        // idle: the same motif as the menu's lines, a conic violet-to-cyan edge turning once every 6 s
        _idle = [CALayer layer];
        _idle.frame = CGRectInset(self.bounds, -3, -3);
        CAGradientLayer *spin = [CAGradientLayer layer];
        spin.type = kCAGradientLayerConic;
        spin.startPoint = CGPointMake(0.5, 0.5);
        spin.endPoint = CGPointMake(0.5, 0);
        spin.frame = _idle.bounds;
        UIColor *a = AMTok(0x8B5CF6, 0x8B5CF6), *b = AMTok(0x22D3EE, 0x22D3EE);
        spin.colors = @[(id)a.CGColor, (id)b.CGColor, (id)[UIColor colorWithRed:0.75 green:0.97 blue:1 alpha:1].CGColor, (id)b.CGColor, (id)a.CGColor, (id)a.CGColor];
        spin.locations = @[@0, @0.18, @0.25, @0.32, @0.5, @1];
        [_idle addSublayer:spin];
        CAShapeLayer *mask = [CAShapeLayer layer];
        mask.path = [UIBezierPath bezierPathWithOvalInRect:CGRectInset(_idle.bounds, 1, 1)].CGPath;
        mask.fillColor = UIColor.clearColor.CGColor;
        mask.strokeColor = UIColor.blackColor.CGColor;
        mask.lineWidth = 1.5;
        _idle.mask = mask;
        _idle.opacity = 0.8;
        [self.layer addSublayer:_idle];
        [self spinIdle];
    }
    return self;
}

- (void)spinIdle {
    CALayer *spin = self.idle.sublayers.firstObject;
    [spin removeAllAnimations];
    if (AMStill()) return;
    CABasicAnimation *turn = [CABasicAnimation animationWithKeyPath:@"transform.rotation.z"];
    turn.fromValue = @0;
    turn.toValue = @(2 * M_PI);
    turn.duration = 6;
    turn.repeatCount = HUGE_VALF;
    [spin addAnimation:turn forKey:@"turn"];
}

- (void)didMoveToWindow {
    [super didMoveToWindow];
    if (self.window) [self spinIdle];  // iOS drops layer animations off screen
}

// The design system's press: dim and shrink to 0.96 on a spring.
- (void)pressStart {
    [UIView animateWithDuration:UIAccessibilityIsReduceMotionEnabled() ? 0 : 0.12 animations:^{ self.transform = CGAffineTransformMakeScale(0.96, 0.96); }];
}

- (void)pressEnd {
    [UIView animateWithDuration:UIAccessibilityIsReduceMotionEnabled() ? 0 : 0.25 delay:0 usingSpringWithDamping:0.7 initialSpringVelocity:0 options:0
                     animations:^{ self.transform = CGAffineTransformIdentity; } completion:nil];
}

- (void)setRingState:(AMRingState)state {
    // same state: nothing to do, unless iOS dropped the pulse (it removes layer animations in the background)
    BOOL pulses = state == AMRingSome || state == AMRingBad;
    BOOL pulsing = [_ring animationForKey:@"pulse"] != nil;
    if (state == _ringState && pulsing == (pulses && AMPulseAllowed())) return;
    _ringState = state;
    _ring.hidden = state == AMRingNone;
    _idle.hidden = state != AMRingNone;  // a status ring replaces the brand edge
    UIColor *c = state == AMRingGood ? AMTok(0x34D399, 0x34D399) : state == AMRingSome ? AMTok(0xF59E0B, 0xF59E0B) : AMTok(0xF87171, 0xF87171);
    _ring.strokeColor = c.CGColor;
    [_ring removeAllAnimations];
    // calm when all is well; a slow pulse only when something needs a look
    if (pulses) AMPulse(_ring, 1, 0.35, 2.4);
}

- (CGRect)allowedArea {
    UIView *parent = self.superview;
    CGFloat r = self.bounds.size.width / 2 + 6;  // the ring stays on screen
    return CGRectInset(UIEdgeInsetsInsetRect(parent.bounds, parent.safeAreaInsets), r, r);
}

- (void)drag:(UIPanGestureRecognizer *)pan {
    UIView *parent = self.superview;
    CGPoint t = [pan translationInView:parent];
    CGRect a = [self allowedArea];
    self.center = CGPointMake(MIN(MAX(self.center.x + t.x, CGRectGetMinX(a)), CGRectGetMaxX(a)),
                              MIN(MAX(self.center.y + t.y, CGRectGetMinY(a)), CGRectGetMaxY(a)));
    [pan setTranslation:CGPointZero inView:parent];
    if (pan.state == UIGestureRecognizerStateBegan) [self wake];
    if (pan.state != UIGestureRecognizerStateEnded && pan.state != UIGestureRecognizerStateCancelled) return;
    [self pressEnd];
    // snap to the nearer side edge, and remember the spot for the next launch
    CGPoint end = CGPointMake(self.center.x < CGRectGetMidX(a) ? CGRectGetMinX(a) : CGRectGetMaxX(a), self.center.y);
    void (^move)(void) = ^{ self.center = end; };
    if (UIAccessibilityIsReduceMotionEnabled()) move();
    else [UIView animateWithDuration:0.35 delay:0 usingSpringWithDamping:0.75 initialSpringVelocity:0.5 options:0 animations:move completion:nil];
    CGSize size = parent.bounds.size;
    if (size.width > 0 && size.height > 0) {
        [NSUserDefaults.standardUserDefaults setObject:NSStringFromCGPoint(CGPointMake(end.x / size.width, end.y / size.height)) forKey:AMButtonSpot];
    }
    [self wake];
}

- (void)restorePosition {
    NSString *saved = [NSUserDefaults.standardUserDefaults stringForKey:AMButtonSpot];
    if (!saved || !self.superview) return;
    CGPoint f = CGPointFromString(saved);
    CGSize size = self.superview.bounds.size;
    CGRect a = [self allowedArea];
    self.center = CGPointMake(MIN(MAX(f.x * size.width, CGRectGetMinX(a)), CGRectGetMaxX(a)),
                              MIN(MAX(f.y * size.height, CGRectGetMinY(a)), CGRectGetMaxY(a)));
}

// Full strength on touch, then half after 3 s so it hides less of the game. The ring keeps its colour.
- (void)wake {
    if (self.menuOpen) return;
    self.alpha = 1;
    [self.idleTimer invalidate];
    __weak typeof(self) weak = self;
    self.idleTimer = [NSTimer scheduledTimerWithTimeInterval:3 repeats:NO block:^(NSTimer *t) {
        AMButton *me = weak;
        if (!me || me.menuOpen) return;
        [UIView animateWithDuration:UIAccessibilityIsReduceMotionEnabled() ? 0 : 0.4 animations:^{ me.alpha = 0.5; }];
    }];
}

@end

#pragma mark - Menu

@implementation AMMenu {
    NSMutableArray<AMItem *> *_items;
    NSHashTable<UIWindow *> *_windows;
    NSHashTable<AMButton *> *_buttons;
    NSTimer *_badgeTimer;
    NSString *_section;
    BOOL _installed;
    BOOL _open;
    UIView *_shield;
}

+ (AMMenu *)shared {
    static AMMenu *menu;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ menu = [AMMenu new]; });
    return menu;
}

- (instancetype)init {
    if ((self = [super init])) {
        _items = [NSMutableArray new];
        _windows = [NSHashTable weakObjectsHashTable];
        _buttons = [NSHashTable weakObjectsHashTable];
        _title = @"Mods";
    }
    return self;
}

+ (BOOL)boolForKey:(NSString *)key {
    return [NSUserDefaults.standardUserDefaults boolForKey:AMKey(key)];
}

+ (float)floatForKey:(NSString *)key {
    return [NSUserDefaults.standardUserDefaults floatForKey:AMKey(key)];
}

- (nullable AMItem *)itemFor:(NSString *)key {
    for (AMItem *item in _items) if ([item.key isEqualToString:key]) return item;
    return nil;
}

- (BOOL)hasItem:(NSString *)key {
    return [self itemFor:key] != nil;
}

- (BOOL)setSwitch:(NSString *)key on:(BOOL)on {
    AMItem *item = [self itemFor:key];
    if (!item || item.isSlider || item.isList) return NO;
    [NSUserDefaults.standardUserDefaults setBool:on forKey:AMKey(key)];
    if (item.onSwitch) item.onSwitch(on);
    os_log(OS_LOG_DEFAULT, "[AccretionMods] set %{public}@ %{public}s", key, on ? "on" : "off");
    if (!on) os_log(OS_LOG_DEFAULT, "[AccretionMods] status %{public}@ off", key);
    return YES;
}

- (BOOL)setSlider:(NSString *)key value:(float)value {
    AMItem *item = [self itemFor:key];
    if (!item || !item.isSlider) return NO;
    value = MIN(MAX(value, item.min), item.max);
    AMSliderSet(item, value);
    os_log(OS_LOG_DEFAULT, "[AccretionMods] set %{public}@ %.2f", key, value);
    return YES;
}

- (nullable NSArray<NSNumber *> *)sliderRange:(NSString *)key {
    AMItem *item = [self itemFor:key];
    return item.isSlider ? @[@(item.min), @(item.max), @(item.defaultValue)] : nil;
}

- (void)addSection:(NSString *)title {
    _section = [title copy];
}

- (void)addSwitch:(NSString *)key title:(NSString *)title defaultValue:(BOOL)value onChange:(void (^)(BOOL))block {
    [NSUserDefaults.standardUserDefaults registerDefaults:@{AMKey(key): @(value)}];
    AMItem *item = [AMItem new];
    item.key = key;
    item.title = title;
    item.onSwitch = block;
    item.section = _section ?: @"";
    [_items addObject:item];
}

- (void)addSlider:(NSString *)key title:(NSString *)title min:(float)min max:(float)max defaultValue:(float)value onChange:(void (^)(float))block {
    [NSUserDefaults.standardUserDefaults registerDefaults:@{AMKey(key): @(value)}];
    AMItem *item = [AMItem new];
    item.key = key;
    item.title = title;
    item.isSlider = YES;
    item.min = min;
    item.max = max;
    item.defaultValue = value;
    item.onSlider = block;
    item.section = _section ?: @"";
    [_items addObject:item];
}

- (void)addList:(NSString *)title symbol:(NSString *)symbol entries:(NSArray<NSDictionary<NSString *, NSString *> *> *(^)(void))entries onPick:(void (^)(NSInteger))block {
    AMItem *item = [AMItem new];
    item.title = title;
    item.isList = YES;
    item.symbol = symbol;
    item.entries = entries;
    item.onPick = block;
    item.section = _section ?: @"";
    [_items addObject:item];
}

- (void)install {
    if (_installed) return;
    _installed = YES;
    [NSNotificationCenter.defaultCenter addObserverForName:UIWindowDidBecomeKeyNotification
                                                    object:nil
                                                     queue:NSOperationQueue.mainQueue
                                                usingBlock:^(NSNotification *note) {
        [self attachTo:note.object];
    }];
}

- (void)attachTo:(UIWindow *)window {
    // Skip system windows such as the keyboard, which are not UIWindow itself or a plain app subclass.
    if (![window isKindOfClass:UIWindow.class] || [_windows containsObject:window]) return;
    if ([NSStringFromClass(window.class) hasPrefix:@"UIRemote"] || [NSStringFromClass(window.class) hasPrefix:@"UIText"]) return;
    [_windows addObject:window];
    AMButton *button = [AMButton new];
    [button addTarget:self action:@selector(open:) forControlEvents:UIControlEventTouchUpInside];
    [window addSubview:button];
    [button restorePosition];
    [button wake];
    [_buttons addObject:button];
    if (!_badgeTimer) {
        __weak typeof(self) weak = self;
        _badgeTimer = [NSTimer scheduledTimerWithTimeInterval:1 repeats:YES block:^(NSTimer *t) { [weak updateBadge]; }];
    }
}

// The button's ring: none when no switch is on, green when every switch that is on works, red when none of
// them does, yellow when some do.
- (void)updateBadge {
    NSUInteger on = 0, failing = 0;
    static NSUInteger ticks;
    BOOL resend = ++ticks % 20 == 0;  // every 20 s the whole state again, for a log capture started late
    for (AMItem *item in _items) {
        if (item.isList || !AMItemOn(item)) continue;
        NSString *line = nil;
        BOOL ok = AMStatusFor(item.key, &line);
        // every 20 s the state of every switch and slider that is on again, for a log reader that attached late
        if (resend) os_log(OS_LOG_DEFAULT, "[AccretionMods] status %{public}@ %{public}@", item.key, ok ? @"ok" : [@"waiting: " stringByAppendingString:line]);
        if (item.isSlider) continue;  // the ring sums up the switches
        on++;
        if (!ok) failing++;
    }
    for (AMButton *button in _buttons) {
        button.ringState = on == 0 ? AMRingNone : failing == 0 ? AMRingGood : failing == on ? AMRingBad : AMRingSome;
        button.accessibilityValue = on == 0 ? nil
            : failing ? [NSString stringWithFormat:@"%lu of %lu cheats not working", (unsigned long)failing, (unsigned long)on]
                      : [NSString stringWithFormat:@"%lu cheats working", (unsigned long)on];
    }
}

// A clear view over the whole window that swallows touches: from the button tap until the menu is fully up,
// so a second tap can't reach the game behind it (that opened a purchase screen once).
- (void)shield:(BOOL)on window:(nullable UIWindow *)window {
    if (on && !_shield && window) {
        _shield = [[UIView alloc] initWithFrame:window.bounds];
        _shield.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        _shield.backgroundColor = [UIColor colorWithWhite:0 alpha:0.001];
        _shield.layer.zPosition = 10000;
        [window addSubview:_shield];
    } else if (!on) {
        [_shield removeFromSuperview];
        _shield = nil;
    }
}

- (void)open:(UIButton *)sender {
    if (_open) return;
    UIViewController *top = sender.window.rootViewController;
    while (top.presentedViewController) top = top.presentedViewController;
    if (!top) return;
    _open = YES;
    [self shield:YES window:sender.window];
    [[[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleLight] impactOccurred];
    if (self.onOpenChange) self.onOpenChange(YES);
    AMPanel *panel = [[AMPanel alloc] initWithItems:_items title:AMGameName(self.title)];
    NSNumber *last = AMCloudState()[@"last_backup"];
    // only once this install has backed up or restored by hand: a fresh install's new game must never push the
    // real save out of the five kept
    if (AMCloudLinked() && last && AMDisplayOn(AMCloudAutoKey) && NSDate.date.timeIntervalSince1970 - last.doubleValue > 3600) {
        AMCloudBackup(^(BOOL ok, NSString *text) {  // at most hourly, in the background
            os_log(OS_LOG_DEFAULT, "[AccretionMods] cloud auto backup: %{public}@", text);
        });
    }
    UINavigationController *nav = [[UINavigationController alloc] initWithRootViewController:panel];
    AMStyleNav(nav);
    // the floating button sits above everything (zPosition): hide it while the menu is open so it covers no row
    __weak typeof(self) weak = self;
    panel.onClose = ^{ [weak closed]; };
    [self setButtonsHidden:YES];
    [top presentViewController:nav animated:YES completion:^{ [weak shield:NO window:nil]; }];
}

- (void)closed {
    _open = NO;
    [self shield:NO window:nil];
    [self setButtonsHidden:NO];
    if (self.onOpenChange) self.onOpenChange(NO);
}

- (void)setButtonsHidden:(BOOL)hidden {
    for (AMButton *button in _buttons) {
        button.userInteractionEnabled = !hidden;
        button.menuOpen = hidden;
        [button.idleTimer invalidate];
        [UIView animateWithDuration:UIAccessibilityIsReduceMotionEnabled() ? 0 : 0.2 animations:^{ button.alpha = hidden ? 0 : 1; }];
        if (!hidden) [button wake];
    }
}

@end
