# Area Scanner (Python GUI)

Python 版 TI Area Scanner GUI，包含：

- Serial 連線（CLI / DATA）
- CFG 載入與發送
- TLV packet 解析
- 視覺化顯示（X-Y / Y-Z / X-Z / 3D fallback）
- Frame 統計與診斷 log

---

## 1. 環境需求

- Python 3.10.x（建議）
- Windows 10/11
- TI mmWave 硬體（如 AWR68xx + 對應 EVM）

---

## 2. 安裝

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
