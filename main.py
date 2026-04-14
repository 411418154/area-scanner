"""
main.py
========

Python 版 Area Scanner 的主程式入口。

[修改註記]
- Updated by assistant on 2026-04-08 02:27 UTC
- 目的：加入 CLI 診斷流程與解析容錯，方便現場快速確認 TLV / tracker 健康度。

本版重點
--------
1. 保留原本 GUI 啟動流程（預設行為不變）。
2. 新增 `--diagnose` 模式，做「TI Area Scanner 常見 TLV 訊號健康度」判讀。
3. 讓現場調校時可以先用命令列確認：
   - 是否有收到 TLV type 10 / 11
   - tracker 是否有持續輸出 target
   - 是否出現「dyn 很多但 target 很少」的常見狀況
"""

from __future__ import annotations

import sys
import time
import argparse
import importlib.util
from dataclasses import dataclass
from pathlib import Path


def _add_project_root_to_sys_path() -> None:
    """
    把目前檔案所在資料夾加入 Python 搜尋路徑。

    為什麼要這樣做？
    ----------------
    如果使用者是從 VS Code、PowerShell、或其他工作目錄啟動，
    Python 有時候不一定會自動把專案資料夾放到 import 路徑最前面。

    先手動加入，可以減少：
    - 找不到 gui_main.py
    - 後續找不到 parser_as.py / serial_manager.py
    這類常見錯誤。
    """
    project_root = Path(__file__).resolve().parent
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)


_add_project_root_to_sys_path()


@dataclass(slots=True)
class DiagnosticStats:
    frames_seen: int = 0
    frames_with_type10: int = 0
    frames_with_type11: int = 0
    frames_with_targets: int = 0
    frames_with_dynamic: int = 0
    frames_dynamic_only: int = 0
    max_targets_in_frame: int = 0
    parse_errors: int = 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Area Scanner Python 啟動器（GUI / 診斷模式）"
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="啟用串口診斷模式（不開 GUI），輸出 tracker / TLV 健康度摘要。",
    )
    parser.add_argument("--cli-port", default="COM6", help="CLI port，預設 COM6")
    parser.add_argument("--data-port", default="COM5", help="DATA port，預設 COM5")
    parser.add_argument("--cli-baud", type=int, default=115200, help="CLI baud，預設 115200")
    parser.add_argument("--data-baud", type=int, default=921600, help="DATA baud，預設 921600")
    parser.add_argument("--cfg", default="", help="cfg 檔案路徑（診斷模式必填）")
    parser.add_argument("--seconds", type=float, default=12.0, help="診斷秒數，預設 12 秒")
    return parser.parse_args(argv[1:])


def _build_serial_config(args: argparse.Namespace):
    from serial_manager import SerialConfig

    return SerialConfig(
        cli_port=args.cli_port,
        data_port=args.data_port,
        cli_baud=args.cli_baud,
        data_baud=args.data_baud,
        timeout_s=1.0,
        cfg_file=args.cfg,
        command_delay_s=0.02,
    )


def _print_diagnostic_report(stats: DiagnosticStats) -> None:
    print("\n========== Area Scanner Diagnostic Report ==========")
    print(f"frames_seen               : {stats.frames_seen}")
    print(f"frames_with_tlv_type_10   : {stats.frames_with_type10}")
    print(f"frames_with_tlv_type_11   : {stats.frames_with_type11}")
    print(f"frames_with_targets       : {stats.frames_with_targets}")
    print(f"frames_with_dynamic       : {stats.frames_with_dynamic}")
    print(f"frames_dynamic_only       : {stats.frames_dynamic_only}")
    print(f"max_targets_in_one_frame  : {stats.max_targets_in_frame}")
    print(f"parse_errors              : {stats.parse_errors}")

    if stats.frames_seen == 0:
        print("\n[判斷] 沒有收到完整 frame。請先檢查 DATA port / baud / cfg 是否正確。")
        return

    ratio_targets = stats.frames_with_targets / stats.frames_seen
    ratio_type10 = stats.frames_with_type10 / stats.frames_seen
    ratio_dynamic_only = stats.frames_dynamic_only / stats.frames_seen

    print("\n[TI Area Scanner 風格判讀]")
    if ratio_type10 < 0.2:
        print("- TLV type 10 出現率偏低：更像是 tracker 輸出路徑未正常啟用，或解析模式錯位。")
    else:
        print("- TLV type 10 出現率正常：代表 target list TLV 已經在資料流中。")

    if ratio_targets < 0.1:
        print("- target 產出率偏低：建議先確認人員移動軌跡與區域設定（1~4m、穩定移動）。")
    else:
        print("- target 產出率可接受：tracker 有穩定分配到目標。")

    if ratio_dynamic_only > 0.5:
        print("- 大量 frame 為 dyn-only：常見於 cluster 有、tracker 尚未鎖定目標。")
    else:
        print("- dyn 與 target 比例健康。")


