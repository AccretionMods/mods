#import "AMMenu.h"
#import <os/lock.h>
#import <os/log.h>

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
        changed = e->at == 0 || e->ok != ok || strcmp(e->reason, why) != 0;
        e->ok = ok;
        strlcpy(e->reason, why, sizeof e->reason);
        e->at = CFAbsoluteTimeGetCurrent();
        if (changed) snprintf(line, sizeof line, "status %s %s%s", key, ok ? "ok" : "waiting: ", why);
    }
    os_unfair_lock_unlock(&sStatusLock);
    // Mod Studio and am test read these from the device log to show each switch's state live
    if (changed) os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", line);
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

// Whether a switch's cheat is working now, and the line shown under its title.
static BOOL AMStatusFor(NSString *key, NSString **line) {
    BOOL ok = NO;
    NSString *text = @"Waiting for the game to load";
    os_unfair_lock_lock(&sStatusLock);
    for (int i = 0; i < sStatusCount; i++) {
        if (strcmp(sStatus[i].key, key.UTF8String)) continue;
        if (CFAbsoluteTimeGetCurrent() - sStatus[i].at > AMStatusStale) {
            text = @"Stopped: no report for 15 s";
        } else if (sStatus[i].ok) {
            ok = YES;
            text = @"Working";
        } else {
            text = @(sStatus[i].reason);
        }
        break;
    }
    os_unfair_lock_unlock(&sStatusLock);
    if (line) *line = text;
    return ok;
}

static void AMPulse(CALayer *layer, CGFloat from, CGFloat to, CFTimeInterval duration) {
    [layer removeAllAnimations];
    if (UIAccessibilityIsReduceMotionEnabled()) return;
    CABasicAnimation *fade = [CABasicAnimation animationWithKeyPath:@"opacity"];
    fade.fromValue = @(from);
    fade.toValue = @(to);
    fade.duration = duration / 2;
    fade.autoreverses = YES;
    fade.repeatCount = HUGE_VALF;
    fade.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
    [layer addAnimation:fade forKey:@"pulse"];
}

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
        self.isAccessibilityElement = YES;
        _ok = YES;
        [self setOk:NO];
    }
    return self;
}

- (void)setOk:(BOOL)ok {
    BOOL changed = ok != _ok || ![self.layer animationForKey:@"pulse"];
    _ok = ok;
    self.backgroundColor = ok ? AM_GOOD : AM_BAD;
    self.accessibilityLabel = ok ? @"Working" : @"Not working";
    if (!changed) return;
    if (ok) [self.layer removeAllAnimations];
    else if (self.window) AMPulse(self.layer, 1, 0.35, 2.4);
}

- (void)didMoveToWindow {
    [super didMoveToWindow];
    if (self.window && !self.ok) AMPulse(self.layer, 1, 0.35, 2.4);
}

@end

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
@end

@implementation AMItem
@end

static BOOL AMItemOn(AMItem *item) {
    if (item.isList) return NO;
    if (item.isSlider) return [NSUserDefaults.standardUserDefaults floatForKey:AMKey(item.key)] != item.defaultValue;
    return [NSUserDefaults.standardUserDefaults boolForKey:AMKey(item.key)];
}

static void AMStyleTable(UITableView *table) {
    table.backgroundColor = AM_BG;  // under the backdrop, where a table has one
    table.separatorColor = AM_LINE;
    table.sectionHeaderTopPadding = 6;
    table.keyboardDismissMode = UIScrollViewKeyboardDismissModeOnDrag;
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

@interface AMListPanel : UITableViewController
@property (nonatomic, strong) AMItem *item;
@property (nonatomic, copy) NSArray<NSDictionary<NSString *, NSString *> *> *entries;
@end

@implementation AMListPanel

- (instancetype)initWithItem:(AMItem *)item {
    if ((self = [super initWithStyle:UITableViewStyleInsetGrouped])) {
        _item = item;
        self.title = item.title;
    }
    return self;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    AMStyleTable(self.tableView);
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    self.entries = self.item.entries ? self.item.entries() : @[];
    [self.tableView reloadData];
}

- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {
    return self.entries.count;
}

- (NSString *)tableView:(UITableView *)tableView titleForFooterInSection:(NSInteger)section {
    return self.entries.count ? [NSString stringWithFormat:@"%lu entries. Tap one to apply it.", (unsigned long)self.entries.count]
                              : @"Nothing to list yet. Open this again once the game has loaded.";
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    NSDictionary *e = self.entries[indexPath.row];
    UITableViewCell *cell = [tableView dequeueReusableCellWithIdentifier:@"entry"]
        ?: [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleValue1 reuseIdentifier:@"entry"];
    cell.backgroundColor = AM_SURFACE;
    cell.textLabel.text = e[@"title"];
    cell.textLabel.textColor = AM_FG;
    cell.detailTextLabel.text = e[@"detail"];
    cell.detailTextLabel.textColor = AM_MUTED;
    cell.imageView.image = e[@"symbol"] ? [UIImage systemImageNamed:e[@"symbol"]] : nil;
    cell.imageView.tintColor = AM_ACCENT_TEXT;
    cell.accessoryType = UITableViewCellAccessoryNone;
    return cell;
}

- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    [tableView deselectRowAtIndexPath:indexPath animated:YES];
    if (self.item.onPick) self.item.onPick(indexPath.row);
    UITableViewCell *cell = [tableView cellForRowAtIndexPath:indexPath];
    cell.accessoryType = UITableViewCellAccessoryCheckmark;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.6 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        cell.accessoryType = UITableViewCellAccessoryNone;
    });
}

