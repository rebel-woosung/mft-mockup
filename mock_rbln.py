#!/usr/bin/env python3
"""Mockup of the Rebellions manufacturing CLI tools for testing without hardware.

One dispatcher, invoked under several names (symlinks): the basename of argv[0]
selects the tool. Output formats mirror verbatim RBLN-CR13 captures from
rebellions_server_run_engine_v2's tests/logs, so the SRT engine parses them like
the real tools.

Tools: rbln-smi, rbln, rbln-smcif, rbln-flash, rbln-security, rbln-spdm,
rblntrace, ipmitool, dmidecode, lspci, systemctl.

8 fake NPUs, all healthy → the full SRT flow reaches an all-PASS verdict.
`rblntrace retrace` runs for its ``--app_execution_time`` (real behaviour). HBM
chiplet temps ramp as a 35→95°C sawtooth (60s period); other rails are static.
RSD group state and hw_cfg writes persist under ``/tmp/mfg_mockup_state`` so
combine/separate and hw_cfg verify pass across separate CLI invocations.

Direct use for testing: `python3 mock_rbln.py rbln-smi -g -j`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import time
import uuid

_STATE = "/tmp/mfg_mockup_state"
_NUM_DEVICES = 7
_CHIPLETS = 4
# Per-device identity (device_id → slot / card_serial / smt_lot), from a real
# rbln-suma-srt-04 tag_info capture. Slots use the dual-socket layout (1-4, 9-12).
_DEVICE_TAGS = (
    # slot, card_serial (8 digits, == sid[-8:]), smt_lot
    (1, "52628079", "267805200000002"),
    (2, "52628066", "267685500000005"),
    (3, "52628076", "267685500000015"),
    (4, "52628072", "267685500000011"),
    (9, "52628071", "267685500000010"),
    (10, "52628063", "267685500000002"),
    (11, "52628069", "267685500000008"),
    #(12, "52628070", "267685500000009"), # for nore present
)
_SLOTS = tuple(t[0] for t in _DEVICE_TAGS)
_TOOLS = {
    "rbln-smi", "rbln", "rbln-smcif", "rbln-flash", "rbln-security", "rbln-spdm",
    "rbln-prog", "rblntrace", "ipmitool", "dmidecode", "lspci", "systemctl",
}

# Identity — must match the seeded tag_info.json and test_config.json target_fw.
_FW_VERSION = "3.3.2"
_PRODUCT_NAME = "RBLN-CR13"
_BOARD_TYPE = "2"
_BOARD_REVISION = "9"
_UUID_SECRET = b"rbln-crx3-uuid-secret"  # mirrors core.util.uuid_gen

# Fault injection — rblntrace retrace raises (non-zero exit) for each
# (device_id, test_unit_id) listed in retrace_fails of fault_injection.json (next
# to this script; override path with $MFG_MOCKUP_FAULT_CONFIG). Empty list = all pass.
_FAULT_CONFIG_PATH = os.environ.get(
    "MFG_MOCKUP_FAULT_CONFIG",
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "fault_injection.json"),
)
_TEST_UNIT_RE = re.compile(r"test_unit_(\d+)")


def _load_fault_config() -> dict:
    """Load fault_injection.json. Missing/invalid -> {} (no faults). Never writes
    to stdout — that stream is parsed by the engine; errors go to stderr."""
    try:
        with open(_FAULT_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"[mock] fault config error ({_FAULT_CONFIG_PATH}): {e}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _retrace_should_fail(device_id: int, test_unit_id: int | None) -> bool:
    """True if fault_injection.json's ``retrace_fails`` lists this exact
    (device_id, test_unit_id) pair. Empty/absent list -> never fails."""
    if test_unit_id is None:
        return False
    for rule in _load_fault_config().get("retrace_fails", []):
        if not isinstance(rule, dict):
            continue
        if rule.get("device_id") == device_id and rule.get("test_unit_id") == test_unit_id:
            return True
    return False

# Reproduce a slow BMC: `ipmitool sdr list all` blocks this long (real BMC ~5-10s).
# It is > BackgroundWorker's 5s stop-join, so a stop that lands mid-poll leaves the
# ServerMetricCollector thread still inside the call → "server-metric did not stop
# cleanly". Set to 0 to disable.
_IPMI_SDR_DELAY_SEC = 6.0

# `rbln sysinfo -t smc_pwr` rails: (key, unit, value). value None → filled from
# hw_cfg dnc_core_vol so verify passes. Values are a real device-0 capture.
_SMC_PWR_RAILS = (
    ("efuse_voltage", "mV", 12473), ("efuse_current", "mA", 14670),
    ("rebel_core0_voltage", "mV", None), ("rebel_core0_current", "mA", 72000),
    ("rebel_core1_voltage", "mV", None), ("rebel_core1_current", "mA", 18000),
    ("rebel_hbm_voltage", "mV", 850), ("rebel_hbm_current", "mA", 21000),
    ("rebel_hbm_vpp_voltage", "mV", 1802), ("rebel_hbm_vpp_current", "mA", 1750),
    ("avdd_1p2v_voltage", "mV", 1209), ("avdd_1p2v_current", "mA", 2740),
    ("avdd_3p3v_voltage", "mV", 3283), ("avdd_3p3v_current", "mA", 0),
    ("ucie_vddq_voltage", "mV", 790), ("ucie_vddq_current", "mA", 47500),
    ("pcie_vp_voltage", "mV", 854), ("pcie_vp_current", "mA", 2921),
    ("pcie_vph_voltage", "mV", 65535), ("pcie_vph_current", "mA", -1),
    ("hbm_vddq_voltage", "mV", 1095), ("hbm_vddq_current", "mA", 14500),
    ("hbm_vddql_top_voltage", "mV", 441), ("hbm_vddql_top_current", "mA", 0),
    ("hbm_vddql_btm_voltage", "mV", 441), ("hbm_vddql_btm_current", "mA", 0),
    ("pci1_vp_voltage", "mV", 856), ("pci1_vp_current", "mA", 76),
    ("sys_5p0v_voltage", "mV", 5004), ("sys_5p0v_current", "mA", 0),
    ("ldo_3p3v_voltage", "mV", 3356), ("ldo_3p3v_current", "mA", 0),
)

# `rbln-smcif power_mon -j`: PMIC rails (device, volt_mv, curr_ma, tdie_c) + one
# untracked rail; and `sensor_mon -j`: board temps + adc block. Real captures.
_SMC_POWER_RAILS = (
    ("eFUSE", 12277, 23337, 37),
    ("CORE0_0P75V", 750, 92000, 34),
    ("CORE1_0P75V", 755, 34000, 41),
    ("HBM_0P85V", 950, 37250, 43),
    ("SYS_1P8V", 1800, 100, 40),
    ("UCIE_VDDQ_0P75V", 790, 50500, 42),
    ("HBM_VDDQ_1P1V", 1100, 15500, 43),
    ("HBM_VDDQL_0P4V(T)", 440, 3000, 33),
    ("HBM_VDDQL_0P4V(B)", 440, 3500, 34),
)
_SMC_ADC = (
    ("SYS_1P8V", 1801, 2937), ("AVDD_1P2V", 1214, 2783), ("SYS_3P3V", 3288, 0),
    ("PCIE_VP_0P85V", 865, 3015), ("PCIE1_VP_0P85V", 865, 0), ("SYS_5P0V", 5028, 0),
    ("LDO_3P3V", 3353, 0), ("VIN_AUX_12V", 12164, 0),
)


def main() -> int:
    prog = os.path.basename(sys.argv[0])
    if prog in _TOOLS:
        tool, args = prog, sys.argv[1:]
    else:  # invoked directly: `mock_rbln.py <tool> ...`
        if len(sys.argv) < 2:
            return 0
        tool, args = sys.argv[1], sys.argv[2:]

    handler = {
        "rbln-smi": _rbln_smi,
        "rbln": _rbln,
        "rbln-smcif": _rbln_smcif,
        "rbln-prog": _rbln_prog,
        "rbln-flash": _rbln_flash,
        "rbln-security": _rbln_security,
        "rbln-spdm": _rbln_spdm,
        "rblntrace": _rblntrace,
        "ipmitool": _ipmitool,
        "dmidecode": _dmidecode,
        "lspci": _lspci,
        "systemctl": _systemctl,
    }.get(tool)
    if handler is None:
        return 0
    return handler(args) or 0


# --- tools -----------------------------------------------------------------
def _rbln_smi(args: list[str]) -> int:
    # `rbln-smi group -c G -a N` (separate) / `group -d G` (combine) — RSD state.
    if args and args[0] == "group":
        groups = _load_groups()
        if "-c" in args:  # create group G → place device (G-1) into it (unique)
            gid = _int_opt(args, "-c", 0)
            if 1 <= gid <= _NUM_DEVICES:
                groups[gid - 1] = gid
        elif "-d" in args:  # dissolve group G → its members return to RSD0
            gid = _int_opt(args, "-d", 0)
            for devid, g in list(groups.items()):
                if g == gid:
                    groups[devid] = 0
        _save_groups(groups)
        return 0

    groups = _load_groups()
    if "-g" in args:  # rbln-smi -g -j : all devices
        devices = [_smi_device(i, group_id=groups.get(i, 0)) for i in range(_NUM_DEVICES)]
        print(json.dumps({"devices": devices}, indent=2))
        return 0
    dev_id = _int_opt(args, "-d", 0)  # rbln-smi -j -d <id> : one device
    dev = _smi_device(dev_id, group_id=groups.get(dev_id, 0), detailed=True)
    print(json.dumps({"devices": [dev], "contexts": []}, indent=2))
    return 0


def _rbln(args: list[str]) -> int:
    if not args:
        return 0
    sub = args[0]
    if sub == "sysinfo":
        kind = _opt(args, "-t", "")
        dev_id = _int_opt(args, "-d", 0)
        if kind == "thermal":
            _print_thermal(dev_id, chiplet=_int_opt(args, "-c", None))
        elif kind == "smc_pwr":
            _print_smc_pwr(dev_id)
        elif kind == "cmu_freq":
            cfg = _load_hwcfg(dev_id)
            print("CMU Frequency Information:")
            print(f"  DRAM : {cfg['hbm3_data_rate']}")
            print(f"  DCL0 : {cfg['dcluster_clk']}")
        elif kind == "id":  # UUIDs — derived from chip_id[0], per uuid_gen scheme
            for i in range(5):
                print(f"  UUID{i} : {_uuid(dev_id, i)}")
        return 0
    if sub == "profile":
        print("------- CHIPLET_ID[0] Memory total read/write bytes ------")
        print("TPUT : R(414696 MB/s) / W(414672 MB/s)")
        print("----------------------------------------------------------")
        print("ber = 0.000000e+00, ecc(0), data(9.349034e+12)")
        return 0
    if sub == "hw_cfg":
        dev_id = _int_opt(args, "-d", 0)
        if "-w" in args:
            _save_hwcfg(dev_id, args)
        elif "-e" in args:  # erase → readback must report all-zero (EraseHwCfg verify)
            _write_state(
                f"hwcfg_{dev_id}.json",
                json.dumps({"dcluster_clk": 0, "hbm3_data_rate": 0, "dnc_core_vol": 0}),
            )
        elif "-r" in args:
            cfg = _load_hwcfg(dev_id)
            print("HW Config:")
            print(f"  DCluster Clock : {cfg['dcluster_clk']} MHz")
            print(f"  HBM3 Data Rate : {cfg['hbm3_data_rate']} Mbps")
            print(f"  DNC Core Voltage : {cfg['dnc_core_vol']} mV")
        return 0
    if sub == "ver":
        for key in ("FW release", "SMC Version", "Tool Version", "Host Driver"):
            print(f"[{key}] {_FW_VERSION}")
        return 0
    if sub == "product_info":
        dev_id = _int_opt(args, "-d", 0)
        print(f"product_name: {_PRODUCT_NAME}")
        print(f"card_serial_num: {_card_serial(dev_id)}")
        print(f"board_type: {_BOARD_TYPE}")
        print(f"board_revision: {_BOARD_REVISION}")
        print(f"smt_lot_num: {_smt_lot(dev_id)}")
        return 0
    if sub == "chip_info":
        dev_id = _int_opt(args, "-d", 0)
        for cl in range(_CHIPLETS):
            print(f"  CL {cl} : {_chip_id(dev_id, cl)}")
        return 0
    if sub == "hbm3_repair":
        print(json.dumps({
            "dword_repair_map": {"total_channels_repaired": 0},
            "aword_repair_map": {"total_channels_repaired": 0},
        }, indent=2))
        return 0
    return 0


def _rbln_smcif(args: list[str]) -> int:
    if not args:
        return 0
    sub = args[0]
    if sub == "power_mon":
        rails = [
            {"device": n, "index": 0, "volt_mv": v, "curr_ma": c, "tdie_c": td}
            for n, v, c, td in _SMC_POWER_RAILS
        ]
        rails.append({"device": "UNTRACKED_RAIL", "index": 0, "volt_mv": 0, "curr_ma": 0, "tdie_c": 99})
        print(json.dumps(rails, indent=2))
        return 0
    if sub == "sensor_mon":
        data = {
            "temp": [{"sensor": "TMP0", "temp_c": 33}, {"sensor": "TMP1", "temp_c": 44}],
            "adc": [{"device": n, "volt_mv": v, "curr_ma": c} for n, v, c in _SMC_ADC],
        }
        print(json.dumps(data, indent=2))
        return 0
    if sub == "nvm":
        slot = _int_opt(args, "-s", 0)
        if "-w" in args:  # persist binning_* / product_info key=value writes
            kv = {
                t.split("=", 1)[0]: t.split("=", 1)[1]
                for t in args
                if "=" in t and not t.startswith("-")
            }
            if kv:
                _save_nvm(slot, kv)
            return 0
        if "-r" in args:  # base identity merged with any persisted writes (readback)
            if slot in _SLOTS:
                data = _nvm_dict(_SLOTS.index(slot))
                data.update(_load_nvm(slot))
                print(json.dumps(data, indent=2))
            else:
                print("{}")
        return 0
    # `otp -w ...` and anything else → succeed.
    return 0


def _rbln_prog(args: list[str]) -> int:
    # `rbln-prog -i oob -c erase -t external -s <slot> -a <addr> -l <len>` — hw_cfg
    # region erase over the BMC side-band. Slot-keyed, so it works with no device_id.
    if _opt(args, "-c", "") != "erase":
        return 0
    slot = _int_opt(args, "-s", -1)
    if slot not in _SLOTS:
        print(f"rbln-prog: slot {slot} not present", file=sys.stderr)
        return 1
    _write_state(
        f"hwcfg_{_SLOTS.index(slot)}.json",
        json.dumps({"dcluster_clk": 0, "hbm3_data_rate": 0, "dnc_core_vol": 0}),
    )
    print(f"erase done: slot {slot} addr {_opt(args, '-a', '?')} len {_opt(args, '-l', '?')}")
    return 0


def _rbln_flash(args: list[str]) -> int:
    is_cp = "--cp" in args
    _print_flash(is_cp=is_cp, image_path=_opt(args, "-f", ""))
    return 0


def _rbln_security(args: list[str]) -> int:
    # `rbln-security srt <flag> ...` — status 0 + MISSION line satisfies every check.
    print("CL0 Status: 0x00000000  Error Code: 0x00000000")
    print("Lifecycle mission: 0x20010D03 (MISSION (deployable))")
    return 0


def _rbln_spdm(args: list[str]) -> int:
    # `-s <bus> --export-idevid-{csr,cert-chain} -o <path>` → write a non-empty
    # artifact; `--import-idevid-cert-chain -i <path>` → succeed with no output.
    out = _opt(args, "-o", "")
    if out:
        try:
            with open(out, "wb") as f:
                f.write(b"MOCK-IDEVID-ARTIFACT\n")
        except OSError:
            pass
    return 0


def _rblntrace(args: list[str]) -> int:
    if not args or args[0] != "retrace":
        return 0
    dev_id = _int_opt(args, "-d", 0)
    # test_unit_<id> is embedded in the -w log dir (…/test_unit_<id>/dev_slot_<slot>).
    tu_match = _TEST_UNIT_RE.search(_opt(args, "-w", ""))
    test_unit_id = int(tu_match.group(1)) if tu_match else None
    raw = next((a.split("=", 1)[1] for a in args if a.startswith("--app_execution_time=")), "0")
    try:
        app_time = float(raw)
    except ValueError:
        app_time = 0.0
    group = _load_groups().get(dev_id, 0) or (dev_id + 1)

    # Startup logs (model load), then run for the requested duration, then the
    # teardown + perf report — mirrors the real tool's server.log output.
    print("Supported version: trace(3.4) app(3.4)")
    print(f"Overwrite device name {dev_id} -> 0")
    print("Overwrite NPU ID list")
    print(" - 0 -> 0")
    print(f"Overwrite group_id from 0 to {group} in cc_info")
    print(f"Run inference 10 with app_execution_time_s {int(app_time)}")
    if _retrace_should_fail(dev_id, test_unit_id):  # config-driven fault injection → non-zero exit
        print("[RBLN_RT_ERR] inference aborted (mock fault injection)")
        print("Report: FAILED(rc -1)")
        return 1
    for dva, size in ((0x100000000, 0x3180), (0x100200000, 0x1980),
                      (0x100400000, 0x3180), (0x100600000, 0x1980)):
        print(f"[RBLN_UMD_api_WARN] rbln_validate_cmd_invoke_cs_desc: "
              f"command stream dva {hex(dva)} size {hex(size)} not CP-accessible")
    sys.stdout.flush()

    time.sleep(app_time + 2.0)  # real tool finishes ~1-2s after app_execution_time

    print(f"[RBLN_TRACE_health_monitor_WARN] app_expiry_check: application runtime "
          f"exceeded limit ({int(app_time)} sec); requesting teardown")
    print("Perf (waited functions) : average(us) 3155.04 total(us) 177215436.75 "
          "count 56169 (min(us) 2936.76 max(us) 4452.21)")
    print(f"Report: PASSED(rc 0), iter count 5616, infer count 56169(10), "
          f"elapsed_time_us {int(app_time * 1_000_000)}")
    return 0


def _ipmitool(args: list[str]) -> int:
    if args and args[0] == "raw":  # fan control — just succeed
        return 0
    if args[:3] == ["sdr", "list", "all"]:
        # Slow, uninterruptible BMC read — the engine can't kill this mid-call, so a
        # stop during the poll reproduces the "did not stop cleanly" race.
        if _IPMI_SDR_DELAY_SEC:
            time.sleep(_IPMI_SDR_DELAY_SEC)
        for label, val in (("CPU1 Temp", 55), ("CPU2 Temp", 57), ("Inlet Temp", 24), ("System Temp", 40)):
            print(f"{label:<16}| {val} degrees C | ok")
        for i in range(1, 11):
            print(f"FAN{i:<13}| {10000 + i * 100} RPM | ok")
        return 0
    if args[:2] == ["sdr", "list"]:  # `sdr list` (get_fan_rpm greps for RPM)
        for i in range(1, 11):
            print(f"FAN{i:<13}| {10000 + i * 100} RPM | ok")
        return 0
    if args[:3] == ["dcmi", "power", "reading"]:  # DCMI power (xlsx v2.2.2)
        # Engine's read_dcmi_power parses the four `<N> Watts` lines; the trailer
        # lines (timestamp / sampling period / state) must be ignored — emit them
        # too so the mock exercises that filtering.
        for label, val in (
            ("Instantaneous power reading:", 443),
            ("Minimum during sampling period:", 210),
            ("Maximum during sampling period:", 720),
            ("Average power reading over sample period:", 445),
        ):
            print(f"    {label:<46}{val} Watts")
        print("    IPMI timestamp:                           Mon Jul 13 16:10:46 2026")
        print("    Sampling period:                          00003587 Seconds.")
        print("    Power reading state is:                   activated")
        return 0
    return 0


def _dmidecode(args: list[str]) -> int:
    # `dmidecode -t slot`; engine greps <bus> -B4 for the `ID:` (slot).
    if "slot" not in args:
        return 0
    for i in range(_NUM_DEVICES):
        slot = _SLOTS[i]
        print(f"Handle 0x{0x0009 + i:04X}, DMI type 9, 17 bytes")
        print("System Slot Information")
        print(f"\tDesignation: CPU SLOT{slot} PCI-E")
        print("\tCurrent Usage: In Use")
        print(f"\tID: {slot}")
        print(f"\tBus Address: {_bus_id(i)}")
        print("")
    return 0


def _lspci(args: list[str]) -> int:
    # `lspci -s <bdf>` (engine's per-device probe, log-only) → that card's line.
    if "-s" in args:
        i = _index_for_bus(_opt(args, "-s", ""))
        if i is not None:
            print(f"{_bus_id(i)} Processing accelerators: Rebellions Inc Device 1eff:0001")
            return 0
        return _exec_real("lspci", args)
    # `lspci -D -d 1eff:` (engine device inventory) → the 8 mock cards. The engine
    # progresses per device found here, so this MUST list them (empty → 0 devices).
    if any("1eff" in a for a in args):
        # This is the SRT run's first hardware query (registration), issued once
        # per run and before any hw_cfg/nvm write — reset accumulated mock state
        # so every run starts clean. (`-nn` is the separate HSM discovery call.)
        if "-nn" not in args:
            _reset_state()
        for i in range(_NUM_DEVICES):
            print(f"{_bus_id(i)} Processing accelerators: Rebellions Inc Device 1eff:0001")
        return 0
    return _exec_real("lspci", args)  # unrelated query → real lspci


def _systemctl(args: list[str]) -> int:
    # Only the rebellions daemon is faked (status must report active for
    # CheckRblnSmdActive); poweroff/reboot are suppressed so a mock never cycles
    # the dev host; everything else defers to the real systemctl.
    if any("rbln-smd" in a for a in args):
        if "status" in args:
            print("● rbln-smd.service - Rebellions System Management Daemon")
            print("     Loaded: loaded (/lib/systemd/system/rbln-smd.service; enabled; preset: enabled)")
            print("     Active: active (running) since Mon 2026-07-13 00:00:00 KST; 1min ago")
        return 0  # start / stop / restart / is-active → success (active)
    if args and args[0] in ("poweroff", "reboot", "halt", "shutdown", "suspend"):
        print(f"[mock] systemctl {args[0]} suppressed")
        return 0
    return _exec_real("systemctl", args)


# --- output builders -------------------------------------------------------
def _smi_device(dev_id: int, *, group_id: int = 0, detailed: bool = False) -> dict:
    t = int(_hbm_temp(dev_id))
    dev = {
        "device": f"rbln{dev_id}",
        "sid": f"00000000{_card_serial(dev_id)}",
        "group_id": group_id,
        "npu": dev_id,
        "status": "normal",
        "temperature": f"{t}C",
        "card_power": "185025000uW",
        "pci": {"bus_id": _bus_id(dev_id)},
    }
    if detailed:
        dev["util"] = 100
        dev["pstate"] = "0"
        # link_speed / link_width must match test_config.json (CheckDeviceState).
        dev["pci"].update({"link_speed": "32.0GT/s", "link_width": "16"})
    return dev


def _print_thermal(dev_id: int, chiplet: int | None) -> None:
    base = _hbm_temp(dev_id)
    if chiplet is not None:  # per-chiplet peak
        print(f"Reading sysinfo from chiplet {chiplet}\n")
        print("Thermal Information:")
        print("  Version                 : 1")
        print(f"  Temperature (C)          : {int(base + chiplet * 0.5)}")
        print("  Sensor Count             : 256")
        return
    print("Reading sysinfo from chiplet 0\n")
    print("Thermal Information:")
    print("  Version                 : 1")
    print(f"  Temperature (C)          : {int(base + 3)}")  # SoC peak (first line)
    print("  Sensor Count             : 256\n")
    for c in range(_CHIPLETS):
        cl = int(base + c * 0.5)
        print(f"  Chiplet {c}:")
        print(f"    DRAM Temperature (C)      : {cl}")
        print(f"    DRAM SID0 Temperature (C) : {max(0, cl - 11)}\n")


def _print_smc_pwr(dev_id: int) -> None:
    core_v = _load_hwcfg(dev_id)["dnc_core_vol"]
    print("Reading sysinfo from chiplet 0\n")
    print("SMC PWR Information:")
    print("  Version       : 1")
    for key, unit, val in _SMC_PWR_RAILS:
        if key in ("rebel_core0_voltage", "rebel_core1_voltage"):
            val = core_v
        print(f"  {key} ({unit}) : {val}")


def _print_flash(*, is_cp: bool, image_path: str) -> None:
    meta_title = "Firmware Image Metadata Information" if is_cp else "SMC Firmware Image Information"
    size_line = (
        "  Binary Size      : 15204224 bytes (0xE7FF80)"
        if is_cp
        else "  Image Size       : 262144 bytes (0x40000)"
    )
    crc = "0xE252770C" if is_cp else "0x212E7302"
    exec_sec = 94 if is_cp else 5
    buses = [_bus_id(i) for i in range(_NUM_DEVICES)]

    print("The logs will only be printed to STDOUT")
    for i, bus in enumerate(buses):
        print(f"  [{i}] {bus} (REBEL)")
    print(f"Detected {_NUM_DEVICES} REBEL device(s)")
    print(f"Image File: {image_path}")
    print("=================================================")
    print(f"        {meta_title}")
    print("=================================================")
    print("  Magic Number     : 0x52424C4E")
    print("  Platform         : REBEL")
    print(size_line)
    print(f"  CRC32 Checksum   : {crc}")
    print(f"  Version String   : {_FW_VERSION}")
    print("=================================================\n")
    print(f"Creating {_NUM_DEVICES} threads for firmware update...")
    for i in range(_NUM_DEVICES):
        print(f"Start for thread {i} update")
    for bus in buses:
        print(f"Update SUCCESS for {bus} (Execution time: {exec_sec} sec)")
    if is_cp:
        for bus in buses:
            print(f"PCI reset triggered for {bus}")
    for bus in buses:
        print(f"Boot verify succeeded for {bus}")
    print("==== Firmware Update Results ====")
    for bus in buses:
        print(f"{bus} : SUCCESS [O]")
    print("=================================")


# --- identity --------------------------------------------------------------
def _card_serial(dev_id: int) -> str:
    return _DEVICE_TAGS[dev_id][1]  # 8 digits; last 8 of the 16-digit sid


def _smt_lot(dev_id: int) -> str:
    return _DEVICE_TAGS[dev_id][2]


def _chip_id(dev_id: int, chiplet: int) -> str:
    return f"CR13-{_card_serial(dev_id)}-CL{chiplet}"


def _uuid(dev_id: int, index: int) -> str:
    """UUID_index derived from chip_id[0], matching core.util.uuid_gen.stable_uuid_v8."""
    msg = (f"chipid:v1:uuid_{index:02d}:" + _chip_id(dev_id, 0)).encode("utf-8")
    raw = bytearray(hmac.new(_UUID_SECRET, msg, hashlib.sha256).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | (8 << 4)
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _nvm_dict(dev_id: int) -> dict:
    # uuid_00 empty → CheckUuid's NVM cross-check short-circuits; card_serial must
    # equal sid[-8:] (reconcile) and the seeded tag's card_serial_num.
    return {
        "product_name": _PRODUCT_NAME,
        "board_type": _BOARD_TYPE,
        "board_revision": _BOARD_REVISION,
        "uuid_00": "",
        "card_serial": _card_serial(dev_id),
        "smt_lot": _smt_lot(dev_id),
    }


# --- RSD group state (persist across CLI invocations) ----------------------
def _load_groups() -> dict[int, int]:
    raw = _read_state("groups.json")
    if raw:
        try:
            return {int(k): int(v) for k, v in json.loads(raw).items()}
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
    return {i: 0 for i in range(_NUM_DEVICES)}


def _save_groups(groups: dict[int, int]) -> None:
    _write_state("groups.json", json.dumps({str(k): v for k, v in groups.items()}))


# --- hw_cfg state (persist writes so -r / cmu_freq / smc_pwr reflect them) --
def _save_hwcfg(dev_id: int, args: list[str]) -> None:
    cfg = _load_hwcfg(dev_id)
    for token in args:
        if token.startswith("--dcluster_clk="):
            cfg["dcluster_clk"] = int(token.split("=", 1)[1])
        elif token.startswith("--hbm3_data_rate="):
            cfg["hbm3_data_rate"] = int(token.split("=", 1)[1])
        elif token.startswith("--dnc_core_vol="):
            cfg["dnc_core_vol"] = int(token.split("=", 1)[1])
    _write_state(f"hwcfg_{dev_id}.json", json.dumps(cfg))


def _load_hwcfg(dev_id: int) -> dict:
    raw = _read_state(f"hwcfg_{dev_id}.json")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {"dcluster_clk": 1400, "hbm3_data_rate": 9200, "dnc_core_vol": 750}


# --- NVM write state (persist binning / product_info so -r readback matches) --
def _load_nvm(slot: int) -> dict:
    raw = _read_state(f"nvm_{slot}.json")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {}


def _save_nvm(slot: int, kv: dict) -> None:
    data = _load_nvm(slot)
    data.update(kv)
    _write_state(f"nvm_{slot}.json", json.dumps(data))


# --- temp ramp (35→95°C sawtooth, 60s period) ------------------------------
def _hbm_temp(seed: int) -> float:
    raw = _read_state("started")
    now = time.time()
    if raw:
        started = float(raw)
    else:
        started = now
        _write_state("started", str(now))
    elapsed = now - started
    return 35.0 + (elapsed % 60.0) + (seed % 3) * 0.4


# --- helpers ---------------------------------------------------------------
def _opt(args: list[str], flag: str, default):
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def _int_opt(args: list[str], flag: str, default):
    val = _opt(args, flag, None)
    if val is None:
        return default
    digits = "".join(ch for ch in val if ch.isdigit())
    return int(digits) if digits else default


def _write_state(name: str, text: str) -> None:
    try:
        os.makedirs(_STATE, exist_ok=True)
        os.chmod(_STATE, 0o777)
    except OSError:
        pass
    try:
        path = os.path.join(_STATE, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(path, 0o666)
    except OSError:
        pass


def _read_state(name: str) -> str:
    try:
        with open(os.path.join(_STATE, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _bus_id(dev_id: int) -> str:
    return f"0000:{(dev_id + 1) * 0x10:02x}:00.0"


def _reset_state() -> None:
    """Wipe persisted mock state so each SRT run starts clean (fresh NVM binning,
    RSD groups, hw_cfg, temp epoch)."""
    shutil.rmtree(_STATE, ignore_errors=True)


def _index_for_bus(bus: str) -> int | None:
    for i in range(_NUM_DEVICES):
        if _bus_id(i) == bus:
            return i
    return None


def _exec_real(tool: str, args: list[str]) -> int:
    """Hand off to the real system binary (this mock shadows it on PATH)."""
    for path in (f"/usr/bin/{tool}", f"/bin/{tool}", f"/usr/sbin/{tool}", f"/sbin/{tool}"):
        if os.path.exists(path):
            os.execv(path, [tool, *args])  # replaces the process; does not return
    return 0  # real tool absent → succeed quietly


if __name__ == "__main__":
    sys.exit(main())
