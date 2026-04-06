import serial
import time


# ==========================================
# 1. 請在這裡填寫你的實際設定
# ==========================================
CLI_PORT = 'COM6'      # 負責傳送指令的 COM Port (通常是號碼比較小的那個)
DATA_PORT = 'COM5'     # 負責接收點雲的 COM Port (通常是號碼比較大的那個)
CLI_BAUD = 115200      # xWR6843 的預設指令傳輸速率
DATA_BAUD = 921600     # xWR6843 3D People Tracking 的預設資料傳輸速率 (截圖中若有改為 1250000 請改這裡)
CFG_FILE = 'C:/ti/radar_toolbox_4_00_00_05/source/ti/examples/Industrial_and_Personal_Electronics/Area_Scanner/chirp_configs/area_scanner_68xx_ISK-0063.cfg'  # 請換成你實際的 .cfg 檔案路徑 (例如: 'C:/ti/3d_people_tracking.cfg')
#"C:\ti\radar_toolbox_4_00_00_05\source\ti\examples\Industrial_and_Personal_Electronics\Area_Scanner\chirp_configs\area_scanner_68xx_ISK-0063.cfg"

# ==========================================
# 2. 開啟通訊埠
# ==========================================
print(f"正在連接雷達...")
cli_serial = serial.Serial(CLI_PORT, CLI_BAUD, timeout=1)
data_serial = serial.Serial(DATA_PORT, DATA_BAUD, timeout=1)
print(f"連線成功！準備傳送設定檔...")

# ==========================================
# 3. 傳送設定檔給雷達
# ==========================================
with open(CFG_FILE, 'r') as file:
    for line in file:
        # 略過註解與空行
        if line.startswith('%') or line == '\n':
            continue
        
        cli_serial.write(line.encode())
        time.sleep(0.02) # 稍微等待雷達消化指令
        response = cli_serial.readline()
        print(f"傳送: {line.strip()} | 回傳: {response.decode().strip()}")

print("\n--- 雷達已啟動，開始攔截 TLV 資料！ ---\n")

# ==========================================
# 4. 攔截 MAGIC WORD 並抓取原始資料 (無限瀑布版)
# ==========================================
import struct

MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'
buffer = b''  

print("\n開始連續攔截！(按 Ctrl+C 可以隨時停止)\n")

try:
    while True:
        # 讀取 Data Port 裡的任何資料
        data = data_serial.read(data_serial.in_waiting or 1)
        
        if data:
            buffer += data  
            
            # 檢查籃子裡有沒有完整的 MAGIC_WORD
            idx = buffer.find(MAGIC_WORD)
            
            # 如果有找到密碼
            if idx != -1:
                # 擷取密碼和後面的 30 個 Byte 印出來
                print(f"[抓到新封包] 原始 Byte 資料: {buffer[idx:idx+99999].hex()} ...")

                
                # 【關鍵修改】把這個已經處理過的密碼從籃子裡「切掉」
                # 這樣程式才會繼續去尋找下一個新的 MAGIC_WORD
                buffer = buffer[idx + 8:] 

                
except KeyboardInterrupt:
    print("\n\n接收到 Ctrl+C，手動停止攔截。")
finally:
    cli_serial.write(b'sensorStop\n')
    cli_serial.close()
    data_serial.close()
    print("通訊埠已關閉。")

    
    
