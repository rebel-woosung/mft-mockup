# mfg_mockup

Rebellions 제조 CLI 툴들의 mock 모음. 실제 NPU나 `rbln*` 툴이 없어도 로컬 개발 환경에서 `rebellions_server_run_engine_v2`의 SRT 전체 흐름을 돌릴 수 있게 합니다. 단일 dispatcher `mock_rbln.py`가 `argv[0]`(호출된 이름)로 분기하며, 엔진이 파싱하는 출력 형식을 그대로 재현합니다.

mock 대상: `rbln-smi` · `rbln` · `rbln-smcif` · `rbln-flash` · `rbln-security` · `rbln-spdm` · `rbln-prog` · `rblntrace` · `ipmitool` · `dmidecode` · `lspci` · `systemctl`

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
- 링크는 `/usr/local/bin`(sudo secure_path 앞쪽 → PATH 호출이 mock으로 잡힘)에 둡니다. `rbln-*`는 엔진이 `find /usr/bin`으로 존재를 확인하고 `rbln-spdm`을 절대경로로 호출하므로 `/usr/bin`에도 함께 링크합니다.

## _v2에서 주석처리 필요

sysfs/debugfs 직접 접근이라 PATH mock으로 못 잡습니다. 아래 3곳을 no-op으로 만들어야 흐름이 끝까지 진행됩니다.

- **`action/shared/combine_rsd_group_action.py`** — `_wait_for_sysfs_ready(devices)` 호출 줄 주석 (hard reset 시 `SystemAbortError` 방지)
- **`adapter/host_command_control/base.py` → `trigger_hard_reset()`** — 본문 주석 (pass)
- **`adapter/host_command_control/base.py` → `read_tdr()`** — 본문 주석 후 `return "0"`

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
