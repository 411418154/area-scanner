import math
import serial
import struct
import time
from typing import Dict, Any, Optional

# ==========================================================
# 1. 使用前請先確認這幾個設定
# ==========================================================
# CLI_PORT：拿來送 cfg 指令給雷達的序列埠
CLI_PORT = 'COM6'

# DATA_PORT：拿來接收雷達輸出的二進位封包資料的序列埠
DATA_PORT = 'COM5'

# CLI_BAUD：Area Scanner 常見的 CLI 鮑率
CLI_BAUD = 115200

# DATA_BAUD：Area Scanner 常見的 Data 鮑率
DATA_BAUD = 921600

# CFG_FILE：你的 cfg 檔路徑，要換成你自己電腦上的實際路徑
CFG_FILE = r"C:\Users\User\Documents\area-scanner\area_scanner_68xx_ISK-0063.cfg"

# Magic Word：TI mmWave 封包開頭同步字
MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'

# 為了防止 total packet length 解錯時暴衝，這裡設一個保護上限
MAX_PACKET_SIZE = 65535

# ==========================================================
# 2. Area Scanner 常用 TLV 名稱對照
# ==========================================================
# 說明：
# - 這裡主要是依照你現在要看的 Area Scanner 流程來整理。
# - 若遇到沒有明確定義的 TLV，就保留原始數字與 hex，不會直接丟掉。
TLV_TYPE_NAMES = {
    1: 'Detected Object (Dynamic Points / 動態點)',
    7: 'Detected Points Side Info (動態點附加資訊)',
    8: 'Static Detected Object (Static Points / 靜態點)',
    9: 'Static Detected Points Side Info (靜態點附加資訊)',
    10: 'Tracked Object List (追蹤目標清單)',
    11: 'Point to Track Association (點對追蹤目標關聯)',
}


# ==========================================================
# 3. 基本工具函式
# ==========================================================
def hex_bytes(data: bytes) -> str:
    """把 bytes 轉成人容易看的十六進位字串。"""
    return ' '.join(f'{b:02X}' for b in data)



def decode_version(version_value: int) -> str:
    """
    把 32-bit 版本號拆成人常看的版本格式。
    TI 常見寫法：Major.Minor.Bugfix.Build
    例如 0x03050004 -> 3.5.0.4
    """
    major = (version_value >> 24) & 0xFF
    minor = (version_value >> 16) & 0xFF
    bugfix = (version_value >> 8) & 0xFF
    build = version_value & 0xFF
    return f'{major}.{minor}.{bugfix}.{build}'



def rad_to_deg(rad_value: float) -> float:
    """弧度轉角度，方便普通人理解。"""
    return rad_value * 180.0 / math.pi



def print_field(packet: bytes, start: int, length: int, name: str, value_repr: str, plain_desc: str) -> None:
    """
    把一段欄位印得清楚一點：
    - byte 範圍
    - 原始 bytes
    - 轉換後的值
    - 白話說明
    """
    end = start + length
    raw = packet[start:end]
    print(f'[{start:03d}:{end - 1:03d}] {name}')
    print(f'    原始 bytes : {hex_bytes(raw)}')
    print(f'    轉換數值   : {value_repr}')
    print(f'    白話說明   : {plain_desc}')



def explain_tlv_type(tlv_type: int) -> str:
    """把 TLV type 數字翻成名稱。"""
    return TLV_TYPE_NAMES.get(tlv_type, '未知 TLV 類型，先保留原始值與 Hex 預覽')


# ==========================================================
# 4. Frame Header 解析
# ==========================================================
def parse_frame_header(packet: bytes) -> Optional[Dict[str, Any]]:
    """
    解析 Area Scanner / mmWave UART 封包的 Frame Header。

    這裡採用 44 bytes header：
    0~7   Magic Word
    8~11  Version
    12~15 Total Packet Length
    16~19 Platform
    20~23 Frame Number
    24~27 Time CPU Cycles
    28~31 Num Detected Obj
    32~35 Num TLVs
    36~39 Subframe Number
    40~43 Num Static Detected Obj
    """
    if len(packet) < 44:
        return None

    return {
        'header_len': 44,
        'magic_word': packet[0:8],
        'version': struct.unpack_from('<I', packet, 8)[0],
        'total_packet_len': struct.unpack_from('<I', packet, 12)[0],
        'platform': struct.unpack_from('<I', packet, 16)[0],
        'frame_number': struct.unpack_from('<I', packet, 20)[0],
        'time_cpu_cycles': struct.unpack_from('<I', packet, 24)[0],
        'num_detected_obj': struct.unpack_from('<I', packet, 28)[0],
        'num_tlvs': struct.unpack_from('<I', packet, 32)[0],
        'subframe_number': struct.unpack_from('<I', packet, 36)[0],
        'num_static_detected_obj': struct.unpack_from('<I', packet, 40)[0],
    }


