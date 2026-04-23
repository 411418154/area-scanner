"""
visualizer_3d.py
================

這是升級版的 2D/3D 混合視覺化模組。
包含雷達半圓形掃描視圖 (X-Y View) 與 3D 視圖 (3D View)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPainterPath, QColor, QBrush, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget, QStackedWidget, QGraphicsPathItem

try:
    import numpy as np
    import pyqtgraph as pg
    import pyqtgraph.opengl as gl
    HAS_PYQTGRAPH = True
except Exception:
    np = None
    pg = None
    gl = None
    HAS_PYQTGRAPH = False


# ==========================================================
# 1. 顯示樣式集中管理
# ==========================================================
@dataclass
class ViewerStyle:
    background: str = "#000000"
    
    # 點 / 目標的顏色 (保持 RGBA 格式 0.0~1.0)
    dynamic_color: tuple = (0.33, 0.78, 0.92, 1.0) # 淺藍色
    static_color: tuple = (1.0, 0.0, 1.0, 1.0)     # 紫紅色
    target_color: tuple = (1.0, 1.0, 1.0, 1.0)     # 目標改為白色圈圈比較明顯


# ==========================================================
# 2. 主要視覺化 Widget (支援 2D / 3D 切換)
# ==========================================================
class AreaScanner3DWidget(QWidget):

    def __init__(self, parent: Optional[QWidget] = None, style: Optional[ViewerStyle] = None) -> None:
        super().__init__(parent)
        self.style = style if style is not None else ViewerStyle()

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
            self.stacked_widget = None
            return

        assert pg is not None
        pg.setConfigOptions(antialias=True)

        self.stacked_widget = QStackedWidget()
        layout.addWidget(self.stacked_widget)

        # ==================================================
        # A. 建立 2D 繪圖區 (X-Y View - 雷達半圓視圖)
        # ==================================================
        self.plot_2d = pg.PlotWidget(background=self.style.background)
        self.plot_2d.showGrid(x=True, y=True, alpha=0.3)
        self.plot_2d.setLabel('bottom', 'X [m]')
        self.plot_2d.setLabel('left', 'Y [m]')
        
        # 鎖定長寬比例，讓半圓看起來是正圓，而不是橢圓
        self.plot_2d.setAspectLocked(True) 
        self.plot_2d.setYRange(0, 14)
        self.plot_2d.setXRange(-8, 8)

        # --- 加入雷達掃描介面元素 (Zones & Guide Lines) ---
        # 1. Critical Zone (紅色半圓填滿)
        self.zone_critical = QGraphicsPathItem()
        self.zone_critical.setBrush(QBrush(QColor(139, 0, 0, 120))) # 半透明暗紅色
        self.zone_critical.setPen(QPen(Qt.NoPen))
        self.plot_2d.addItem(self.zone_critical)

        # 2. Warning Zone (虛線半圓弧)
        self.zone_warning = QGraphicsPathItem()
        self.zone_warning.setPen(QPen(QColor(255, 255, 255, 200), 1, Qt.DashLine))
        self.plot_2d.addItem(self.zone_warning)

        # 3. FOV Guide Lines (視角邊界線)
        self.fov_line_left = pg.PlotCurveItem(pen=pg.mkPen(color=(200, 200, 200), width=1, style=Qt.DashLine))
        self.fov_line_right = pg.PlotCurveItem(pen=pg.mkPen(color=(200, 200, 200), width=1, style=Qt.DashLine))
        self.plot_2d.addItem(self.fov_line_left)
        self.plot_2d.addItem(self.fov_line_right)

        # 4. Boresight Guide Line (正前方中心線 Y軸)
        self.boresight_line = pg.PlotCurveItem([0, 0], [0, 20], pen=pg.mkPen('y', width=1))
        self.plot_2d.addItem(self.boresight_line)

        # --- 點雲與目標圖層 ---
        def to_pg_color(rgba: tuple) -> tuple:
            return tuple(int(c * 255) for c in rgba)

        self.scatter_2d_dynamic = pg.ScatterPlotItem(
            size=5, pen=None, brush=pg.mkBrush(*to_pg_color(self.style.dynamic_color))
        )
        self.scatter_2d_static = pg.ScatterPlotItem(
            size=7, pen=None, brush=pg.mkBrush(*to_pg_color(self.style.static_color)), symbol='s' # 靜態點用方塊表示
        )
        self.scatter_2d_targets = pg.ScatterPlotItem(
            size=14, pen=pg.mkPen('w', width=2), brush=pg.mkBrush(0, 0, 0, 0), symbol='o' # 目標用空心圓
        )

        self.plot_2d.addItem(self.scatter_2d_dynamic)
        self.plot_2d.addItem(self.scatter_2d_static)
        self.plot_2d.addItem(self.scatter_2d_targets)

        self.stacked_widget.addWidget(self.plot_2d)

        # ==================================================
        # B. 建立 3D 繪圖區 (3D View)
        # ==================================================
        self.plot_3d = gl.GLViewWidget()
        self.plot_3d.setBackgroundColor(pg.mkColor(self.style.background))
        self.plot_3d.setCameraPosition(distance=15, elevation=30, azimuth=45)

        grid = gl.GLGridItem()
        grid.setSize(x=20, y=20)
        grid.setSpacing(x=1, y=1)
        self.plot_3d.addItem(grid)

        axis = gl.GLAxisItem()
        axis.setSize(x=5, y=5, z=5)
        self.plot_3d.addItem(axis)

        self.scatter_3d_dynamic = gl.GLScatterPlotItem(
            pos=np.empty((0, 3)), color=self.style.dynamic_color, size=5
        )
        self.scatter_3d_static = gl.GLScatterPlotItem(
            pos=np.empty((0, 3)), color=self.style.static_color, size=8
        )
        self.scatter_3d_targets = gl.GLScatterPlotItem(
            pos=np.empty((0, 3)), color=self.style.target_color, size=15
        )

        self.plot_3d.addItem(self.scatter_3d_dynamic)
        self.plot_3d.addItem(self.scatter_3d_static)
        self.plot_3d.addItem(self.scatter_3d_targets)

        self.stacked_widget.addWidget(self.plot_3d)

    # ------------------------------------------------------
    # 3. 對外公開：設定類方法
    # ------------------------------------------------------
    def set_view_mode(self, view_mode: str) -> None:
        if self.stacked_widget is None:
            return
            
        self._view_mode = view_mode.strip()
        if self._view_mode == "X-Y View":
            self.stacked_widget.setCurrentWidget(self.plot_2d)
        else:
            self.stacked_widget.setCurrentWidget(self.plot_3d)

    def set_zone_config(self, enable_zones: bool = True, critical_start_m: float = 0.0,
                        critical_end_m: float = 2.0, warn_start_m: float = 2.0,
                        warn_end_m: float = 4.0, projection_time_s: float = 2.0) -> None:
        
        self._enable_zones = enable_zones
        self._critical_start_m = critical_start_m
        self._critical_end_m = critical_end_m
        self._warn_start_m = warn_start_m
        self._warn_end_m = warn_end_m

        if not HAS_PYQTGRAPH or self.stacked_widget is None:
            return

        # 控制 2D Zone 的顯示與隱藏
        self.zone_critical.setVisible(enable_zones)
        self.zone_warning.setVisible(enable_zones)

        if enable_zones:
            # 畫 Critical Zone (紅底半圓)
            path_crit = QPainterPath()
            r_crit = self._critical_end_m
            path_crit.moveTo(0, 0)
            # arcTo 參數：(x, y, width, height, startAngle, sweepLength)
            # 在 PyQt 中，繪圖原點在左上，所以包圍圓的左上角是 (-r, -r)
            path_crit.arcTo(-r_crit, -r_crit, 2 * r_crit, 2 * r_crit, 0, 180)
            path_crit.closeSubpath()
            self.zone_critical.setPath(path_crit)

            # 畫 Warning Zone (外圍虛線弧)
            path_warn = QPainterPath()
            r_warn = self._warn_end_m
            # 只畫圓弧不封閉
            path_warn.arcMoveTo(-r_warn, -r_warn, 2 * r_warn, 2 * r_warn, 0)
            path_warn.arcTo(-r_warn, -r_warn, 2 * r_warn, 2 * r_warn, 0, 180)
            self.zone_warning.setPath(path_warn)

        # 更新 FOV 虛線 (假設方位角 Azimuth 視野為 ±60 度)
        fov_deg = 60
        line_length = max(15.0, self._warn_end_m * 2)
        
        x_left = -line_length * math.sin(math.radians(fov_deg))
        y_left = line_length * math.cos(math.radians(fov_deg))
        self.fov_line_left.setData([0, x_left], [0, y_left])

        x_right = line_length * math.sin(math.radians(fov_deg))
        y_right = line_length * math.cos(math.radians(fov_deg))
        self.fov_line_right.setData([0, x_right], [0, y_right])

    def set_mount_config(self, mounting_height_m: float, elevation_tilt_deg: float) -> None:
        """
        [新增] 接收來自 GUI 的感測器安裝設定
        """
        self._mounting_height_m = mounting_height_m
        self._elevation_tilt_deg = elevation_tilt_deg
        # 註解：如果未來需要處理傾斜補償，可以在這裡計算旋轉矩陣

    # ------------------------------------------------------
    # 4. 對外公開：用 frame 更新畫面
    # ------------------------------------------------------
    def update_from_frame(self, frame, buffer_frame_count: int = 1) -> None:
        if not HAS_PYQTGRAPH or self.stacked_widget is None:
            return

        frame_info = self._normalize_frame(frame)
        dyn_pts = frame_info["dynamic_points"]
        sta_pts = frame_info["static_points"]
        targets = frame_info["targets"]
        
        is_2d = (self._view_mode == "X-Y View")
        # 取得目前的高度設定
        h_offset = self._mounting_height_m

        if is_2d:
            # --- 更新 2D 雷達視圖 ---
            self.scatter_2d_dynamic.setData(
                pos=[(p[0], p[1]) for p in dyn_pts] if dyn_pts else []
            )
            self.scatter_2d_static.setData(
                pos=[(p[0], p[1]) for p in sta_pts] if sta_pts else []
            )
            self.scatter_2d_targets.setData(
                pos=[(t["x"], t["y"]) for t in targets] if targets else []
            )
        else:
            # --- 更新 3D 視圖 (套用高度偏移) ---
            # 將感測器座標系的 Z 加上安裝高度，使 3D 網格的 Z=0 成為地面
            if dyn_pts:
                pos_dyn = np.array([[p[0], p[1], p[2] + h_offset] for p in dyn_pts])
                self.scatter_3d_dynamic.setData(pos=pos_dyn)
            else:
                self.scatter_3d_dynamic.setData(pos=np.empty((0, 3)))

            if sta_pts:
                pos_sta = np.array([[p[0], p[1], p[2] + h_offset] for p in sta_pts])
                self.scatter_3d_static.setData(pos=pos_sta)
            else:
                self.scatter_3d_static.setData(pos=np.empty((0, 3)))

            if targets:
                # 目標 (Target) 的 Z 座標也要跟著偏移
                pos_tar = np.array([[t["x"], t["y"], t["z"] + h_offset] for t in targets])
                self.scatter_3d_targets.setData(pos=pos_tar)
            else:
                self.scatter_3d_targets.setData(pos=np.empty((0, 3)))

    def clear(self) -> None:
        if not HAS_PYQTGRAPH or self.stacked_widget is None:
            return
            
        self.scatter_2d_dynamic.setData(pos=[])
        self.scatter_2d_static.setData(pos=[])
        self.scatter_2d_targets.setData(pos=[])
        
        self.scatter_3d_dynamic.setData(pos=np.empty((0, 3)))
        self.scatter_3d_static.setData(pos=np.empty((0, 3)))
        self.scatter_3d_targets.setData(pos=np.empty((0, 3)))

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