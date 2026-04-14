"""
main.py
========
Python 版 Area Scanner 的主程式入口。
"""

from __future__ import annotations

import sys
import time
import argparse
import importlib.util
from dataclasses import dataclass
from pathlib import Path


def _add_project_root_to_sys_path() -> None:
    project_root = Path(__file__).resolve().parent
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

_add_project_root_to_sys_path()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Area Scanner Python 啟動器")
    parser.add_argument("--diagnose", action="store_true", help="啟用串口診斷模式")
    return parser.parse_args(argv[1:])


def run_gui_mode() -> int:
    if importlib.util.find_spec("PySide6") is None:
        print("[錯誤] 目前環境沒有安裝 PySide6。")
        print("請先安裝：")
        print(" python -m pip install PySide6 pyqtgraph PyOpenGL numpy pyserial")
        return 1

    from PySide6.QtWidgets import QApplication
    # 這裡會去呼叫正確的 gui_main.py
    from gui_main import AreaScannerMainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Area Scanner Python Visualizer")
    
    window = AreaScannerMainWindow()
    window.show()
    return app.exec()


def main() -> int:
    args = _parse_args(sys.argv)
    if args.diagnose:
        print("請使用一般 GUI 模式執行。")
        return 0
    return run_gui_mode()


if __name__ == "__main__":
    raise SystemExit(main())