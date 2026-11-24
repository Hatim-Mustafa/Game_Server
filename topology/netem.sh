#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: netem.sh <apply|clear|status> [options]

apply options:
  --rtt-ms N            target round-trip delay (split across both router hops)
  --loss P              packet loss percent (default 0)
  --jitter-ms N         delay jitter per hop (default 0)
  --loss-correlation P  loss correlation percent (default 0)

examples:
  netem.sh apply --rtt-ms 100 --loss 2
  netem.sh apply --rtt-ms 50 --jitter-ms 20 --loss 0
  netem.sh status
  netem.sh clear
EOF
}

if [[ ${EUID} -ne 0 ]]; then
  echo "error: run as root (sudo $0)" >&2
  exit 1
fi

action=${1:-}
if [[ -z $action ]]; then
  usage
  exit 1
fi
shift || true

rtt_ms=0
loss=0
jitter_ms=0
loss_corr=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rtt-ms) rtt_ms=$2; shift 2 ;;
    --loss) loss=$2; shift 2 ;;
    --jitter-ms) jitter_ms=$2; shift 2 ;;
    --loss-correlation) loss_corr=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if ! ip netns list | awk '{print $1}' | grep -qx r1 \
   || ! ip netns list | awk '{print $1}' | grep -qx r2; then
  echo "error: topology not set up (run setup.sh first)" >&2
  exit 1
fi

IFACE_R1=veth-r1r2
IFACE_R2=veth-r2r1

case "$action" in
  apply)
    one_way=$(awk -v rtt="$rtt_ms" 'BEGIN { printf "%.1f", rtt / 2.0 }')
    delay_args=(delay "${one_way}ms")
    if awk -v j="$jitter_ms" 'BEGIN { exit !(j > 0) }'; then
      delay_args+=("${jitter_ms}ms")
    fi
    loss_args=(loss "${loss}%")
    if awk -v c="$loss_corr" 'BEGIN { exit !(c > 0) }'; then
      loss_args+=("${loss_corr}%")
    fi
    ip netns exec r1 tc qdisc replace dev "$IFACE_R1" root netem "${delay_args[@]}" "${loss_args[@]}"
    ip netns exec r2 tc qdisc replace dev "$IFACE_R2" root netem "${delay_args[@]}" "${loss_args[@]}"
    echo "netem applied: rtt=${rtt_ms}ms (${one_way}ms/hop) loss=${loss}% jitter=${jitter_ms}ms corr=${loss_corr}%"
    ;;
  clear)
    ip netns exec r1 tc qdisc del dev "$IFACE_R1" root 2>/dev/null || true
    ip netns exec r2 tc qdisc del dev "$IFACE_R2" root 2>/dev/null || true
    echo "netem cleared"
    ;;
  status)
    echo "--- r1:$IFACE_R1 ---"
    ip netns exec r1 tc -s qdisc show dev "$IFACE_R1"
    echo "--- r2:$IFACE_R2 ---"
    ip netns exec r2 tc -s qdisc show dev "$IFACE_R2"
    ;;
  *)
    usage
    exit 1
    ;;
esac
