/*
 * Shared tweak globals + ObjC method swizzle helpers.
 * Concatenated first into TweakCOMPILE.xm by scripts/assemble.py.
 */

#import "Include/ThetaTweakCommon.h"
#import "Include.h"
#import <os/lock.h>
#import "Include/THProfileAnalyzerViewController.h"
#import "fishhook.h"
#import <Photos/Photos.h>
#import "Include/ThetaHelper.h"
#import <os/log.h>
#import <objc/runtime.h>
#import <dlfcn.h>
#import <sys/mman.h>
#import <mach-o/dyld.h>
#import <mach-o/nlist.h>
#import <UserNotifications/UserNotifications.h>
#import <mach-o/loader.h>
#import <Security/Security.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#import <Foundation/Foundation.h>

NSString *appVersion;
NSString *keychainAccessGroup;
NSURL *fakeGroupContainerURL;
static BOOL shouldBeSeen = false;
static BOOL storeUserSearch = NO;
static NSTimeInterval lastSpamTime = 0;
static BOOL hooksInitialized = NO;
static NSTimeInterval s_lastToastShowTime = 0;

/**
 * Cached `<setting>_Enabled` lookup behind the ENABLED() macro.
 *
 * Hot enough to matter: an unfiltered device capture logged 997 Theta preference reads in 35
 * seconds — 42% of all CFPrefs traffic in the process — because the old macro allocated a key
 * with `stringWithFormat:` and hit NSUserDefaults on every call, from predicates UIKit
 * evaluates while rendering.
 *
 * The cache is dropped on any defaults change, and again when the app becomes active so an
 * out-of-process write cannot leave it stale.
 */
BOOL ThetaSettingEnabled(NSString *setting) {
#if defined(THETA_CONTROL) && THETA_CONTROL >= 1
    // Control build: every feature reads as off regardless of what is stored in prefs, so a
    // capture cannot be contaminated by whatever the device happened to have toggled on.
    // Note this only silences feature *behaviour* — at level 1 every hook is still installed.
    return NO;
#else
    if (setting.length == 0) {
        return NO;
    }

    static NSMutableDictionary<NSString *, NSNumber *> *sSettingCache;
    static os_unfair_lock sSettingLock = OS_UNFAIR_LOCK_INIT;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        sSettingCache = [NSMutableDictionary dictionary];
        void (^flush)(NSNotification *) = ^(__unused NSNotification *note) {
            os_unfair_lock_lock(&sSettingLock);
            [sSettingCache removeAllObjects];
            os_unfair_lock_unlock(&sSettingLock);
        };
        [[NSNotificationCenter defaultCenter] addObserverForName:NSUserDefaultsDidChangeNotification
                                                          object:nil
                                                           queue:nil
                                                      usingBlock:flush];
        [[NSNotificationCenter defaultCenter] addObserverForName:UIApplicationDidBecomeActiveNotification
                                                          object:nil
                                                           queue:nil
                                                      usingBlock:flush];
    });

    os_unfair_lock_lock(&sSettingLock);
    NSNumber *cached = sSettingCache[setting];
    os_unfair_lock_unlock(&sSettingLock);
    if (cached) {
        return cached.boolValue;
    }

    BOOL value = [[NSUserDefaults standardUserDefaults]
                     boolForKey:[setting stringByAppendingString:@"_Enabled"]];

    os_unfair_lock_lock(&sSettingLock);
    sSettingCache[setting] = @(value);
    os_unfair_lock_unlock(&sSettingLock);
    return value;
#endif
}

static NSMutableArray *sFailedHookLines;
static NSLock *sFailedHookLock;

static void FailedHooksEnsureInit(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        sFailedHookLines = [NSMutableArray array];
        sFailedHookLock = [[NSLock alloc] init];
    });
}

static void RecordFailedHookLine(NSString *line) {
    if (!line.length) return;
    FailedHooksEnsureInit();
    [sFailedHookLock lock];
    [sFailedHookLines addObject:[line copy]];
    [sFailedHookLock unlock];
}

static UIViewController *ThetaPresenterForAlert(void) {
    UIApplication *app = [UIApplication sharedApplication];
    UIWindow *window = app.keyWindow;
    if (!window) {
        for (UIWindow *w in app.windows) {
            if (w.isKeyWindow && !w.hidden) {
                window = w;
                break;
            }
        }
    }
    if (!window && app.windows.count) {
        window = app.windows[0];
    }
    if (!window) return nil;
    UIViewController *vc = window.rootViewController;
    while (vc && vc.presentedViewController) {
        vc = vc.presentedViewController;
    }
    return vc;
}

