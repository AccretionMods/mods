#import "AMMenu.h"
#import <os/lock.h>
#import <os/log.h>

static NSString *AMKey(NSString *key) {
    return [@"AM_" stringByAppendingString:key];
}

#pragma mark - Live status

// What each switch last reported (AMMenuStatus). A fixed table: hooks may report from any thread and often.
typedef struct {
    char key[48];
    bool ok;
    char reason[160];
    CFAbsoluteTime at;
} AMStatusEntry;
static AMStatusEntry sStatus[96];
static int sStatusCount;
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
    // Mod Studio reads these from the device log (am log) to show each switch's state live
    if (changed) os_log(OS_LOG_DEFAULT, "[AccretionMods] %{public}s", line);
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

static UIColor *AMNeon(BOOL ok) {
    return ok ? [UIColor colorWithRed:0.22 green:1.00 blue:0.08 alpha:1]   // neon green
              : [UIColor colorWithRed:1.00 green:0.19 blue:0.19 alpha:1];  // neon red
}

// A glowing dot that pulses (steady when Reduce Motion is on).
@interface AMStatusDot : UIView
@property (nonatomic) BOOL ok;
@end

@implementation AMStatusDot

- (instancetype)init {
    if ((self = [super initWithFrame:CGRectMake(0, 0, 12, 12)])) {
        self.userInteractionEnabled = NO;
        self.layer.cornerRadius = 6;
        self.layer.shadowOffset = CGSizeZero;
        self.layer.shadowRadius = 6;
        self.layer.shadowOpacity = 0.9;
        self.isAccessibilityElement = YES;
        [self setOk:NO];
    }
    return self;
}

- (void)setOk:(BOOL)ok {
    _ok = ok;
    UIColor *c = AMNeon(ok);
    self.backgroundColor = c;
    self.layer.shadowColor = c.CGColor;
    self.accessibilityLabel = ok ? @"Working" : @"Not working";
}

- (void)didMoveToWindow {
    [super didMoveToWindow];
    [self.layer removeAllAnimations];
    if (!self.window || UIAccessibilityIsReduceMotionEnabled()) return;
    CABasicAnimation *glow = [CABasicAnimation animationWithKeyPath:@"shadowRadius"];
    glow.fromValue = @3;
    glow.toValue = @9;
    CABasicAnimation *fade = [CABasicAnimation animationWithKeyPath:@"opacity"];
    fade.fromValue = @1;
    fade.toValue = @0.55;
    CAAnimationGroup *pulse = [CAAnimationGroup animation];
    pulse.animations = @[glow, fade];
    pulse.duration = 0.9;
    pulse.autoreverses = YES;
    pulse.repeatCount = HUGE_VALF;
    pulse.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
    [self.layer addAnimation:pulse forKey:@"pulse"];
}

@end

@interface AMItem : NSObject
@property (nonatomic, copy) NSString *key;
@property (nonatomic, copy) NSString *title;
@property (nonatomic) BOOL isSlider;
@property (nonatomic) float min, max;
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
                              : @"Nothing to list yet: open this again once the game has loaded.";
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    NSDictionary *e = self.entries[indexPath.row];
    UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleValue1 reuseIdentifier:nil];
    cell.textLabel.text = e[@"title"];
    cell.detailTextLabel.text = e[@"detail"];
    if (e[@"symbol"]) cell.imageView.image = [UIImage systemImageNamed:e[@"symbol"]];
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

#pragma mark - Panel

@interface AMPanel : UITableViewController
@property (nonatomic, strong) NSArray<AMItem *> *items;
@property (nonatomic, strong) NSArray<NSString *> *sectionTitles;           // in the order first added
@property (nonatomic, strong) NSArray<NSArray<NSNumber *> *> *sectionRows;  // indexes into items
@property (nonatomic, strong, nullable) NSTimer *statusTimer;
@property (nonatomic, copy, nullable) void (^onClose)(void);  // the whole menu went away (Done or swipe down)
@end

@implementation AMPanel

- (instancetype)initWithItems:(NSArray<AMItem *> *)items title:(NSString *)title {
    if ((self = [super initWithStyle:UITableViewStyleInsetGrouped])) {
        _items = items;
        self.title = title;
        NSMutableArray<NSString *> *titles = [NSMutableArray new];
        NSMutableDictionary<NSString *, NSMutableArray<NSNumber *> *> *rows = [NSMutableDictionary new];
        [items enumerateObjectsUsingBlock:^(AMItem *item, NSUInteger i, BOOL *stop) {
            NSString *t = item.section ?: @"";
            if (!rows[t]) { rows[t] = [NSMutableArray new]; [titles addObject:t]; }
            [rows[t] addObject:@(i)];
        }];
        _sectionTitles = titles;
        NSMutableArray *ordered = [NSMutableArray new];
        for (NSString *t in titles) [ordered addObject:rows[t]];
        _sectionRows = ordered;
    }
    return self;
}

- (NSInteger)flatIndexAt:(NSIndexPath *)path {
    return self.sectionRows[path.section][path.row].integerValue;
}

