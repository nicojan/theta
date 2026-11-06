#import <os/log.h>
#import <objc/runtime.h>
#import "Include/ThetaHelper.h"

static void (*orig_followConfirmation)(id self, SEL _cmd);
static void hook_followConfirmation(id self, SEL _cmd) {
    if (!orig_followConfirmation) return;
    if (!ENABLED(@"Follow Confirmation")) {
        orig_followConfirmation(self, _cmd);
        return;
    }
    
    // 447+: IGFollowController is a Swift class; its `_user` stored property is not reliably
    // KVC-visible, so read the ivar first and fall back to KVC for the old ObjC class.
    id user = nil;
    Ivar userIvar = class_getInstanceVariable([self class], "_user");
    if (userIvar) user = object_getIvar(self, userIvar);
    if (!user) user = ThetaValueForKey(self, @"user");
    NSInteger userFollowStatus = [ThetaValueForKey(user, @"followStatus") integerValue];
    os_log(OS_LOG_DEFAULT, "[Theta] FollowConfirm: tap user=%{public}s status=%ld",
           user ? class_getName([user class]) : "nil", (long)userFollowStatus);
    
    if (userFollowStatus == 2) {
        [ThetaHelper showCustomAlertWithActions:@"✋ Woah! Hold up!" description:@"Are you sure you want to follow this user?" actions:@[
            @{
                @"title": @"Yes, follow them!",
                @"handler": ^(id sender) {
                    orig_followConfirmation(self, _cmd);
                }
            },
            @{
                @"title": @"No, I'm good.",
                @"handler": ^(id sender) {
                    // Do nothing, just cancel
                }
            }
        ]];
    } else {
        orig_followConfirmation(self, _cmd);
    }
}

void THRegisterFollowConfirmationHooks(void) {
    // <=446: ObjC IGFollowController -_didPressFollowButton. 447+: Swift IGFollowing.IGFollowController,
    // where every tap path (didPressFollowButtonWith:, ...FromControlEvent) funnels into -tryToggleFriendship.
    Class swiftCls = ThetaFirstClass(@[ @"_TtC11IGFollowing18IGFollowController", @"IGFollowing.IGFollowController" ]);
    NullHookMessageIfPresent(swiftCls, NSSelectorFromString(@"tryToggleFriendship"), (void *)hook_followConfirmation, &orig_followConfirmation);
    if (!orig_followConfirmation) {
        NullHookMessageIfPresent(objc_getClass("IGFollowController"), NSSelectorFromString(@"_didPressFollowButton"), (void *)hook_followConfirmation, &orig_followConfirmation);
    }
}