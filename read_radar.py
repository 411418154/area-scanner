import serial
import time


# ==========================================
# 1. 請在這裡填寫你的實際設定
# ==========================================
CLI_PORT = 'COM4'      # 負責傳送指令的 COM Port (通常是號碼比較小的那個)
DATA_PORT = 'COM5'     # 負責接收點雲的 COM Port (通常是號碼比較大的那個)
CLI_BAUD = 115200      # xWR6843 的預設指令傳輸速率
DATA_BAUD = 921600     # xWR6843 3D People Tracking 的預設資料傳輸速率 (截圖中若有改為 1250000 請改這裡)
CFG_FILE = 'C:/Users/user/Desktop/area scanner/area_scanner_68xx_ISK-0063.cfg'  # 請換成你實際的 .cfg 檔案路徑 (例如: 'C:/ti/3d_people_tracking.cfg')
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

# # ==========================================
# # 4. 攔截 MAGIC WORD 並抓取原始資料
# # ==========================================
# # MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'

# # try:
    # # while True:
        # # # 從 Data Port 讀取資料
        # # data = data_serial.read(data_serial.in_waiting or 1)
        # # if data:
            # # # 尋找 Magic Word
            # # idx = data.find(MAGIC_WORD)
            # # if idx != -1:
                # # print("\n[成功攔截] 發現 MAGIC_WORD！這是一個完整的 TLV 資料包開頭。")
                # # print(f"原始 Byte 資料 (前 50 bytes): {data[idx:idx+50]}")
                # # # 這裡就是你之後可以接上 struct.unpack 的地方！

# # except KeyboardInterrupt:
    # # print("\n手動停止攔截。")
    # # cli_serial.write(b'sensorStop\n')
    # # cli_serial.close()
    # # data_serial.close()
    # # print("通訊埠已關閉。")
    
    
    # # ==========================================
# # 4. 攔截 MAGIC WORD 並抓取原始資料 (進化版)
# # ==========================================
# # MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'
# # buffer = b''  # 建立一個籃子來裝碎片的資料

# # try:
    # # while True:
        # # # 讀取 Data Port 裡的任何資料
        # # data = data_serial.read(data_serial.in_waiting or 1)
        
        # # if data:
            # # buffer += data  # 把新資料丟進籃子裡
            # # print(".", end="", flush=True)  # 只要有收到東西，就印一個點，證明線路有通！
            
            # # # 檢查籃子裡有沒有完整的 MAGIC_WORD
            # # idx = buffer.find(MAGIC_WORD)
            # # if idx != -1:
                # # print("\n\n[成功攔截] 發現 MAGIC_WORD！抓到 TLV 封包了！")
                
                # # # 印出密碼後面的 50 個 Byte 給教授看
                # # print(f"原始 Byte 資料: {buffer[idx:idx+50]}")
                
                # # # 為了方便你截圖，我們抓到一次就自動暫停程式
                # # print("\n已成功攔截並顯示，程式自動結束。")
                # # break

# # except KeyboardInterrupt:
    # # print("\n手動停止攔截。")
# # finally:
    # # cli_serial.write(b'sensorStop\n')
    # # cli_serial.close()
    # # data_serial.close()
    # # print("通訊埠已關閉。")
    
# ==========================================
# 4. 攔截 MAGIC WORD 並抓取原始資料 (無限瀑布版)
# ==========================================
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
    # # ==========================================
# # 4. 攔截 MAGIC WORD 並解析出貨單 (超級駭客版)
# # ==========================================
# # import struct # 記得要在最上面加入這個用來解碼的工具

# # MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'
# # buffer = b''  

# # print("\n開始連續攔截與精準解析！(按 Ctrl+C 可以隨時停止)\n")

# # try:
    # # while True:
        # # data = data_serial.read(data_serial.in_waiting or 1)
        
        # # if data:
            # # buffer += data  
            # # idx = buffer.find(MAGIC_WORD)
            
            # # if idx != -1:
                # # # 確保籃子裡至少收到了完整的 Header (密碼 8 bytes + 出貨單 32 bytes = 40 bytes)
                # # if len(buffer) >= idx + 40:
                    
                    # # # 💡 【解碼出貨單】用 struct 從對應的位置把 4-byte 整數 (I) 抓出來
                    # # total_len = struct.unpack('<I', buffer[idx+12:idx+16])[0]
                    # # frame_num = struct.unpack('<I', buffer[idx+20:idx+24])[0]
                    # # num_tlvs  = struct.unpack('<I', buffer[idx+32:idx+36])[0]
                    
                    # # # 檢查籃子裡的資料，是不是已經把這「一整箱」都收齊了？
                    # # if len(buffer) >= idx + total_len:
                        # # print(f"\n[📦 完整封包] 影格編號: {frame_num} | 總長度: {total_len} bytes | 內含 TLV 數量: {num_tlvs} 個")
                        
                        # # # 💡 【顯示後面的 TLV 亂碼】
                        # # # 略過前面 40 bytes 的 Header，把裡面真正的 TLV 資料抓一小段出來看
                        # # # 為了好閱讀，我們把它轉成 十六進位 (Hex) 格式
                        # # tlv_raw_data = buffer[idx+40:idx+80] # 抓前 40 個 byte 的 TLV 資料
                        # # hex_str = " ".join([f"{b:02X}" for b in tlv_raw_data])
                        # # print(f"  └─> [TLV 原始內容預覽]: {hex_str} ...")
                        
                        # # # 【完美切割】既然這箱資料處理完了，就把「整箱」從籃子裡切掉，準備接下一箱！
                        # # buffer = buffer[idx + total_len:]
                    # # else:
                        # # # 密碼找到了，但後面的資料還沒傳完，先不切掉，等下一次迴圈繼續收
                        # # pass
                # # else:
                    # # # Header 還沒收齊，等下一次迴圈
                    # # pass

