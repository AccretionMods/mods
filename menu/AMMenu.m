#import "AMMenu.h"

static NSString *AMKey(NSString *key) {
    return [@"AM_" stringByAppendingString:key];
}

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
    return @"Made by Accretion. Settings are saved on this device.";
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    AMItem *item = self.items[indexPath.row];
    UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleValue1 reuseIdentifier:nil];
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
    }
    return cell;
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
