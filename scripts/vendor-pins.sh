#!/bin/sh
# Print the VENDOR.md table rows for every clone in vendor/.
cd "$(dirname "$0")/../vendor" || exit 1
for d in */; do
  d=${d%/}
  url=$(git -C "$d" remote get-url origin)
  sha=$(git -C "$d" rev-parse --short HEAD)
  date=$(git -C "$d" log -1 --format=%cs)
  echo "| $d | $url | | $sha | $date | |"
done
