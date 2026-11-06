// Hook fire counters: one trampoline per installed ObjC hook.
//
// ThetaInstallMessageHook installs trampoline #i as the method's IMP instead of the hook
// function itself. The trampoline bumps th_hook_slots[i].count and branches to
// th_hook_slots[i].target (the real hook function). It touches only x9, x10, x16, x17 —
// scratch registers with no defined value at function entry under AAPCS64 — and never sp,
// x8 or the argument registers, so every hook signature (any arity, float args, struct
// returns, stack args) passes through unchanged. Only meaningful on arm64 (no PAC: the
// dylib is built ARCHS = arm64, so `br` to a raw pointer is valid).

#if defined(__arm64__) || defined(__aarch64__)

#define TH_HOOK_SLOT_COUNT 1024

    .zerofill __DATA,__bss,_th_hook_slots,TH_HOOK_SLOT_COUNT*16,4
    .globl _th_hook_slots
    .private_extern _th_hook_slots

    .text
    .p2align 2

    // x17 = slot index on entry.
_th_tramp_common:
    adrp    x16, _th_hook_slots@PAGE
    add     x16, x16, _th_hook_slots@PAGEOFF
    add     x16, x16, x17, lsl #4          // &slots[i]  (16 bytes: target, count)
    add     x17, x16, #8                   // &slots[i].count
1:  ldxr    x9, [x17]
    add     x9, x9, #1
    stxr    w10, x9, [x17]
    cbnz    w10, 1b
    ldr     x16, [x16]                     // slots[i].target
    br      x16

    // Fixed 8-byte entries, so the C side computes entry i as base + 8*i.
    .globl _th_hook_tramps
    .private_extern _th_hook_tramps
    .p2align 3
_th_hook_tramps:
    .set th_i, 0
    .rept TH_HOOK_SLOT_COUNT
    movz    x17, #th_i
    b       _th_tramp_common
    .set th_i, th_i + 1
    .endr

#endif
