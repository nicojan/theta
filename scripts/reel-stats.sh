#!/bin/bash
# Summarise FigVideoQueue starvation in a device capture.
#
#   ./scripts/reel-stats.sh logs/theta-<stamp>.log
#
# A video renderer running (rate 1.00) while no frames are enqueued and
# max PTS never leaves 0.000 is a starved renderer -- the home-feed reel
# signature. Renderers at rate 0.00 are simply idle and are not a fault.
#
# The second table is the load-bearing one: queue addresses are recycled
# inside a capture, so per-address totals blend playback sessions, but an
# unbroken run of starved samples is one session. Compare the longest runs
# between captures, not the percentage, which moves with how much was scrolled.
# A whole-device capture also holds queues from before a cold launch; cut the
# log at the launch line first (awk '$3 >= "HH:MM:SS"') when that matters.
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

echo
echo "starved runs (rate 1.00, 0 frames, unbroken), longest last:"
command grep -a "FigVideoQueueGMStats" "$LOG" \
| sed -E 's/^[A-Z][a-z]+ +[0-9]+ ([0-9:.]+) .*\(0x([0-9a-f]+)\) ([0-9]+) frames.*rate ([0-9.]+)\..*/\1 \2 \3 \4/' \
| awk '
function secs(t,  a) { split(t, a, ":"); return a[1]*3600 + a[2]*60 + a[3] }
function flush(q) {
	if (n[q] > 0) printf "%-12s %4d samples %5.0fs  from %s\n", q, n[q], last[q]-start[q], from[q]
	n[q] = 0
}
{
	t=$1; q=$2; frames=$3; rate=$4
	if (rate=="1.00" && frames==0) {
		if (n[q]==0) { start[q]=secs(t); from[q]=t }
		n[q]++; last[q]=secs(t)
	} else flush(q)
}
END { for (q in n) flush(q) }' | sort -k2,2n
