"""
visualizer_3d.py
================

這是升級版的 3D 視覺化模組。
使用 pyqtgraph.opengl (GLViewWidget) 來取代原本的 2D PlotWidget。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

try:
    import numpy as np
    import pyqtgraph as pg
    import pyqtgraph.opengl as gl  # 修正了這裡的拼字
    HAS_PYQTGRAPH = True
except Exception:
    np = None
    pg = None
    gl = None
    HAS_PYQTGRAPH = False


# ==========================================================
# 1. 顯示樣式集中管理
# ==========================================================
@dataclass #(slots=True)
class ViewerStyle:
    background: str = "#000000"
    
    # 點 / 目標的顏色 (改為 RGBA 格式給 OpenGL 使用 0.0~1.0)
    dynamic_color: tuple = (0.33, 0.78, 0.92, 1.0)
    static_color: tuple = (1.0, 0.0, 1.0, 1.0)
    target_color: tuple = (0.4, 0.8, 1.0, 1.0)

# ==========================================================
# 2. 主要視覺化 Widget
# ==========================================================
class AreaScanner3DWidget(QWidget):

    def __init__(self, parent: Optional[QWidget] = None, style: Optional[ViewerStyle] = None) -> None:
        super().__init__(parent)
        self.style = style if style is not None else ViewerStyle()

        # 這裡先保留原有的屬性，以免 gui_main.py 呼叫時找不到
        self._view_mode = "X-Y View"
        self._enable_zones = True
        self._critical_start_m = 0.0
        self._critical_end_m = 2.0
        self._warn_start_m = 2.0
        self._warn_end_m = 4.0
        self._projection_time_s = 2.0
        self._mounting_height_m = 2.0
        self._elevation_tilt_deg = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if not HAS_PYQTGRAPH:
            placeholder = QLabel("無法匯入 pyqtgraph，請先確認安裝。")
            placeholder.setAlignment(Qt.AlignCenter)
            layout.addWidget(placeholder)
            self.plot = None
            return

        assert pg is not None
        pg.setConfigOptions(antialias=True)

        # ==================================================
        # 全新 3D 繪圖區
        # ==================================================
        
        # 1. 建立主圖表區 (3D)
        self.plot = gl.GLViewWidget()
        self.plot.setBackgroundColor(pg.mkColor(self.style.background))
        
        # 設定攝影機初始視角 (類似 3D People Tracker 的斜角俯視)
        self.plot.setCameraPosition(distance=15, elevation=30, azimuth=45)

        # 2. 建立背景靜態圖層：3D 網格與座標軸
        grid = gl.GLGridItem()
        grid.setSize(x=20, y=20)
        grid.setSpacing(x=1, y=1)
        self.plot.addItem(grid)

        axis = gl.GLAxisItem()
        axis.setSize(x=5, y=5, z=5) # X=紅, Y=綠, Z=藍
        self.plot.addItem(axis)

        # 3. 建立動態圖層：3D 點雲與目標
        self.scatter_dynamic = gl.GLScatterPlotItem(
            pos=np.empty((0, 3)), color=self.style.dynamic_color, size=5
        )
        self.scatter_static = gl.GLScatterPlotItem(
            pos=np.empty((0, 3)), color=self.style.static_color, size=8
        )
        self.scatter_targets = gl.GLScatterPlotItem(
            pos=np.empty((0, 3)), color=self.style.target_color, size=15
        )

        self.plot.addItem(self.scatter_dynamic)
        self.plot.addItem(self.scatter_static)
        self.plot.addItem(self.scatter_targets)

        layout.addWidget(self.plot)

    # ------------------------------------------------------
    # 3. 對外公開：設定類方法 (暫時保留介面，不操作 2D 元件)
    # ------------------------------------------------------
    def set_view_mode(self, view_mode: str) -> None:
        self._view_mode = view_mode.strip()
        # 3D 模式下我們讓使用者自由旋轉，不需要強制切換 Label

    def set_zone_config(self, *args, **kwargs) -> None:
        pass # 暫時關閉 2D Zone 邏輯

    def set_mount_config(self, *args, **kwargs) -> None:
        pass

    # ------------------------------------------------------
    # 4. 對外公開：用 frame 更新畫面
    # ------------------------------------------------------
    def update_from_frame(self, frame, buffer_frame_count: int = 1) -> None:
        if not HAS_PYQTGRAPH or self.plot is None:
            return

        frame_info = self._normalize_frame(frame)
        
        # 提取動態點 3D 座標
        dyn_pts = frame_info["dynamic_points"]
        if dyn_pts:
            pos_dyn = np.array([[p[0], p[1], p[2]] for p in dyn_pts])
            self.scatter_dynamic.setData(pos=pos_dyn)
        else:
            self.scatter_dynamic.setData(pos=np.empty((0, 3)))

        # 提取靜態點 3D 座標
        sta_pts = frame_info["static_points"]
        if sta_pts:
            pos_sta = np.array([[p[0], p[1], p[2]] for p in sta_pts])
            self.scatter_static.setData(pos=pos_sta)
        else:
            self.scatter_static.setData(pos=np.empty((0, 3)))

        # 提取 Target 3D 座標
        targets = frame_info["targets"]
        if targets:
            pos_tar = np.array([[t["x"], t["y"], t["z"]] for t in targets])
            self.scatter_targets.setData(pos=pos_tar)
        else:
            self.scatter_targets.setData(pos=np.empty((0, 3)))

    def clear(self) -> None:
        """清空目前畫面資料。"""
        if not HAS_PYQTGRAPH or self.plot is None:
            return
        self.scatter_dynamic.setData(pos=np.empty((0, 3)))
        self.scatter_static.setData(pos=np.empty((0, 3)))
        self.scatter_targets.setData(pos=np.empty((0, 3)))

    # ------------------------------------------------------
    # 5. 內部工具：frame 標準化
    # ------------------------------------------------------
    def _normalize_frame(self, frame) -> dict:
        if hasattr(frame, "header"):
            dynamic_points = [(float(p.x), float(p.y), float(p.z)) for p in getattr(frame, "dynamic_points", [])]
            static_points = [(float(p.x), float(p.y), float(p.z)) for p in getattr(frame, "static_points", [])]
            targets = [
                {
                    "tid": int(t.tid), "x": float(t.pos_x), "y": float(t.pos_y), "z": float(t.pos_z),
                    "vel_x": float(t.vel_x), "vel_y": float(t.vel_y), "vel_z": float(t.vel_z),
                }
                for t in getattr(frame, "targets", [])
            ]
            return {
                "frame_number": int(frame.header.frame_number),
                "dynamic_points": dynamic_points,
                "static_points": static_points,
                "targets": targets,
            }

        frame_dict = dict(frame)
        return {
            "frame_number": int(frame_dict.get("frame_number", 0)),
            "dynamic_points": list(frame_dict.get("dynamic_points", [])),
            "static_points": list(frame_dict.get("static_points", [])),
            "targets": list(frame_dict.get("tracked_targets", [])),
        }

# 相容別名
AreaScannerVisualizerWidget = AreaScanner3DWidget
__all__ = ['AreaScanner3DWidget', 'AreaScannerVisualizerWidget', 'ViewerStyle']