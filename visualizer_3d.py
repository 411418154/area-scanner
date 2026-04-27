"""
visualizer_3d.py
================

這是升級版的 2D/3D 混合視覺化模組。
包含功能：
1. 2D 雷達半圓形掃描視圖 (X-Y View) 與 3D 視圖 (3D View) 切換。
2. 支援設定感測器高度，讓 3D 點雲正確對齊地面 (Z=0)，並顯示雷達實體。
3. 支援目標軌跡 (Tracking Trajectories) 記錄與繪製。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPainterPath, QColor, QBrush, QPen, QVector3D
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
    target_color: tuple = (1.0, 1.0, 1.0, 1.0)     # 白色 (目標與軌跡)


# ==========================================================
# 2. 主要視覺化 Widget (支援 2D / 3D 切換)
# ==========================================================
class AreaScanner3DWidget(QWidget):

    def __init__(self, parent: Optional[QWidget] = None, style: Optional[ViewerStyle] = None) -> None:
        super().__init__(parent)
        self.style = style if style is not None else ViewerStyle()

        # --- 狀態變數 ---
        self._view_mode = "X-Y View"
        self._enable_zones = True
        self._critical_start_m = 0.0
        self._critical_end_m = 2.0
        self._warn_start_m = 2.0
        self._warn_end_m = 4.0
        self._projection_time_s = 2.0
        self._mounting_height_m = 2.0
        self._elevation_tilt_deg = 0.0

        # --- 軌跡 (Trajectory) 記錄變數 ---
        self._target_history = {} # tid -> deque
        self._traj_2d = {}        # tid -> pg.PlotCurveItem
        self._traj_3d = {}        # tid -> gl.GLLinePlotItem
        self._max_history = 50    # 軌跡最多保留最近的 50 個點

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
        self.plot_2d.setAspectLocked(True) # 鎖定長寬比，確保半圓是正圓
        self.plot_2d.setYRange(0, 14)
        self.plot_2d.setXRange(-8, 8)

        # 1. Zone & 輔助線
        self.zone_critical = QGraphicsPathItem()
        self.zone_critical.setBrush(QBrush(QColor(139, 0, 0, 120)))
        self.zone_critical.setPen(QPen(Qt.NoPen))
        self.plot_2d.addItem(self.zone_critical)

        self.zone_warning = QGraphicsPathItem()
        self.zone_warning.setPen(QPen(QColor(255, 255, 255, 200), 1, Qt.DashLine))
        self.plot_2d.addItem(self.zone_warning)

        self.fov_line_left = pg.PlotCurveItem(pen=pg.mkPen(color=(200, 200, 200), width=1, style=Qt.DashLine))
        self.fov_line_right = pg.PlotCurveItem(pen=pg.mkPen(color=(200, 200, 200), width=1, style=Qt.DashLine))
        self.plot_2d.addItem(self.fov_line_left)
        self.plot_2d.addItem(self.fov_line_right)

        self.boresight_line = pg.PlotCurveItem([0, 0], [0, 20], pen=pg.mkPen('y', width=1))
        self.plot_2d.addItem(self.boresight_line)

        # 2. 點雲與目標圖層
        def to_pg_color(rgba: tuple) -> tuple:
            return tuple(int(c * 255) for c in rgba)

        self.scatter_2d_dynamic = pg.ScatterPlotItem(size=5, pen=None, brush=pg.mkBrush(*to_pg_color(self.style.dynamic_color)))
        self.scatter_2d_static = pg.ScatterPlotItem(size=7, pen=None, brush=pg.mkBrush(*to_pg_color(self.style.static_color)), symbol='s')
        self.scatter_2d_targets = pg.ScatterPlotItem(size=14, pen=pg.mkPen('w', width=2), brush=pg.mkBrush(0, 0, 0, 0), symbol='o')

        self.plot_2d.addItem(self.scatter_2d_dynamic)
        self.plot_2d.addItem(self.scatter_2d_static)
        self.plot_2d.addItem(self.scatter_2d_targets)

        self.stacked_widget.addWidget(self.plot_2d)

        # ==================================================
        # B. 建立 3D 繪圖區 (3D View)
        # ==================================================
        self.plot_3d = gl.GLViewWidget()
        self.plot_3d.setBackgroundColor(pg.mkColor(self.style.background))

        # 網格與座標軸
        grid = gl.GLGridItem()
        grid.setSize(x=20, y=20)
        grid.setSpacing(x=1, y=1)
        self.plot_3d.addItem(grid)

        axis = gl.GLAxisItem()
        axis.setSize(x=5, y=5, z=5)
        self.plot_3d.addItem(axis)

        # --- 雷達實體與安裝支柱 ---
        # 1. 安裝支柱 (灰色實線，代表安裝桿)
        self.mounting_pole = gl.GLLinePlotItem(pos=np.array([[0,0,0], [0,0,2]]), color=(0.7, 0.7, 0.7, 1.0), width=3, antialias=True)
        self.plot_3d.addItem(self.mounting_pole)

        # 2. 雷達實體方形 (純藍色實心方塊)
        verts = np.array([
            [-0.075, -0.075, -0.05], [0.075, -0.075, -0.05], [0.075, 0.075, -0.05], [-0.075, 0.075, -0.05],
            [-0.075, -0.075,  0.05], [0.075, -0.075,  0.05], [0.075, 0.075,  0.05], [-0.075, 0.075,  0.05]
        ])
        faces = np.array([
            [0,1,2], [0,2,3], [0,1,5], [0,5,4], [1,2,6], [1,6,5],
            [2,3,7], [2,7,6], [3,0,4], [3,4,7], [4,5,6], [4,6,7]
        ])
        md = gl.MeshData(vertexes=verts, faces=faces)
        self.sensor_body = gl.GLMeshItem(meshdata=md, smooth=False, color=(0.0, 0.3, 1.0, 1.0), glOptions='opaque')
        self.plot_3d.addItem(self.sensor_body)

        if hasattr(self, 'sensor_body'):
            self.sensor_body.resetTransform()

        # 3. 調整相機視角 (往下看，並聚焦在前方)
        self.plot_3d.opts['center'] = QVector3D(0, 3, 1)
        self.plot_3d.setCameraPosition(distance=15, elevation=30, azimuth=45)

        # --- 點雲與目標圖層 ---
        self.scatter_3d_dynamic = gl.GLScatterPlotItem(pos=np.empty((0, 3)), color=self.style.dynamic_color, size=5)
        self.scatter_3d_static = gl.GLScatterPlotItem(pos=np.empty((0, 3)), color=self.style.static_color, size=8)
        self.scatter_3d_targets = gl.GLScatterPlotItem(pos=np.empty((0, 3)), color=self.style.target_color, size=15)

        self.plot_3d.addItem(self.scatter_3d_dynamic)
        self.plot_3d.addItem(self.scatter_3d_static)
        self.plot_3d.addItem(self.scatter_3d_targets)

        self.stacked_widget.addWidget(self.plot_3d)

    # ------------------------------------------------------
    # 3. 對外公開：設定類方法
    # ------------------------------------------------------
    def set_view_mode(self, view_mode: str) -> None:
        """切換 2D / 3D 畫布"""
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
        """更新 2D 雷達視角的警示區與輔助線"""
        self._enable_zones = enable_zones
        self._critical_start_m = critical_start_m
        self._critical_end_m = critical_end_m
        self._warn_start_m = warn_start_m
        self._warn_end_m = warn_end_m

        if not HAS_PYQTGRAPH or self.stacked_widget is None:
            return

        self.zone_critical.setVisible(enable_zones)
        self.zone_warning.setVisible(enable_zones)

        if enable_zones:
            # Critical Zone (紅底半圓)
            path_crit = QPainterPath()
            r_crit = self._critical_end_m
            path_crit.moveTo(0, 0)
            path_crit.arcTo(-r_crit, -r_crit, 2 * r_crit, 2 * r_crit, 0, 180)
            path_crit.closeSubpath()
            self.zone_critical.setPath(path_crit)

            # Warning Zone (外圍虛線弧)
            path_warn = QPainterPath()
            r_warn = self._warn_end_m
            path_warn.arcMoveTo(-r_warn, -r_warn, 2 * r_warn, 2 * r_warn, 0)
            path_warn.arcTo(-r_warn, -r_warn, 2 * r_warn, 2 * r_warn, 0, 180)
            self.zone_warning.setPath(path_warn)

        # FOV 虛線 (設定為 ±60 度)
        fov_deg = 60
        line_length = max(15.0, self._warn_end_m * 2)
        x_left = -line_length * math.sin(math.radians(fov_deg))
        y_left = line_length * math.cos(math.radians(fov_deg))
        self.fov_line_left.setData([0, x_left], [0, y_left])

        x_right = line_length * math.sin(math.radians(fov_deg))
        y_right = line_length * math.cos(math.radians(fov_deg))
        self.fov_line_right.setData([0, x_right], [0, y_right])

    def set_mount_config(self, mounting_height_m: float, elevation_tilt_deg: float) -> None:
        """接收安裝高度，供 3D 視圖做 Z 軸補償，並連動實體方塊與支柱"""
        self._mounting_height_m = mounting_height_m
        self._elevation_tilt_deg = elevation_tilt_deg

        if not HAS_PYQTGRAPH or self.stacked_widget is None:
            return

        h = self._mounting_height_m

        # 1. 更新支柱：起點為地面 (0,0,0)，終點為高度 H (0,0,h)
        if hasattr(self, 'mounting_pole'):
            self.mounting_pole.setData(pos=np.array([[0, 0, 0], [0, 0, h]]))

        # 2. 更新雷達方形實體位置
        if hasattr(self, 'sensor_body'):
            self.sensor_body.resetTransform()
            self.sensor_body.translate(-0.1, -0.1, -0.05) # 先置中
            self.sensor_body.translate(0, 0, h)           # 再升空
    def set_trajectory_enabled(self, enabled: bool) -> None:
        """[新增] 設定是否顯示軌跡"""
        self._enable_trajectory = enabled
        if not enabled:
            self.clear_trajectories()
    def clear_trajectories(self) -> None:
        """[新增] 只清除軌跡線而不影響點雲"""
        for item in self._traj_2d.values():
            self.plot_2d.removeItem(item)
        for item in self._traj_3d.values():
            self.plot_3d.removeItem(item)
        self._target_history.clear()
        self._traj_2d.clear()
        self._traj_3d.clear()

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
        h_offset = self._mounting_height_m  # 取出高度補償值

        # ==========================================
        # 軌跡處理 (Trajectory Tracking)
        # ==========================================
        if self._enable_trajectory:
            current_tids = set(t["tid"] for t in targets)
            # 1. 移除已消失的目標軌跡
            for tid in list(self._target_history.keys()):
                if tid not in current_tids:
                    del self._target_history[tid]
                    if tid in self._traj_2d:
                        self.plot_2d.removeItem(self._traj_2d[tid])
                        del self._traj_2d[tid]
                    if tid in self._traj_3d:
                        self.plot_3d.removeItem(self._traj_3d[tid])
                        del self._traj_3d[tid]
        else:
            if self._target_history:
                self.clear_trajectories()

        # 2. 更新並畫出當前軌跡
        def to_pg_color(rgba: tuple) -> tuple:
            return tuple(int(c * 255) for c in rgba)
        traj_color_2d = to_pg_color(self.style.target_color)

        for t in targets:
            tid = t["tid"]
            if tid not in self._target_history:
                self._target_history[tid] = deque(maxlen=self._max_history)
                # 建立 2D 軌跡虛線
                pen = pg.mkPen(color=traj_color_2d, width=2, style=Qt.DashLine)
                self._traj_2d[tid] = pg.PlotCurveItem(pen=pen)
                self.plot_2d.addItem(self._traj_2d[tid])
                # 建立 3D 軌跡實線
                self._traj_3d[tid] = gl.GLLinePlotItem(color=self.style.target_color, width=2, antialias=True)
                self.plot_3d.addItem(self._traj_3d[tid])

            # 記錄軌跡點 (3D座標需加上高度補償)
            self._target_history[tid].append((t["x"], t["y"], t["z"] + h_offset))

            # 刷新繪圖資料
            pts = list(self._target_history[tid])
            if len(pts) > 1:
                self._traj_2d[tid].setData([p[0] for p in pts], [p[1] for p in pts])
                self._traj_3d[tid].setData(pos=np.array(pts))

        # ==========================================
        # 散佈點圖 (Scatter) 更新
        # ==========================================
        if is_2d:
            # 2D 視角 (忽略 Z 軸)
            self.scatter_2d_dynamic.setData(pos=[(p[0], p[1]) for p in dyn_pts] if dyn_pts else [])
            self.scatter_2d_static.setData(pos=[(p[0], p[1]) for p in sta_pts] if sta_pts else [])
            self.scatter_2d_targets.setData(pos=[(t["x"], t["y"]) for t in targets] if targets else [])
        else:
            # 3D 視角 (加上高度補償 h_offset)
            self.scatter_3d_dynamic.setData(pos=np.array([[p[0], p[1], p[2] + h_offset] for p in dyn_pts]) if dyn_pts else np.empty((0, 3)))
            self.scatter_3d_static.setData(pos=np.array([[p[0], p[1], p[2] + h_offset] for p in sta_pts]) if sta_pts else np.empty((0, 3)))
            self.scatter_3d_targets.setData(pos=np.array([[t["x"], t["y"], t["z"] + h_offset] for t in targets]) if targets else np.empty((0, 3)))

    def clear(self) -> None:
        """清空畫面，包含所有點雲與歷史軌跡"""
        if not HAS_PYQTGRAPH or self.stacked_widget is None:
            return
            
        self.scatter_2d_dynamic.setData(pos=[])
        self.scatter_2d_static.setData(pos=[])
        self.scatter_2d_targets.setData(pos=[])
        
        self.scatter_3d_dynamic.setData(pos=np.empty((0, 3)))
        self.scatter_3d_static.setData(pos=np.empty((0, 3)))
        self.scatter_3d_targets.setData(pos=np.empty((0, 3)))

        # 清除軌跡線
        for item in self._traj_2d.values():
            self.plot_2d.removeItem(item)
        for item in self._traj_3d.values():
            self.plot_3d.removeItem(item)
            
        self._target_history.clear()
        self._traj_2d.clear()
        self._traj_3d.clear()

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