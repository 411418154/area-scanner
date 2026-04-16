"""
gui_main.py
===========

這個檔案是 Python 版 Area Scanner GUI 的主視窗。

這一版的核心目標
----------------
不是只把視窗打開，而是把整條主要流程真正接起來：

1. 選擇 CLI / DATA Port
2. 載入 cfg
3. 開啟 serial
4. 傳送 cfg 給雷達
5. 持續讀取 DATA Port
6. 用 parser_as.py 解析 TLV
7. 把資料丟給 visualizer_3d.py 顯示成接近 MATLAB 的 X-Y 畫面

重點差異
--------
這一版不再自己手刻 OpenGL 物件，
而是統一透過 `AreaScanner3DWidget` 來顯示。

這樣的好處是：
- GUI 主程式會乾淨很多
- 視覺化細節集中在 visualizer_3d.py
- 後面如果要再調整成更像 MATLAB GUI，只需要主要改 viewer 模組

建議環境版本
------------
- Python 3.10.x
- PySide6 >= 6.6
- pyserial == 3.5
- pyqtgraph >= 0.13
- numpy >= 1.24
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Optional
import time

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from parser_as import AreaScannerParser, ParsedFrame, parse_packet
from serial_manager import PortInfo, SerialConfig, SerialManager, SerialManagerError
from visualizer_3d import AreaScanner3DWidget

STATE_SCHEMA_VERSION = 1
STATE_FILE_PATH = Path.home() / ".area_scanner_state.json"
FLOW_IDLE = "idle"
FLOW_PORTS_READY = "ports_ready"
FLOW_CFG_READY = "cfg_ready"
FLOW_RUNNING = "running"


# ==========================================================
# 1. 執行期設定資料
# ==========================================================
@dataclass
class RuntimeConfig:
    """
    集中保存 GUI 目前的參數。

    為什麼要多包一層 RuntimeConfig？
    --------------------------------
    因為 GUI 的設定不只有 serial，還包括：
    - cfg 路徑
    - 顯示模式
    - zone 參數
    - sensor 安裝資訊

    集中後比較不容易散掉。
    """

    cli_port: str = "COM6"
    data_port: str = "COM5"
    cli_baud: int = 115200
    data_baud: int = 921600
    cfg_file: str = ""

    mounting_height_m: float = 2.0
    elevation_tilt_deg: float = 0.0

    enable_zone: bool = True
    critical_start_m: float = 0.0
    critical_end_m: float = 2.0
    warn_start_m: float = 2.0
    warn_end_m: float = 4.0
    projection_time_s: float = 2.0
    fov_outer_angle_deg: float = 59.0
    fov_inner_angle_deg: float = 30.0
    fov_outer_range_m: float = 7.2
    fov_inner_range_m: float = 2.1

    view_mode: str = "X-Y View"

    def to_dict(self) -> dict:
        """序列化成可寫入 JSON 的 dict。"""
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, payload: dict) -> "RuntimeConfig":
        """
        由 dict 建立 RuntimeConfig。

        未知欄位會忽略、缺少欄位會使用 dataclass 預設值，
        這樣 schema 未來變更時可平滑升級。
        """
        defaults = cls()
        for key, value in payload.items():
            if hasattr(defaults, key):
                setattr(defaults, key, value)
        return defaults

    def to_serial_config(self) -> SerialConfig:
        """只把 serial 層需要的欄位轉成 SerialConfig。"""
        return SerialConfig(
            cli_port=self.cli_port,
            data_port=self.data_port,
            cli_baud=self.cli_baud,
            data_baud=self.data_baud,
            cfg_file=self.cfg_file,
            timeout_s=1.0,
            command_delay_s=0.03,
        )


# ==========================================================
# 2. 背景執行緒：真正跟雷達通訊
# ==========================================================
class RadarWorker(QThread):
    """
    背景工作執行緒。

    為什麼要把 serial / parser 放進執行緒？
    ----------------------------------------
    因為 DATA Port 讀取是持續進行的 while 迴圈。
    如果直接在 GUI 主執行緒做，視窗很容易卡住。
    """

    status_signal = Signal(str)
    log_signal = Signal(str)
    frame_signal = Signal(object)   # 送 ParsedFrame
    error_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, config: RuntimeConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = RuntimeConfig(**config.__dict__)
        self._running = False

    def stop(self) -> None:
        """通知背景執行緒停止。"""
        self._running = False

    def run(self) -> None:
        """
        背景主流程。

        流程：
        1. 開 port
        2. 送 cfg
        3. 持續讀 DATA
        4. 切 packet
        5. parse packet
        6. 回傳 frame 給 GUI
        """
        manager = SerialManager(self._config.to_serial_config())
        parser = AreaScannerParser()
        self._running = True

        last_wait_log_ts = 0.0

        try:
            self.status_signal.emit("正在開啟序列埠...")
            self.log_signal.emit(
                f"[Worker] 開啟 CLI={self._config.cli_port}/{self._config.cli_baud}, "
                f"DATA={self._config.data_port}/{self._config.data_baud}"
            )
            manager.open_ports()
            manager.clear_buffers()
            self.log_signal.emit("[Worker] CLI / DATA Port 開啟成功。")

            if not self._config.cfg_file:
                raise ValueError("尚未選擇 cfg 檔案。")

            self.status_signal.emit("正在傳送 cfg...")
            for line in manager.send_cfg_file(self._config.cfg_file):
                self.log_signal.emit(line)

            self.status_signal.emit("cfg 已送出，開始接收 TLV 資料。")
            self.log_signal.emit("[Worker] 已進入資料接收迴圈。")

            while self._running:
                raw = manager.read_data_once(max_bytes=8192)

                if not raw:
                    now = time.time()
                    if now - last_wait_log_ts > 2.0:
                        self.log_signal.emit("[Worker] 等待 DATA Port 資料中...")
                        last_wait_log_ts = now
                    self.msleep(5)
                    continue

                parser.append_data(raw)
                packets = parser.extract_packets()

                if not packets:
                    self.msleep(1)
                    continue

                for packet in packets:
                    if not self._running:
                        break

                    try:
                        frame: ParsedFrame = parse_packet(packet)
                    except Exception as exc:
                        self.log_signal.emit(f"[解析警告] 單一 packet 解析失敗：{exc}")
                        continue

                    self.frame_signal.emit(frame)
                    self.status_signal.emit(
                        f"接收中：Frame #{frame.header.frame_number} | "
                        f"Dyn={len(frame.dynamic_points)} | "
                        f"Static={len(frame.static_points)} | "
                        f"Targets={len(frame.targets)}"
                    )

        except Exception as exc:
            self.error_signal.emit(str(exc))
        finally:
            try:
                # 很多 cfg 最後都會 sensorStart，所以停止時盡量送一次 sensorStop。
                manager.send_cli_command("sensorStop", read_response=False)
            except Exception as exc:
                self.log_signal.emit(f"[Worker 警告] 送出 sensorStop 失敗：{exc}")

            try:
                manager.close_ports()
            except Exception:
                pass

            self.status_signal.emit("已停止。")
            self.finished_signal.emit()


# ==========================================================
# 3. 主視窗
# ==========================================================
class AreaScannerMainWindow(QMainWindow):
    """Python 版 Area Scanner 主視窗。"""

    def __init__(self) -> None:
        super().__init__()

        self.config = RuntimeConfig()
        self.worker: Optional[RadarWorker] = None
        self._flow_state = FLOW_IDLE
        self._connection_test_passed = False

        self.setWindowTitle("Area Scanner Python Visualizer")
        self.resize(1550, 900)

        self._build_actions()
        self._build_toolbar()
        self._build_status_bar()
        self._build_central_ui()
        self._connect_signals()
        self.load_state()
        self._apply_default_values()
        self.refresh_ports()
        self._apply_viewer_config()
        self._update_flow_state()

        self.append_log("[系統] GUI 已建立。")
        self.append_log("[系統] 這一版的 Viewer 會盡量接近 MATLAB 的 X-Y 畫面。")

    # ------------------------------------------------------
    # A. 基本 UI 建立
    # ------------------------------------------------------
    def _build_actions(self) -> None:
        self.action_open_cfg = QAction("載入 CFG", self)
        self.action_start = QAction("開始", self)
        self.action_stop = QAction("停止", self)
        self.action_refresh_ports = QAction("重新整理 COM", self)
        self.action_about = QAction("關於", self)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction(self.action_open_cfg)
        toolbar.addSeparator()
        toolbar.addAction(self.action_refresh_ports)
        toolbar.addSeparator()
        toolbar.addAction(self.action_start)
        toolbar.addAction(self.action_stop)
        toolbar.addSeparator()
        toolbar.addAction(self.action_about)

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)
        self.status_label = QLabel("就緒")
        self.step_status_label = QLabel("Step 1/4：先測試連線（Test）")
        bar.addWidget(self.step_status_label)
        bar.addPermanentWidget(self.status_label)

    def _build_central_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        self.step_top_label = QLabel("Step 1/4：先測試連線（Test）")
        self.step_top_label.setStyleSheet("font-weight: 600; color: #2d6a9f;")
        root_layout.addWidget(self.step_top_label)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(10)

        left = self._build_left_panel()
        right = self._build_right_panel()

        content_layout.addWidget(left, 0)
        content_layout.addWidget(right, 1)
        root_layout.addLayout(content_layout)

    # ------------------------------------------------------
    # B. 左側控制面板
    # ------------------------------------------------------
    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(420)
        panel.setMaximumWidth(520)

        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        layout.addWidget(self._create_serial_group())
        layout.addWidget(self._create_cfg_group())
        layout.addWidget(self._create_sensor_group())
        layout.addWidget(self._create_zone_group())
        layout.addWidget(self._create_run_group())
        layout.addStretch(1)
        return panel

    def _create_serial_group(self) -> QGroupBox:
        group = QGroupBox("COM / Serial Settings")
        layout = QGridLayout(group)

        self.combo_cli_port = QComboBox()
        self.combo_cli_port.setEditable(True)
        self.combo_data_port = QComboBox()
        self.combo_data_port.setEditable(True)

        self.spin_cli_baud = QSpinBox()
        self.spin_cli_baud.setRange(9600, 3000000)
        self.spin_cli_baud.setSingleStep(115200)

        self.spin_data_baud = QSpinBox()
        self.spin_data_baud.setRange(9600, 3000000)
        self.spin_data_baud.setSingleStep(115200)

        self.btn_refresh_ports = QPushButton("Refresh Ports")
        self.btn_test_connection = QPushButton("Test Connection")

        layout.addWidget(QLabel("CLI Port"), 0, 0)
        layout.addWidget(self.combo_cli_port, 0, 1)
        layout.addWidget(QLabel("DATA Port"), 1, 0)
        layout.addWidget(self.combo_data_port, 1, 1)
        layout.addWidget(QLabel("CLI Baud"), 2, 0)
        layout.addWidget(self.spin_cli_baud, 2, 1)
        layout.addWidget(QLabel("DATA Baud"), 3, 0)
        layout.addWidget(self.spin_data_baud, 3, 1)
        layout.addWidget(self.btn_refresh_ports, 4, 0)
        layout.addWidget(self.btn_test_connection, 4, 1)
        return group

    def _create_cfg_group(self) -> QGroupBox:
        group = QGroupBox("CFG File")
        layout = QGridLayout(group)

        self.edit_cfg_path = QLineEdit()
        self.btn_browse_cfg = QPushButton("Browse...")

        layout.addWidget(QLabel("CFG 路徑"), 0, 0)
        layout.addWidget(self.edit_cfg_path, 0, 1)
        layout.addWidget(self.btn_browse_cfg, 0, 2)
        return group

    def _create_sensor_group(self) -> QGroupBox:
        group = QGroupBox("Sensor Information")
        layout = QFormLayout(group)

        self.spin_mounting_height = QDoubleSpinBox()
        self.spin_mounting_height.setRange(0.0, 20.0)
        self.spin_mounting_height.setDecimals(2)
        self.spin_mounting_height.setSingleStep(0.1)

        self.spin_elevation_tilt = QDoubleSpinBox()
        self.spin_elevation_tilt.setRange(-90.0, 90.0)
        self.spin_elevation_tilt.setDecimals(2)
        self.spin_elevation_tilt.setSingleStep(0.5)

        layout.addRow("Mounting Height (m)", self.spin_mounting_height)
        layout.addRow("Elevation Tilt (deg)", self.spin_elevation_tilt)
        return group

    def _create_zone_group(self) -> QGroupBox:
        group = QGroupBox("Viewer / Zones")
        layout = QFormLayout(group)

        self.combo_view_mode = QComboBox()
        self.combo_view_mode.addItems(["X-Y View", "Y-Z View", "X-Z View", "3D View"])

        self.check_enable_zone = QCheckBox("Enable Zones")

        self.spin_critical_start = QDoubleSpinBox()
        self.spin_critical_start.setRange(0.0, 100.0)
        self.spin_critical_start.setDecimals(2)

        self.spin_critical_end = QDoubleSpinBox()
        self.spin_critical_end.setRange(0.0, 100.0)
        self.spin_critical_end.setDecimals(2)

        self.spin_warn_start = QDoubleSpinBox()
        self.spin_warn_start.setRange(0.0, 100.0)
        self.spin_warn_start.setDecimals(2)

        self.spin_warn_end = QDoubleSpinBox()
        self.spin_warn_end.setRange(0.0, 100.0)
        self.spin_warn_end.setDecimals(2)

        self.spin_projection_time = QDoubleSpinBox()
        self.spin_projection_time.setRange(0.0, 20.0)
        self.spin_projection_time.setDecimals(2)

        self.spin_fov_outer_angle = QDoubleSpinBox()
        self.spin_fov_outer_angle.setRange(0.0, 89.9)
        self.spin_fov_outer_angle.setDecimals(2)

        self.spin_fov_inner_angle = QDoubleSpinBox()
        self.spin_fov_inner_angle.setRange(0.0, 89.9)
        self.spin_fov_inner_angle.setDecimals(2)

        self.spin_fov_outer_range = QDoubleSpinBox()
        self.spin_fov_outer_range.setRange(0.0, 100.0)
        self.spin_fov_outer_range.setDecimals(2)

        self.spin_fov_inner_range = QDoubleSpinBox()
        self.spin_fov_inner_range.setRange(0.0, 100.0)
        self.spin_fov_inner_range.setDecimals(2)

        layout.addRow("View Mode", self.combo_view_mode)
        layout.addRow(self.check_enable_zone)
        layout.addRow("Critical Start (m)", self.spin_critical_start)
        layout.addRow("Critical End (m)", self.spin_critical_end)
        layout.addRow("Warn Start (m)", self.spin_warn_start)
        layout.addRow("Warn End (m)", self.spin_warn_end)
        layout.addRow("Projection Time (s)", self.spin_projection_time)
        layout.addRow("FOV Outer Angle (deg)", self.spin_fov_outer_angle)
        layout.addRow("FOV Inner Angle (deg)", self.spin_fov_inner_angle)
        layout.addRow("FOV Outer Range (m)", self.spin_fov_outer_range)
        layout.addRow("FOV Inner Range (m)", self.spin_fov_inner_range)
        return group

    def _create_run_group(self) -> QGroupBox:
        group = QGroupBox("Run Control")
        layout = QVBoxLayout(group)

        self.btn_start = QPushButton("Start")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)

        layout.addWidget(self.btn_start)
        layout.addWidget(self.btn_stop)
        return group

    # ------------------------------------------------------
    # C. 右側顯示區
    # ------------------------------------------------------
    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        layout.addWidget(self._create_viewer_group(), 5)
        layout.addWidget(self._create_stats_group(), 1)
        layout.addWidget(self._create_log_group(), 2)
        return panel

    def _create_viewer_group(self) -> QGroupBox:
        group = QGroupBox("Viewer")
        layout = QVBoxLayout(group)

        self.viewer = AreaScanner3DWidget()
        layout.addWidget(self.viewer, 1)
        return group

    def _create_stats_group(self) -> QGroupBox:
        group = QGroupBox("Frame Stats")
        layout = QGridLayout(group)

        self.label_frame_number = QLabel("0")
        self.label_num_tlvs = QLabel("0")
        self.label_num_dynamic = QLabel("0")
        self.label_num_static = QLabel("0")
        self.label_num_targets = QLabel("0")
        self.label_runtime = QLabel("Idle")

        layout.addWidget(QLabel("Frame Number"), 0, 0)
        layout.addWidget(self.label_frame_number, 0, 1)
        layout.addWidget(QLabel("Num TLVs"), 1, 0)
        layout.addWidget(self.label_num_tlvs, 1, 1)
        layout.addWidget(QLabel("Dynamic Points"), 2, 0)
        layout.addWidget(self.label_num_dynamic, 2, 1)
        layout.addWidget(QLabel("Static Points"), 3, 0)
        layout.addWidget(self.label_num_static, 3, 1)
        layout.addWidget(QLabel("Tracked Targets"), 4, 0)
        layout.addWidget(self.label_num_targets, 4, 1)
        layout.addWidget(QLabel("Runtime Status"), 5, 0)
        layout.addWidget(self.label_runtime, 5, 1)
        return group

    def _create_log_group(self) -> QGroupBox:
        group = QGroupBox("Log Output")
        layout = QVBoxLayout(group)

        self.text_log = QPlainTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.text_log)
        return group

    # ------------------------------------------------------
    # D. 訊號連接與初始值
    # ------------------------------------------------------
    def _connect_signals(self) -> None:
        self.action_open_cfg.triggered.connect(self.browse_cfg_file)
        self.action_refresh_ports.triggered.connect(self.refresh_ports)
        self.action_start.triggered.connect(self.start_worker)
        self.action_stop.triggered.connect(self.stop_worker)
        self.action_about.triggered.connect(self.show_about_dialog)

        self.btn_browse_cfg.clicked.connect(self.browse_cfg_file)
        self.btn_refresh_ports.clicked.connect(self.refresh_ports)
        self.btn_test_connection.clicked.connect(self.test_connection)
        self.btn_start.clicked.connect(self.start_worker)
        self.btn_stop.clicked.connect(self.stop_worker)
        self.combo_cli_port.currentTextChanged.connect(self._on_serial_settings_changed)
        self.combo_data_port.currentTextChanged.connect(self._on_serial_settings_changed)
        self.spin_cli_baud.valueChanged.connect(self._on_serial_settings_changed)
        self.spin_data_baud.valueChanged.connect(self._on_serial_settings_changed)
        self.edit_cfg_path.textChanged.connect(self._on_cfg_path_changed)

        self.combo_view_mode.currentTextChanged.connect(self.on_view_mode_changed)
        self.check_enable_zone.toggled.connect(self._apply_viewer_config)
        self.spin_critical_start.valueChanged.connect(self._apply_viewer_config)
        self.spin_critical_end.valueChanged.connect(self._apply_viewer_config)
        self.spin_warn_start.valueChanged.connect(self._apply_viewer_config)
        self.spin_warn_end.valueChanged.connect(self._apply_viewer_config)
        self.spin_projection_time.valueChanged.connect(self._apply_viewer_config)
        self.spin_mounting_height.valueChanged.connect(self._apply_viewer_config)
        self.spin_elevation_tilt.valueChanged.connect(self._apply_viewer_config)
        self.spin_fov_outer_angle.valueChanged.connect(self._apply_viewer_config)
        self.spin_fov_inner_angle.valueChanged.connect(self._apply_viewer_config)
        self.spin_fov_outer_range.valueChanged.connect(self._apply_viewer_config)
        self.spin_fov_inner_range.valueChanged.connect(self._apply_viewer_config)

    def _apply_default_values(self) -> None:
        self.spin_cli_baud.setValue(self.config.cli_baud)
        self.spin_data_baud.setValue(self.config.data_baud)
        self.edit_cfg_path.setText(self.config.cfg_file)

        self.spin_mounting_height.setValue(self.config.mounting_height_m)
        self.spin_elevation_tilt.setValue(self.config.elevation_tilt_deg)

        self.check_enable_zone.setChecked(self.config.enable_zone)
        self.spin_critical_start.setValue(self.config.critical_start_m)
        self.spin_critical_end.setValue(self.config.critical_end_m)
        self.spin_warn_start.setValue(self.config.warn_start_m)
        self.spin_warn_end.setValue(self.config.warn_end_m)
        self.spin_projection_time.setValue(self.config.projection_time_s)
        self.spin_fov_outer_angle.setValue(self.config.fov_outer_angle_deg)
        self.spin_fov_inner_angle.setValue(self.config.fov_inner_angle_deg)
        self.spin_fov_outer_range.setValue(self.config.fov_outer_range_m)
        self.spin_fov_inner_range.setValue(self.config.fov_inner_range_m)

        self.combo_view_mode.setCurrentText(self.config.view_mode)

    def _sync_widgets_to_config(self) -> None:
        self.config.cli_port = self.combo_cli_port.currentText().strip()
        self.config.data_port = self.combo_data_port.currentText().strip()
        self.config.cli_baud = int(self.spin_cli_baud.value())
        self.config.data_baud = int(self.spin_data_baud.value())
        self.config.cfg_file = self.edit_cfg_path.text().strip()

        self.config.mounting_height_m = float(self.spin_mounting_height.value())
        self.config.elevation_tilt_deg = float(self.spin_elevation_tilt.value())

        self.config.enable_zone = self.check_enable_zone.isChecked()
        self.config.critical_start_m = float(self.spin_critical_start.value())
        self.config.critical_end_m = float(self.spin_critical_end.value())
        self.config.warn_start_m = float(self.spin_warn_start.value())
        self.config.warn_end_m = float(self.spin_warn_end.value())
        self.config.projection_time_s = float(self.spin_projection_time.value())
        self.config.fov_outer_angle_deg = float(self.spin_fov_outer_angle.value())
        self.config.fov_inner_angle_deg = float(self.spin_fov_inner_angle.value())
        self.config.fov_outer_range_m = float(self.spin_fov_outer_range.value())
        self.config.fov_inner_range_m = float(self.spin_fov_inner_range.value())
        self.config.view_mode = self.combo_view_mode.currentText()

    def _apply_viewer_config(self) -> None:
        """把目前 GUI 上的 viewer 參數同步到顯示元件。"""
        self._sync_widgets_to_config()
        self.viewer.set_view_mode(self.config.view_mode)
        self.viewer.set_zone_config(
            enable_zones=self.config.enable_zone,
            critical_start_m=self.config.critical_start_m,
            critical_end_m=self.config.critical_end_m,
            warn_start_m=self.config.warn_start_m,
            warn_end_m=self.config.warn_end_m,
            projection_time_s=self.config.projection_time_s,
        )
        self.viewer.set_mount_config(
            mounting_height_m=self.config.mounting_height_m,
            elevation_tilt_deg=self.config.elevation_tilt_deg,
        )
        self.viewer.set_fov_config(
            outer_angle_deg=self.config.fov_outer_angle_deg,
            inner_angle_deg=self.config.fov_inner_angle_deg,
            outer_range_m=self.config.fov_outer_range_m,
            inner_range_m=self.config.fov_inner_range_m,
        )

    def _on_serial_settings_changed(self) -> None:
        if self._connection_test_passed:
            self._connection_test_passed = False
            self.append_log("[流程] Serial 設定已變更，請重新執行 Test Connection。")
        self._update_flow_state()

    def _on_cfg_path_changed(self) -> None:
        self._update_flow_state()

    def _compute_flow_state(self) -> str:
        if self.worker is not None and self.worker.isRunning():
            return FLOW_RUNNING
        if self._connection_test_passed and self.edit_cfg_path.text().strip():
            return FLOW_CFG_READY
        if self._connection_test_passed:
            return FLOW_PORTS_READY
        return FLOW_IDLE

    def _step_text_for_state(self, state: str) -> str:
        mapping = {
            FLOW_IDLE: "Step 1/4：先測試連線（Test）",
            FLOW_PORTS_READY: "Step 2/4：選擇 CFG",
            FLOW_CFG_READY: "Step 3/4：可開始執行（Start）",
            FLOW_RUNNING: "Step 4/4：執行中（Running）",
        }
        return mapping.get(state, "Step 1/4：先測試連線（Test）")

    def _set_critical_inputs_enabled(self, enabled: bool) -> None:
        widgets = [
            self.combo_cli_port,
            self.combo_data_port,
            self.spin_cli_baud,
            self.spin_data_baud,
            self.btn_refresh_ports,
            self.btn_test_connection,
            self.edit_cfg_path,
            self.btn_browse_cfg,
            self.spin_mounting_height,
            self.spin_elevation_tilt,
            self.combo_view_mode,
            self.check_enable_zone,
            self.spin_critical_start,
            self.spin_critical_end,
            self.spin_warn_start,
            self.spin_warn_end,
            self.spin_projection_time,
            self.spin_fov_outer_angle,
            self.spin_fov_inner_angle,
            self.spin_fov_outer_range,
            self.spin_fov_inner_range,
            self.action_open_cfg,
            self.action_refresh_ports,
        ]
        for widget in widgets:
            widget.setEnabled(enabled)

    def _update_flow_state(self) -> None:
        self._flow_state = self._compute_flow_state()
        step_text = self._step_text_for_state(self._flow_state)
        self.step_top_label.setText(step_text)
        self.step_status_label.setText(step_text)

        running = self._flow_state == FLOW_RUNNING
        can_start = self._flow_state == FLOW_CFG_READY

        self.btn_start.setEnabled(can_start)
        self.action_start.setEnabled(can_start)
        self.btn_stop.setEnabled(running)
        self.action_stop.setEnabled(running)
        self._set_critical_inputs_enabled(not running)

    def load_state(self) -> None:
        """
        載入上次 GUI 狀態。

        若 state JSON 損毀或格式不合法，會回退到預設值並寫入 log。
        """
        if not STATE_FILE_PATH.exists():
            return

        try:
            raw = json.loads(STATE_FILE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self.config = RuntimeConfig()
            self.append_log(f"[State] 狀態檔 JSON 損毀，已回退預設值：{exc}")
            return
        except Exception as exc:
            self.config = RuntimeConfig()
            self.append_log(f"[State] 讀取狀態檔失敗，已回退預設值：{exc}")
            return

        try:
            migrated = self._migrate_state(raw)
            self.config = RuntimeConfig.from_dict(migrated.get("config", {}))
            self.append_log(f"[State] 已載入狀態檔：{STATE_FILE_PATH}")
        except Exception as exc:
            self.config = RuntimeConfig()
            self.append_log(f"[State] 狀態檔格式不正確，已回退預設值：{exc}")

    def save_state(self) -> None:
        """保存目前 GUI 狀態。"""
        self._sync_widgets_to_config()

        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "config": self.config.to_dict(),
        }
        try:
            STATE_FILE_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.append_log(f"[State] 已保存狀態檔：{STATE_FILE_PATH}")
        except Exception as exc:
            self.append_log(f"[State] 保存狀態檔失敗：{exc}")

    def _migrate_state(self, raw_state: dict) -> dict:
        """
        升級舊版 state 到目前 schema。

        - v0（沒有 schema_version）視為舊格式，內容即為 config 欄位。
        - v1 起使用 {"schema_version": 1, "config": {...}}。
        """
        if not isinstance(raw_state, dict):
            raise ValueError("state 根物件必須是 JSON object。")

        version = raw_state.get("schema_version", 0)

        # v0：舊格式，直接把 root 視為 config。
        if version == 0:
            return {"schema_version": STATE_SCHEMA_VERSION, "config": raw_state}

        if version > STATE_SCHEMA_VERSION:
            self.append_log(
                f"[State] 偵測到較新 schema_version={version}，將盡力相容讀取。"
            )
            return raw_state

        if version == 1:
            if "config" not in raw_state or not isinstance(raw_state["config"], dict):
                raise ValueError("state 缺少 config 欄位或型別錯誤。")
            return raw_state

        raise ValueError(f"不支援的 schema_version={version}")

    # ------------------------------------------------------
    # E. 使用者操作
    # ------------------------------------------------------
    def browse_cfg_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select CFG File",
            str(Path.home()),
            "CFG Files (*.cfg);;All Files (*.*)",
        )
        if not file_path:
            return

        self.edit_cfg_path.setText(file_path)
        self.append_log(f"[設定] 已選擇 CFG：{file_path}")
        self._update_flow_state()

    def refresh_ports(self) -> None:
        """重新掃描目前電腦可見的 COM Port。"""
        current_cli = self.combo_cli_port.currentText().strip()
        current_data = self.combo_data_port.currentText().strip()

        self.combo_cli_port.clear()
        self.combo_data_port.clear()

        ports = SerialManager.list_available_ports()
        for info in ports:
            self.combo_cli_port.addItem(info.device)
            self.combo_data_port.addItem(info.device)

        self._restore_combo_selection(self.combo_cli_port, current_cli or self.config.cli_port)
        self._restore_combo_selection(self.combo_data_port, current_data or self.config.data_port)

        self.append_log("[系統] 已重新整理 COM Port 清單。")
        for info in ports:
            self.append_log(f"  - {info.device}: {info.description}")

        self._connection_test_passed = False
        self._update_flow_state()

    @staticmethod
    def _restore_combo_selection(combo: QComboBox, target: str) -> None:
        index = combo.findText(target)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(target)

    def test_connection(self) -> None:
        """
        快速做一次 serial 層測試。

        這不是完整測試，但很適合先確認：
        - COM Port 能不能開
        - CLI / DATA 有沒有明顯寫反
        """
        self._sync_widgets_to_config()
        manager = SerialManager(self.config.to_serial_config())

        try:
            logs = manager.test_basic_connection()
            for line in logs:
                self.append_log(f"[Test] {line}")
            self._connection_test_passed = True
            self._update_flow_state()
            QMessageBox.information(self, "Test Connection", "基本連線測試已完成，請看下方 Log。")
        except Exception as exc:
            self.append_log(f"[Test Error] {exc}")
            self._connection_test_passed = False
            self._update_flow_state()
            QMessageBox.warning(self, "Test Connection", str(exc))
        finally:
            try:
                manager.close_ports()
            except Exception:
                pass

    def start_worker(self) -> None:
        """啟動背景工作執行緒。"""
        if self.worker is not None and self.worker.isRunning():
            self.append_log("[系統] 背景工作已在執行。")
            return

        self._sync_widgets_to_config()
        self._apply_viewer_config()

        if not self._connection_test_passed:
            QMessageBox.warning(self, "Test Required", "尚未通過連線測試，請先按 Test Connection。")
            return

        if not self.config.cfg_file:
            QMessageBox.warning(self, "CFG Required", "請先選擇 cfg 檔案。")
            return

        self.worker = RadarWorker(self.config, self)
        self.worker.status_signal.connect(self.update_status)
        self.worker.log_signal.connect(self.append_log)
        self.worker.frame_signal.connect(self.on_new_frame)
        self.worker.error_signal.connect(self.on_worker_error)
        self.worker.finished_signal.connect(self.on_worker_finished, Qt.QueuedConnection)
        self.worker.start()

        self.label_runtime.setText("Running")
        self.append_log("[系統] 已啟動背景工作。")
        self._update_flow_state()

    def stop_worker(self) -> None:
        """停止背景工作。"""
        if self.worker is None:
            return

        if self.worker.isRunning():
            self.worker.stop()
            stopped = self.worker.wait(2000)
            if not stopped:
                self.append_log("[系統] 停止 worker 等待逾時（2 秒），進入 fallback 保護模式。")
                self._enter_worker_timeout_fallback()
                return

        self.worker = None
        self.label_runtime.setText("Stopped")
        self.update_status("已停止")
        self.append_log("[系統] 已停止背景工作。")
        self._update_flow_state()

    def _enter_worker_timeout_fallback(self) -> None:
        """worker 停止逾時時的保護流程。"""
        self.worker = None
        self._connection_test_passed = False
        self._update_flow_state()
        self.label_runtime.setText("Stop Timeout")
        self.update_status("停止逾時，請重新連線")
        self.append_log("[系統] fallback：已停用 Start/Stop 控制，請重新整理 COM Port 並重啟連線。")
        QMessageBox.warning(
            self,
            "Stop Timeout",
            "停止背景工作逾時，已進入保護模式。\n"
            "請重新整理 COM Port 並重啟連線。",
        )

    def show_about_dialog(self) -> None:
        QMessageBox.information(
            self,
            "About",
            "Area Scanner Python Visualizer\n\n"
            "這一版重點：\n"
            "1. 真的接上 serial / parser\n"
            "2. Viewer 風格拉近 MATLAB X-Y View\n"
            "3. 後續可以再補得更像原版 GUI",
        )

    def on_view_mode_changed(self, view_text: str) -> None:
        self.config.view_mode = view_text
        self.viewer.set_view_mode(view_text)
        self.append_log(f"[Viewer] 已切換到：{view_text}")

    # ------------------------------------------------------
    # F. GUI 更新
    # ------------------------------------------------------
    def update_status(self, text: str) -> None:
        self.statusBar().showMessage(text)
        self.status_label.setText(text)

    def append_log(self, text: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.text_log.appendPlainText(f"[{timestamp}] {text}")

    def on_new_frame(self, frame: ParsedFrame) -> None:
        """收到新 frame 後更新 viewer 與 stats。"""
        self.label_frame_number.setText(str(frame.header.frame_number))
        self.label_num_tlvs.setText(str(frame.header.num_tlvs))
        self.label_num_dynamic.setText(str(len(frame.dynamic_points)))
        self.label_num_static.setText(str(len(frame.static_points)))
        self.label_num_targets.setText(str(len(frame.targets)))

        self.viewer.update_from_frame(frame, buffer_frame_count=1)

    def on_worker_error(self, message: str) -> None:
        self.append_log(f"[錯誤] {message}")
        self.worker = None
        self.label_runtime.setText("Error")
        self._update_flow_state()
        QMessageBox.warning(self, "Worker Error", message)

    def on_worker_finished(self) -> None:
        self._update_flow_state()
        if self.label_runtime.text() != "Error":
            self.label_runtime.setText("Stopped")

    # ------------------------------------------------------
    # G. 關閉視窗時先安全停止背景工作
    # ------------------------------------------------------
    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self.save_state()
            self.stop_worker()
        finally:
            event.accept()