# ==========================================================
# 5. 各種 TLV Payload 的白話解析
# ==========================================================
def print_dynamic_points(payload: bytes) -> None:
    """
    Area Scanner 的動態點 TLV（Type 1）通常是：
    range, angle, elev, doppler
    每筆 16 bytes。

    為了讓人好懂，這裡除了印原始值，也順手換算 x/y/z。
    """
    item_size = 16
    count = len(payload) // item_size

    print('    內容名稱   : 動態點雲')
    print(f'    白話說明   : 這些是目前被偵測到、而且帶有動態特徵的雷達點。共 {count} 筆。')

    for i in range(count):
        base = i * item_size
        rng, angle, elev, doppler = struct.unpack_from('<4f', payload, base)

        # 把極座標換成比較直覺的 xyz
        z = rng * math.sin(elev)
        r_xy = rng * math.cos(elev)
        y = r_xy * math.cos(angle)
        x = r_xy * math.sin(angle)

        print(f'      - 動態點 {i + 1}')
        print(f'        原始 bytes : {hex_bytes(payload[base:base + item_size])}')
        print(f'        range      : {rng:.3f} m  -> 與雷達距離約 {rng:.3f} 公尺')
        print(f'        angle      : {angle:.3f} rad ({rad_to_deg(angle):.2f}°) -> 左右方向角')
        print(f'        elev       : {elev:.3f} rad ({rad_to_deg(elev):.2f}°) -> 上下方向角')
        print(f'        doppler    : {doppler:.3f} m/s -> 朝向/遠離雷達的速度')
        print(f'        換算 X/Y/Z : X={x:.3f} m, Y={y:.3f} m, Z={z:.3f} m')



def print_side_info(payload: bytes, title: str) -> None:
    """
    Side Info：每筆 4 bytes
    - snr   : uint16 2 bytes
    - noise : uint16 2 bytes
    """
    item_size = 4
    count = len(payload) // item_size

    print(f'    內容名稱   : {title}')
    print(f'    白話說明   : 這是每個點的附加品質資訊。共 {count} 筆。')

    for i in range(count):
        base = i * item_size
        snr, noise = struct.unpack_from('<HH', payload, base)
        print(f'      - Side Info {i + 1}')
        print(f'        原始 bytes : {hex_bytes(payload[base:base + item_size])}')
        print(f'        snr        : {snr} dB -> 訊號強度，通常越大代表此點越明顯')
        print(f'        noise      : {noise} dB -> 背景雜訊強度')



def print_static_points(payload: bytes) -> None:
    """
    靜態點 TLV（Type 8）：
    x, y, z, doppler
    每筆 16 bytes。
    """
    item_size = 16
    count = len(payload) // item_size

    print('    內容名稱   : 靜態點雲')
    print(f'    白話說明   : 這些點通常來自較不動的物體，例如牆面、箱子、設備。共 {count} 筆。')

    for i in range(count):
        base = i * item_size
        x, y, z, doppler = struct.unpack_from('<4f', payload, base)
        print(f'      - 靜態點 {i + 1}')
        print(f'        原始 bytes : {hex_bytes(payload[base:base + item_size])}')
        print(f'        x          : {x:.3f} m -> X 座標')
        print(f'        y          : {y:.3f} m -> Y 座標')
        print(f'        z          : {z:.3f} m -> Z 座標 / 高度')
        print(f'        doppler    : {doppler:.3f} m/s -> 理想上通常接近 0，代表幾乎不動')



def print_tracked_objects(payload: bytes) -> None:
    """
    追蹤目標清單 TLV（Type 10）：
    每筆 40 bytes = 1 個 uint32 + 9 個 float
    """
    item_size = 40
    count = len(payload) // item_size

    print('    內容名稱   : 追蹤目標清單')
    print(f'    白話說明   : 這不是單純雷達點，而是系統已經追蹤成「一個目標」的結果。共 {count} 個目標。')

    for i in range(count):
        base = i * item_size
        tid, pos_x, pos_y, vel_x, vel_y, acc_x, acc_y, pos_z, vel_z, acc_z = struct.unpack_from('<I9f', payload, base)

        print(f'      - 追蹤目標 {i + 1}')
        print(f'        原始 bytes : {hex_bytes(payload[base:base + item_size])}')
        print(f'        Target ID  : {tid} -> 追蹤器給這個目標的編號')
        print(f'        pos X      : {pos_x:.3f} m -> 目標 X 位置')
        print(f'        pos Y      : {pos_y:.3f} m -> 目標 Y 位置')
        print(f'        vel X      : {vel_x:.3f} m/s -> 目標 X 方向速度')
        print(f'        vel Y      : {vel_y:.3f} m/s -> 目標 Y 方向速度')
        print(f'        acc X      : {acc_x:.3f} m/s² -> 目標 X 方向加速度')
        print(f'        acc Y      : {acc_y:.3f} m/s² -> 目標 Y 方向加速度')
        print(f'        pos Z      : {pos_z:.3f} m -> 目標高度')
        print(f'        vel Z      : {vel_z:.3f} m/s -> 目標垂直速度')
        print(f'        acc Z      : {acc_z:.3f} m/s² -> 目標垂直加速度')



