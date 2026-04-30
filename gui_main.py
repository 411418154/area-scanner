"""
gui_main.py
===========
Python 版 Area Scanner GUI 主視窗。

[本次整理重點]：
1. 移除左側重複的 Start/Stop 按鈕，統一使用上方工具列 (Toolbar) 控制。
2. 新增「同步記錄軌跡資料 (.csv)」功能。
3. 整合 Z 軸高度補償與軌跡顯示設定。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
import csv
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QPlainTextEdit,
    QSpinBox, QStatusBar, QToolBar, QVBoxLayout, QWidget,
)

# 匯入自定義模組
from parser_as import AreaScannerParser, ParsedFrame, parse_packet
from serial_manager import SerialConfig, SerialManager
from visualizer_3d import AreaScanner3DWidget


# ==========================================================
# 1. 執行期設定資料 (RuntimeConfig)
# ==========================================================
@dataclass
class RuntimeConfig:
    # 序列埠設定
    cli_port: str = "COM6"
    data_port: str = "COM5"
    cli_baud: int = 115200
    data_baud: int = 921600
    cfg_file: str = ""

    # 安裝設定
    mounting_height_m: float = 2.0
    elevation_tilt_deg: float = 0.0

    # 區域設定 (Zones)
    enable_zone: bool = True
    critical_start_m: float = 0.0
    critical_end_m: float = 2.0
    warn_start_m: float = 2.0
    warn_end_m: float = 4.0
    projection_time_s: float = 2.0

    view_mode: str = "X-Y View"
    
    # 存檔設定
    record_bin: bool = False  # 儲存原始資料 (.bin)
    record_csv: bool = False  # 儲存軌跡資料 (.csv)

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
# 2. 背景執行緒 (RadarWorker)：負責通訊與存檔
# ==========================================================
class RadarWorker(QThread):
    status_signal = Signal(str)
    log_signal = Signal(str)
    frame_signal = Signal(object)   
    error_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, config: RuntimeConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # 複製一份設定，避免執行中被 GUI 意外修改
        self._config = RuntimeConfig(**config.__dict__)
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        manager = SerialManager(self._config.to_serial_config())
        parser = AreaScannerParser()
        self._running = True
        last_wait_log_ts = 0.0
        
        # --- 準備檔案記錄物件 ---
        bin_file = None
        csv_file = None

        try:
            log_dir = Path("logs")
            if self._config.record_bin or self._config.record_csv:
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            if self._config.record_bin:
                bin_path = log_dir / f'tlv_raw_{ts}.bin'
                bin_file = open(bin_path, 'wb')
                self.log_signal.emit(f"[錄製] 開始存儲二進位檔：{bin_path.name}")

            if self._config.record_csv:
                csv_path = log_dir / f'target_tracks_{ts}.csv'
                csv_file = open(csv_path, 'w', encoding='utf-8')
                csv_file.write("Timestamp,Frame,TID,X,Y,Z,VX,VY,VZ\n")
                self.log_signal.emit(f"[錄製] 開始記錄軌跡 CSV：{csv_path.name}")
                
        except Exception as e:
            self.log_signal.emit(f"[警告] 無法建立檔案: {e}")

        try:
            self.status_signal.emit("正在開啟序列埠...")
            manager.open_ports()
            manager.clear_buffers()

            if not self._config.cfg_file:
                raise ValueError("未選擇 CFG 檔案。")

            self.status_signal.emit("正在傳送 CFG...")
            for line in manager.send_cfg_file(self._config.cfg_file):
                self.log_signal.emit(line)

            self.status_signal.emit("運作中：接收資料中...")

            while self._running:
                raw = manager.read_data_once(max_bytes=8192)
                if not raw:
                    self.msleep(5)
                    continue
                
                # 1. 寫入原始資料 (.bin)
                if bin_file:
                    bin_file.write(raw)
                    bin_file.flush()

                # 2. 解析封包
                parser.append_data(raw)
                packets = parser.extract_packets()

                for packet in packets:
                    if not self._running: break
                    try:
                        frame: ParsedFrame = parse_packet(packet)
                    except:
                        continue

                    # 3. 寫入軌跡資料 (.csv)
                    if csv_file and frame.targets:
                        cur_ts = time.time()
                        for t in frame.targets:
                            csv_file.write(f"{cur_ts:.3f},{frame.header.frame_number},"
                                           f"{t.tid},{t.pos_x:.4f},{t.pos_y:.4f},{t.pos_z:.4f},"
                                           f"{t.vel_x:.4f},{t.vel_y:.4f},{t.vel_z:.4f}\n")
                        csv_file.flush()

                    self.frame_signal.emit(frame)

        except Exception as exc:
            self.error_signal.emit(str(exc))
        finally:
            # 安全關閉
            try: manager.send_cli_command("sensorStop", read_response=False)
            except: pass
            manager.close_ports()
            
            if bin_file: bin_file.close()
            if csv_file: csv_file.close()

            self.status_signal.emit("已停止。")
            self.finished_signal.emit()
class PlaybackWorker(QThread):
    status_signal = Signal(str)
    log_signal = Signal(str)
    frame_signal = Signal(object)
    error_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, filepath : str, parent = None):
        super().__init__(parent)
        self.filepath = filepath
        self._running = False

    def stop(self) -> None:
        self._running = False
    
    def run(self) -> None:
        self._running = True
        path = Path(self.filepath)
        self.log_signal.emit(f"[重播] 開始讀取檔案:{path.name}")
        self.status_signal.emit(f"重播中:{path.name}")

        try:
            if path.suffix.lower() == '.bin':
                self._play_bin(path)
            elif path.suffix.lower() == '.csv':
                self._play_csv(path)
            else:
                self.error_signal.emit("不支援的格式,請選擇.bin或.csv")
        except Exception as e:
            self.error_signal.emit(f"重播發生錯誤{e}")
        finally:
            self.status_signal.emit("重播已結束！")
            self.finished_signal.emit()

    def _play_bin(self, path : Path) -> None:
        parser = AreaScannerParser()
        with open(path,'rb') as f:
            while self._running:
                chunk = f.read(4096)
                if not chunk: break

                parser.append_data(chunk)
                for pkt in parser.extract_packets():
                    if not self._running:break
                    try:
                        frame = parse_packet(pkt)
                        self.frame_signal.emit(frame)
                        self.msleep(50)
                    except: pass
    def _play_csv(self, path : Path) -> None:
        from parser_as import ParsedFrame, FrameHeader, TargetRecord
        import collections

        frames_data = collections.defaultdict(list)
        with open(path, 'r',encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                frames_data[int(row['Frame'])].append(row)

        for frame_idx in sorted(frames_data.keys()):
            if not self._running:break
            header = FrameHeader(b'',0,0,0,frame_idx,0,0,1,0,0)
            frame = ParsedFrame(header=header)

            for r in frames_data[frame_idx]:
                t = TargetRecord(tid=int(r['TID']),pos_x=float(r['X']),pos_y=float(r['Y']),pos_z=float(r['Z']),
                                 vel_x=float(r['VX']),vel_y=float(r['VY']),vel_z=float(r['VZ']),acc_x=0,acc_y=0,acc_z=0)
                frame.targets.append(t)
            self.frame_signal.emit(frame)
            self.msleep(50)






# ==========================================================
# 3. 主視窗 (AreaScannerMainWindow)
# ==========================================================
class AreaScannerMainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self.config = RuntimeConfig()
        self.worker: Optional[RadarWorker] = None

        self.setWindowTitle("Area Scanner Python Visualizer")
        self.resize(1550, 900)

        # UI 組建
        self._build_actions()
        self._build_toolbar()
        self._build_status_bar()
        self._build_central_ui()
        
        # 初始化
        self._connect_signals()
        self._apply_default_values()
        self.refresh_ports()
        self._apply_viewer_config()

        self.append_log("[系統] GUI 初始化完成。")

    # ------------------------------------------------------
    # A. UI 組件建立 (拆解邏輯)
    # ------------------------------------------------------
    def _build_actions(self) -> None:
        """建立選單/工具列的動作按鈕"""
        self.action_open_cfg = QAction("載入 CFG", self)
        self.action_start = QAction("開始 (Start)", self)
        self.action_stop = QAction("停止 (Stop)", self)
        self.action_stop.setEnabled(False)
        self.action_refresh_ports = QAction("重整 COM", self)
        self.action_about = QAction("關於", self)
        self.action_clear = QAction("淨空畫面 (Clear)", self)

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
        toolbar.addAction(self.action_clear) # [新增] 加入工具列
        toolbar.addAction(self.action_about)

    def _build_status_bar(self) -> None:
        self.status_label = QLabel("就緒")
        self.statusBar().addPermanentWidget(self.status_label)

    def _build_central_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)

        # 左側：控制面板 | 右側：顯示區
        self.left_panel = self._build_left_panel()
        self.right_panel = self._build_right_panel()

        layout.addWidget(self.left_panel, 0)
        layout.addWidget(self.right_panel, 1)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(450)
        layout = QVBoxLayout(panel)

        # 分組區塊
        layout.addWidget(self._create_serial_group())
        layout.addWidget(self._create_cfg_group())
        layout.addWidget(self._create_sensor_group())
        layout.addWidget(self._create_zone_group())
        layout.addWidget(self._create_record_group()) # 原 Run Group
        layout.addWidget(self._create_replay_group())
        layout.addStretch(1)
        return panel

    def _create_serial_group(self) -> QGroupBox:
        group = QGroupBox("COM / Serial Settings")
        layout = QGridLayout(group)
        self.combo_cli_port = QComboBox(); self.combo_cli_port.setEditable(True)
        self.combo_data_port = QComboBox(); self.combo_data_port.setEditable(True)
        self.spin_cli_baud = QSpinBox(); self.spin_cli_baud.setRange(9600, 3000000)
        self.spin_data_baud = QSpinBox(); self.spin_data_baud.setRange(9600, 3000000)
        self.btn_test_conn = QPushButton("連線測試")

        layout.addWidget(QLabel("CLI Port"), 0, 0); layout.addWidget(self.combo_cli_port, 0, 1)
        layout.addWidget(QLabel("DATA Port"), 1, 0); layout.addWidget(self.combo_data_port, 1, 1)
        layout.addWidget(QLabel("CLI Baud"), 2, 0); layout.addWidget(self.spin_cli_baud, 2, 1)
        layout.addWidget(QLabel("DATA Baud"), 3, 0); layout.addWidget(self.spin_data_baud, 3, 1)
        layout.addWidget(self.btn_test_conn, 4, 0, 1, 2)
        return group

    def _create_cfg_group(self) -> QGroupBox:
        group = QGroupBox("CFG File")
        layout = QHBoxLayout(group)
        self.edit_cfg_path = QLineEdit()
        self.btn_browse_cfg = QPushButton("...")
        layout.addWidget(self.edit_cfg_path); layout.addWidget(self.btn_browse_cfg)
        return group

    def _create_sensor_group(self) -> QGroupBox:
        group = QGroupBox("Sensor Mounting")
        layout = QFormLayout(group)
        self.spin_height = QDoubleSpinBox(); self.spin_height.setRange(0, 10); self.spin_height.setSingleStep(0.1)
        self.spin_tilt = QDoubleSpinBox(); self.spin_tilt.setRange(-90, 90)
        layout.addRow("Mounting Height (m)", self.spin_height)
        layout.addRow("Elevation Tilt (deg)", self.spin_tilt)
        return group

    def _create_zone_group(self) -> QGroupBox:
        group = QGroupBox("Viewer / Zones")
        layout = QFormLayout(group)
        self.combo_view = QComboBox(); self.combo_view.addItems(["X-Y View", "3D View"])
        self.check_zone = QCheckBox("Enable Zones")
        self.spin_crit = QDoubleSpinBox(); self.spin_warn = QDoubleSpinBox()
        layout.addRow("View Mode", self.combo_view)
        layout.addRow(self.check_zone)
        layout.addRow("Critical Range (m)", self.spin_crit)
        layout.addRow("Warning Range (m)", self.spin_warn)
        return group

    def _create_record_group(self) -> QGroupBox:
        """[修改] 整合錄製選項，移除 Start/Stop 按鈕"""
        group = QGroupBox("Options")
        layout = QVBoxLayout(group)
        self.check_rec_bin = QCheckBox("同步儲存 raw data (.bin)")
        self.check_rec_csv = QCheckBox("同步記錄軌跡資料 (.csv)")
        self.check_show_traj = QCheckBox("顯示即時追蹤軌跡 (Trajectory)") # [新增]
        self.check_show_traj.setChecked(True)
        
        layout.addWidget(self.check_rec_bin)
        layout.addWidget(self.check_rec_csv)
        layout.addWidget(self.check_show_traj)
        return group
    
    def _create_replay_group(self) -> QGroupBox:
        group = QGroupBox("Replay")
        layout = QVBoxLayout(group)

        self.edit_replay_file = QLineEdit()
        self.edit_replay_file.setPlaceholderText("選擇.bin或.csv檔案...")
        self.btn_browse_replay = QPushButton("瀏覽..")
        self.btn_start_replay = QPushButton("開始重播")

        h_layout = QHBoxLayout()
        h_layout.addWidget(self.edit_replay_file)
        h_layout.addWidget(self.btn_browse_replay)

        layout.addLayout(h_layout)
        layout.addWidget(self.btn_start_replay)
        return group




    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.viewer = AreaScanner3DWidget()
        self.text_log = QPlainTextEdit(); self.text_log.setReadOnly(True)
        layout.addWidget(self.viewer, 5)
        layout.addWidget(QLabel("Log Output"), 0)
        layout.addWidget(self.text_log, 1)
        return panel

    # ------------------------------------------------------
    # B. 設定與訊號處理
    # ------------------------------------------------------
    def _connect_signals(self) -> None:
        # 工具列訊號
        self.action_start.triggered.connect(self.start_worker)
        self.action_stop.triggered.connect(self.stop_worker)
        self.action_open_cfg.triggered.connect(self.browse_cfg)
        self.action_refresh_ports.triggered.connect(self.refresh_ports)
        self.action_clear.triggered.connect(self.viewer.clear) # 連接按鈕到清除功能
        self.check_show_traj.toggled.connect(self._apply_viewer_config)
        self.btn_browse_replay.clicked.connect(self.browse_replay_file)
        self.btn_start_replay.clicked.connect(self.start_playback)
        
        # 數值連動
        self.btn_browse_cfg.clicked.connect(self.browse_cfg)
        self.btn_test_conn.clicked.connect(self.test_connection)
        self.combo_view.currentTextChanged.connect(self._apply_viewer_config)
        self.spin_height.valueChanged.connect(self._apply_viewer_config)
        self.spin_crit.valueChanged.connect(self._apply_viewer_config)
        self.spin_warn.valueChanged.connect(self._apply_viewer_config)
        self.check_zone.toggled.connect(self._apply_viewer_config)

    def _apply_default_values(self) -> None:
        self.spin_cli_baud.setValue(self.config.cli_baud)
        self.spin_data_baud.setValue(self.config.data_baud)
        self.spin_height.setValue(self.config.mounting_height_m)
        self.spin_crit.setValue(self.config.critical_end_m)
        self.spin_warn.setValue(self.config.warn_end_m)
        self.check_zone.setChecked(self.config.enable_zone)

    def _sync_config(self) -> None:
        """將 UI 上的數值同步到 config 物件中"""
        self.config.cli_port = self.combo_cli_port.currentText()
        self.config.data_port = self.combo_data_port.currentText()
        self.config.cli_baud = self.spin_cli_baud.value()
        self.config.data_baud = self.spin_data_baud.value()
        self.config.cfg_file = self.edit_cfg_path.text()
        self.config.mounting_height_m = self.spin_height.value()
        self.config.elevation_tilt_deg = self.spin_tilt.value()
        self.config.view_mode = self.combo_view.currentText()
        self.config.enable_zone = self.check_zone.isChecked()
        self.config.critical_end_m = self.spin_crit.value()
        self.config.warn_end_m = self.spin_warn.value()
        self.config.record_bin = self.check_rec_bin.isChecked()
        self.config.record_csv = self.check_rec_csv.isChecked()

    def _apply_viewer_config(self) -> None:
        self._sync_config()
        self.viewer.set_trajectory_enabled(self.check_show_traj.isChecked())
        self.viewer.set_view_mode(self.config.view_mode)
        self.viewer.set_mount_config(self.config.mounting_height_m, self.config.elevation_tilt_deg)
        self.viewer.set_zone_config(
            enable_zones=self.config.enable_zone,
            critical_end_m=self.config.critical_end_m,
            warn_end_m=self.config.warn_end_m
        )

    # ------------------------------------------------------
    # C. 功能操作
    # ------------------------------------------------------
    def browse_cfg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇 CFG", "", "CFG (*.cfg)")
        if path: self.edit_cfg_path.setText(path)

    def refresh_ports(self) -> None:
        self.combo_cli_port.clear(); self.combo_data_port.clear()
        ports = SerialManager.list_available_ports()
        for p in ports:
            self.combo_cli_port.addItem(p.device)
            self.combo_data_port.addItem(p.device)
        self.append_log("[系統] 串口清單已更新。")

    def test_connection(self) -> None:
        self._sync_config()
        manager = SerialManager(self.config.to_serial_config())
        try:
            logs = manager.test_basic_connection()
            for l in logs: self.append_log(f"[測試] {l}")
            QMessageBox.information(self, "測試", "連線測試完成，請查看 Log。")
        except Exception as e:
            QMessageBox.warning(self, "測試失敗", str(e))
        finally:
            manager.close_ports()

    def start_worker(self) -> None:
        if self.worker and self.worker.isRunning(): return
        self._apply_viewer_config()
        if not self.config.cfg_file:
            QMessageBox.warning(self, "錯誤", "請先選擇 CFG 檔案。")
            return

        self.worker = RadarWorker(self.config, self)
        self.worker.log_signal.connect(self.append_log)
        self.worker.status_signal.connect(self.update_status)
        self.worker.frame_signal.connect(self.on_new_frame)
        self.worker.error_signal.connect(self.on_worker_error)
        self.worker.finished_signal.connect(self.on_worker_finished)
        
        self.worker.start()
        self.action_start.setEnabled(False)
        self.action_stop.setEnabled(True)
        self.status_label.setText("運行中")
    def browse_replay_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self,"選擇重播檔案","logs","Replay Files (*.bin *.csv)")
        if path:
            self.edit_replay_file.setText(path)

    def start_playback(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "警告","請先停止即時連線")
            return
        filepath = self.edit_replay_file.text().strip()
        if not filepath:
            QMessageBox.warning(self,"錯誤","請選擇要開啟的")
            return
        
        self.playback_worker = PlaybackWorker(filepath, self)
        self.playback_worker.log_signal.connect(self.append_log)
        self.playback_worker.status_signal.connect(self.update_status)
        self.playback_worker.frame_signal.connect(self.on_new_frame)
        self.playback_worker.error_signal.connect(self.on_worker_error)
        self.playback_worker.finished_signal.connect(self.on_worker_finished)

        self.viewer.clear()
        self.playback_worker.start()

        self.action_start.setEnabled(False)
        self.action_stop.setEnabled(True)
        self.btn_start_replay.setEnabled(False)

        

    def stop_worker(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(2000)
            self.worker = None
        
        if hasattr(self, 'playback_worker') and self.playback_worker and self.playback_worker.isRunning():
            self.playback_worker.stop()
            self.playback_worker.wait(2000)
            self.playback_worker = None
        self.on_worker_finished()
        self.btn_start_replay.setEnabled(True)

    def on_new_frame(self, frame: ParsedFrame) -> None:
        self.viewer.update_from_frame(frame)

    def on_worker_error(self, msg: str) -> None:
        self.append_log(f"[錯誤] {msg}")
        QMessageBox.critical(self, "背景錯誤", msg)
        self.on_worker_finished()

    def on_worker_finished(self) -> None:
        self.action_start.setEnabled(True)
        self.action_stop.setEnabled(False)
        if hasattr(self, 'btn_start_replay'):
            self.btn_start_replay.setEnabled(True)
    
        self.status_label.setText("已停止")

    def append_log(self, text: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.text_log.appendPlainText(f"[{ts}] {text}")

    def update_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def closeEvent(self, event) -> None:
        self.stop_worker()
        event.accept()