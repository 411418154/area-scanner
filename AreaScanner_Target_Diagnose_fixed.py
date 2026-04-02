"""
AreaScanner_Target_Diagnose.py
==============================

用途
----
用最直接的方法確認：
1. 你的雷達資料流裡到底有沒有 TLV type 10（tracked target list）
2. 有沒有 dynamic point 但沒有 target
3. 目前 packet 解析採用的是哪種 TLV length 模式

這支程式不依賴 GUI，適合先把「沒有 target」這件事查清楚。

使用方式
--------
1. 把 CLI / DATA / CFG 路徑改成你目前可用的設定
2. 執行：python AreaScanner_Target_Diagnose.py
3. 觀察輸出裡面的：
   - tlv_types=[...]
   - dyn=...
   - static=...
   - targets=...
   - mode=payload_only / inclusive
"""

from __future__ import annotations

import time
from pathlib import Path

from parser_as import AreaScannerParser, parse_packet
from serial_manager import SerialConfig, SerialManager

# 依你目前已確認可用的設定先填好
CLI_PORT = "COM4"
DATA_PORT = "COM5"
CLI_BAUD = 115200
DATA_BAUD = 921600
CFG_FILE = r"C:\ti\radar_toolbox_3_30_00_06\source\ti\examples\Industrial_and_Personal_Electronics\Area_Scanner\chirp_configs\area_scanner_68xx_ISK.cfg"
RUN_SECONDS = 12


def main() -> None:
    cfg_path = Path(CFG_FILE)
    if not cfg_path.exists():
        print(f"[錯誤] 找不到 cfg：{cfg_path}")
        return

    cfg = SerialConfig(
        cli_port=CLI_PORT,
        data_port=DATA_PORT,
        cli_baud=CLI_BAUD,
        data_baud=DATA_BAUD,
        timeout_s=1.0,
        cfg_file=str(cfg_path),
        command_delay_s=0.02,
    )

    manager = SerialManager(cfg)
    parser = AreaScannerParser()

    frames_seen = 0
    frames_with_target_tlv = 0
    frames_with_targets = 0
    frames_with_dyn_only = 0
    start_time = time.time()

    try:
        print("[INFO] Opening ports...")
        manager.open_ports()
        manager.clear_buffers()

        print("[INFO] Sending cfg...")
        for line in manager.send_cfg_file(str(cfg_path)):
            print(line)

        print("\n[INFO] Start reading frames...\n")
        last_print = 0.0

        while time.time() - start_time < RUN_SECONDS:
            chunk = manager.read_data_once()
            if not chunk:
                time.sleep(0.002)
                continue

            parser.append_data(chunk)
            packets = parser.extract_packets()
            for packet in packets:
                frame = parse_packet(packet)
                frames_seen += 1

                if frame.has_target_list_tlv:
                    frames_with_target_tlv += 1
                if len(frame.targets) > 0:
                    frames_with_targets += 1
                if len(frame.dynamic_points) > 0 and len(frame.targets) == 0:
                    frames_with_dyn_only += 1

                # 每 0.3 秒印一次，避免刷太快。
                now = time.time()
                if now - last_print >= 0.3:
                    print(
                        f"frame={frame.header.frame_number:4d} | "
                        f"mode={frame.tlv_length_mode:12s} | "
                        f"tlv_types={frame.tlv_types} | "
                        f"dyn={len(frame.dynamic_points):3d} | "
                        f"static={len(frame.static_points):3d} | "
                        f"targets={len(frame.targets):2d}"
                    )
                    if frame.warnings:
                        print("  warnings:", " ; ".join(frame.warnings))
                    last_print = now

        print("\n========== Summary ==========")
        print(f"frames_seen            = {frames_seen}")
        print(f"frames_with_target_tlv = {frames_with_target_tlv}")
        print(f"frames_with_targets    = {frames_with_targets}")
        print(f"frames_with_dyn_only   = {frames_with_dyn_only}")

        if frames_seen == 0:
            print("[判斷] 沒收到完整 frame，先檢查 DATA port / baud / cfg。")
        elif frames_with_target_tlv == 0:
            print("[判斷] 目前 packet 裡根本沒有 TLV type 10。這比較像是資料流或解析模式問題，不是 viewer 畫不出來。")
        elif frames_with_target_tlv > 0 and frames_with_targets == 0:
            print("[判斷] 有 target TLV，但 target 數量一直是 0。這比較像 tracker 尚未成功分配目標。")
            print("       你可以讓一個人以穩定速度在 1~4m 區間移動幾秒再看。")
        else:
            print("[判斷] target TLV 與 targets 都有出現，接下來就更像是 GUI 顯示層或更新節奏問題。")

    finally:
        try:
            manager.send_cli_command("sensorStop", read_response=False)
        except Exception:
            pass
        manager.close_ports()
        print("[INFO] Ports closed.")


if __name__ == "__main__":
    main()