# # except KeyboardInterrupt:
    # # print("\n\n接收到 Ctrl+C，手動停止攔截。")
# # finally:
    # # cli_serial.write(b'sensorStop\n')
    # # cli_serial.close()
    # # data_serial.close()
    # # print("通訊埠已關閉。")
    
# # ==========================================
# # 4. 攔截 MAGIC WORD 並全面解析 TLV (1000系列終極版)
# # ==========================================
# import struct

# MAGIC_WORD = b'\x02\x01\x04\x03\x06\x05\x08\x07'
# buffer = b''  

# print("\n開始攔截並全面解析！(按 Ctrl+C 可以隨時停止)\n")

# try:
    # while True:
        # # 從 Data Port 讀取資料並放入籃子
        # data = data_serial.read(data_serial.in_waiting or 1)
        
        # if data:
            # buffer += data  
            # idx = buffer.find(MAGIC_WORD)
            
            # # 如果籃子裡有找到密碼
            # if idx != -1:
                # # 確保收到了完整的 Header (密碼 8 bytes + 出貨單 32 bytes = 40 bytes)
                # if len(buffer) >= idx + 40:
                    
                    # # 讀取總長度與 TLV 數量 (從 Header 的特定位置抓取 4-byte 整數)
                    # total_len = struct.unpack('<I', buffer[idx+12:idx+16])[0]
                    # num_tlvs  = struct.unpack('<I', buffer[idx+32:idx+36])[0]
                    
                    # # 確保「整箱資料」都已經完整下載到籃子裡了
                    # if len(buffer) >= idx + total_len:
                        # print(f"\n=========================================")
                        # print(f"[📦 收到新影格] 總長度: {total_len} bytes | 內含 TLV 數量: {num_tlvs}")
                        
                        # # TLV 資料從第 40 個 byte (Header 之後) 開始
                        # tlv_offset = idx + 40 
                        
                        # # 開始逐一拆解裡面的 TLV 盒子
                        # for i in range(num_tlvs):
                            # # 讀取這個 TLV 的表頭 (Type 和 Length，各 4 bytes)
                            # tlv_type = struct.unpack('<I', buffer[tlv_offset : tlv_offset+4])[0]
                            # tlv_length = struct.unpack('<I', buffer[tlv_offset+4 : tlv_offset+8])[0]
                            
                            # print(f"  ├─ [解開 TLV {i+1}] 種類(Type): {tlv_type} | 長度: {tlv_length} bytes")
                            
                            # # 抓出這個 TLV 真正的內容物 payload
                            # tlv_payload = buffer[tlv_offset+8 : tlv_offset+8+tlv_length]
                            
                            # # 💡 【種類 1020：點雲資料】
                            # if tlv_type == 1020:
                                # print(f"  │    -> 🎯 發現點雲 (Type 1020)！")
                                # # 為了不洗版，我們只印出一小段 Hex 證明我們抓到了原始資料
                                # hex_str = " ".join([f"{b:02X}" for b in tlv_payload[:16]])
                                # print(f"  │       [原始 Hex 預覽]: {hex_str} ...")

                            # # 💡 【種類 1010：目標追蹤資料 (卡爾曼濾波器結果)】
                            # elif tlv_type == 1010:
                                # # 每個 3D 目標的資料長度固定是 112 bytes
                                # num_targets = tlv_length // 112
                                # print(f"  │    -> 👤 發現目標！卡爾曼濾波器鎖定了 {num_targets} 個人")
                                
                                # # 逐一印出每個目標的座標
                                # for t in range(num_targets):
                                    # # 每個目標 112 bytes，我們只取前 16 bytes 來解碼基本座標
                                    # target_bytes = tlv_payload[t*112 : t*112+16] 
                                    
                                    # # 格式解碼：目標ID(uint32), X座標(float), Y座標(float), Z座標(float)
                                    # tid, tx, ty, tz = struct.unpack('<I3f', target_bytes)
                                    # print(f"  │       [目標 ID: {tid}] 實體座標: X={tx:>6.2f}, Y={ty:>6.2f}, Z={tz:>6.2f}")

                            # # 移動游標到下一個 TLV 盒子的起點
                            # tlv_offset += (8 + tlv_length)
                        
                        # print(f"=========================================")
                        
                        # # 整箱拆解完畢，把這包資料從籃子裡安全切除，準備接下一箱！
                        # buffer = buffer[idx + total_len:]

# except KeyboardInterrupt:
    # print("\n\n接收到 Ctrl+C，手動停止攔截。")
# finally:
    # cli_serial.write(b'sensorStop\n')
    # cli_serial.close()
    # data_serial.close()
    # print("通訊埠已關閉。")
    
    
    
