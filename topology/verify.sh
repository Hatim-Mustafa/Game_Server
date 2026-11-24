#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "error: run as root (sudo $0)" >&2
  exit 1
fi

for ns in clientA r1 r2 clientB server; do
  if ! ip netns list | awk '{print $1}' | grep -qx "$ns"; then
    echo "error: namespace $ns missing (run setup.sh first)" >&2
    exit 1
  fi
done

echo "== routes =="
ip -n clientA route get 10.0.3.10
ip -n clientB route get 10.0.3.10
ip -n server route get 10.0.1.10

echo "== ping clientA -> server =="
ip netns exec clientA ping -c 3 -W 2 -q 10.0.3.10

echo "== ping clientB -> server =="
ip netns exec clientB ping -c 3 -W 2 -q 10.0.3.10

if command -v traceroute >/dev/null 2>&1; then
  echo "== traceroute clientA -> server =="
  ip netns exec clientA traceroute -n -w 1 -q 1 10.0.3.10 || true
fi

echo "== UDP probe (returns 'pong:<payload>') =="
ip netns exec server python3 -c 'import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", 9099))
s.settimeout(6.0)
try:
    while True:
        data, addr = s.recvfrom(2048)
        s.sendto(b"pong:" + data, addr)
except Exception:
    pass' &
srv_pid=$!
trap 'kill "$srv_pid" 2>/dev/null || true' EXIT
sleep 0.5

probe() {
  local ns=$1
  ip netns exec "$ns" python3 -c 'import socket, sys
ns = sys.argv[1]
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(1.0)
for _ in range(5):
    s.sendto(b"ping", ("10.0.3.10", 9099))
    try:
        data, _ = s.recvfrom(2048)
        print("  UDP OK from %s: %s" % (ns, data.decode()))
        sys.exit(0)
    except Exception:
        pass
print("  UDP FAILED from %s" % ns)
sys.exit(1)' "$ns"
}

probe clientA
probe clientB

kill "$srv_pid" 2>/dev/null || true
wait "$srv_pid" 2>/dev/null || true
trap - EXIT

echo "verification complete"
echo "start the game with:"
echo "  sudo ip netns exec server python3 -m netcode_lab.server --host 10.0.3.10 --port 9000"
echo "  sudo ip netns exec clientA python3 -m netcode_lab.client --mode c3 --host 10.0.3.10 --port 9000 --player 1"
echo "  sudo ip netns exec clientB python3 -m netcode_lab.client --mode c3 --host 10.0.3.10 --port 9000 --player 2"
