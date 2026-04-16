"""
visualizer_3d.py
================

這個模組雖然沿用原本的檔名 `visualizer_3d.py`，
但這一版的核心目標其實是：

**先把畫面做得更像 MATLAB Area Scanner GUI 的 X-Y View。**

也就是使用者圖片裡那種：
- 黑底
- X / Y 座標軸
- 中央白色垂直線
- 左右虛線 FOV 線
- Warning / Critical 半圓區域
- Dynamic / Static / Tracked 目標
- 左上角 frame 資訊

為什麼不先硬做 OpenGL 3D？
----------------------------
因為你現在最在意的是「畫面要像原本 GUI」，
而原本常用的顯示模式其實就是 **X-Y View 的 2D 投影畫面**。

所以這一版改用 pyqtgraph 的 `PlotWidget`，好處是：
- 在 Windows + PySide6 + pyqtgraph 上通常比 OpenGL 更穩
- 更容易畫出 MATLAB 那種 2D 風格
- 更容易疊加區域、虛線、文字標註

建議環境版本
------------
- Python 3.10.x
- PySide6 >= 6.6
- pyqtgraph >= 0.13
- numpy >= 1.24

目前支援的功能
--------------
1. X-Y View（最接近原圖）
2. Y-Z View（簡化投影）
3. X-Z View（簡化投影）
4. 3D View（目前先退回 X-Y 畫法，保留選項名稱）
5. Dynamic / Static / Tracked 目標顯示
6. Target 投影線
7. 左上角統計文字
8. Warning / Critical 區域

提醒
----
這一版重點是把「視覺效果」先拉近你要的 MATLAB 畫面。
如果之後你要更接近原 GUI，還可以再補：
- point type 顏色分群
- target ID 文字更貼近原版樣式
- 更多 zone 顯示
- 更完整的 3D 視圖
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
    HAS_PYQTGRAPH = True
except Exception:
    np = None
    pg = None
    HAS_PYQTGRAPH = False


# ==========================================================
# 1. 顯示樣式集中管理
# ==========================================================
@dataclass(slots=True)
class ViewerStyle:
    """
    統一管理顏色 / 尺寸 / 預設範圍。

    這樣之後如果你或教授想調整畫面風格，
    只要改這裡，不用到處找散落的數字。
    """

    background: str = "#000000"

    # 軸與網格
    x_axis_color: str = "#0000ff"      # 藍色，接近圖中的底部 X 軸
    y_axis_color: str = "#ffffff"      # 白色，中間垂直線
    grid_color: str = "#4a4a4a"
    label_color: str = "#ffff00"       # 黃色字

    # 點 / 目標
    dynamic_brush: str = "#54c7ec"     # 淺藍
    static_brush: str = "#ff00ff"      # 洋紅
    target_pen: str = "#66ccff"        # 藍色空心圈
    projection_pen: str = "#ffff00"    # 黃色投影線

    # 區域顏色
    warn_brush: tuple = (128, 128, 0, 120)     # 橄欖黃半透明
    crit_brush: tuple = (110, 0, 0, 130)       # 深紅半透明
    zone_edge_pen: str = "#ffffff"
    fov_pen: str = "#d9d9d9"

    # 顯示範圍（先用最像圖中的 XY 視角）
    x_range_xy: tuple[float, float] = (-12.5, 12.5)
    y_range_xy: tuple[float, float] = (0.0, 14.4)

    # 其他投影視角的預設範圍（簡化版）
    x_range_other: tuple[float, float] = (-6.0, 6.0)
    y_range_other: tuple[float, float] = (-0.5, 6.0)


# ==========================================================
# 2. 主要視覺化 Widget
# ==========================================================
class AreaScanner3DWidget(QWidget):
    """
    雖然名字叫 3DWidget，但這一版以「2D MATLAB 風格顯示」為主。

    對 gui_main.py 來說，這個 widget 最重要的介面有三個：
    1. set_view_mode(view_mode)
    2. set_zone_config(...)
    3. update_from_frame(frame, buffer_frame_count)

    這樣 GUI 主程式不用知道細節，只要把新 frame 丟進來即可。
    """

    def __init__(self, parent: Optional[QWidget] = None, style: Optional[ViewerStyle] = None) -> None:
        super().__init__(parent)
        self.style = style if style is not None else ViewerStyle()

        # 目前顯示模式：預設先放 X-Y，因為最接近你要的 MATLAB 畫面。
        self._view_mode = "X-Y View"

        # Zone 相關設定，預設值先沿用你 GUI 常見設定。
        self._enable_zones = True
        self._critical_start_m = 0.0
        self._critical_end_m = 2.0
        self._warn_start_m = 2.0
        self._warn_end_m = 4.0
        self._projection_time_s = 2.0
        self._fov_outer_angle_deg = 59.0
        self._fov_inner_angle_deg = 30.0
        self._fov_outer_range_m = 7.2
        self._fov_inner_range_m = 2.1

        # 這兩個先保留，未來若要做高度補償 / tilt 修正可再接。
        self._mounting_height_m = 2.0
        self._elevation_tilt_deg = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if not HAS_PYQTGRAPH:
            placeholder = QLabel(
                "無法匯入 pyqtgraph。\n"
                "請先確認安裝：\n"
                "python -m pip install pyqtgraph numpy"
            )
            placeholder.setAlignment(Qt.AlignCenter)
            layout.addWidget(placeholder)
            self.plot = None
            return

        assert pg is not None
        pg.setConfigOptions(antialias=True)

        # --------------------------------------------------
        # A. 建立主圖表區
        # --------------------------------------------------
        self.plot = pg.PlotWidget()
        self.plot.setBackground(self.style.background)
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()

        self.plot.showGrid(x=True, y=True, alpha=0.20)

        # 座標軸外觀
        bottom_axis = self.plot.getAxis("bottom")
        left_axis = self.plot.getAxis("left")
        bottom_axis.setPen(pg.mkPen(self.style.x_axis_color, width=1.2))
        bottom_axis.setTextPen(pg.mkPen(self.style.x_axis_color, width=1.0))
        left_axis.setPen(pg.mkPen(self.style.label_color, width=1.2))
        left_axis.setTextPen(pg.mkPen(self.style.label_color, width=1.0))

        self.plot.setLabel("bottom", "X [m]", color=self.style.x_axis_color)
        self.plot.setLabel("left", "Y [m]", color=self.style.label_color)

        # 固定顯示範圍，先貼近 MATLAB Area Scanner 圖
        self._apply_plot_range_for_view(self._view_mode)

        # --------------------------------------------------
        # B. 背景靜態圖層（軸、FOV、zones）
        # --------------------------------------------------
        self.item_x_axis = self.plot.plot([], [], pen=pg.mkPen(self.style.x_axis_color, width=1.4))
        self.item_y_axis = self.plot.plot([], [], pen=pg.mkPen(self.style.y_axis_color, width=1.3))

        self.item_fov_left = self.plot.plot([], [], pen=pg.mkPen(self.style.fov_pen, width=1.1, style=Qt.DashLine))
        self.item_fov_right = self.plot.plot([], [], pen=pg.mkPen(self.style.fov_pen, width=1.1, style=Qt.DashLine))
        self.item_fov_inner_left = self.plot.plot([], [], pen=pg.mkPen(self.style.fov_pen, width=1.0, style=Qt.DashLine))
        self.item_fov_inner_right = self.plot.plot([], [], pen=pg.mkPen(self.style.fov_pen, width=1.0, style=Qt.DashLine))

        # Warning / Critical 半圓區域
        self.item_warn_zone = self.plot.plot(
            [], [],
            pen=pg.mkPen(self.style.zone_edge_pen, width=0.9),
            fillLevel=0.0,
            brush=pg.mkBrush(*self.style.warn_brush),
        )
        self.item_crit_zone = self.plot.plot(
            [], [],
            pen=pg.mkPen(self.style.zone_edge_pen, width=0.9),
            fillLevel=0.0,
            brush=pg.mkBrush(*self.style.crit_brush),
        )

        # --------------------------------------------------
        # C. 動態圖層（點雲 / target / projection）
        # --------------------------------------------------
        self.scatter_dynamic = pg.ScatterPlotItem(size=7, pen=None, brush=pg.mkBrush(self.style.dynamic_brush))
        self.scatter_static = pg.ScatterPlotItem(size=14, pen=None, brush=pg.mkBrush(self.style.static_brush), symbol="s")
        self.scatter_targets = pg.ScatterPlotItem(size=18, pen=pg.mkPen(self.style.target_pen, width=2.4), brush=None, symbol="o")

        self.plot.addItem(self.scatter_dynamic)
        self.plot.addItem(self.scatter_static)
        self.plot.addItem(self.scatter_targets)

        # target projection 線與文字會每個 frame 重建，所以先保存清單方便清除。
        self._projection_items: list = []
        self._target_text_items: list = []

        # 左上角統計資訊
        self.stats_text = pg.TextItem(anchor=(0, 0), fill=pg.mkBrush(0, 0, 0, 0))
        font = QFont("Consolas")
        font.setPointSize(12)
        self.stats_text.setFont(font)
        self.plot.addItem(self.stats_text)

        layout.addWidget(self.plot)

        # 先畫一次背景圖層
        self._refresh_background_layers()
        self._update_stats_text(
            frame_number=0,
            num_frames_in_buffer=0,
            num_dynamic=0,
            num_static=0,
            num_targets=0,
        )

    # ------------------------------------------------------
    # 3. 對外公開：設定類方法
    # ------------------------------------------------------
    def set_view_mode(self, view_mode: str) -> None:
        """
        設定目前視角。

        注意：
        - X-Y View 是最接近原 MATLAB 畫面。
        - Y-Z / X-Z View 目前是簡化投影版本。
        - 3D View 目前先沿用 X-Y 呈現，不做 OpenGL 3D。
        """
        self._view_mode = view_mode.strip()

        if not HAS_PYQTGRAPH or self.plot is None:
            return

        # 軸標籤跟範圍會依視角切換。
        if self._view_mode == "X-Y View":
            self.plot.setLabel("bottom", "X [m]", color=self.style.x_axis_color)
            self.plot.setLabel("left", "Y [m]", color=self.style.label_color)
        elif self._view_mode == "Y-Z View":
            self.plot.setLabel("bottom", "Y [m]", color=self.style.x_axis_color)
            self.plot.setLabel("left", "Z [m]", color=self.style.label_color)
        elif self._view_mode == "X-Z View":
            self.plot.setLabel("bottom", "X [m]", color=self.style.x_axis_color)
            self.plot.setLabel("left", "Z [m]", color=self.style.label_color)
        else:
            # 3D View 目前先用 X-Y 平面顯示，至少畫面先穩定。
            self.plot.setLabel("bottom", "X [m]", color=self.style.x_axis_color)
            self.plot.setLabel("left", "Y [m]", color=self.style.label_color)

        self._apply_plot_range_for_view(self._view_mode)
        self._refresh_background_layers()

    def set_zone_config(
        self,
        enable_zones: bool,
        critical_start_m: float,
        critical_end_m: float,
        warn_start_m: float,
        warn_end_m: float,
        projection_time_s: float,
    ) -> None:
        """
        由 gui_main.py 把 zone 設定同步進來。

        這樣視覺化層不用自己去讀 GUI 控制項。
        """
        self._enable_zones = enable_zones
        self._critical_start_m = max(0.0, critical_start_m)
        self._critical_end_m = max(self._critical_start_m, critical_end_m)
        self._warn_start_m = max(self._critical_end_m, warn_start_m)
        self._warn_end_m = max(self._warn_start_m, warn_end_m)
        self._projection_time_s = max(0.0, projection_time_s)
        self._refresh_background_layers()

    def set_mount_config(self, mounting_height_m: float, elevation_tilt_deg: float) -> None:
        """
        更新雷達安裝高度與仰角。

        這些參數會在 _normalize_frame 內用於雷達座標 -> 世界座標補償。
        """
        self._mounting_height_m = mounting_height_m
        self._elevation_tilt_deg = elevation_tilt_deg

    def set_fov_config(
        self,
        outer_angle_deg: float,
        inner_angle_deg: float,
        outer_range_m: float,
        inner_range_m: float,
    ) -> None:
        """由 gui_main.py 同步 FOV 角度與範圍設定。"""
        self._fov_outer_angle_deg = max(0.0, outer_angle_deg)
        self._fov_inner_angle_deg = min(self._fov_outer_angle_deg, max(0.0, inner_angle_deg))
        self._fov_outer_range_m = max(0.0, outer_range_m)
        self._fov_inner_range_m = min(self._fov_outer_range_m, max(0.0, inner_range_m))
        self._refresh_background_layers()

    # ------------------------------------------------------
    # 4. 對外公開：用 frame 更新畫面
    # ------------------------------------------------------
    def update_from_frame(self, frame, buffer_frame_count: int = 1) -> None:
        """
        用新 frame 更新畫面。

        支援兩種輸入：
        1. parser_as.ParsedFrame
        2. dict（只要欄位名稱對得上）
        """
        if not HAS_PYQTGRAPH or self.plot is None:
            return

        frame_info = self._normalize_frame(frame)

        # 先把舊的 projection 線和 target 文字清掉，避免殘影。
        self._clear_dynamic_overlay_items()

        dyn_2d = self._project_points(frame_info["dynamic_points"])
        sta_2d = self._project_points(frame_info["static_points"])
        tar_2d = self._project_targets(frame_info["targets"])

        # 更新三種散點
        self._set_scatter_data(self.scatter_dynamic, dyn_2d)
        self._set_scatter_data(self.scatter_static, sta_2d)
        self._set_scatter_data(self.scatter_targets, tar_2d)

        # 更新 target 的投影線與文字
        self._draw_target_overlays(frame_info["targets"])

        # 左上角統計資訊
        self._update_stats_text(
            frame_number=frame_info["frame_number"],
            num_frames_in_buffer=buffer_frame_count,
            num_dynamic=len(frame_info["dynamic_points"]),
            num_static=len(frame_info["static_points"]),
            num_targets=len(frame_info["targets"]),
        )

    def clear(self) -> None:
        """清空目前畫面資料。"""
        if not HAS_PYQTGRAPH or self.plot is None:
            return

        self._set_scatter_data(self.scatter_dynamic, [])
        self._set_scatter_data(self.scatter_static, [])
        self._set_scatter_data(self.scatter_targets, [])
        self._clear_dynamic_overlay_items()
        self._update_stats_text(0, 0, 0, 0, 0)

    # ------------------------------------------------------
    # 5. 內部工具：畫背景與範圍
    # ------------------------------------------------------
    def _apply_plot_range_for_view(self, view_mode: str) -> None:
        """依照視角設定固定顯示範圍。"""
        if self.plot is None:
            return

        if view_mode == "X-Y View" or view_mode == "3D View":
            self.plot.setXRange(*self.style.x_range_xy, padding=0.0)
            self.plot.setYRange(*self.style.y_range_xy, padding=0.0)
        else:
            self.plot.setXRange(*self.style.x_range_other, padding=0.0)
            self.plot.setYRange(*self.style.y_range_other, padding=0.0)

    def _refresh_background_layers(self) -> None:
        """
        重畫背景層。

        會在以下情況呼叫：
        - 初始化
        - 視角改變
        - zone 參數改變
        """
        if not HAS_PYQTGRAPH or self.plot is None:
            return

        # 先重畫軸
        x_min, x_max, y_min, y_max = self._current_plot_bounds()
        self.item_x_axis.setData([x_min, x_max], [0.0, 0.0])
        self.item_y_axis.setData([0.0, 0.0], [y_min, y_max])

        if self._view_mode != "X-Y View" and self._view_mode != "3D View":
            # 非 XY 視角時，先不畫 MATLAB 那種扇形區域，避免投影看起來混亂。
            self.item_warn_zone.setData([], [])
            self.item_crit_zone.setData([], [])
            self.item_fov_left.setData([], [])
            self.item_fov_right.setData([], [])
            self.item_fov_inner_left.setData([], [])
            self.item_fov_inner_right.setData([], [])
            return

        # 畫外圈 warning 環形扇區（warn_start ~ warn_end）
        if self._enable_zones and self._warn_end_m > 0:
            x_warn, y_warn = self._ring_sector_polygon(self._warn_start_m, self._warn_end_m)
            self.item_warn_zone.setData(x_warn, y_warn)
        else:
            self.item_warn_zone.setData([], [])

        # 畫內圈 critical 環形扇區（critical_start ~ critical_end）
        if self._enable_zones and self._critical_end_m > 0:
            x_crit, y_crit = self._ring_sector_polygon(self._critical_start_m, self._critical_end_m)
            self.item_crit_zone.setData(x_crit, y_crit)
        else:
            self.item_crit_zone.setData([], [])

        # FOV 線：角度先抓成接近圖片的外觀
        # 這裡的角度是相對 Y 軸展開的角度。
        outer_angle_deg = self._fov_outer_angle_deg
        inner_angle_deg = self._fov_inner_angle_deg
        outer_y_end = self._fov_outer_range_m
        inner_y_end = self._fov_inner_range_m

        self.item_fov_left.setData(*self._ray_from_origin(-outer_angle_deg, outer_y_end))
        self.item_fov_right.setData(*self._ray_from_origin(+outer_angle_deg, outer_y_end))
        self.item_fov_inner_left.setData(*self._ray_from_origin(-inner_angle_deg, inner_y_end))
        self.item_fov_inner_right.setData(*self._ray_from_origin(+inner_angle_deg, inner_y_end))

    # ------------------------------------------------------
    # 6. 內部工具：frame 標準化與投影
    # ------------------------------------------------------
    def _normalize_frame(self, frame) -> dict:
        """
        把 ParsedFrame / dict 轉成統一格式，方便後面統一處理。

        注意：這裡會先把雷達座標轉成世界座標（含 pitch/高度補償），
        後續 _project_points / _project_targets 都直接吃補償後座標。
        """
        # 先處理 parser_as.ParsedFrame
        if hasattr(frame, "header"):
            dynamic_points = []
            for p in getattr(frame, "dynamic_points", []):
                xw, yw, zw = self._transform_radar_to_world(float(p.x), float(p.y), float(p.z))
                dynamic_points.append((xw, yw, zw))

            static_points = []
            for p in getattr(frame, "static_points", []):
                xw, yw, zw = self._transform_radar_to_world(float(p.x), float(p.y), float(p.z))
                static_points.append((xw, yw, zw))

            targets = []
            for t in getattr(frame, "targets", []):
                xw, yw, zw = self._transform_radar_to_world(float(t.pos_x), float(t.pos_y), float(t.pos_z))
                targets.append(
                    {
                        "tid": int(t.tid),
                        "x": xw,
                        "y": yw,
                        "z": zw,
                        "vel_x": float(t.vel_x),
                        "vel_y": float(t.vel_y),
                        "vel_z": float(t.vel_z),
                    }
                )

            return {
                "frame_number": int(frame.header.frame_number),
                "dynamic_points": dynamic_points,
                "static_points": static_points,
                "targets": targets,
            }

        # 再處理 dict
        frame_dict = dict(frame)

        dynamic_points = []
        for p in frame_dict.get("dynamic_points", []):
            if len(p) < 3:
                continue
            xw, yw, zw = self._transform_radar_to_world(float(p[0]), float(p[1]), float(p[2]))
            dynamic_points.append((xw, yw, zw))

        static_points = []
        for p in frame_dict.get("static_points", []):
            if len(p) < 3:
                continue
            xw, yw, zw = self._transform_radar_to_world(float(p[0]), float(p[1]), float(p[2]))
            static_points.append((xw, yw, zw))

        targets = []
        for t in frame_dict.get("tracked_targets", []):
            xw, yw, zw = self._transform_radar_to_world(
                float(t.get("x", 0.0)),
                float(t.get("y", 0.0)),
                float(t.get("z", 0.0)),
            )
            target = dict(t)
            target["x"] = xw
            target["y"] = yw
            target["z"] = zw
            targets.append(target)

        return {
            "frame_number": int(frame_dict.get("frame_number", 0)),
            "dynamic_points": dynamic_points,
            "static_points": static_points,
            "targets": targets,
        }

    def _transform_radar_to_world(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """
        將雷達座標轉到世界座標。

        座標系定義：
        - 雷達座標：x 向右、y 向前、z 向上。
        - 安裝傾角：繞 X 軸旋轉（右手定則，正角讓 y 軸朝 +z 方向抬升）。
        - 安裝高度：旋轉後再做世界座標 z 方向平移（+mounting_height）。
        """
        tilt_rad = math.radians(self._elevation_tilt_deg)
        cos_tilt = math.cos(tilt_rad)
        sin_tilt = math.sin(tilt_rad)

        # X 軸旋轉：x 不變，(y, z) 進行 2D 旋轉。
        xr = x
        yr = y * cos_tilt - z * sin_tilt
        zr = y * sin_tilt + z * cos_tilt

        # 旋轉後再補償雷達離地高度。
        return xr, yr, zr + self._mounting_height_m

    def _project_points(self, points: Iterable[Sequence[float]]) -> list[tuple[float, float]]:
        """
        把 3D 點位投影成 2D。

        規則：
        - X-Y View / 3D View： (x, y)
        - Y-Z View：           (y, z)
        - X-Z View：           (x, z)
        """
        projected: list[tuple[float, float]] = []
        for p in points:
            if len(p) < 3:
                continue
            x, y, z = float(p[0]), float(p[1]), float(p[2])

            if self._view_mode == "Y-Z View":
                projected.append((y, z))
            elif self._view_mode == "X-Z View":
                projected.append((x, z))
            else:
                projected.append((x, y))
        return projected

    def _project_targets(self, targets: Iterable[dict]) -> list[tuple[float, float]]:
        """把 target dict 投影成 2D。"""
        projected: list[tuple[float, float]] = []
        for t in targets:
            x = float(t.get("x", 0.0))
            y = float(t.get("y", 0.0))
            z = float(t.get("z", 0.0))

            if self._view_mode == "Y-Z View":
                projected.append((y, z))
            elif self._view_mode == "X-Z View":
                projected.append((x, z))
            else:
                projected.append((x, y))
        return projected

    # ------------------------------------------------------
    # 7. 內部工具：target 投影線與文字
    # ------------------------------------------------------
    def _draw_target_overlays(self, targets: Iterable[dict]) -> None:
        """
        根據 target 的位置與速度，畫出：
        - 黃色 projection line
        - target 編號文字
        """
        if not HAS_PYQTGRAPH or self.plot is None:
            return

        assert pg is not None

        for target in targets:
            tid = target.get("tid", "?")
            x = float(target.get("x", 0.0))
            y = float(target.get("y", 0.0))
            z = float(target.get("z", 0.0))
            vx = float(target.get("vel_x", 0.0))
            vy = float(target.get("vel_y", 0.0))
            vz = float(target.get("vel_z", 0.0))

            if self._view_mode == "Y-Z View":
                x0, y0 = y, z
                x1, y1 = y + vy * self._projection_time_s, z + vz * self._projection_time_s
            elif self._view_mode == "X-Z View":
                x0, y0 = x, z
                x1, y1 = x + vx * self._projection_time_s, z + vz * self._projection_time_s
            else:
                x0, y0 = x, y
                x1, y1 = x + vx * self._projection_time_s, y + vy * self._projection_time_s

            line_item = self.plot.plot(
                [x0, x1],
                [y0, y1],
                pen=pg.mkPen(self.style.projection_pen, width=2.2),
            )
            self._projection_items.append(line_item)

            text_item = pg.TextItem(
                html=f'<div style="color:#ffff00; font-size:12pt;">T{tid}</div>',
                anchor=(0.5, 1.0),
            )
            text_item.setPos(x0, y0)
            self.plot.addItem(text_item)
            self._target_text_items.append(text_item)

    def _clear_dynamic_overlay_items(self) -> None:
        """清掉上一個 frame 留下的 target 線 / 文字。"""
        if not HAS_PYQTGRAPH or self.plot is None:
            return

        for item in self._projection_items:
            try:
                self.plot.removeItem(item)
            except Exception:
                pass
        self._projection_items.clear()

        for item in self._target_text_items:
            try:
                self.plot.removeItem(item)
            except Exception:
                pass
        self._target_text_items.clear()

    # ------------------------------------------------------
    # 8. 內部工具：散點與文字
    # ------------------------------------------------------
    def _set_scatter_data(self, scatter_item, points_2d: Iterable[Sequence[float]]) -> None:
        """把 2D 點資料塞給 ScatterPlotItem。"""
        pts = list(points_2d)
        if not pts:
            scatter_item.setData([], [])
            return

        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        scatter_item.setData(xs, ys)

    def _update_stats_text(
        self,
        frame_number: int,
        num_frames_in_buffer: int,
        num_dynamic: int,
        num_static: int,
        num_targets: int,
    ) -> None:
        """
        更新左上角統計文字。

        這裡刻意排版得接近你給的 MATLAB 圖。
        """
        if not HAS_PYQTGRAPH or self.plot is None:
            return

        x_min, _, _, y_max = self._current_plot_bounds()
        self.stats_text.setHtml(
            f"""
            <div style="color:white; font-size:16pt; line-height:1.5;">
                Frame: {frame_number}<br>
                Num Frames in Buffer: {num_frames_in_buffer}<br>
                Dynamic Points: {num_dynamic}<br>
                Static Points: {num_static}<br>
                Num Tracked Obj: {num_targets}
            </div>
            """
        )
        self.stats_text.setPos(x_min + 0.6, y_max - 0.6)

    # ------------------------------------------------------
    # 9. 純數學 / 座標輔助函式
    # ------------------------------------------------------
    def _current_plot_bounds(self) -> tuple[float, float, float, float]:
        """取得目前 plot 的顯示範圍。"""
        if self.plot is None:
            return (-1.0, 1.0, -1.0, 1.0)

        view_range = self.plot.viewRange()
        x_min, x_max = view_range[0]
        y_min, y_max = view_range[1]
        return float(x_min), float(x_max), float(y_min), float(y_max)

    @staticmethod
    def _semi_circle(radius: float, num_points: int = 300):
        """
        回傳上半圓資料，用來畫和原圖接近的扇形區。
        """
        assert np is not None
        theta = np.linspace(0.0, math.pi, num_points)
        x = radius * np.cos(theta)
        y = radius * np.sin(theta)
        return x, y

    def _ring_sector_polygon(
        self,
        r_inner: float,
        r_outer: float,
        half_angle_deg: float = 90.0,
        n: int = 181,
    ) -> tuple[list[float], list[float]]:
        """
        產生以 +Y 軸為中心展開的環形扇區 polygon，可直接給 PlotDataItem.setData(x, y)。
        """
        assert np is not None

        # 最後一道防呆，避免 GUI 輸入或資料同步誤差造成奇怪形狀。
        r_inner = max(0.0, float(r_inner))
        r_outer = max(r_inner, float(r_outer))
        if r_outer <= 0.0 or r_outer <= r_inner:
            return [], []

        num_points = max(3, int(n))
        theta = np.linspace(-float(half_angle_deg), float(half_angle_deg), num_points)
        theta_rad = np.deg2rad(theta)

        # 外弧：由左到右（相對 +Y 軸）
        x_outer = r_outer * np.sin(theta_rad)
        y_outer = r_outer * np.cos(theta_rad)

        # 內弧：反向由右到左回來，構成封閉環形區域
        theta_rev_rad = theta_rad[::-1]
        x_inner = r_inner * np.sin(theta_rev_rad)
        y_inner = r_inner * np.cos(theta_rev_rad)

        x_poly = np.concatenate([x_outer, x_inner])
        y_poly = np.concatenate([y_outer, y_inner])

        # 額外補首點，確保視覺上是封閉 polygon。
        x_poly = np.append(x_poly, x_poly[0])
        y_poly = np.append(y_poly, y_poly[0])
        return x_poly.tolist(), y_poly.tolist()

    @staticmethod
    def _ray_from_origin(angle_from_y_deg: float, y_end: float):
        """
        由原點畫出一條射線。

        角度定義方式：
        - 以正 Y 軸為基準
        - 向右為正、向左為負

        這樣比較直觀對應到原本雷達朝前方看的畫面。
        """
        angle_rad = math.radians(angle_from_y_deg)
        x_end = math.tan(angle_rad) * y_end
        return [0.0, x_end], [0.0, y_end]


# 相容別名：有些版本的 gui_main.py 會匯入這個名稱。
AreaScannerVisualizerWidget = AreaScanner3DWidget

__all__ = ['AreaScanner3DWidget', 'AreaScannerVisualizerWidget', 'ViewerStyle']
