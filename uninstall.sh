#!/usr/bin/env bash
# Remove the mock tool symlinks (from /usr/local/bin and /usr/bin), the stubbed
# firmware images, and mock state.
set -euo pipefail

BIN=/usr/local/bin
SYSBIN=/usr/bin
RBLN_TOOLS=(rbln-smi rbln rbln-smcif rbln-flash rbln-security rbln-spdm rblntrace)
SYS_TOOLS=(ipmitool dmidecode lspci systemctl)
FW_VERS=(3.3.0 3.3.1)

for t in "${RBLN_TOOLS[@]}" "${SYS_TOOLS[@]}"; do
  for dir in "$BIN" "$SYSBIN"; do
    if [ -L "$dir/$t" ]; then
      sudo rm -f "$dir/$t"
      echo "removed $dir/$t"
    fi
  done
done

for v in "${FW_VERS[@]}"; do
  for f in "cp/rebel-q-cp.bin" "smc/rebel-smc.bin"; do
    p="/lib/firmware/rebellions/$v/$f"
    # only remove our empty stub, never a real firmware image
    if [ -f "$p" ] && [ ! -s "$p" ]; then
      sudo rm -f "$p"
      echo "removed stub $p"
    fi
  done
done

rm -rf /tmp/mfg_mockup_state
echo "done."