def print_point_track_assoc(payload: bytes) -> None:
    """
    點對追蹤目標關聯 TLV（Type 11）：
    每個 byte 對應一個點。

    常見概念：
    - 這個 byte 的數字 = 這個點屬於哪個目標 ID
    - 255 往往可視為未分配
    """
    print('    內容名稱   : 點對追蹤目標關聯表')
    print('    白話說明   : 每一個偵測點被分到哪個追蹤目標。')

    for i, target_id in enumerate(payload):
        if target_id == 255:
            meaning = '這個點目前沒有分配給任何追蹤目標'
        else:
            meaning = f'這個點屬於目標 ID {target_id}'

        print(f'      - 點 {i + 1}: 原始 byte = {target_id:02X} -> 數值 {target_id} -> {meaning}')



def print_unknown_tlv(payload: bytes) -> None:
    """未知 TLV 就先保留 Hex 預覽，方便後續再補。"""
    preview = payload[:64]
    print('    內容名稱   : 此版本尚未定義的 TLV')
    print('    白話說明   : 先保留原始資料，之後可再對照 TI 文件或 GUI 原始碼補上。')
    print(f'    Payload Hex : {hex_bytes(preview)}')
    if len(payload) > 64:
        print(f'    （後面尚有 {len(payload) - 64} bytes 省略）')


