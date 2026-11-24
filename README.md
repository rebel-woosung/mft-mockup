# mfg_mockup

Rebellions 제조 CLI 툴들의 mock 모음. 실제 NPU나 `rbln*` 툴이 없어도 로컬 개발 환경에서 `rebellions_server_run_engine_v2`의 SRT 전체 흐름을 돌릴 수 있게 합니다. 단일 dispatcher `mock_rbln.py`가 `argv[0]`(호출된 이름)로 분기하며, 엔진이 파싱하는 출력 형식을 그대로 재현합니다.

mock 대상: `rbln-smi` · `rbln` · `rbln-smcif` · `rbln-flash` · `rbln-security` · `rbln-spdm` · `rbln-prog` · `rblntrace` · `ipmitool` · `dmidecode` · `lspci` · `setpci` · `systemctl` · `modprobe`

## 설치 / 제거

```bash
cd ~/script/mfg_mockup
./install.sh      # 심볼릭 링크 + 더미 FW 이미지 생성 (sudo)
./uninstall.sh    # 원복
```

- **NOPASSWD sudo 필요** — 엔진이 모든 명령을 `sudo bash -lc ...`로 실행합니다.
  ```bash
  echo "$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/rbcn-dev && sudo chmod 440 /etc/sudoers.d/rbcn-dev
  ```
- FW 버전은 `mock_rbln.py`의 `_FW_VERSION`만 바꾸면 됩니다. `install.sh`/`uninstall.sh`가 이 값을 읽어 `/lib/firmware/rebellions/<버전>/` 더미 이미지를 만들고 지웁니다. 엔진 `test_config.json`의 `target_fw`/`cp_fw_update.path`도 같은 버전이어야 합니다.
- workload 파일: 엔진이 `continue_on_fail: false`일 때 켜진 test_unit의 workload 파일이 `workload_dir`에 있는지 확인하므로, `install.sh`가 엔진 CR13.json(state 사본이 있으면 그것, 없으면 seed)의 파일명으로 `/data/rbcn/rb-srt-main-engine/workload/`에 빈 파일을 만듭니다. 엔진 위치가 다르면 `ENGINE_DIR=<경로> ./install.sh`. `uninstall.sh`는 빈 stub만 지웁니다.
- 팬 RPM은 `ipmitool raw 0x30 0x70 0x66 0x01 <zone> <duty>`로 설정한 duty를 따라갑니다 (100% → 약 16500, 30% → 약 6000). 엔진의 팬 100% 후 평균 > 15000, 30% 후 < 8000 판정을 통과합니다.
- 링크는 `/usr/local/bin`(sudo secure_path 앞쪽 → PATH 호출이 mock으로 잡힘)에 둡니다. `rbln-*`는 엔진이 `find /usr/bin`으로 존재를 확인하고 `rbln-spdm`을 절대경로로 호출하므로 `/usr/bin`에도 함께 링크합니다.

## _v2에서 주석처리 필요

sysfs/debugfs 직접 접근이라 PATH mock으로 못 잡습니다. 아래 6곳을 no-op/고정값으로 만들어야 흐름이 끝까지 진행됩니다.

- **`action/shared/combine_rsd_group_action.py`** — `_wait_for_sysfs_ready(devices)` 호출 줄 주석 (hard reset 시 `SystemAbortError` 방지)
- **`adapter/host_command_control/base.py` → `trigger_hard_reset()`** — 본문 주석 (pass)
- **`adapter/host_command_control/base.py` → `read_tdr()`** — 본문 주석 후 `return "0"`
- **`adapter/host_command_control/base.py` → `pci_sysfs_exists()`** — 본문 주석 후 `return True` (`CheckNpuCardStatusStep`에서 mock 카드 전부가 Linkdown 되는 것 방지)
- **`adapter/host_command_control/base.py` → `pci_sysfs_path()`** — 본문 주석 후 `return f"/sys/devices/pci0000:f0/0000:f0:01.0/0000:f1:00.0/{bus_address}"` (mock 카드를 가짜 Root Port `0000:f0:01.0` / switch USP `0000:f1:00.0` 아래에 둠. `CheckHostSwitchLinkAction`이 USP를 찾고, mock `lspci -vvv`·`setpci`가 이 USP에 응답)
- **`adapter/host_command_control/base.py` → `_read_pci_link()`** — 본문 주석 후 `return "32.0 GT/s PCIe", "16"` (`CheckHostSwitchLinkAction`·`CheckPcieDeviceLinkStatusAction`이 sysfs `current/max_link_speed|width`를 읽음)

## 태그 등록

`POST /api/v1/cr13_srt/info/tag`

```bash
curl -X POST http://localhost:8000/api/v1/cr13_srt/info/tag \
  -H 'Content-Type: application/json' \
  -d '{
  "01": "267805200000002;52628079;RBLN-CR13;2;9;RBADB43EENBC-550",
  "02": "267685500000005;52628066;RBLN-CR13;2;9;RBADB43EENBC-550",
  "03": "267685500000015;52628076;RBLN-CR13;2;9;RBADB43EENBC-550",
  "04": "267685500000011;52628072;RBLN-CR13;2;9;RBADB43EENBC-550",
  "09": "267685500000010;52628071;RBLN-CR13;2;9;RBADB43EENBC-550",
  "10": "267685500000002;52628063;RBLN-CR13;2;9;RBADB43EENBC-550",
  "11": "267685500000008;52628069;RBLN-CR13;2;9;RBADB43EENBC-550",
  "12": "267685500000009;52628070;RBLN-CR13;2;9;RBADB43EENBC-550"
}'
```
