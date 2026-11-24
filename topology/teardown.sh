#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "error: run as root (sudo $0)" >&2
  exit 1
fi

for ns in clientA r1 r2 clientB server; do
  ip netns del "$ns" 2>/dev/null || true
done

for link in veth-ca veth-r1a veth-r1r2 veth-r2r1 veth-cb veth-r2b veth-srv veth-r2s; do
  ip link del "$link" 2>/dev/null || true
done

echo "topology removed"
