#!/usr/bin/env bash
# Remove the mock tool symlinks (from /usr/local/bin and /usr/bin), the stubbed
# firmware images, and mock state.
set -euo pipefail

BIN=/usr/local/bin
SYSBIN=/usr/bin
RBLN_TOOLS=(rbln-smi rbln rbln-smcif rbln-flash rbln-security rbln-spdm rbln-prog rblntrace)
SYS_TOOLS=(ipmitool dmidecode lspci setpci systemctl)
SRC="$(cd "$(dirname "$0")" && pwd)/mock_rbln.py"
FW_VER="$(sed -n 's/^_FW_VERSION = "\(.*\)"$/\1/p' "$SRC")"
[ -n "$FW_VER" ] || { echo "cannot read _FW_VERSION from $SRC" >&2; exit 1; }

for t in "${RBLN_TOOLS[@]}" "${SYS_TOOLS[@]}"; do
  for dir in "$BIN" "$SYSBIN"; do
    if [ -L "$dir/$t" ]; then
      sudo rm -f "$dir/$t"
      echo "removed $dir/$t"
    fi
  done
done

for f in "cp/rebel-q-cp.bin" "smc/rebel-smc.bin"; do
  p="/lib/firmware/rebellions/$FW_VER/$f"
  # only remove our empty stub, never a real firmware image
  if [ -f "$p" ] && [ ! -s "$p" ]; then
    sudo rm -f "$p"
    echo "removed stub $p"
  fi
done

# Mock-written fault-injection coredumps. The dir itself is only removed when it
# ends up empty, so a real rbln-smd's coredumps are never taken with it.
rm -rf /var/log/rebellions/rbln*.coredump 2>/dev/null || true
rmdir /var/log/rebellions 2>/dev/null && echo "removed /var/log/rebellions" || true

rm -rf /tmp/mfg_mockup_state
echo "done."