static void PresentAggregatedHookFailureAlert(void) {
    FailedHooksEnsureInit();
    NSArray *lines;
    [sFailedHookLock lock];
    lines = [sFailedHookLines copy];
    [sFailedHookLines removeAllObjects];
    [sFailedHookLock unlock];
    if (lines.count == 0) return;

    NSUInteger n = lines.count;
    NSString *title = [NSString stringWithFormat:@"Theta Hook Errors (%lu)", (unsigned long)n];
    NSString *message = [lines componentsJoinedByString:@"\n"];
    const NSUInteger kMaxLen = 3500;
    if (message.length > kMaxLen) {
        message = [[message substringToIndex:kMaxLen] stringByAppendingString:@"…"];
    }

    UIAlertController *alert = [UIAlertController alertControllerWithTitle:title
                                                                   message:message
                                                            preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];

    UIViewController *root = ThetaPresenterForAlert();
    if (root) {
        [root presentViewController:alert animated:YES completion:nil];
    } else {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            UIViewController *retry = ThetaPresenterForAlert();
            if (retry) {
                [retry presentViewController:alert animated:YES completion:nil];
            }
        });
    }
}

/** KVC that never throws (Swift/ObjC ivars often aren't KVC-compliant). */
static id ThetaValueForKey(id obj, NSString *key) {
    if (!obj || !key.length) return nil;
    @try {
        return [obj valueForKey:key];
    } @catch (__unused NSException *e) {
        return nil;
    }
}

static void ThetaSetValueForKey(id obj, id value, NSString *key) {
    if (!obj || !key.length) return;
    @try {
        [obj setValue:value forKey:key];
    } @catch (__unused NSException *e) {
    }
}

static void ThetaSetCaptureHiding(UIView *view) {
    if (!view) return;
    if (ENABLED(@"Hide Theta From Screenshots")) {
        ThetaSetValueForKey(view.layer, @((1 << 1) | (1 << 4)), @"disableUpdateMask");
    }
}

#pragma mark - Hook fire counters

/*
 * Every hook installed through ThetaInstallMessageHook gets a trampoline from
 * THHookCounterTramp.S that counts calls, then branches to the real hook. Counts accumulate
 * across launches in NSUserDefaults (flushed on background/terminate), so a hook still at zero
 * after normal use is one that installed but never fired — the 454 `-mediaView` failure mode,
 * which no static check can see. Shown under Theta settings → gear → Hook Activity.
 */
#define TH_HOOK_SLOT_COUNT 1024
typedef struct { void *target; volatile uint64_t count; } THHookSlot;
extern THHookSlot th_hook_slots[TH_HOOK_SLOT_COUNT];
extern char th_hook_tramps[];
static char *th_hook_names[TH_HOOK_SLOT_COUNT];
static uint32_t th_hook_used = 0;
static uint32_t th_hook_overflow = 0;
static NSString *const kTHHookTotalsKey = @"Theta_HookActivity_Totals";
static NSString *const kTHHookSinceKey = @"Theta_HookActivity_Since";
static NSDictionary<NSString *, NSNumber *> *th_hook_baseTotals;

/// Returns the IMP to install: a counting trampoline, or `replacement` itself if the pool is spent.
static void *THHookCounterWrap(void *replacement, Class cls, SEL sel, BOOL isClassMethod) {
#if defined(__arm64__) || defined(__aarch64__)
    if (!replacement) return replacement;
    if (th_hook_used >= TH_HOOK_SLOT_COUNT) { th_hook_overflow++; return replacement; }
    uint32_t i = th_hook_used++;
    th_hook_slots[i].target = replacement;
    th_hook_slots[i].count = 0;
    NSString *name = [NSString stringWithFormat:@"%c[%@ %@]", isClassMethod ? '+' : '-',
                      NSStringFromClass(cls) ?: @"?", NSStringFromSelector(sel) ?: @"?"];
    th_hook_names[i] = strdup(name.UTF8String ?: "?");
    __atomic_thread_fence(__ATOMIC_SEQ_CST); // slot visible before the trampoline is installed
    return (void *)(th_hook_tramps + 8 * i);
#else
    return replacement;
#endif
}

