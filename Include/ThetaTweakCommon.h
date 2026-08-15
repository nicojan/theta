/**
 * Shared preference helpers and cross-module exports.
 */
#ifndef ThetaTweakCommon_h
#define ThetaTweakCommon_h

#import <Foundation/Foundation.h>

/**
 * Cached lookup for `<setting>_Enabled`. Implemented in THGlobalsAndHooking.m.
 *
 * ENABLED() is called from UIKit render paths — the Liquid Glass predicates alone were read
 * ~18x/second — so building the key with `stringWithFormat:` and hitting NSUserDefaults on
 * every call was pure overhead. The value is cached and dropped whenever defaults change.
 */
BOOL ThetaSettingEnabled(NSString *setting);

#define ENABLED(setting) ThetaSettingEnabled(setting)

/** Implemented in HideFeedFiltering.m; chained from HideAds home feed adapter. */
NSArray *ThetaApplyHideFeedFiltering(NSArray *list, BOOL isMainFeed);

#endif
