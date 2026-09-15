#!/bin/bash
# Summarise FigVideoQueue starvation in a device capture.
#
#   ./scripts/reel-stats.sh logs/theta-<stamp>.log
#
# A video renderer running (rate 1.00) while no frames are enqueued and
# max PTS never leaves 0.000 is a starved renderer -- the home-feed reel
# signature. Renderers at rate 0.00 are simply idle and are not a fault.
set -euo pipefail
LOG="${1:?usage: reel-stats.sh <logfile>}"

command grep -a "FigVideoQueueGMStats" "$LOG" \
| sed -E 's/.*\(0x([0-9a-f]+)\) ([0-9]+) frames.*max PTS: ([^;]+);.*rate ([0-9.]+)\..*/\1 \2 \3 \4/' \
| awk '
{
	addr=$1; frames=$2; pts=$3; rate=$4
	seen[addr]=1
	if (rate=="1.00") {
		run[addr]++
		f[addr]+=frames
		if (frames>0) fed[addr]++
		else starved[addr]++
		if (pts!="0.000" && pts!="nan") moved[addr]=1
	} else idle[addr]++
}
END {
	printf "%-12s %8s %8s %8s %8s %7s  %s\n", "queue", "running", "starved", "fed", "frames", "ptsmvd", "verdict"
	for (a in seen) {
		v = (run[a]==0) ? "never ran" : (starved[a] && !fed[a]) ? "STARVED" : (starved[a]) ? "partial" : "healthy"
		printf "%-12s %8d %8d %8d %8d %7s  %s\n", a, run[a]+0, starved[a]+0, fed[a]+0, f[a]+0, (moved[a]?"yes":"no"), v
		T+=run[a]; S+=starved[a]; F+=fed[a]
	}
	printf "\ntotals: running=%d starved=%d fed=%d  -> starvation %.0f%% of running samples\n", T, S, F, (T? 100*S/T : 0)
}' | sort -k7,7 -k1,1
