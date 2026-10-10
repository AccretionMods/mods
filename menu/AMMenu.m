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
@end

@implementation AMItem
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

    if (item.isSlider) {
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
