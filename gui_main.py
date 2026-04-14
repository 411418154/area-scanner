"""
gui_main.py
===========

這個檔案是 Python 版 Area Scanner GUI 的主視窗。
[新增] 整合了邊看 3D 畫面、邊把原始 TLV 串流寫入 .bin 檔的功能。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from datetime import datetime  # 新增：用來產生 log 檔名

from PySide6.QtCore import QThread, Signal
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


# ==========================================================
# 1. 執行期設定資料
# ==========================================================
@dataclass
class RuntimeConfig:
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

    view_mode: str = "X-Y View"
    
    # 新增：是否同步寫入二進位檔案
    record_bin: bool = False 

    def to_serial_config(self) -> SerialConfig:
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
    status_signal = Signal(str)
    log_signal = Signal(str)
    frame_signal = Signal(object)   
    error_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, config: RuntimeConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = RuntimeConfig(**config.__dict__)
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        manager = SerialManager(self._config.to_serial_config())
        parser = AreaScannerParser()
        self._running = True
        last_wait_log_ts = 0.0
        
        # --- 準備 Log 檔案物件 ---
        log_file = None
        if self._config.record_bin:
            try:
                log_dir = Path("logs")
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                log_path = log_dir / f'tlv_stream_raw_{ts}.bin'
                log_file = open(log_path, 'wb')
                self.log_signal.emit(f"[Worker] 開始同步錄製原始資料：{log_path.name}")
            except Exception as e:
                self.log_signal.emit(f"[警告] 無法建立 log 檔案: {e}")

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
                
                # --- [新增] 同步寫入硬碟 ---
                if log_file is not None:
                    log_file.write(raw)
                    log_file.flush() # 確保立刻寫入硬碟，防止當機遺失

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
                manager.send_cli_command("sensorStop", read_response=False)
            except Exception:
                pass

            try:
                manager.close_ports()
            except Exception:
                pass
            
            # --- 安全關閉 Log 檔案 ---
            if log_file is not None:
                try:
                    log_file.close()
                    self.log_signal.emit("[Worker] 錄製檔案已安全關閉。")
                except Exception:
                    pass

            self.status_signal.emit("已停止。")
            self.finished_signal.emit()


# ==========================================================
# 3. 主視窗
# ==========================================================
class AreaScannerMainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()

        self.config = RuntimeConfig()
        self.worker: Optional[RadarWorker] = None

        self.setWindowTitle("Area Scanner Python Visualizer")
        self.resize(1550, 900)

        self._build_actions()
        self._build_toolbar()
        self._build_status_bar()
        self._build_central_ui()
        self._connect_signals()
        self._apply_default_values()
        self.refresh_ports()
        self._apply_viewer_config()

        self.append_log("[系統] GUI 已建立。")

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
        bar.addPermanentWidget(self.status_label)

    def _build_central_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        left = self._build_left_panel()
        right = self._build_right_panel()

        root_layout.addWidget(left, 0)
        root_layout.addWidget(right, 1)

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

        layout.addRow("View Mode", self.combo_view_mode)
        layout.addRow(self.check_enable_zone)
        layout.addRow("Critical Start (m)", self.spin_critical_start)
        layout.addRow("Critical End (m)", self.spin_critical_end)
        layout.addRow("Warn Start (m)", self.spin_warn_start)
        layout.addRow("Warn End (m)", self.spin_warn_end)
        layout.addRow("Projection Time (s)", self.spin_projection_time)
        return group

    def _create_run_group(self) -> QGroupBox:
        group = QGroupBox("Run Control")
        layout = QVBoxLayout(group)

        # 新增：讓使用者可以在 GUI 上打勾決定要不要存 .bin
        self.check_record_bin = QCheckBox("同步儲存 raw data (.bin)")

        self.btn_start = QPushButton("Start")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)

        layout.addWidget(self.check_record_bin)
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

        self.combo_view_mode.currentTextChanged.connect(self.on_view_mode_changed)
        self.check_enable_zone.toggled.connect(self._apply_viewer_config)
        self.spin_critical_start.valueChanged.connect(self._apply_viewer_config)
        self.spin_critical_end.valueChanged.connect(self._apply_viewer_config)
        self.spin_warn_start.valueChanged.connect(self._apply_viewer_config)
        self.spin_warn_end.valueChanged.connect(self._apply_viewer_config)
        self.spin_projection_time.valueChanged.connect(self._apply_viewer_config)
        self.spin_mounting_height.valueChanged.connect(self._apply_viewer_config)
        self.spin_elevation_tilt.valueChanged.connect(self._apply_viewer_config)

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

        self.combo_view_mode.setCurrentText(self.config.view_mode)
        self.check_record_bin.setChecked(self.config.record_bin)

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
        self.config.view_mode = self.combo_view_mode.currentText()
        
        self.config.record_bin = self.check_record_bin.isChecked()

    def _apply_viewer_config(self) -> None:
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

    def refresh_ports(self) -> None:
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

    @staticmethod
    def _restore_combo_selection(combo: QComboBox, target: str) -> None:
        index = combo.findText(target)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(target)

    def test_connection(self) -> None:
        self._sync_widgets_to_config()
        manager = SerialManager(self.config.to_serial_config())

        try:
            logs = manager.test_basic_connection()
            for line in logs:
                self.append_log(f"[Test] {line}")
            QMessageBox.information(self, "Test Connection", "基本連線測試已完成，請看下方 Log。")
        except Exception as exc:
            self.append_log(f"[Test Error] {exc}")
            QMessageBox.warning(self, "Test Connection", str(exc))
        finally:
            try:
                manager.close_ports()
            except Exception:
                pass

    def start_worker(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.append_log("[系統] 背景工作已在執行。")
            return

        self._sync_widgets_to_config()
        self._apply_viewer_config()

        if not self.config.cfg_file:
            QMessageBox.warning(self, "CFG Required", "請先選擇 cfg 檔案。")
            return

        self.worker = RadarWorker(self.config, self)
        self.worker.status_signal.connect(self.update_status)
        self.worker.log_signal.connect(self.append_log)
        self.worker.frame_signal.connect(self.on_new_frame)
        self.worker.error_signal.connect(self.on_worker_error)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.label_runtime.setText("Running")
        self.append_log("[系統] 已啟動背景工作。")

    def stop_worker(self) -> None:
        if self.worker is None:
            return

        if self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)

        self.worker = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.label_runtime.setText("Stopped")
        self.update_status("已停止")
        self.append_log("[系統] 已停止背景工作。")

    def show_about_dialog(self) -> None:
        QMessageBox.information(
            self,
            "About",
            "Area Scanner Python Visualizer\n\n"
            "這一版重點：\n"
            "1. 真的接上 serial / parser\n"
            "2. Viewer 風格拉近 MATLAB X-Y View\n"
            "3. 可以邊畫圖、邊錄製二進位資料",
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
        self.label_frame_number.setText(str(frame.header.frame_number))
        self.label_num_tlvs.setText(str(frame.header.num_tlvs))
        self.label_num_dynamic.setText(str(len(frame.dynamic_points)))
        self.label_num_static.setText(str(len(frame.static_points)))
        self.label_num_targets.setText(str(len(frame.targets)))

        self.viewer.update_from_frame(frame, buffer_frame_count=1)

    def on_worker_error(self, message: str) -> None:
        self.append_log(f"[錯誤] {message}")
        self.label_runtime.setText("Error")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        QMessageBox.warning(self, "Worker Error", message)

    def on_worker_finished(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if self.label_runtime.text() != "Error":
            self.label_runtime.setText("Stopped")

    # ------------------------------------------------------
    # G. 關閉視窗時先安全停止背景工作
    # ------------------------------------------------------
    def closeEvent(self, event) -> None:
        try:
            self.stop_worker()
        finally:
            event.accept()