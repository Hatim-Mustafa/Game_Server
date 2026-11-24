#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "error: run as root (sudo $0)" >&2
  exit 1
fi

NS_LIST=(clientA r1 r2 clientB server)

for ns in "${NS_LIST[@]}"; do
  ip netns del "$ns" 2>/dev/null || true
done

for ns in "${NS_LIST[@]}"; do
  ip netns add "$ns"
  ip -n "$ns" link set lo up
done

ip link add veth-ca type veth peer name veth-r1a
ip link set veth-ca netns clientA
ip link set veth-r1a netns r1

ip link add veth-r1r2 type veth peer name veth-r2r1
ip link set veth-r1r2 netns r1
ip link set veth-r2r1 netns r2

ip link add veth-cb type veth peer name veth-r2b
ip link set veth-cb netns clientB
ip link set veth-r2b netns r2

ip link add veth-srv type veth peer name veth-r2s
ip link set veth-srv netns server
ip link set veth-r2s netns r2

ip -n clientA addr add 10.0.1.10/24 dev veth-ca
ip -n r1 addr add 10.0.1.1/24 dev veth-r1a
ip -n r1 addr add 10.0.12.1/30 dev veth-r1r2
ip -n r2 addr add 10.0.12.2/30 dev veth-r2r1
ip -n r2 addr add 10.0.2.1/24 dev veth-r2b
ip -n r2 addr add 10.0.3.1/24 dev veth-r2s
ip -n clientB addr add 10.0.2.10/24 dev veth-cb
ip -n server addr add 10.0.3.10/24 dev veth-srv

ip -n clientA link set veth-ca up
ip -n r1 link set veth-r1a up
ip -n r1 link set veth-r1r2 up
ip -n r2 link set veth-r2r1 up
ip -n r2 link set veth-r2b up
ip -n r2 link set veth-r2s up
ip -n clientB link set veth-cb up
ip -n server link set veth-srv up

for ns in r1 r2; do
  ip netns exec "$ns" sysctl -qw net.ipv4.ip_forward=1
  ip netns exec "$ns" sysctl -qw net.ipv4.conf.all.rp_filter=0
  ip netns exec "$ns" sysctl -qw net.ipv4.conf.default.rp_filter=0
done

ip -n clientA route add default via 10.0.1.1
ip -n clientB route add default via 10.0.2.1
ip -n server route add default via 10.0.3.1
ip -n r1 route add 10.0.2.0/24 via 10.0.12.2
ip -n r1 route add 10.0.3.0/24 via 10.0.12.2
ip -n r2 route add 10.0.1.0/24 via 10.0.12.1

echo "topology ready"
echo "  clientA  10.0.1.10/24   gw 10.0.1.1 (r1)"
echo "  r1       10.0.1.1/24    10.0.12.1/30"
echo "  r2       10.0.12.2/30   10.0.2.1/24  10.0.3.1/24"
echo "  clientB  10.0.2.10/24   gw 10.0.2.1 (r2)"
echo "  server   10.0.3.10/24   gw 10.0.3.1 (r2)"
echo "  server port: UDP 9000 (server namespace host 10.0.3.10)"
here=$(cd "$(dirname "$0")" && pwd)
echo "  next: sudo bash $here/verify.sh"
echo "        sudo bash $here/netem.sh apply --rtt-ms 50 --loss 2"
