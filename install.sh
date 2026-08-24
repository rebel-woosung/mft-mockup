#!/usr/bin/env bash
# Symlink the mock tools so both plain and `sudo` calls resolve them (the SRT
# engine runs most commands via `sudo bash -lc ...`, and /usr/local/bin is on
# sudo's secure_path).
#
# rbln-* tools also go into /usr/bin because the engine probes tool presence
# with `find /usr/bin -name <tool>` (installation checks) and invokes rbln-spdm
# by the absolute path /usr/bin/rbln-spdm — neither of which a /usr/local/bin
# link satisfies.
set -euo pipefail

BIN=/usr/local/bin
SYSBIN=/usr/bin
SRC="$(cd "$(dirname "$0")" && pwd)/mock_rbln.py"

# Vendor tools: linked into BOTH /usr/local/bin (PATH) and /usr/bin (find probe
# + rbln-spdm absolute path).
RBLN_TOOLS=(rbln-smi rbln rbln-smcif rbln-flash rbln-security rbln-spdm rbln-prog rblntrace)
# System tools: /usr/local/bin only (PATH-shadow; the mock defers non-rebellions
# calls to the real binary). Do NOT put these in /usr/bin.
SYS_TOOLS=(ipmitool dmidecode lspci systemctl)

# Firmware versions to stub. Must cover whatever test_config.json points at
# (cp_fw_update.path / smc_fw_update.path) so CheckCp/SmcFwImage's find passes.
FW_VERS=(3.3.0 3.3.1)

chmod +x "$SRC"

link_into() {  # link_into <dir> <tool...>
  local dir="$1"; shift
  for t in "$@"; do
    if command -v "$t" >/dev/null 2>&1 && [ ! -L "$dir/$t" ] && [ -e "$dir/$t" ]; then
      echo "WARNING: real '$t' exists at $(command -v "$t") — mock link will shadow it"
    fi
    sudo ln -sf "$SRC" "$dir/$t"
    echo "linked  $dir/$t -> $SRC"
  done
}

link_into "$BIN" "${RBLN_TOOLS[@]}" "${SYS_TOOLS[@]}"
link_into "$SYSBIN" "${RBLN_TOOLS[@]}"

# Dummy firmware images so CheckCpFwImage / CheckSmcFwImage (find ... -name *.bin) pass.
for v in "${FW_VERS[@]}"; do
  sudo mkdir -p "/lib/firmware/rebellions/$v/cp" "/lib/firmware/rebellions/$v/smc"
  sudo touch "/lib/firmware/rebellions/$v/cp/rebel-q-cp.bin" \
             "/lib/firmware/rebellions/$v/smc/rebel-smc.bin"
  echo "stubbed /lib/firmware/rebellions/$v/{cp/rebel-q-cp.bin,smc/rebel-smc.bin}"
done

echo
echo "done. verify:  rbln-smi -g -j   |   lspci -D -d 1eff:   |   rbln ver -d 0"
echo "uninstall:     ./uninstall.sh"
echo
echo "STILL REQUIRED on the engine side (cannot be mocked at the CLI):"
echo "  1) seed tag_info.json (operator barcodes) — see README."
echo "  2) comment out the 3 sysfs/debugfs touches — see README."