- (NSIndexPath *)pathOfFlat:(NSInteger)flat {
    for (NSUInteger s = 0; s < self.sectionRows.count; s++) {
        NSUInteger r = [self.sectionRows[s] indexOfObject:@(flat)];
        if (r != NSNotFound) return [NSIndexPath indexPathForRow:r inSection:s];
    }
    return [NSIndexPath indexPathForRow:0 inSection:0];
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView {
    return self.sectionTitles.count;
}

- (NSString *)tableView:(UITableView *)tableView titleForHeaderInSection:(NSInteger)section {
    return self.sectionTitles[section].length ? self.sectionTitles[section] : nil;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.navigationItem.rightBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemDone
                                                      target:self
                                                      action:@selector(close)];
}

- (void)close {
    [self dismissViewControllerAnimated:YES completion:nil];
}

- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {
    return self.sectionRows[section].count;
}

- (NSString *)tableView:(UITableView *)tableView titleForFooterInSection:(NSInteger)section {
    if (section != (NSInteger)self.sectionTitles.count - 1) return nil;
    return @"Made by Accretion. Settings are saved on this device. A switch that is on shows a dot: green when its cheat is working, red when it is not, with what to do. The ring around the menu button sums them up: green all working, yellow some, red none.";
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    NSInteger flat = [self flatIndexAt:indexPath];
    AMItem *item = self.items[flat];
    BOOL isSwitch = !item.isList && !item.isSlider;
    UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:isSwitch ? UITableViewCellStyleSubtitle : UITableViewCellStyleValue1 reuseIdentifier:nil];
    cell.selectionStyle = UITableViewCellSelectionStyleNone;
    cell.textLabel.text = item.title;
    cell.textLabel.numberOfLines = 0;  // whole titles, on two lines when they need it
    NSUserDefaults *d = NSUserDefaults.standardUserDefaults;

    if (item.isList) {
        cell.selectionStyle = UITableViewCellSelectionStyleDefault;
        cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
        if (item.symbol) cell.imageView.image = [UIImage systemImageNamed:item.symbol];
    } else if (item.isSlider) {
        UISlider *s = [[UISlider alloc] initWithFrame:CGRectMake(0, 0, 150, 30)];
        s.minimumValue = item.min;
        s.maximumValue = item.max;
        s.value = [d floatForKey:AMKey(item.key)];
        s.tag = flat;
        [s addTarget:self action:@selector(sliderChanged:) forControlEvents:UIControlEventValueChanged];
        cell.accessoryView = s;
        cell.detailTextLabel.text = [NSString stringWithFormat:@"%.1f", s.value];
    } else {
        UISwitch *s = [UISwitch new];
        s.on = [d boolForKey:AMKey(item.key)];
        s.tag = flat;
        [s addTarget:self action:@selector(switchChanged:) forControlEvents:UIControlEventValueChanged];
        cell.accessoryView = s;
        // room for the status dot, drawn on top of an empty image so the title lines up with or without it
        UIGraphicsImageRenderer *blank = [[UIGraphicsImageRenderer alloc] initWithSize:CGSizeMake(14, 14)];
        cell.imageView.image = [blank imageWithActions:^(UIGraphicsImageRendererContext *ctx) {}];
        AMStatusDot *dot = [AMStatusDot new];
        dot.tag = 7001;
        dot.center = CGPointMake(7, 7);
        [cell.imageView addSubview:dot];
        cell.imageView.clipsToBounds = NO;
        [self showStatusOf:item inCell:cell];
    }
    return cell;
}

- (void)showStatusOf:(AMItem *)item inCell:(UITableViewCell *)cell {
    AMStatusDot *dot = [cell.imageView viewWithTag:7001];
    BOOL on = [NSUserDefaults.standardUserDefaults boolForKey:AMKey(item.key)];
    dot.hidden = !on;
    if (!on) { cell.detailTextLabel.text = nil; return; }
    NSString *line = nil;
    BOOL ok = AMStatusFor(item.key, &line);
    dot.ok = ok;
    dot.accessibilityValue = line;
    // the green dot says "working" on its own; a line only when something needs doing
    cell.detailTextLabel.text = ok ? nil : line;
    cell.detailTextLabel.textColor = UIColor.systemRedColor;
    cell.detailTextLabel.numberOfLines = 0;
}

