import serial
import time
from pathlib import Path
from datetime import datetime

# ==========================================================
# 1. 參數設定
# ==========================================================
CLI_PORT = 'COM6'          # 送 cfg 的序列埠
DATA_PORT = 'COM5'         # 接收雷達資料的序列埠
CLI_BAUD = 115200
DATA_BAUD = 921600
CFG_FILE = r"C:\Users\User\Documents\area-scanner\area_scanner_68xx_ISK-0063.cfg"

# 是否要先送 cfg 啟動雷達
SEND_CFG = True

# 是否要先等到第一個 Magic Word 才開始寫檔
WAIT_FIRST_MAGIC = True
MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'

# 幾次寫入後 flush 到硬碟一次
FLUSH_EVERY_CHUNKS = 20

# log 檔資料夾
LOG_DIR = Path('logs')


# ==========================================================
# 2. 開啟 Log 檔案 (改為二進位模式)
# ==========================================================
def open_log_file() -> tuple[Path, object]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    # 副檔名改為 .bin
    log_path = LOG_DIR / f'tlv_stream_raw_{ts}.bin'
    # 使用 'wb' (Write Binary) 模式，不加 encoding
    f = open(log_path, 'wb')
    return log_path, f


# ==========================================================
# 3. 主記錄流程：直接寫入原始 Bytes
# ==========================================================
def log_raw_stream(data_serial: serial.Serial, log_file) -> None:
    started = not WAIT_FIRST_MAGIC
    prebuffer = b''
    chunk_count = 0

    print('\n開始記錄原始資料流...（按 Ctrl+C 停止）')
    if WAIT_FIRST_MAGIC:
        print('目前設定：會先等到第一個 Magic Word，再開始連續寫檔。')
    else:
        print('目前設定：不等 Magic Word，Data Port 收到什麼就直接寫檔。')

    while True:
        incoming = data_serial.read(data_serial.in_waiting or 1)
        if not incoming:
            continue

        # 還沒抓到第一個 Magic Word 時的處理
        if not started:
            prebuffer += incoming
            idx = prebuffer.find(MAGIC_WORD)
            if idx == -1:
                if len(prebuffer) > len(MAGIC_WORD) - 1:
                    prebuffer = prebuffer[-(len(MAGIC_WORD) - 1):]
                continue

            # 抓到 Magic Word 了，截斷前面的雜訊
            prebuffer = prebuffer[idx:]
            if prebuffer:
                log_file.write(prebuffer)  # 直接寫入 bytes
                chunk_count += 1
            
            log_file.flush()
            prebuffer = b''
            started = True
            print('已抓到第一個 Magic Word，開始連續寫入 log。')
            continue

        # 已經開始記錄後，收到什麼就直接寫入二進位資料
        log_file.write(incoming)
        chunk_count += 1

        if chunk_count >= FLUSH_EVERY_CHUNKS:
            log_file.flush()
            chunk_count = 0


# ==========================================================
# 4. 輔助功能：傳送 CFG
# ==========================================================
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


# ==========================================================
# 5. 主程式
# ==========================================================
def main() -> None:
    print('正在連接雷達...')
    cli_serial = None
    data_serial = None
    log_file = None
    log_path = None

    try:
        if SEND_CFG:
            cli_serial = serial.Serial(CLI_PORT, CLI_BAUD, timeout=1)
        data_serial = serial.Serial(DATA_PORT, DATA_BAUD, timeout=1)
        print('連線成功。')

        log_path, log_file = open_log_file()
        print(f'log 檔路徑: {log_path.resolve()}')

        if SEND_CFG:
            send_cfg(cli_serial, CFG_FILE)
            print('\n--- 雷達已啟動，開始記錄原始資料流 ---')
        else:
            print('\n--- 不送 cfg，直接開始記錄 Data Port 原始資料流 ---')

        log_raw_stream(data_serial, log_file)

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
                log_file.flush()
                log_file.close()
            except Exception:
                pass

        if log_path is not None:
            print(f'log 已儲存: {Path(log_path).resolve()}')
        print('通訊埠已關閉。')


if __name__ == '__main__':
    main()