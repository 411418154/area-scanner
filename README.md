# Area Scanner Python Visualizer (Boss branch)

這個專案是 **TI Area Scanner 的 Python GUI 版本**，目的是把雷達的資料接收、TLV 封包解析、目標顯示，整合成一個比較好操作、也比較接近 MATLAB Area Scanner GUI 的畫面。

目前這個版本的重點不是只把視窗打開，而是把整條流程接起來：

1. 選擇 CLI / DATA Port  
2. 載入 `.cfg` 設定檔  
3. 開啟 serial  
4. 把 cfg 傳送給雷達  
5. 持續讀取 DATA Port  
6. 解析 TLV 封包  
7. 把結果顯示在畫面上  

---

## 專案特色

- 使用 **PySide6** 製作 GUI
- 使用 **pyserial** 控制 CLI Port / DATA Port
- 使用 **parser_as.py** 解析 Area Scanner 的 TLV 封包
- 使用 **pyqtgraph** 做出接近 MATLAB 風格的 X-Y View
- 支援 **Warning / Critical 區域顯示**
- 支援 **Dynamic / Static / Tracked Target 顯示**
- 可顯示 target projection line（預判線）
- 內建 **診斷輸出**，方便查 target 為什麼沒有出現

---

## 需要安裝的套件

建議使用：

- Python **3.10.x**

先安裝以下套件：

```bash
python -m pip install PySide6 pyqtgraph pyserial numpy
```

### 套件用途

- `PySide6`：建立圖形介面
- `pyqtgraph`：畫 2D / 3D 雷達顯示畫面
- `pyserial`：和雷達的 CLI / DATA Port 通訊
- `numpy`：處理點雲、投影、座標計算

---

## 如何執行

### 1. 下載專案
把這個分支下載下來，或用 git clone：

```bash
git clone -b Boss https://github.com/411418154/area-scanner.git
cd area-scanner
```

### 2. 安裝套件

```bash
python -m pip install PySide6 pyqtgraph pyserial numpy
```

### 3. 執行主程式

```bash
python main.py
```

### 4. 在 GUI 中操作

進入程式後，依照下面步驟：

1. 按 **Refresh Ports**
2. 選擇 **CLI Port** 與 **DATA Port**
3. 載入 `.cfg` 檔案
4. 按 **Test Connection**
5. 按 **Start** 開始接收雷達資料

---

## 使用前注意

### 1. 不要讓 COM Port 被別的程式占用
同一時間不要同時打開：

- MATLAB GUI
- TI Visualizer
- 這個 Python GUI

否則 COM Port 很容易被占用，導致無法連線。

### 2. CLI / DATA Port 不要接反
常見設定通常是：

- CLI Port：`115200`
- DATA Port：`921600`

如果接反，常見情況是：

- CLI 指令沒有正常回應
- DATA 收不到資料
- 封包解析結果錯誤

### 3. 需要自己準備 `.cfg` 檔
這個分支目前主要是 Python 程式碼，執行時需要另外選擇對應的 TI `.cfg` 檔案。

---

## 主要檔案說明

### `main.py`
主程式入口。

功能：
- 建立 QApplication
- 啟動主視窗 `AreaScannerMainWindow`
- 檢查是否已安裝基本 GUI 套件

一句話理解：
> 這是整個程式的啟動點。

---

### `gui_main.py`
整個 GUI 的主視窗，也是專案最核心的控制流程。

功能：
- 建立三個分頁：設定 / 即時監控 / 診斷
- 選擇 COM Port
- 載入 cfg
- 測試連線
- 啟動背景執行緒讀取雷達資料
- 把解析後的 frame 更新到畫面
- 顯示 log 與 parse warnings
- 匯出診斷紀錄

一句話理解：
> 它像是整個 Python Area Scanner 的控制中心。

---

### `serial_manager.py`
專門管理序列埠通訊。

功能：
- 列出可用 COM Port
- 開啟 / 關閉 CLI Port 與 DATA Port
- 傳送單條 CLI 指令
- 傳送整份 cfg 檔
- 讀取 DATA Port 的原始 bytes
- 做基本連線測試

一句話理解：
> 它負責和雷達硬體真正溝通。

---

### `parser_as.py`
負責解析 Area Scanner 的 TLV 封包。

功能：
- 找 Magic Word
- 拆出完整 packet
- 解析 frame header
- 解析 TLV type 1~11
- 解析 dynamic points、static points、target list、target index
- 提供診斷資訊，判斷 target 為什麼沒出現

一句話理解：
> 它把原始二進位資料轉成人和 GUI 看得懂的結構化資料。

---

### `visualizer_3d.py`
負責畫面顯示。

功能：
- 主要以 **X-Y View** 為主
- 模擬接近 MATLAB Area Scanner 的顯示風格
- 顯示 Dynamic / Static / Target
- 顯示 FOV 線
- 顯示 Warning / Critical 區域
- 顯示 target projection line
- 支援 X-Y / Y-Z / X-Z / 3D View

一句話理解：
> 它負責把解析後的雷達資料畫成你看得到的圖。

---

### `AreaScanner_Target_Diagnose_fixed.py`
額外的診斷工具，不需要 GUI。

功能：
- 確認資料流中有沒有 **TLV type 10**
- 檢查有沒有 dynamic point 但沒有 target
- 顯示目前解析採用的 TLV mode
- 幫助判斷問題是出在資料、tracker，還是 GUI 顯示層

一句話理解：
> 當你畫面看不到 target 時，這支程式可以幫你快速查原因。

---

## 程式流程（簡單版）

可以把整個程式想成下面這條線：

```text
雷達 -> serial_manager.py -> parser_as.py -> gui_main.py -> visualizer_3d.py
```

更白話一點：

- `serial_manager.py` 負責收資料
- `parser_as.py` 負責整理資料
- `gui_main.py` 負責控制流程
- `visualizer_3d.py` 負責把結果畫出來

---

## 這個版本適合做什麼

這個 Boss 分支很適合：

- 課堂展示
- 跟老師介紹 Python 版 Area Scanner 架構
- 研究 TLV 封包解析流程
- 比對 MATLAB GUI 與 Python GUI 的差異
- 後續再加強 target 顯示、預判線、區域判斷

---

## 目前可再改進的地方

- 補上 `requirements.txt`
- 補上正式 `README.md`
- 補上範例 `.cfg`
- 補上畫面截圖
- 補上不同雷達板子的設定說明
- 補上常見錯誤排除教學

---

## 建議給老師看的簡單介紹

這份程式的重點不是單純把雷達資料讀出來，而是把 **雷達連線、TLV 解析、GUI 顯示、診斷工具** 全部整合在一起。

如果用一句話介紹：

> 這是一個用 Python 製作的 TI Area Scanner GUI，能夠接收雷達資料、解析 TLV 封包，並把動態點、靜態點與追蹤目標顯示在接近 MATLAB 風格的畫面上。

