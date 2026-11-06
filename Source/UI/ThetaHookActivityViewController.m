#import <UIKit/UIKit.h>

// Defined in Source/Runtime/THGlobalsAndHooking.m (unity TU).
extern NSString *THHookActivityReport(void);
extern void THHookActivityReset(void);

/// Theta settings → gear → Hook Activity: every installed hook with its cumulative fire count,
/// never-fired first. Read-only apart from Copy and Reset.
@interface ThetaHookActivityViewController : UIViewController
@end

@implementation ThetaHookActivityViewController {
    UITextView *_textView;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = @"Hook Activity";
    self.view.backgroundColor = [UIColor systemBackgroundColor];

    _textView = [[UITextView alloc] initWithFrame:CGRectZero];
    _textView.translatesAutoresizingMaskIntoConstraints = NO;
    _textView.editable = NO;
    _textView.font = [UIFont monospacedSystemFontOfSize:11 weight:UIFontWeightRegular];
    _textView.textContainerInset = UIEdgeInsetsMake(12, 12, 12, 12);
    [self.view addSubview:_textView];
    [NSLayoutConstraint activateConstraints:@[
        [_textView.topAnchor constraintEqualToAnchor:self.view.topAnchor],
        [_textView.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor],
        [_textView.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [_textView.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor],
    ]];

    self.navigationItem.leftBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemDone target:self action:@selector(close)];
    UIBarButtonItem *copy = [[UIBarButtonItem alloc] initWithImage:[UIImage systemImageNamed:@"doc.on.doc"]
                                                             style:UIBarButtonItemStylePlain target:self action:@selector(copyReport)];
    UIBarButtonItem *reset = [[UIBarButtonItem alloc] initWithImage:[UIImage systemImageNamed:@"arrow.counterclockwise"]
                                                              style:UIBarButtonItemStylePlain target:self action:@selector(confirmReset)];
    self.navigationItem.rightBarButtonItems = @[ copy, reset ];
    [self reload];
}

- (void)reload {
    _textView.text = THHookActivityReport() ?: @"(no report)";
}

- (void)close {
    [self dismissViewControllerAnimated:YES completion:nil];
}

- (void)copyReport {
    [UIPasteboard generalPasteboard].string = _textView.text;
}

- (void)confirmReset {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Reset hook counts?"
                                                                   message:@"Counting starts again from zero."
                                                            preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:nil]];
    __weak typeof(self) weakSelf = self;
    [alert addAction:[UIAlertAction actionWithTitle:@"Reset" style:UIAlertActionStyleDestructive handler:^(__unused UIAlertAction *a) {
        THHookActivityReset();
        [weakSelf reload];
    }]];
    [self presentViewController:alert animated:YES completion:nil];
}

@end
