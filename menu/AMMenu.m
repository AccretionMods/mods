#import "AMMenu.h"
#import <os/lock.h>

static NSString *AMKey(NSString *key) {
    return [@"AM_" stringByAppendingString:key];
}

#pragma mark - Live status

// What each switch last reported (AMMenuStatus). A fixed table: hooks may report from any thread and often.
typedef struct {
    char key[48];
    bool ok;
    char reason[96];
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
    if (e) {
        e->ok = ok;
        strlcpy(e->reason, ok ? "" : (reason ?: "not reaching the game"), sizeof e->reason);
        e->at = CFAbsoluteTimeGetCurrent();
    }
    os_unfair_lock_unlock(&sStatusLock);
}

// Whether a switch's cheat is working now, and the line shown under its title.
static BOOL AMStatusFor(NSString *key, NSString **line) {
    BOOL ok = NO;
    NSString *text = @"No report from the mod yet (is the game loaded?)";
    os_unfair_lock_lock(&sStatusLock);
    for (int i = 0; i < sStatusCount; i++) {
        if (strcmp(sStatus[i].key, key.UTF8String)) continue;
        if (CFAbsoluteTimeGetCurrent() - sStatus[i].at > AMStatusStale) {
            text = @"No report for 15 s: not applying";
        } else if (sStatus[i].ok) {
            ok = YES;
            text = @"Working";
        } else {
            text = [NSString stringWithFormat:@"Not working: %s", sStatus[i].reason];
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
@property (nonatomic, strong, nullable) NSTimer *statusTimer;
@end

@implementation AMPanel

- (instancetype)initWithItems:(NSArray<AMItem *> *)items title:(NSString *)title {
    if ((self = [super initWithStyle:UITableViewStyleInsetGrouped])) {
        _items = items;
        self.title = title;
    }
    return self;
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
    return self.items.count;
}

- (NSString *)tableView:(UITableView *)tableView titleForFooterInSection:(NSInteger)section {
    return @"Made by Accretion. Settings are saved on this device. A switch that is on shows a dot: green when its cheat is reaching the game, red when it is not, with the reason.";
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    AMItem *item = self.items[indexPath.row];
    BOOL isSwitch = !item.isList && !item.isSlider;
    UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:isSwitch ? UITableViewCellStyleSubtitle : UITableViewCellStyleValue1 reuseIdentifier:nil];
    cell.selectionStyle = UITableViewCellSelectionStyleNone;
    cell.textLabel.text = item.title;
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
        s.tag = indexPath.row;
        [s addTarget:self action:@selector(sliderChanged:) forControlEvents:UIControlEventValueChanged];
        cell.accessoryView = s;
        cell.detailTextLabel.text = [NSString stringWithFormat:@"%.1f", s.value];
    } else {
        UISwitch *s = [UISwitch new];
        s.on = [d boolForKey:AMKey(item.key)];
        s.tag = indexPath.row;
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
    cell.detailTextLabel.text = line;
    cell.detailTextLabel.textColor = ok ? [UIColor colorWithRed:0.10 green:0.70 blue:0.20 alpha:1] : UIColor.systemRedColor;
    cell.detailTextLabel.numberOfLines = 0;
}

- (void)refreshStatus {
    for (NSIndexPath *path in self.tableView.indexPathsForVisibleRows) {
        AMItem *item = self.items[path.row];
        if (item.isList || item.isSlider) continue;
        UITableViewCell *cell = [self.tableView cellForRowAtIndexPath:path];
        if (cell) [self showStatusOf:item inCell:cell];
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

- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    AMItem *item = self.items[indexPath.row];
    [tableView deselectRowAtIndexPath:indexPath animated:YES];
    if (item.isList) [self.navigationController pushViewController:[[AMListPanel alloc] initWithItem:item] animated:YES];
}

- (void)switchChanged:(UISwitch *)s {
    AMItem *item = self.items[s.tag];
    [NSUserDefaults.standardUserDefaults setBool:s.on forKey:AMKey(item.key)];
    if (item.onSwitch) item.onSwitch(s.on);
    UITableViewCell *cell = [self.tableView cellForRowAtIndexPath:[NSIndexPath indexPathForRow:s.tag inSection:0]];
    if (cell) {
        [self showStatusOf:item inCell:cell];
        [self.tableView beginUpdates];  // the status line appears or goes: let the row resize
        [self.tableView endUpdates];
    }
}

- (void)sliderChanged:(UISlider *)s {
    AMItem *item = self.items[s.tag];
    [NSUserDefaults.standardUserDefaults setFloat:s.value forKey:AMKey(item.key)];
    UITableViewCell *cell = [self.tableView cellForRowAtIndexPath:[NSIndexPath indexPathForRow:s.tag inSection:0]];
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%.1f", s.value];
    if (item.onSlider) item.onSlider(s.value);
}

@end

#pragma mark - Floating button

@interface AMButton : UIButton
@property (nonatomic, strong) AMStatusDot *dot;
@end

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
        _dot = [AMStatusDot new];
        _dot.center = CGPointMake(38, 6);
        _dot.hidden = YES;
        [self addSubview:_dot];
    }
    return self;
}

- (void)drag:(UIPanGestureRecognizer *)pan {
    UIView *parent = self.superview;
    CGPoint t = [pan translationInView:parent];
    CGRect b = UIEdgeInsetsInsetRect(parent.bounds, parent.safeAreaInsets);
    CGFloat r = self.bounds.size.width / 2;
    self.center = CGPointMake(MIN(MAX(self.center.x + t.x, CGRectGetMinX(b) + r), CGRectGetMaxX(b) - r),
                              MIN(MAX(self.center.y + t.y, CGRectGetMinY(b) + r), CGRectGetMaxY(b) - r));
    [pan setTranslation:CGPointZero inView:parent];
}

@end

#pragma mark - Menu

@implementation AMMenu {
    NSMutableArray<AMItem *> *_items;
    NSHashTable<UIWindow *> *_windows;
    NSHashTable<AMButton *> *_buttons;
    NSTimer *_badgeTimer;
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

- (void)addSwitch:(NSString *)key title:(NSString *)title defaultValue:(BOOL)value onChange:(void (^)(BOOL))block {
    [NSUserDefaults.standardUserDefaults registerDefaults:@{AMKey(key): @(value)}];
    AMItem *item = [AMItem new];
    item.key = key;
    item.title = title;
    item.onSwitch = block;
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
    [_items addObject:item];
}

- (void)addList:(NSString *)title symbol:(NSString *)symbol entries:(NSArray<NSDictionary<NSString *, NSString *> *> *(^)(void))entries onPick:(void (^)(NSInteger))block {
    AMItem *item = [AMItem new];
    item.title = title;
    item.isList = YES;
    item.symbol = symbol;
    item.entries = entries;
    item.onPick = block;
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
    [_buttons addObject:button];
    if (!_badgeTimer) {
        __weak typeof(self) weak = self;
        _badgeTimer = [NSTimer scheduledTimerWithTimeInterval:1 repeats:YES block:^(NSTimer *t) { [weak updateBadge]; }];
    }
}

// The button's dot: hidden when no switch is on, red when any switch that is on is not working, else green.
- (void)updateBadge {
    NSUInteger on = 0, failing = 0;
    for (AMItem *item in _items) {
        if (item.isList || item.isSlider || ![NSUserDefaults.standardUserDefaults boolForKey:AMKey(item.key)]) continue;
        on++;
        if (!AMStatusFor(item.key, NULL)) failing++;
    }
    for (AMButton *button in _buttons) {
        button.dot.hidden = on == 0;
        button.dot.ok = failing == 0;
        button.accessibilityValue = on == 0 ? nil
            : failing ? [NSString stringWithFormat:@"%lu of %lu cheats not working", (unsigned long)failing, (unsigned long)on]
                      : [NSString stringWithFormat:@"%lu cheats working", (unsigned long)on];
    }
}

- (void)open:(UIButton *)sender {
    UIViewController *top = sender.window.rootViewController;
    while (top.presentedViewController) top = top.presentedViewController;
    if (!top) return;
    AMPanel *panel = [[AMPanel alloc] initWithItems:_items title:self.title];
    UINavigationController *nav = [[UINavigationController alloc] initWithRootViewController:panel];
    [top presentViewController:nav animated:YES completion:nil];
}

@end