@end

#pragma mark - Backdrop and summary (the store's live-index look)

static BOOL AMStill(void) {
    return UIAccessibilityIsReduceMotionEnabled() || NSProcessInfo.processInfo.lowPowerModeEnabled;
}

static UIColor *AMResolved(UIColor *c, UITraitCollection *t) {
    return [c resolvedColorWithTraitCollection:t];
}

// The Backdrop: `bg`, a hairline grid every 28 pt and two soft lights (violet, cyan) drifting over 40 s.
// Core Animation moves the lights on the render server: no timer, nothing redrawn per frame. Still with
// Reduce Motion or Low Power Mode.
@interface AMBackdrop : UIView
@end

@implementation AMBackdrop {
    CAShapeLayer *_grid;
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
        _grid = [CAShapeLayer layer];
        _grid.fillColor = UIColor.clearColor.CGColor;
        _grid.lineWidth = 1 / UIScreen.mainScreen.scale;
        [self.layer addSublayer:_grid];
        [self applyColors];
    }
    return self;
}

- (void)applyColors {
    BOOL light = self.traitCollection.userInterfaceStyle == UIUserInterfaceStyleLight;
    CGFloat glow = light ? 0.09 : 0.16;
    UIColor *a = AMResolved(AM_ACCENT, self.traitCollection), *b = AMResolved(AMTok(0x22D3EE, 0x0E8FB0), self.traitCollection);
    _lights[0].colors = @[(id)[a colorWithAlphaComponent:glow].CGColor, (id)[a colorWithAlphaComponent:0].CGColor];
    _lights[1].colors = @[(id)[b colorWithAlphaComponent:glow].CGColor, (id)[b colorWithAlphaComponent:0].CGColor];
    _grid.strokeColor = (light ? [UIColor colorWithRed:0x1E / 255.0 green:0x1B / 255.0 blue:0x4B / 255.0 alpha:0.05]
                               : [UIColor colorWithWhite:1 alpha:0.035]).CGColor;
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
    UIBezierPath *path = [UIBezierPath bezierPath];
    for (CGFloat x = 28; x < size.width; x += 28) { [path moveToPoint:CGPointMake(x, 0)]; [path addLineToPoint:CGPointMake(x, size.height)]; }
    for (CGFloat y = 28; y < size.height; y += 28) { [path moveToPoint:CGPointMake(0, y)]; [path addLineToPoint:CGPointMake(size.width, y)]; }
    _grid.frame = self.bounds;
    _grid.path = path.CGPath;
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

// One number tile: an uppercase mono label over a big tabular number.
@interface AMTile : UIView
@property (nonatomic, strong) UILabel *label, *number;
@end

@implementation AMTile

- (instancetype)initWithLabel:(NSString *)label {
    if ((self = [super initWithFrame:CGRectZero])) {
        self.backgroundColor = AM_RAISED;
        self.layer.cornerRadius = 10;
        self.layer.borderWidth = 1;
        _label = [UILabel new];
        _label.attributedText = AMLabel(label, AM_MUTED);
        _label.adjustsFontSizeToFitWidth = YES;
        _label.minimumScaleFactor = 0.7;
        _number = [UILabel new];
        _number.font = [UIFont monospacedDigitSystemFontOfSize:22 weight:UIFontWeightSemibold];
        _number.textColor = AM_FG;
        _number.text = @"0";
        [self addSubview:_label];
        [self addSubview:_number];
        self.isAccessibilityElement = YES;
    }
    return self;
}

- (void)layoutSubviews {
    [super layoutSubviews];
    self.layer.borderColor = AMResolved(AM_LINE, self.traitCollection).CGColor;
    CGFloat w = self.bounds.size.width - 20;
    self.label.frame = CGRectMake(10, 9, w, 13);
    self.number.frame = CGRectMake(10, 25, w, 28);
}

- (void)setValue:(NSUInteger)value color:(UIColor *)color {
    self.number.text = [NSString stringWithFormat:@"%lu", (unsigned long)value];
    self.number.textColor = color;
    self.accessibilityLabel = [NSString stringWithFormat:@"%@ %lu", self.label.text.lowercaseString, (unsigned long)value];
}

@end

// The card above the switches: "● ACCRETION / mod menu" and the build, the violet-to-cyan hairline, four
// number tiles, and the search field. Corner brackets as on the store's live index.
@interface AMSummary : UIView
@property (nonatomic, strong) UITextField *search;
- (void)showSwitches:(NSUInteger)all on:(NSUInteger)on working:(NSUInteger)working waiting:(NSUInteger)waiting;
@end

@implementation AMSummary {
    UIView *_card;
    AMStatusDot *_live;
    UILabel *_brand, *_build;
    CAGradientLayer *_rule;
    NSArray<AMTile *> *_tiles;
    NSArray<CAShapeLayer *> *_corners;
}

- (instancetype)initWithCount:(NSUInteger)count search:(BOOL)search {
    if ((self = [super initWithFrame:CGRectZero])) {
        _card = [UIView new];
        _card.backgroundColor = AM_SURFACE;
        _card.layer.cornerRadius = 14;
        _card.layer.borderWidth = 1;
        [self addSubview:_card];
        _live = [AMStatusDot new];
        [_card addSubview:_live];
        _brand = [UILabel new];
        NSMutableAttributedString *brand = [[NSMutableAttributedString alloc] initWithString:@"ACCRETION"
            attributes:@{NSFontAttributeName: AMMono(12, UIFontWeightSemibold), NSForegroundColorAttributeName: AM_FG, NSKernAttributeName: @2}];
        [brand appendAttributedString:[[NSAttributedString alloc] initWithString:@"  /  mod menu"
            attributes:@{NSFontAttributeName: AMMono(12, UIFontWeightRegular), NSForegroundColorAttributeName: AM_MUTED}]];
        _brand.attributedText = brand;
        [_card addSubview:_brand];
        _build = [UILabel new];
        _build.font = AMMono(11, UIFontWeightRegular);
        _build.textColor = AM_MUTED;
        _build.textAlignment = NSTextAlignmentRight;
        NSString *version = NSBundle.mainBundle.infoDictionary[@"CFBundleShortVersionString"];
        _build.text = version.length ? [@"v" stringByAppendingString:version] : nil;
        [_card addSubview:_build];
        _rule = [CAGradientLayer layer];
        _rule.startPoint = CGPointMake(0, 0.5);
        _rule.endPoint = CGPointMake(1, 0.5);
        [_card.layer addSublayer:_rule];
        NSMutableArray *tiles = [NSMutableArray new];
        for (NSString *label in @[@"switches", @"on", @"working", @"waiting"]) {
            AMTile *tile = [[AMTile alloc] initWithLabel:label];
            [_card addSubview:tile];
            [tiles addObject:tile];
        }
        _tiles = tiles;
        NSMutableArray *corners = [NSMutableArray new];
        for (int i = 0; i < 4; i++) {
            CAShapeLayer *corner = [CAShapeLayer layer];
            corner.fillColor = UIColor.clearColor.CGColor;
            corner.lineWidth = 1.5;
            [self.layer addSublayer:corner];
            [corners addObject:corner];
        }
        _corners = corners;
        if (search) {
            _search = [UITextField new];
            _search.backgroundColor = AM_RAISED;
            _search.textColor = AM_FG;
            _search.tintColor = AM_ACCENT_TEXT;
            _search.font = [UIFont systemFontOfSize:15];
            _search.layer.cornerRadius = 10;
            _search.layer.borderWidth = 1;
            _search.clearButtonMode = UITextFieldViewModeWhileEditing;
            _search.returnKeyType = UIReturnKeyDone;
            _search.autocorrectionType = UITextAutocorrectionTypeNo;
            _search.attributedPlaceholder = [[NSAttributedString alloc]
                initWithString:[NSString stringWithFormat:@"Search %lu switches", (unsigned long)count]
                     attributes:@{NSForegroundColorAttributeName: AM_MUTED}];
            UIImageView *glass = [[UIImageView alloc] initWithImage:[UIImage systemImageNamed:@"magnifyingglass"]];
            glass.tintColor = AM_MUTED;
            glass.contentMode = UIViewContentModeCenter;
            glass.frame = CGRectMake(0, 0, 36, 36);
            _search.leftView = glass;
            _search.leftViewMode = UITextFieldViewModeAlways;
            [_card addSubview:_search];
        }
        [self applyColors];
    }
    return self;
}

- (CGFloat)heightForWidth:(CGFloat)width {
    return 12 + 16 + 18 + 12 + 1 + 14 + 62 + (self.search ? 12 + 40 : 0) + 16 + 8;
}

- (void)applyColors {
    UITraitCollection *t = self.traitCollection;
    _card.layer.borderColor = AMResolved(AM_LINE, t).CGColor;
    _search.layer.borderColor = AMResolved(AMTok(0x6458AF, 0x8F7ED2), t).CGColor;  // `line-strong`: a field's edge
    UIColor *a = AMResolved(AM_ACCENT, t), *b = AMResolved(AMTok(0x22D3EE, 0x0E8FB0), t);
    _rule.colors = @[(id)a.CGColor, (id)b.CGColor];
    for (NSUInteger i = 0; i < _corners.count; i++) _corners[i].strokeColor = (i % 3 ? b : a).CGColor;
}

- (void)traitCollectionDidChange:(UITraitCollection *)previous {
    [super traitCollectionDidChange:previous];
    [self applyColors];
}

- (void)layoutSubviews {
    [super layoutSubviews];
    CGFloat side = MAX(self.layoutMargins.left, 16);
    CGRect card = CGRectMake(side, 12, self.bounds.size.width - 2 * side, self.bounds.size.height - 20);
    _card.frame = card;
    CGFloat w = card.size.width - 32, y = 16;
    _live.center = CGPointMake(16 + 4, y + 9);
    _brand.frame = CGRectMake(30, y, w * 0.65, 18);
    _build.frame = CGRectMake(16 + w * 0.6, y, w * 0.4, 18);
    y += 18 + 12;
    _rule.frame = CGRectMake(16, y, w, 1);
    y += 1 + 14;
    CGFloat gap = 8, tw = (w - gap * 3) / 4;
    for (NSUInteger i = 0; i < _tiles.count; i++) _tiles[i].frame = CGRectMake(16 + i * (tw + gap), y, tw, 62);
    y += 62 + 12;
    _search.frame = CGRectMake(16, y, w, 40);
    // corner brackets just outside the card, 10 pt arms
    CGFloat l = 10, o = 4;
    CGPoint pts[4] = {CGPointMake(CGRectGetMinX(card) - o, CGRectGetMinY(card) - o), CGPointMake(CGRectGetMaxX(card) + o, CGRectGetMinY(card) - o),
                      CGPointMake(CGRectGetMaxX(card) + o, CGRectGetMaxY(card) + o), CGPointMake(CGRectGetMinX(card) - o, CGRectGetMaxY(card) + o)};
    int dx[4] = {1, -1, -1, 1}, dy[4] = {1, 1, -1, -1};
    for (int i = 0; i < 4; i++) {
        UIBezierPath *p = [UIBezierPath bezierPath];
        [p moveToPoint:CGPointMake(pts[i].x, pts[i].y + dy[i] * l)];
        [p addLineToPoint:pts[i]];
        [p addLineToPoint:CGPointMake(pts[i].x + dx[i] * l, pts[i].y)];
        _corners[i].path = p.CGPath;
    }
}

- (void)showSwitches:(NSUInteger)all on:(NSUInteger)on working:(NSUInteger)working waiting:(NSUInteger)waiting {
    [_tiles[0] setValue:all color:AM_FG];
    [_tiles[1] setValue:on color:on ? AM_FG : AM_MUTED];
    [_tiles[2] setValue:working color:working ? AM_GOOD : AM_MUTED];
    [_tiles[3] setValue:waiting color:waiting ? AM_WARN : AM_MUTED];
    _live.hidden = on == 0;
    _live.ok = waiting == 0;
}

@end

#pragma mark - Rows

// A slider row: the title on its own line (never squeezed by the slider), the value in mono on the right
// (accent when it is not the default), the game's own value under the title, the slider full width below.
@interface AMSliderCell : UITableViewCell
@property (nonatomic, strong) UILabel *title, *value, *note;
@property (nonatomic, strong) UISlider *slider;
@end

@implementation AMSliderCell

- (instancetype)initWithStyle:(UITableViewCellStyle)style reuseIdentifier:(NSString *)reuseIdentifier {
    if ((self = [super initWithStyle:style reuseIdentifier:reuseIdentifier])) {
        self.backgroundColor = AM_SURFACE;
        self.selectionStyle = UITableViewCellSelectionStyleNone;
        _title = [UILabel new];
        _title.font = [UIFont systemFontOfSize:15];
        _title.textColor = AM_FG;
        _title.numberOfLines = 0;
        _value = [UILabel new];
        _value.font = [UIFont monospacedDigitSystemFontOfSize:15 weight:UIFontWeightSemibold];
        _value.textAlignment = NSTextAlignmentRight;
        [_value setContentHuggingPriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];
        [_value setContentCompressionResistancePriority:UILayoutPriorityRequired forAxis:UILayoutConstraintAxisHorizontal];
        _note = [UILabel new];
        _note.font = AMMono(11, UIFontWeightRegular);
        _note.textColor = AM_MUTED;
        _slider = [UISlider new];
        _slider.minimumTrackTintColor = AM_ACCENT;
        _slider.maximumTrackTintColor = AM_RAISED;
        for (UIView *v in @[_title, _value, _note, _slider]) {
            v.translatesAutoresizingMaskIntoConstraints = NO;
            [self.contentView addSubview:v];
        }
        UILayoutGuide *m = self.contentView.layoutMarginsGuide;
        [NSLayoutConstraint activateConstraints:@[
            [_title.topAnchor constraintEqualToAnchor:m.topAnchor constant:2],
            [_title.leadingAnchor constraintEqualToAnchor:m.leadingAnchor],
            [_title.trailingAnchor constraintLessThanOrEqualToAnchor:_value.leadingAnchor constant:-12],
            [_value.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [_value.firstBaselineAnchor constraintEqualToAnchor:_title.firstBaselineAnchor],
            [_note.topAnchor constraintEqualToAnchor:_title.bottomAnchor constant:2],
            [_note.leadingAnchor constraintEqualToAnchor:m.leadingAnchor],
            [_note.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [_slider.topAnchor constraintEqualToAnchor:_note.bottomAnchor constant:6],
            [_slider.leadingAnchor constraintEqualToAnchor:m.leadingAnchor],
            [_slider.trailingAnchor constraintEqualToAnchor:m.trailingAnchor],
            [_slider.bottomAnchor constraintEqualToAnchor:m.bottomAnchor constant:-2],
        ]];
    }
    return self;
}

@end

#pragma mark - Panel

@interface AMPanel : UITableViewController <UITextFieldDelegate>
@property (nonatomic, strong) NSArray<AMItem *> *items;
@property (nonatomic, strong) NSArray<NSString *> *allTitles;            // sections in the order first added
@property (nonatomic, strong) NSArray<NSArray<NSNumber *> *> *allRows;   // indexes into items
@property (nonatomic, strong) NSArray<NSString *> *shownTitles;          // after the search filter
@property (nonatomic, strong) NSArray<NSArray<NSNumber *> *> *shownRows;
@property (nonatomic, copy) NSString *query;
@property (nonatomic) BOOL folds;  // long menus fold their sections
@property (nonatomic, strong, nullable) NSTimer *statusTimer;
@property (nonatomic, strong) AMSummary *summary;
@property (nonatomic, copy, nullable) void (^onClose)(void);  // the whole menu went away (Done or swipe down)
@end

static NSString *const AMOpenSections = @"AM__openSections";

@implementation AMPanel

- (instancetype)initWithItems:(NSArray<AMItem *> *)items title:(NSString *)title {
    if ((self = [super initWithStyle:UITableViewStyleInsetGrouped])) {
        _items = items;
        _query = @"";
        self.title = title;
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
        _folds = items.count > 30 && titles.count > 1;
        [self filter];
    }
    return self;
}

- (BOOL)isOpen:(NSString *)section {
    if (!self.folds || self.query.length || !section.length) return YES;
    return [[NSUserDefaults.standardUserDefaults arrayForKey:AMOpenSections] containsObject:section];
}

- (void)toggle:(NSString *)section {
    NSMutableArray *open = [[NSUserDefaults.standardUserDefaults arrayForKey:AMOpenSections] mutableCopy] ?: [NSMutableArray new];
    if ([open containsObject:section]) [open removeObject:section];
    else [open addObject:section];
    [NSUserDefaults.standardUserDefaults setObject:open forKey:AMOpenSections];
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
    AMStyleTable(self.tableView);
    self.tableView.estimatedRowHeight = 52;
    self.tableView.backgroundView = [AMBackdrop new];  // fixed behind the rows while they scroll
    self.navigationItem.rightBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemDone target:self action:@selector(close)];
    NSUInteger switches = 0;
    for (AMItem *item in self.items) if (!item.isList) switches++;
    // the summary card, with the search field in it when the menu is long (not a bar floating over the rows)
    self.summary = [[AMSummary alloc] initWithCount:switches search:self.items.count > 12];
    self.summary.search.delegate = self;
    [self.summary.search addTarget:self action:@selector(searchChanged:) forControlEvents:UIControlEventEditingChanged];
    [self sizeSummary];
    self.tableView.tableHeaderView = self.summary;
    [self refreshSummary];
}

- (void)sizeSummary {
    CGFloat width = self.tableView.bounds.size.width ?: UIScreen.mainScreen.bounds.size.width;
    CGRect frame = CGRectMake(0, 0, width, [self.summary heightForWidth:width]);
    if (!CGRectEqualToRect(frame, self.summary.frame)) {
        self.summary.frame = frame;
        self.tableView.tableHeaderView = self.summary;
    }
}

- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    [self sizeSummary];
}

- (BOOL)textFieldShouldReturn:(UITextField *)field {
    [field resignFirstResponder];
    return YES;
}

// The tiles: switches and sliders, how many are on, how many of those reach the game, how many wait.
- (void)refreshSummary {
    NSUInteger all = 0, on = 0, working = 0;
    for (AMItem *item in self.items) {
        if (item.isList) continue;
        all++;
        if (!AMItemOn(item)) continue;
        on++;
        if (AMStatusFor(item.key, NULL)) working++;
    }
    [self.summary showSwitches:all on:on working:working waiting:on - working];
}

- (void)close {
    [self dismissViewControllerAnimated:YES completion:nil];
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView {
    return MAX(self.shownTitles.count, 1);
}

- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {
    if (!self.shownTitles.count) return 0;
    return [self isOpen:self.shownTitles[section]] ? self.shownRows[section].count : 0;
}

// Section header: the mono label, then how many are on, and ▾ / › when the menu folds.
- (UIView *)tableView:(UITableView *)tableView viewForHeaderInSection:(NSInteger)section {
    if (!self.shownTitles.count || !self.shownTitles[section].length) return nil;
    UITableViewHeaderFooterView *head = [tableView dequeueReusableHeaderFooterViewWithIdentifier:@"head"];
    if (!head) {
        head = [[UITableViewHeaderFooterView alloc] initWithReuseIdentifier:@"head"];
        UIButton *button = [UIButton buttonWithType:UIButtonTypeCustom];
        button.tag = 9101;
        button.contentHorizontalAlignment = UIControlContentHorizontalAlignmentLeft;
        button.translatesAutoresizingMaskIntoConstraints = NO;
        [button addTarget:self action:@selector(headerTapped:) forControlEvents:UIControlEventTouchUpInside];
        UILabel *right = [UILabel new];
        right.tag = 9102;
        right.textAlignment = NSTextAlignmentRight;
        right.translatesAutoresizingMaskIntoConstraints = NO;
        [head.contentView addSubview:button];
        [head.contentView addSubview:right];
        UILayoutGuide *g = head.contentView.layoutMarginsGuide;
        [NSLayoutConstraint activateConstraints:@[
            [button.leadingAnchor constraintEqualToAnchor:g.leadingAnchor],
            [button.trailingAnchor constraintEqualToAnchor:g.trailingAnchor],
            [button.topAnchor constraintEqualToAnchor:head.contentView.topAnchor constant:4],
            [button.bottomAnchor constraintEqualToAnchor:head.contentView.bottomAnchor constant:-4],
            [button.heightAnchor constraintGreaterThanOrEqualToConstant:32],
            [right.trailingAnchor constraintEqualToAnchor:g.trailingAnchor],
            [right.centerYAnchor constraintEqualToAnchor:button.centerYAnchor],
        ]];
        right.userInteractionEnabled = NO;
    }
    [self configureHeader:head section:section];
    return head;
}

- (void)configureHeader:(UITableViewHeaderFooterView *)head section:(NSInteger)section {
    NSString *title = self.shownTitles[section];
    UIButton *button = [head.contentView viewWithTag:9101];
    UILabel *right = [head.contentView viewWithTag:9102];
    [button setAttributedTitle:AMLabel(title, AM_MUTED) forState:UIControlStateNormal];
    button.accessibilityIdentifier = title;
    button.userInteractionEnabled = self.folds && !self.query.length;
    NSUInteger on = 0;
    for (NSNumber *n in self.shownRows[section]) if (AMItemOn(self.items[n.integerValue])) on++;
    NSMutableAttributedString *r = [NSMutableAttributedString new];
    if (on) [r appendAttributedString:AMLabel([NSString stringWithFormat:@"%lu on", (unsigned long)on], AM_GOOD)];
    if (button.userInteractionEnabled) {
        NSString *tail = [NSString stringWithFormat:@"%@%lu  %@", on ? @" · " : @"", (unsigned long)self.shownRows[section].count,
                                                                [self isOpen:title] ? @"▾" : @"›"];
        [r appendAttributedString:AMLabel(tail, AM_MUTED)];
    }
    right.attributedText = r;
    button.accessibilityLabel = [NSString stringWithFormat:@"%@, %lu switches, %lu on, %@", title,
                                 (unsigned long)self.shownRows[section].count, (unsigned long)on, [self isOpen:title] ? @"open" : @"folded"];
}

- (CGFloat)tableView:(UITableView *)tableView heightForHeaderInSection:(NSInteger)section {
    return self.shownTitles.count && self.shownTitles[section].length ? UITableViewAutomaticDimension : 8;
}

- (void)headerTapped:(UIButton *)button {
    if (button.accessibilityIdentifier) [self toggle:button.accessibilityIdentifier];
}

- (NSString *)tableView:(UITableView *)tableView titleForFooterInSection:(NSInteger)section {
    if (!self.shownTitles.count) return [NSString stringWithFormat:@"No switch matches “%@”.", self.query];
    if (section != (NSInteger)self.shownTitles.count - 1) return nil;
    return @"Made by Accretion. Settings stay on this device. The game pauses while this menu is open. A dot by a switch that is on: green when it reaches the game, red with what to do.";
}

- (void)tableView:(UITableView *)tableView willDisplayFooterView:(UIView *)view forSection:(NSInteger)section {
    if ([view isKindOfClass:UITableViewHeaderFooterView.class]) {
        ((UITableViewHeaderFooterView *)view).textLabel.textColor = AM_MUTED;
    }
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    NSInteger flat = [self flatIndexAt:indexPath];
    AMItem *item = self.items[flat];
    NSString *kind = item.isList ? @"list" : item.isSlider ? @"slider" : @"switch";
    if (item.isSlider) {
        AMSliderCell *row = [tableView dequeueReusableCellWithIdentifier:kind];
        if (!row) {
            row = [[AMSliderCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:kind];
            [row.slider addTarget:self action:@selector(sliderChanged:) forControlEvents:UIControlEventValueChanged];
        }
        row.title.text = item.title;
        row.slider.minimumValue = item.min;
        row.slider.maximumValue = item.max;
        row.slider.value = [NSUserDefaults.standardUserDefaults floatForKey:AMKey(item.key)];
        row.slider.tag = flat;
        row.note.text = AMNoteFor(item.key);
        [self showValueOf:item inRow:row];
        return row;
    }
    UITableViewCell *cell = [tableView dequeueReusableCellWithIdentifier:kind];
    if (!cell) {
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:kind];
        cell.backgroundColor = AM_SURFACE;
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        cell.textLabel.textColor = AM_FG;
        cell.textLabel.font = [UIFont systemFontOfSize:15];
        cell.textLabel.numberOfLines = 0;  // whole titles, on two lines when they need it
        cell.detailTextLabel.font = [UIFont systemFontOfSize:12];
        cell.detailTextLabel.numberOfLines = 0;
        if (item.isList) {
            cell.selectionStyle = UITableViewCellSelectionStyleDefault;
            cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
            cell.imageView.tintColor = AM_ACCENT_TEXT;
        } else {
            UISwitch *s = [UISwitch new];
            s.onTintColor = AM_ACCENT_FILL;
            [s addTarget:self action:@selector(switchChanged:) forControlEvents:UIControlEventValueChanged];
            cell.accessoryView = s;
            // room for the status dot, drawn over an empty image so titles line up with or without it
            UIGraphicsImageRenderer *blank = [[UIGraphicsImageRenderer alloc] initWithSize:CGSizeMake(8, 8)];
            cell.imageView.image = [blank imageWithActions:^(UIGraphicsImageRendererContext *ctx) {}];
            AMStatusDot *dot = [AMStatusDot new];
            dot.tag = 7001;
            [cell.imageView addSubview:dot];
            cell.imageView.clipsToBounds = NO;
        }
    }
    cell.textLabel.text = item.title;
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;
    if (item.isList) {
        cell.imageView.image = item.symbol ? [UIImage systemImageNamed:item.symbol] : nil;
        cell.detailTextLabel.text = nil;
    } else {
        UISwitch *s = (UISwitch *)cell.accessoryView;
        s.on = [d boolForKey:AMKey(item.key)];
        s.tag = flat;
        [self showStatusOf:item inCell:cell];
    }
    return cell;
}

- (void)showValueOf:(AMItem *)item inRow:(AMSliderCell *)row {
    float v = row.slider.value;
    row.value.text = fabsf(v) >= 100 || v == roundf(v) ? [NSString stringWithFormat:@"%.0f", v] : [NSString stringWithFormat:@"%.1f", v];
    row.value.textColor = v != item.defaultValue ? AM_ACCENT_TEXT : AM_MUTED;
}

- (void)showStatusOf:(AMItem *)item inCell:(UITableViewCell *)cell {
    AMStatusDot *dot = [cell.imageView viewWithTag:7001];
    dot.center = CGPointMake(4, 4);
    BOOL on = [NSUserDefaults.standardUserDefaults boolForKey:AMKey(item.key)];
    dot.hidden = !on;
    if (!on) { cell.detailTextLabel.text = nil; return; }
    NSString *line = nil;
    BOOL ok = AMStatusFor(item.key, &line);
    dot.ok = ok;
    dot.accessibilityValue = line;
    // the green dot says "working" on its own; a line only when something needs doing
    cell.detailTextLabel.text = ok ? nil : line;
    cell.detailTextLabel.textColor = AM_BAD;
}

- (void)refreshStatus {
    BOOL resize = NO;
    for (NSIndexPath *path in self.tableView.indexPathsForVisibleRows) {
        AMItem *item = self.items[[self flatIndexAt:path]];
        if (item.isList) continue;
        UITableViewCell *cell = [self.tableView cellForRowAtIndexPath:path];
        if (!cell) continue;
        if (item.isSlider) {
            AMSliderCell *row = (AMSliderCell *)cell;
            BOOL had = row.note.text.length > 0;
            row.note.text = AMNoteFor(item.key);
            if (had != (row.note.text.length > 0)) resize = YES;
            continue;
        }
        BOOL had = cell.detailTextLabel.text.length > 0;
        [self showStatusOf:item inCell:cell];
        if (had != (cell.detailTextLabel.text.length > 0)) resize = YES;
    }
    [self refreshSummary];
    if (resize) {
        [UIView performWithoutAnimation:^{ [self.tableView beginUpdates]; [self.tableView endUpdates]; }];
    }
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
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
    AMItem *item = self.items[[self flatIndexAt:indexPath]];
    [tableView deselectRowAtIndexPath:indexPath animated:YES];
    if (item.isList) [self.navigationController pushViewController:[[AMListPanel alloc] initWithItem:item] animated:YES];
}

// The "N on" count in a section header follows the switches.
- (void)refreshHeaderOf:(NSInteger)flat {
    NSString *section = self.items[flat].section ?: @"";
    NSUInteger s = [self.shownTitles indexOfObject:section];
    if (s == NSNotFound) return;
    UITableViewHeaderFooterView *head = [self.tableView headerViewForSection:s];
    if (head) [self configureHeader:head section:s];
}

- (void)switchChanged:(UISwitch *)s {
    AMItem *item = self.items[s.tag];
    [NSUserDefaults.standardUserDefaults setBool:s.on forKey:AMKey(item.key)];
    if (item.onSwitch) item.onSwitch(s.on);
    if (!s.on) os_log(OS_LOG_DEFAULT, "[AccretionMods] status %{public}@ off", item.key);
    [[[UISelectionFeedbackGenerator alloc] init] selectionChanged];
    NSIndexPath *path = [self pathOfFlat:s.tag];
    UITableViewCell *cell = path ? [self.tableView cellForRowAtIndexPath:path] : nil;
    if (cell) {
        [self showStatusOf:item inCell:cell];
        [self.tableView beginUpdates];  // the status line appears or goes: let the row resize
        [self.tableView endUpdates];
    }
    [self refreshHeaderOf:s.tag];
    [self refreshSummary];
}

- (void)sliderChanged:(UISlider *)s {
    AMItem *item = self.items[s.tag];
    [NSUserDefaults.standardUserDefaults setFloat:s.value forKey:AMKey(item.key)];
    NSIndexPath *path = [self pathOfFlat:s.tag];
    AMSliderCell *row = path ? (AMSliderCell *)[self.tableView cellForRowAtIndexPath:path] : nil;
    if ([row isKindOfClass:AMSliderCell.class]) [self showValueOf:item inRow:row];
    if (item.onSlider) item.onSlider(s.value);
    [self refreshHeaderOf:s.tag];
    [self refreshSummary];
}

@end

#pragma mark - Floating button

typedef NS_ENUM(NSInteger, AMRingState) { AMRingNone, AMRingGood, AMRingSome, AMRingBad };

@interface AMButton : UIButton
@property (nonatomic, strong) CAShapeLayer *ring;
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
    }
    return self;
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
    if (state == _ringState && (!pulses || UIAccessibilityIsReduceMotionEnabled() || [_ring animationForKey:@"pulse"])) return;
    _ringState = state;
    _ring.hidden = state == AMRingNone;
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
    [NSUserDefaults.standardUserDefaults setFloat:value forKey:AMKey(key)];
    if (item.onSlider) item.onSlider(value);
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
        if (item.isList || item.isSlider || ![NSUserDefaults.standardUserDefaults boolForKey:AMKey(item.key)]) continue;
        on++;
        NSString *line = nil;
        BOOL ok = AMStatusFor(item.key, &line);
        if (!ok) failing++;
        if (resend) os_log(OS_LOG_DEFAULT, "[AccretionMods] status %{public}@ %{public}@", item.key, ok ? @"ok" : [@"waiting: " stringByAppendingString:line]);
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
    AMPanel *panel = [[AMPanel alloc] initWithItems:_items title:self.title];
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
