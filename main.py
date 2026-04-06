"""
main.py
========

這個檔案是 Python 版 Area Scanner GUI 的主程式入口。

設計目標：
1. 結構簡單、好懂。
2. 先把 GUI 啟動流程固定下來。
3. 後續可以很自然地再接上 serial / TLV parser / 3D visualizer。

建議環境：
- Python 3.10.x
- PySide6 == 6.6.1
- pyqtgraph == 0.13.7
- pyserial == 3.5

說明：
- 這個版本先專注在「主程式入口」與「GUI 主視窗啟動」。
- 如果電腦尚未安裝 PySide6，執行時會直接提示安裝方式。
"""

from __future__ import annotations

import sys
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


try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    print("[錯誤] 目前環境沒有安裝 PySide6。")
    print("請先安裝：")
    print("    python -m pip install PySide6 pyqtgraph pyserial")
    raise SystemExit(1) from exc

from gui_main import AreaScannerMainWindow


# ----------------------------------------------------------
# GUI 入口主函式
# ----------------------------------------------------------
def main() -> int:
    """
    啟動 QApplication 並建立主視窗。

    回傳值：
    --------
    int
        交給 sys.exit() 使用的結束代碼。
    """
    app = QApplication(sys.argv)

    # 設定整體應用程式名稱，未來在視窗標題、系統列、log 中會比較整齊。
    app.setApplicationName("Area Scanner Python Visualizer")
    app.setOrganizationName("TI_Project")

    window = AreaScannerMainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