def run_diagnose_mode(args: argparse.Namespace) -> int:
    if not args.cfg:
        print("[錯誤] 診斷模式必須提供 --cfg 路徑。")
        return 2

    cfg_path = Path(args.cfg).expanduser().resolve()
    if not cfg_path.exists():
        print(f"[錯誤] 找不到 cfg 檔案：{cfg_path}")
        return 2

    cfg = _build_serial_config(args)
    cfg.cfg_file = str(cfg_path)

    from parser_as import AreaScannerParser, parse_packet
    from serial_manager import SerialManager

    manager = SerialManager(cfg)
    parser = AreaScannerParser()
    stats = DiagnosticStats()
    start_ts = time.time()
    last_log_ts = 0.0

    try:
        print("[INFO] Opening ports...")
        manager.open_ports()
        manager.clear_buffers()

        print("[INFO] Sending cfg...")
        for line in manager.send_cfg_file(cfg.cfg_file):
            print(line)

        print("[INFO] Reading frames...\n")
        while time.time() - start_ts < args.seconds:
            chunk = manager.read_data_once(max_bytes=8192)
            if not chunk:
                time.sleep(0.002)
                continue

            parser.append_data(chunk)
            packets = parser.extract_packets()
            for packet in packets:
                try:
                    frame = parse_packet(packet)
                except Exception as exc:
                    stats.parse_errors += 1
                    print(f"[WARN] parse error: {exc}")
                    continue
                stats.frames_seen += 1

                has_dynamic = len(frame.dynamic_points) > 0
                has_targets = len(frame.targets) > 0
                if has_dynamic:
                    stats.frames_with_dynamic += 1
                if has_targets:
                    stats.frames_with_targets += 1
                if has_dynamic and not has_targets:
                    stats.frames_dynamic_only += 1

                if 10 in frame.tlv_types:
                    stats.frames_with_type10 += 1
                if 11 in frame.tlv_types:
                    stats.frames_with_type11 += 1

                stats.max_targets_in_frame = max(stats.max_targets_in_frame, len(frame.targets))

                now = time.time()
                if now - last_log_ts >= 0.4:
                    print(
                        f"frame={frame.header.frame_number:5d} | "
                        f"tlv={frame.tlv_types} | "
                        f"dyn={len(frame.dynamic_points):3d} | "
                        f"targets={len(frame.targets):2d} | "
                        f"mode={frame.tlv_length_mode}"
                    )
                    last_log_ts = now

        _print_diagnostic_report(stats)
        return 0
    finally:
        try:
            manager.send_cli_command("sensorStop", read_response=False)
        except Exception:
            pass
        manager.close_ports()


def run_gui_mode() -> int:
    if importlib.util.find_spec("PySide6") is None:
        print("[錯誤] 目前環境沒有安裝 PySide6。")
        print("請先安裝：")
        print(" python -m pip install PySide6 pyqtgraph PyOpenGL numpy pyserial")
        return 1

    from PySide6.QtWidgets import QApplication
    from gui_main import AreaScannerMainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Area Scanner Python Visualizer")
    app.setOrganizationName("TI_Project")

    window = AreaScannerMainWindow()
    window.show()
    return app.exec()


# ----------------------------------------------------------
# GUI 入口主函式
# ----------------------------------------------------------
def main() -> int:
    args = _parse_args(sys.argv)
    if args.diagnose:
        return run_diagnose_mode(args)
    return run_gui_mode()


if __name__ == "__main__":
    raise SystemExit(main())