# ==========================================================
# 6. TLV 總解析
# ==========================================================
def parse_tlvs(packet: bytes, header: Dict[str, Any]) -> None:
    """從 header 後面開始，逐個拆 TLV。"""
    offset = header['header_len']
    num_tlvs = header['num_tlvs']

    print('\n[3] TLV 分段解析')
    print('-' * 100)

    for i in range(num_tlvs):
        if offset + 8 > len(packet):
            print(f'[TLV {i + 1}] 資料不足，連 TLV Header 都不夠，停止解析。')
            break

        tlv_type = struct.unpack_from('<I', packet, offset)[0]
        tlv_length = struct.unpack_from('<I', packet, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + tlv_length

        print(f'\n[TLV {i + 1}]')
        print_field(packet, offset, 4, 'TLV Type', str(tlv_type), explain_tlv_type(tlv_type))
        print_field(packet, offset + 4, 4, 'TLV Length', f'{tlv_length} bytes', '這個 TLV payload 的長度，不包含前面 8 bytes 的 TLV header')

        if payload_end > len(packet):
            print('    ⚠ 這個 TLV 的 payload 超出目前封包長度，停止解析。')
            break

        payload = packet[payload_start:payload_end]
        print(f'    Payload 範圍 : [{payload_start:03d}:{payload_end - 1:03d}]')
        print(f'    Payload 大小 : {len(payload)} bytes')

        # 依照 TLV type 決定怎麼白話解析
        if tlv_type == 1 and len(payload) % 16 == 0:
            print_dynamic_points(payload)
        elif tlv_type == 7 and len(payload) % 4 == 0:
            print_side_info(payload, '動態點 Side Info')
        elif tlv_type == 8 and len(payload) % 16 == 0:
            print_static_points(payload)
        elif tlv_type == 9 and len(payload) % 4 == 0:
            print_side_info(payload, '靜態點 Side Info')
        elif tlv_type == 10 and len(payload) % 40 == 0:
            print_tracked_objects(payload)
        elif tlv_type == 11:
            print_point_track_assoc(payload)
        else:
            print_unknown_tlv(payload)

        offset = payload_end


# ==========================================================
# 7. 整包封包報告輸出
# ==========================================================
def print_packet_report(packet: bytes) -> None:
    """
    把一整個封包印成三層：
    1. 原始完整數字輸出（完整 Hex）
    2. Header 分段
    3. TLV 分段 + 白話說明
    """
    print('\n' + '=' * 120)
    print('[1] 原始封包 Hex（完整保留原始數字輸出）')
    print(hex_bytes(packet))

    header = parse_frame_header(packet)
    if header is None:
        print('\n[錯誤] 目前拿到的封包長度不足 44 bytes，無法依 Area Scanner Header 格式完整解析。')
        print('=' * 120)
        return

    print('\n[2] Frame Header 分段解析')
    print('-' * 100)
    print_field(packet, 0, 8, 'Magic Word', hex_bytes(header['magic_word']), '封包開頭同步字，程式就是靠這串數字找到一包新資料的開始')
    print_field(packet, 8, 4, 'Version', f"{header['version']} (0x{header['version']:08X}, {decode_version(header['version'])})", '韌體或 demo 的版本資訊')
    print_field(packet, 12, 4, 'Total Packet Length', f"{header['total_packet_len']} bytes", '整包封包總長度，包含 Header 與後面的所有 TLV')
    print_field(packet, 16, 4, 'Platform', f"{header['platform']} (0x{header['platform']:08X})", '晶片平台代碼，例如 IWR6843 系列')
    print_field(packet, 20, 4, 'Frame Number', str(header['frame_number']), '第幾張影格，可以拿來看資料有沒有跳號')
    print_field(packet, 24, 4, 'Time [CPU Cycles]', str(header['time_cpu_cycles']), '裝置內部時間戳，單位是 CPU cycles')
    print_field(packet, 28, 4, 'Num Detected Obj', str(header['num_detected_obj']), '這一幀有多少個偵測到的動態點')
    print_field(packet, 32, 4, 'Num TLVs', str(header['num_tlvs']), '後面有幾個 TLV 區塊要繼續拆')
    print_field(packet, 36, 4, 'Subframe Number', str(header['subframe_number']), '子影格編號；若沒開 advanced subframe，通常可視為 0')
    print_field(packet, 40, 4, 'Num Static Detected Obj', str(header['num_static_detected_obj']), '這一幀有多少個靜態點')

    parse_tlvs(packet, header)
    print('=' * 120)


# ==========================================================
# 8. 串流讀取：先找完整封包，再做漂亮輸出
# ==========================================================
def read_and_parse_stream(data_serial: serial.Serial) -> None:
    """
    持續讀 Data Port：
    - 找 Magic Word
    - 讀 total packet length
    - 收滿一包
    - 印完整 Hex
    - 再分段解析
    """
    buffer = b''
    print('\n開始連續攔截與分段解析！(按 Ctrl+C 可停止)\n')

    while True:
        incoming = data_serial.read(data_serial.in_waiting or 1)
        if not incoming:
            continue

        buffer += incoming

        # 一次可能收到多包，所以內層 while 會一直拆到拆不動為止
        while True:
            idx = buffer.find(MAGIC_WORD)

            # 如果整個 buffer 都還找不到 magic word
            if idx == -1:
                # 只留最後 7 bytes，避免 magic word 被切半時漏掉
                buffer = buffer[-7:] if len(buffer) > 7 else buffer
                break

            # 如果前面有雜訊，就把雜訊丟掉，只從 magic word 開始看
            if idx > 0:
                buffer = buffer[idx:]

            # 至少要有前 16 bytes 才能讀 total packet length
            if len(buffer) < 16:
                break

            total_packet_len = struct.unpack_from('<I', buffer, 12)[0]

            # 基本防呆：若 total length 超怪，就往後滑 1 byte 再找一次
            if total_packet_len <= 0 or total_packet_len > MAX_PACKET_SIZE:
                print(f'\n[警告] 讀到不合理的 Total Packet Length = {total_packet_len}，往後滑 1 byte 後重找。')
                buffer = buffer[1:]
                continue

            # 若目前還沒收滿一整包，就先等待下一輪資料
            if len(buffer) < total_packet_len:
                break

            # 收滿一整包後，把這包切出來處理
            packet = buffer[:total_packet_len]
            buffer = buffer[total_packet_len:]
            print_packet_report(packet)


# ==========================================================
# 9. 主程式：開 COM、送 cfg、開始監聽
# ==========================================================
def main() -> None:
    print('正在連接雷達...')
    cli_serial = serial.Serial(CLI_PORT, CLI_BAUD, timeout=1)
    data_serial = serial.Serial(DATA_PORT, DATA_BAUD, timeout=1)
    print('連線成功！準備傳送設定檔...')

    try:
        # 逐行送 cfg 給雷達
        with open(CFG_FILE, 'r', encoding='utf-8') as file:
            for line in file:
                # 跳過註解與空行
                if line.startswith('%') or line.strip() == '':
                    continue

                cli_serial.write(line.encode())
                time.sleep(0.02)
                response = cli_serial.readline()
                print(f'傳送: {line.strip()} | 回傳: {response.decode(errors="ignore").strip()}')

        print('\n--- 雷達已啟動，開始攔截 TLV 資料！ ---')
        read_and_parse_stream(data_serial)

    except KeyboardInterrupt:
        print('\n\n接收到 Ctrl+C，手動停止攔截。')

    finally:
        try:
            cli_serial.write(b'sensorStop\n')
        except Exception:
            pass

        cli_serial.close()
        data_serial.close()
        print('通訊埠已關閉。')


if __name__ == '__main__':
    main()