/// Name → total fires: persisted totals from earlier launches plus this launch's live counts.
static NSDictionary<NSString *, NSNumber *> *THHookActivityTotals(void) {
    if (!th_hook_baseTotals) {
        NSDictionary *stored = [[NSUserDefaults standardUserDefaults] dictionaryForKey:kTHHookTotalsKey];
        th_hook_baseTotals = [stored isKindOfClass:[NSDictionary class]] ? [stored copy] : @{};
    }
    NSMutableDictionary<NSString *, NSNumber *> *out = [th_hook_baseTotals mutableCopy];
    for (uint32_t i = 0; i < th_hook_used; i++) {
        NSString *name = th_hook_names[i] ? @(th_hook_names[i]) : nil;
        if (!name) continue;
        uint64_t prior = [out[name] unsignedLongLongValue];
        out[name] = @(prior + th_hook_slots[i].count);
    }
    return [out copy];
}

static void THHookActivityFlush(void) {
    NSDictionary *totals = THHookActivityTotals();
    NSUserDefaults *d = [NSUserDefaults standardUserDefaults];
    [d setObject:totals forKey:kTHHookTotalsKey];
    if (![d objectForKey:kTHHookSinceKey]) [d setObject:[NSDate date] forKey:kTHHookSinceKey];
    // Live counts are now inside the persisted totals; rebase so the next flush does not double-count.
    th_hook_baseTotals = totals;
    for (uint32_t i = 0; i < th_hook_used; i++) th_hook_slots[i].count = 0;

    NSUInteger never = 0;
    for (uint32_t i = 0; i < th_hook_used; i++) {
        if (th_hook_names[i] && [totals[@(th_hook_names[i])] unsignedLongLongValue] == 0) never++;
    }
    os_log(OS_LOG_DEFAULT, "[Theta] HookActivity: %u hooks installed this launch, %lu never fired yet, %u not counted (pool full)",
           th_hook_used, (unsigned long)never, th_hook_overflow);
}

// C linkage: this file is compiled inside TweakCOMPILE.xm (Objective-C++), the settings page is plain ObjC.
FOUNDATION_EXTERN NSString *THHookActivityReport(void);
FOUNDATION_EXTERN void THHookActivityReset(void);

/// Report for the settings page: hooks installed this launch, never-fired first.
NSString *THHookActivityReport(void) {
    NSDictionary<NSString *, NSNumber *> *totals = THHookActivityTotals();
    NSMutableOrderedSet<NSString *> *installed = [NSMutableOrderedSet orderedSet];
    for (uint32_t i = 0; i < th_hook_used; i++) if (th_hook_names[i]) [installed addObject:@(th_hook_names[i])];

    NSArray<NSString *> *sorted = [installed.array sortedArrayUsingSelector:@selector(compare:)];
    NSMutableArray<NSString *> *never = [NSMutableArray array];
    NSMutableArray<NSString *> *fired = [NSMutableArray array];
    for (NSString *n in sorted) {
        uint64_t c = [totals[n] unsignedLongLongValue];
        if (c == 0) [never addObject:[NSString stringWithFormat:@"  %@", n]];
        else [fired addObject:[NSString stringWithFormat:@"%10llu  %@", c, n]];
    }
    NSDate *since = [[NSUserDefaults standardUserDefaults] objectForKey:kTHHookSinceKey];
    NSString *sinceText = [since isKindOfClass:[NSDate class]]
        ? [NSDateFormatter localizedStringFromDate:since dateStyle:NSDateFormatterShortStyle timeStyle:NSDateFormatterShortStyle]
        : @"this launch";
    NSMutableString *s = [NSMutableString string];
    [s appendFormat:@"Counting since %@\n%lu hooks installed, %lu never fired",
     sinceText, (unsigned long)sorted.count, (unsigned long)never.count];
    if (th_hook_overflow) [s appendFormat:@", %u not counted (pool full)", th_hook_overflow];
    [s appendString:@"\n\nA hook at zero after you have used its feature installed but never runs.\n"];
    [s appendFormat:@"\n— NEVER FIRED (%lu) —\n%@\n", (unsigned long)never.count, [never componentsJoinedByString:@"\n"]];
    [s appendFormat:@"\n— FIRED (%lu) —\n%@\n", (unsigned long)fired.count, [fired componentsJoinedByString:@"\n"]];
    return s;
}

void THHookActivityReset(void) {
    NSUserDefaults *d = [NSUserDefaults standardUserDefaults];
    [d removeObjectForKey:kTHHookTotalsKey];
    [d setObject:[NSDate date] forKey:kTHHookSinceKey];
    th_hook_baseTotals = @{};
    for (uint32_t i = 0; i < th_hook_used; i++) th_hook_slots[i].count = 0;
}

static void THHookActivityStartFlushing(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        for (NSNotificationName n in @[ UIApplicationDidEnterBackgroundNotification, UIApplicationWillTerminateNotification ]) {
            [[NSNotificationCenter defaultCenter] addObserverForName:n object:nil queue:[NSOperationQueue mainQueue]
                                                          usingBlock:^(__unused NSNotification *note) { THHookActivityFlush(); }];
        }
    });
}

