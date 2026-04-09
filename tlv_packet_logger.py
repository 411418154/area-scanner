import serial
import struct
import time
from pathlib import Path
from datetime import datetime

# ==========================================================
# 1. 請先改這裡
# ==========================================================
CLI_PORT = 'COM4'          # 送 cfg 的序列埠
DATA_PORT = 'COM5'         # 接收雷達資料的序列埠
CLI_BAUD = 115200
DATA_BAUD = 921600
CFG_FILE = r"C:\Users\user\Desktop\area scanner\area_scanner_68xx_ISK-0063.cfg"

# log 檔資料夾；程式會自動建立
LOG_DIR = Path('logs')

# 若不想每包都顯示在終端，可改成 False
PRINT_TO_CONSOLE = True

# ==========================================================
# 2. mmWave / Area Scanner 基本設定
# ==========================================================
MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'
HEADER_MIN_LEN = 16          # 先至少讀到 totalPacketLen 所需長度
MAX_PACKET_SIZE = 65535      # 防呆，避免長度異常
BYTES_PER_LINE = 32          # 寫 log 時每行幾個 byte


def bytes_to_spaced_hex(data: bytes, bytes_per_line: int = BYTES_PER_LINE) -> str:
    """把 bytes 轉成你要的格式：每個 byte 中間一個空格。"""
    hex_list = [f'{b:02X}' for b in data]
    if bytes_per_line <= 0:
        return ' '.join(hex_list)

    lines = []
    for i in range(0, len(hex_list), bytes_per_line):
        lines.append(' '.join(hex_list[i:i + bytes_per_line]))
    return '\n'.join(lines)


def send_cfg(cli_serial: serial.Serial, cfg_file: str) -> None:
    """逐行送 cfg 給雷達。"""
    print('準備傳送 cfg...')
    with open(cfg_file, 'r', encoding='utf-8', errors='ignore') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith('%'):
                continue

            cli_serial.write((line + '\n').encode())
            time.sleep(0.02)
            resp = cli_serial.readline().decode(errors='ignore').strip()
            print(f'傳送: {line} | 回傳: {resp}')


def open_log_file() -> tuple[Path, object]:
    """建立 log 檔。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = LOG_DIR / f'tlv_raw_log_{ts}.txt'
    f = open(log_path, 'w', encoding='utf-8')
    return log_path, f


def write_packet(log_file, packet_index: int, packet: bytes) -> None:
    """把一整包原始封包寫進 log。"""
    text = bytes_to_spaced_hex(packet)

    # 為了讓日後分辨每一包，前後加簡單標記
    log_file.write(f'=== PACKET {packet_index} | {len(packet)} bytes ===\n')
    log_file.write(text)
    log_file.write('\n\n')
    log_file.flush()

    if PRINT_TO_CONSOLE:
        print(f'\n[PACKET {packet_index}] {len(packet)} bytes')
        print(text)


def read_packets(data_serial: serial.Serial, log_file) -> None:
    """
    持續讀取 data port：
    1. 找 magic word
    2. 讀 total packet length
    3. 收滿整包
    4. 寫入 log
    """
    buffer = b''
    packet_index = 0

    print('\n開始接收 TLV 原始封包...（按 Ctrl+C 停止）\n')

    while True:
        incoming = data_serial.read(data_serial.in_waiting or 1)
        if incoming:
            buffer += incoming

        while True:
            idx = buffer.find(MAGIC_WORD)

            # 找不到 magic word，保留最後 7 bytes 防止切半
            if idx == -1:
                if len(buffer) > 7:
                    buffer = buffer[-7:]
                break

            # 把 magic word 前面的雜訊丟掉
            if idx > 0:
                buffer = buffer[idx:]
                idx = 0

            # 長度還不夠讀 totalPacketLen
            if len(buffer) < HEADER_MIN_LEN:
                break

            total_packet_len = struct.unpack_from('<I', buffer, 12)[0]

            # 長度不合理就往後滑 1 byte 重新找
            if total_packet_len < len(MAGIC_WORD) or total_packet_len > MAX_PACKET_SIZE:
                print(f'[警告] 不合理的 totalPacketLen = {total_packet_len}，略過 1 byte 重找。')
                buffer = buffer[1:]
                continue

            # 還沒收滿整包
            if len(buffer) < total_packet_len:
                break

            # 切出完整封包
            packet = buffer[:total_packet_len]
            buffer = buffer[total_packet_len:]

            packet_index += 1
            write_packet(log_file, packet_index, packet)


def main() -> None:
    print('正在連接雷達...')
    cli_serial = None
    data_serial = None
    log_file = None
    log_path = None

    try:
        cli_serial = serial.Serial(CLI_PORT, CLI_BAUD, timeout=1)
        data_serial = serial.Serial(DATA_PORT, DATA_BAUD, timeout=1)
        print('連線成功。')

        log_path, log_file = open_log_file()
        print(f'log 檔路徑: {log_path.resolve()}')

        send_cfg(cli_serial, CFG_FILE)
        print('\n--- 雷達已啟動，開始記錄原始 TLV 封包 ---')

        read_packets(data_serial, log_file)

    except KeyboardInterrupt:
        print('\n\n已手動停止。')

    finally:
        try:
            if cli_serial is not None:
                cli_serial.write(b'sensorStop\n')
                time.sleep(0.1)
        except Exception:
            pass

        if cli_serial is not None:
            try:
                cli_serial.close()
            except Exception:
                pass

        if data_serial is not None:
            try:
                data_serial.close()
            except Exception:
                pass

        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass

        if log_path is not None:
            print(f'\nlog 已儲存: {Path(log_path).resolve()}')
        print('通訊埠已關閉。')


if __name__ == '__main__':
    main()
