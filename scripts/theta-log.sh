#!/bin/bash
# Capture iPhone syslog while reproducing a Theta bug, into one shareable file.
#
#   ./scripts/theta-log.sh            capture Instagram + Theta lines
#   ./scripts/theta-log.sh --all      capture everything (use this for hangs/freezes)
#   ./scripts/theta-log.sh --list     show what devices this Mac can see
#
# Reproduce the bug while it runs, then press Ctrl-C.
# The path printed at the end is the file to send.
#
# Requires: brew install libimobiledevice
# The iPhone must be plugged in over USB and unlocked. Network-only pairing
# cannot start the syslog relay (lockdownd returns -8).

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGDIR="$REPO/logs"
MODE="filtered"

for arg in "$@"; do
	case "$arg" in
		--all)  MODE="all" ;;
		--list) MODE="list" ;;
		-h|--help)
			awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"
			exit 0 ;;
		*) echo "unknown option: $arg" >&2; exit 2 ;;
	esac
done

have() { command -v "$1" >/dev/null 2>&1; }

console_instructions() {
	cat >&2 <<'EOF'

Fallback that always works, no install needed:

  1. Open Console.app
  2. Pick your iPhone in the left sidebar
  3. Type  Instagram  in the search box, press Start
  4. Reproduce the bug
  5. File > Export As...  and send that file

EOF
}

if ! have idevicesyslog; then
	echo "!! idevicesyslog not found. Install it with:" >&2
	echo "     brew install libimobiledevice" >&2
	console_instructions
	exit 1
fi

USB_UDID="$(idevice_id -l 2>/dev/null | head -1)"
NET_UDID="$(idevice_id -n 2>/dev/null | head -1)"

if [ "$MODE" = "list" ]; then
	echo "USB:     ${USB_UDID:-<none>}"
	echo "Network: ${NET_UDID:-<none>}"
	[ -z "$USB_UDID" ] && echo && echo "Plug the iPhone in over USB and unlock it — syslog needs a USB connection."
	exit 0
fi

if [ -z "$USB_UDID" ]; then
	if [ -n "$NET_UDID" ]; then
		echo "!! Device $NET_UDID is visible over the network, but syslog needs USB." >&2
	else
		echo "!! No iPhone detected." >&2
	fi
	echo "!! Plug it in, unlock it, and tap Trust if prompted. Then re-run." >&2
	console_instructions
	exit 1
fi

mkdir -p "$LOGDIR"
OUT="$LOGDIR/theta-$(date +%Y%m%d-%H%M%S).log"

{
	echo "# Theta device log"
	echo "# captured : $(date '+%Y-%m-%d %H:%M:%S %Z')"
	echo "# mode     : $MODE"
	echo "# device   : $USB_UDID"
	echo "# host     : $(sw_vers -productName) $(sw_vers -productVersion)"
	echo "#"
} > "$OUT"

# Ctrl-C kills the capture pipeline, so the summary has to come from a trap.
summarise() {
	trap - EXIT INT TERM
	[ -s "$OUT" ] || return 0
	echo
	echo "======================================================================"
	echo "  Log saved: $OUT"
	echo "  Size:      $(du -h "$OUT" | cut -f1), $(wc -l < "$OUT" | tr -d ' ') lines"
	echo "  Notable:   $(grep -ciE 'theta|exception|crash|assert' "$OUT" 2>/dev/null || echo 0) lines mentioning Theta/exception/crash/assert"
	echo
	echo "  Send that file as-is."
	echo "======================================================================"
}
trap summarise EXIT INT TERM

echo "==> Device:  $USB_UDID"
echo "==> Writing: $OUT"
echo "==> Reproduce the bug now. Press Ctrl-C when done."
echo

if [ "$MODE" = "all" ]; then
	idevicesyslog -u "$USB_UDID" 2>&1 | tee -a "$OUT"
else
	idevicesyslog -u "$USB_UDID" -m Instagram -m Theta 2>&1 | tee -a "$OUT"
fi