/**
 * Safe ObjC swizzle.
 *
 * IMPORTANT: class_replaceMethod() returns NULL when it *adds* an override for an
 * inherited method. Using that return value as `orig` leaves a NULL function
 * pointer and crashes on first call (EXC_BAD_ACCESS / PC=0). Always capture the
 * IMP via method_getImplementation / class_addMethod+method_setImplementation.
 */
static BOOL ThetaInstallMessageHook(Class cls, SEL sel, void *replacement, void *original, BOOL reportMissing) {
    if (!cls || !replacement) {
        if (reportMissing && !cls) {
            RecordFailedHookLine([NSString stringWithFormat:@"Class is nil for %@", NSStringFromSelector(sel)]);
        }
        return NO;
    }

    Method instanceMethod = class_getInstanceMethod(cls, sel);
    Method classMethod = instanceMethod ? NULL : class_getClassMethod(cls, sel);
    Method method = instanceMethod ?: classMethod;
    if (!method) {
        if (reportMissing) {
            NSLog(@"-[%@ %@] not found", NSStringFromClass(cls), NSStringFromSelector(sel));
            RecordFailedHookLine([NSString stringWithFormat:@"-[%@ %@] — method not found", NSStringFromClass(cls), NSStringFromSelector(sel)]);
        }
        return NO;
    }

    const char *types = method_getTypeEncoding(method);
    IMP real = method_getImplementation(method);
    if (!real) {
        if (reportMissing) {
            RecordFailedHookLine([NSString stringWithFormat:@"-[%@ %@] — NULL IMP", NSStringFromClass(cls), NSStringFromSelector(sel)]);
        }
        return NO;
    }

    Class targetClass = instanceMethod ? cls : object_getClass((id)cls);
    IMP previous = NULL;
    THHookActivityStartFlushing();
    void *installed = THHookCounterWrap(replacement, cls, sel, instanceMethod == NULL);

    if (class_addMethod(targetClass, sel, (IMP)installed, types)) {
        // Method was inherited; we added an override. `real` is the super IMP.
        previous = real;
    } else {
        // Method already exists on this class — swap implementation.
        Method local = instanceMethod ? class_getInstanceMethod(targetClass, sel)
                                      : class_getClassMethod(cls, sel);
        if (!local) local = method;
        previous = method_setImplementation(local, (IMP)installed);
        if (!previous) previous = real;
    }

    if (original) {
        *(IMP *)original = previous;
    }
    return previous != NULL;
}

static void NullHookMessageEx(Class cls, SEL sel, void *replacement, void *original) {
    (void)ThetaInstallMessageHook(cls, sel, replacement, original, YES);
}

/** Silent install when class/selector may be absent across IG versions. */
static void NullHookMessageIfPresent(Class cls, SEL sel, void *replacement, void *original) {
    (void)ThetaInstallMessageHook(cls, sel, replacement, original, NO);
}

static void NullHookMessage(Class cls, SEL sel, void *replacement) {
    if (!cls) {
        RecordFailedHookLine(@"class_addMethod: target class is nil");
        return;
    }
    if (!class_addMethod(cls, sel, (IMP)replacement, "v@:")) {
        RecordFailedHookLine([NSString stringWithFormat:@"-[%@ %@] — class_addMethod failed", NSStringFromClass(cls), NSStringFromSelector(sel)]);
    }
}

/** First NSClassFromString hit across ObjC + Swift mangled names. */
static Class ThetaFirstClass(NSArray<NSString *> *names) {
    if (!names.count) return Nil;
    for (NSString *name in names) {
        if (![name isKindOfClass:[NSString class]] || !name.length) continue;
        Class cls = NSClassFromString(name);
        if (cls) return cls;
    }
    return Nil;
}

/** Hook first available (class, selector) pair; silent if none match. */
static BOOL ThetaHookFirst(NSArray<NSString *> *classNames, NSArray<NSString *> *selectorNames, void *replacement, void *original) {
    Class cls = ThetaFirstClass(classNames);
    if (!cls) return NO;
    for (NSString *selName in selectorNames) {
        if (![selName isKindOfClass:[NSString class]] || !selName.length) continue;
        SEL sel = NSSelectorFromString(selName);
        if (!sel) continue;
        Method method = class_getInstanceMethod(cls, sel) ?: class_getClassMethod(cls, sel);
        if (!method) continue;
        NullHookMessageIfPresent(cls, sel, replacement, original);
        return YES;
    }
    return NO;
}