- (void)refreshStatus {
    BOOL resize = NO;
    for (NSIndexPath *path in self.tableView.indexPathsForVisibleRows) {
        AMItem *item = self.items[[self flatIndexAt:path]];
        if (item.isList || item.isSlider) continue;
        UITableViewCell *cell = [self.tableView cellForRowAtIndexPath:path];
        if (!cell) continue;
        BOOL had = cell.detailTextLabel.text.length > 0;
        [self showStatusOf:item inCell:cell];
        if (had != (cell.detailTextLabel.text.length > 0)) resize = YES;
    }
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

- (void)switchChanged:(UISwitch *)s {
    AMItem *item = self.items[s.tag];
    [NSUserDefaults.standardUserDefaults setBool:s.on forKey:AMKey(item.key)];
    if (item.onSwitch) item.onSwitch(s.on);
    if (!s.on) os_log(OS_LOG_DEFAULT, "[AccretionMods] status %{public}@ off", item.key);
    UITableViewCell *cell = [self.tableView cellForRowAtIndexPath:[self pathOfFlat:s.tag]];
    if (cell) {
        [self showStatusOf:item inCell:cell];
        [self.tableView beginUpdates];  // the status line appears or goes: let the row resize
        [self.tableView endUpdates];
    }
}

- (void)sliderChanged:(UISlider *)s {
    AMItem *item = self.items[s.tag];
    [NSUserDefaults.standardUserDefaults setFloat:s.value forKey:AMKey(item.key)];
    UITableViewCell *cell = [self.tableView cellForRowAtIndexPath:[self pathOfFlat:s.tag]];
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%.1f", s.value];
    if (item.onSlider) item.onSlider(s.value);
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
        self.backgroundColor = [UIColor colorWithWhite:0.1 alpha:0.75];
        self.layer.cornerRadius = 22;
        self.layer.zPosition = 9999;
        self.accessibilityLabel = @"Mod menu";
        UIImage *icon = [UIImage systemImageNamed:@"slider.horizontal.3"];
        [self setImage:icon forState:UIControlStateNormal];
        self.tintColor = UIColor.whiteColor;
        [self addGestureRecognizer:[[UIPanGestureRecognizer alloc] initWithTarget:self action:@selector(drag:)]];
        [self addTarget:self action:@selector(wake) forControlEvents:UIControlEventTouchDown];
        // the status ring just outside the button: green all working, yellow some, red none (AMMenu updateBadge)
        _ring = [CAShapeLayer layer];
        _ring.path = [UIBezierPath bezierPathWithOvalInRect:CGRectInset(self.bounds, -3, -3)].CGPath;
        _ring.fillColor = UIColor.clearColor.CGColor;
        _ring.lineWidth = 3;
        _ring.shadowOffset = CGSizeZero;
        _ring.shadowRadius = 6;
        _ring.shadowOpacity = 0.9;
        _ring.hidden = YES;
        [self.layer addSublayer:_ring];
    }
    return self;
}

- (void)setRingState:(AMRingState)state {
    // same state: nothing to do, unless iOS dropped the pulse (it removes layer animations in the background)
    BOOL pulses = (state == AMRingSome || state == AMRingBad) && !UIAccessibilityIsReduceMotionEnabled();
    if (state == _ringState && (!pulses || [_ring animationForKey:@"pulse"])) return;
    _ringState = state;
    _ring.hidden = state == AMRingNone;
    UIColor *c = state == AMRingGood ? [UIColor colorWithRed:0.22 green:1.00 blue:0.08 alpha:1]   // neon green
               : state == AMRingSome ? [UIColor colorWithRed:1.00 green:0.86 blue:0.10 alpha:1]   // neon yellow
                                     : [UIColor colorWithRed:1.00 green:0.19 blue:0.19 alpha:1];  // neon red
    _ring.strokeColor = c.CGColor;
    _ring.shadowColor = c.CGColor;
    [_ring removeAllAnimations];
    // calm when all is well; a pulse only when something needs a look (none with Reduce Motion)
    if ((state == AMRingSome || state == AMRingBad) && !UIAccessibilityIsReduceMotionEnabled()) {
        CABasicAnimation *glow = [CABasicAnimation animationWithKeyPath:@"shadowRadius"];
        glow.fromValue = @3;
        glow.toValue = @11;
        CABasicAnimation *fade = [CABasicAnimation animationWithKeyPath:@"opacity"];
        fade.fromValue = @1;
        fade.toValue = @0.5;
        CAAnimationGroup *pulse = [CAAnimationGroup animation];
        pulse.animations = @[glow, fade];
        pulse.duration = state == AMRingBad ? 0.6 : 1.0;
        pulse.autoreverses = YES;
        pulse.repeatCount = HUGE_VALF;
        pulse.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
        [_ring addAnimation:pulse forKey:@"pulse"];
    }
}

- (CGRect)allowedArea {
    UIView *parent = self.superview;
    CGFloat r = self.bounds.size.width / 2 + 6;  // the ring and its glow stay on screen
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

- (BOOL)hasItem:(NSString *)key {
    for (AMItem *item in _items) if ([item.key isEqualToString:key]) return YES;
    return NO;
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

- (void)open:(UIButton *)sender {
    [[[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleLight] impactOccurred];
    UIViewController *top = sender.window.rootViewController;
    while (top.presentedViewController) top = top.presentedViewController;
    if (!top) return;
    AMPanel *panel = [[AMPanel alloc] initWithItems:_items title:self.title];
    UINavigationController *nav = [[UINavigationController alloc] initWithRootViewController:panel];
    // the floating button sits above everything (zPosition): hide it while the menu is open so it covers no row
    __weak typeof(self) weak = self;
    panel.onClose = ^{ [weak setButtonsHidden:NO]; };
    [self setButtonsHidden:YES];
    [top presentViewController:nav animated:YES completion:nil];
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
