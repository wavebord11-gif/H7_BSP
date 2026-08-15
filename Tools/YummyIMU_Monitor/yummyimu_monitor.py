"""H7 BMI088 YummyIMU 上位机。

文件变更记录：
2026-08-15：创建 Python/Tk 桌面版本，接入姿态、加速度、角速度、静止判断和 CSV。
2026-08-15：数据接收保持 200Hz，界面限速 30Hz，并限制异常日志刷新频率。
2026-08-15：静止计时增加数据新鲜度检查，断流后立即停止计时。
2026-08-15：参考 YummyIMU Studio 重构浅色工作台、侧边导航与分区页面。
2026-08-15：新增与参考上位机一致的自绘标题栏和窗口控制。
"""
import csv
import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (AttributeError, OSError):
    try: ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError): pass

import math
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import serial
from serial.tools import list_ports

BG, PANEL, PANEL2, LINE = "#f3f6fb", "#ffffff", "#f6f8fc", "#dce4f1"
TEXT, MUTED, CYAN, AMBER, RED = "#13233a", "#71809a", "#426de6", "#e9a52f", "#ef5b63"
AXIS = ("#2fbb82", "#ef5964", "#4275e8")


def resource_path(relative_path):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


class H7Monitor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("H7 YummyIMU 上位机")
        self._window_icon = None
        icon_png = resource_path(os.path.join("assets", "app_icon.png"))
        icon_ico = resource_path(os.path.join("assets", "app_icon.ico"))
        try:
            self._window_icon = tk.PhotoImage(file=icon_png)
            self.iconphoto(True, self._window_icon)
            self.iconbitmap(icon_ico)
        except tk.TclError:
            pass
        self.geometry("1400x900")
        self.minsize(1120, 720)
        self.overrideredirect(True)
        self.configure(bg=BG)
        self._maximized = False
        self._normal_geometry = "1400x900"
        self._drag_origin = (0, 0)
        self.serial = None
        self.running = False
        self.rx_queue = queue.Queue()
        self.rows = []
        self.recording = False
        self.samples = self.errors = self.packets = self.last_packets = 0
        self.last_still = False
        self.still_since = None
        self.rest_gyro_deviation = 0.0
        self.rest_accel_deviation = 0.0
        self.temperature = 0.0
        self.last_sensor_time = 0.0
        self.ui_dirty = True
        self.last_malformed_log = 0.0
        self.angles = [0.0, 0.0, 0.0]
        self.zero = [0.0, 0.0, 0.0]
        self.accel = [0.0, 0.0, 0.0]
        self.gyro = [0.0, 0.0, 0.0]
        self.acc_history = [[], [], []]
        self.gyro_history = [[], [], []]
        self._style()
        self._build()
        self.scan_ports()
        self.after(15, self._poll_queue)
        self.after(33, self._render_ui)
        self.after(1000, self._tick_hz)
        self.protocol("WM_DELETE_WINDOW", self.close_app)

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=TEXT,
                        arrowcolor=CYAN, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)

    def _button(self, parent, text, command, accent=False, width=None):
        return tk.Button(parent, text=text, command=command, width=width, cursor="hand2",
                         bg=CYAN if accent else PANEL, fg="#ffffff" if accent else TEXT,
                         activebackground="#315bc8" if accent else "#edf2fb",
                         activeforeground="#ffffff" if accent else CYAN,
                         relief="flat", bd=0, padx=15, pady=9,
                         font=("Microsoft YaHei UI", 10, "bold" if accent else "normal"))

    def _panel(self, parent):
        return tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)

    def _nav_button(self, parent, key, text):
        button = tk.Button(parent, text=text, anchor="w", cursor="hand2", relief="flat", bd=0,
                           bg=PANEL, fg=MUTED, activebackground="#edf2ff", activeforeground=CYAN,
                           padx=20, pady=13, font=("Microsoft YaHei UI", 11),
                           command=lambda: self._show_page(key))
        button.pack(fill="x", padx=12, pady=3)
        self.nav_buttons[key] = button

    def _show_page(self, key):
        for page in self.pages.values(): page.place_forget()
        self.pages[key].place(x=0, y=0, relwidth=1, relheight=1)
        for name, button in self.nav_buttons.items():
            active = name == key
            button.configure(bg="#eaf0ff" if active else PANEL,
                             fg=CYAN if active else MUTED,
                             font=("Microsoft YaHei UI", 11, "bold" if active else "normal"))

    def _start_move(self, event):
        if not self._maximized:
            self._drag_origin = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _do_move(self, event):
        if not self._maximized:
            self.geometry(f"+{event.x_root-self._drag_origin[0]}+{event.y_root-self._drag_origin[1]}")

    def _minimize_window(self):
        self.overrideredirect(False)
        self.iconify()
        self.bind("<Map>", self._restore_custom_frame, add="+")

    def _restore_custom_frame(self, _event=None):
        self.after(10, lambda: self.overrideredirect(True))

    def _toggle_maximize(self):
        if self._maximized:
            self.geometry(self._normal_geometry)
            self.maximize_btn.configure(text="□")
        else:
            self._normal_geometry = self.geometry()
            self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()-40}+0+0")
            self.maximize_btn.configure(text="❐")
        self._maximized = not self._maximized

    def _window_button(self, parent, text, command, close=False):
        button = tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                           bg=PANEL, fg=TEXT, activebackground=RED if close else "#edf1f7",
                           activeforeground="#ffffff" if close else TEXT,
                           width=4, cursor="hand2", font=("Microsoft YaHei UI", 13))
        button.pack(side="left", fill="y")
        return button

    def _build(self):
        windowbar = tk.Frame(self, bg=PANEL, height=58, bd=0, highlightthickness=0)
        windowbar.pack(fill="x"); windowbar.pack_propagate(False)
        windowbar.bind("<ButtonPress-1>", self._start_move)
        windowbar.bind("<B1-Motion>", self._do_move)
        windowbar.bind("<Double-Button-1>", lambda _event: self._toggle_maximize())
        brand = tk.Frame(windowbar, bg=PANEL); brand.pack(side="left", padx=28, fill="y")
        brand.bind("<ButtonPress-1>", self._start_move); brand.bind("<B1-Motion>", self._do_move)
        brand.bind("<Double-Button-1>", lambda _event: self._toggle_maximize())
        brand_name = tk.Label(brand, text="H7 IMU Studio", bg=PANEL, fg=TEXT,
                              font=("Bahnschrift SemiBold", 17))
        brand_name.pack(side="left"); brand_name.bind("<ButtonPress-1>", self._start_move); brand_name.bind("<B1-Motion>", self._do_move)
        subtitle = tk.Label(brand, text="BMI088 姿态与传感器工作台", bg=PANEL, fg=MUTED,
                            font=("Microsoft YaHei UI", 10))
        subtitle.pack(side="left", padx=42); subtitle.bind("<ButtonPress-1>", self._start_move); subtitle.bind("<B1-Motion>", self._do_move)
        window_controls = tk.Frame(windowbar, bg=PANEL); window_controls.pack(side="right", fill="y")
        self._window_button(window_controls, "─", self._minimize_window)
        self.maximize_btn = self._window_button(window_controls, "□", self._toggle_maximize)
        self._window_button(window_controls, "×", self.close_app, True)
        self.status = tk.Label(windowbar, text="●  未连接", bg="#f3f6fb", fg=MUTED,
                               padx=18, pady=7, font=("Microsoft YaHei UI", 9))
        self.status.place(relx=0.67, rely=0.5, anchor="center")

        header = tk.Frame(self, bg=PANEL, height=64, bd=0, highlightthickness=0)
        header.pack(fill="x"); header.pack_propagate(False)
        tk.Label(header, text="USB CDC 连接", bg=PANEL, fg=TEXT,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=30)
        conn = tk.Frame(header, bg=PANEL); conn.pack(side="right", padx=26)
        self.port_box = ttk.Combobox(conn, width=26, state="readonly"); self.port_box.pack(side="left", padx=4)
        self.baud_box = ttk.Combobox(conn, width=9, state="readonly", values=("921600", "460800", "115200")); self.baud_box.set("921600"); self.baud_box.pack(side="left", padx=4)
        self._button(conn, "扫描", self.scan_ports).pack(side="left", padx=4)
        self.connect_btn = self._button(conn, "连接", self.toggle_connection, True); self.connect_btn.pack(side="left", padx=4)

        workspace = tk.Frame(self, bg=BG); workspace.pack(fill="both", expand=True)
        sidebar = tk.Frame(workspace, bg=PANEL, width=220, highlightbackground=LINE, highlightthickness=1)
        sidebar.pack(side="left", fill="y"); sidebar.pack_propagate(False)
        tk.Label(sidebar, text="工作台", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei UI", 10)).pack(anchor="w", padx=28, pady=(28, 12))
        self.nav_buttons = {}
        self._nav_button(sidebar, "attitude", "实时姿态")
        self._nav_button(sidebar, "sensors", "传感器数据")
        self._nav_button(sidebar, "record", "采集与记录")
        self._nav_button(sidebar, "device", "设备信息")
        tk.Label(sidebar, text="USB CDC · 200 Hz\nVQF Fusion · BMI088", justify="left",
                 bg=PANEL, fg="#9ba7bb", font=("Bahnschrift", 9)).pack(side="bottom", anchor="w", padx=28, pady=26)

        host = tk.Frame(workspace, bg=BG); host.pack(side="left", fill="both", expand=True, padx=24, pady=20)
        self.pages = {name: tk.Frame(host, bg=BG) for name in ("attitude", "sensors", "record", "device")}

        attitude = self.pages["attitude"]
        tk.Label(attitude, text="实时姿态", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w")
        tk.Label(attitude, text="Pitch / Roll / Yaw 均为度；姿态模型使用右手旋转顺序",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(2, 14))
        angles = tk.Frame(attitude, bg=BG); angles.pack(fill="x", pady=(0, 14))
        self.angle_vars = []
        angle_meta = (("PITCH · 俯仰", "绕 Y 轴"), ("ROLL · 横滚", "绕 X 轴"), ("YAW · 航向", "绕 Z 轴"))
        for i, (name, axis) in enumerate(angle_meta):
            box = self._panel(angles); box.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 6, 0 if i == 2 else 6))
            strip = tk.Frame(box, bg=AXIS[i], width=5); strip.pack(side="left", fill="y", padx=(18, 12), pady=18)
            content = tk.Frame(box, bg=PANEL); content.pack(side="left", fill="both", expand=True, pady=14)
            tk.Label(content, text=name, bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w")
            value = tk.StringVar(value="0.00"); self.angle_vars.append(value)
            tk.Label(content, textvariable=value, bg=PANEL, fg=TEXT, font=("Bahnschrift", 25)).pack(anchor="w", pady=(2, 0))
            tk.Label(content, text=axis, bg=PANEL, fg="#9aa7bb", font=("Microsoft YaHei UI", 9)).pack(anchor="w")

        attitude_body = tk.Frame(attitude, bg=BG); attitude_body.pack(fill="both", expand=True)
        pose = self._panel(attitude_body); pose.pack(side="left", fill="both", expand=True, padx=(0, 7))
        pose_head = tk.Frame(pose, bg=PANEL); pose_head.pack(fill="x", padx=20, pady=(16, 2))
        tk.Label(pose_head, text="三维姿态仪", bg=PANEL, fg=TEXT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        tk.Label(pose_head, text="实时融合姿态", bg="#edf2ff", fg=CYAN,
                 font=("Microsoft YaHei UI", 9), padx=9, pady=4).pack(side="right")
        self.att_canvas = tk.Canvas(pose, bg=PANEL, height=390, bd=0, highlightthickness=0)
        self.att_canvas.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        side = tk.Frame(attitude_body, bg=BG, width=300); side.pack(side="left", fill="y", padx=(7, 0)); side.pack_propagate(False)
        compass = self._panel(side); compass.pack(fill="x")
        tk.Label(compass, text="航向罗盘", bg=PANEL, fg=TEXT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=18, pady=(15, 0))
        self.heading_canvas = tk.Canvas(compass, bg=PANEL, height=205, bd=0, highlightthickness=0)
        self.heading_canvas.pack(fill="x", padx=12, pady=(0, 8))
        state = self._panel(side); state.pack(fill="x", pady=(14, 0))
        tk.Label(state, text="VQF 运动状态", bg=PANEL, fg=TEXT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=18, pady=(15, 8))
        self.still_frame = tk.Frame(state, bg="#f7f9fd"); self.still_frame.pack(fill="x", padx=16, pady=(0, 14))
        self.still_dot = tk.Label(self.still_frame, text="●", bg="#f7f9fd", fg=RED, font=("Arial", 16)); self.still_dot.pack(side="left", padx=10, pady=12)
        self.still_text = tk.StringVar(value="等待连接")
        tk.Label(self.still_frame, textvariable=self.still_text, bg="#f7f9fd", fg=TEXT,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.still_time = tk.StringVar(value="0.0 s")
        tk.Label(self.still_frame, textvariable=self.still_time, bg="#f7f9fd", fg=MUTED,
                 font=("Bahnschrift", 12)).pack(side="right", padx=12)

        sensors = self.pages["sensors"]
        tk.Label(sensors, text="传感器数据", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w")
        tk.Label(sensors, text="原始加速度与VQF校正角速度；曲线显示最近约1.2秒",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(2, 14))
        sensor_body = tk.Frame(sensors, bg=BG); sensor_body.pack(fill="both", expand=True)
        acc = self._sensor_panel(sensor_body, "ACCELEROMETER", "加速度计", "m/s²", True); acc.pack(side="left", fill="both", expand=True, padx=(0, 7))
        gyr = self._sensor_panel(sensor_body, "GYROSCOPE · CORRECTED", "陀螺仪", "rad/s", False); gyr.pack(side="left", fill="both", expand=True, padx=(7, 0))

        record = self.pages["record"]
        tk.Label(record, text="采集与记录", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w")
        tk.Label(record, text="CSV保持原始200Hz数据，界面刷新独立限速",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(2, 14))
        record_body = tk.Frame(record, bg=BG); record_body.pack(fill="both", expand=True)
        controls = self._panel(record_body); controls.pack(side="left", fill="both", expand=True, padx=(0, 7))
        tk.Label(controls, text="采集控制", bg=PANEL, fg=TEXT,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=20, pady=(18, 10))
        actions = tk.Frame(controls, bg=PANEL); actions.pack(fill="x", padx=15)
        self.record_btn = self._button(actions, "开始记录", self.toggle_record, True); self.record_btn.pack(side="left", padx=4)
        self._button(actions, "导出 CSV", self.export_csv).pack(side="left", padx=4)
        self._button(actions, "姿态归零", self.zero_attitude).pack(side="left", padx=4)
        self._button(actions, "查询状态", lambda: self.send("STATUS IMU")).pack(side="left", padx=4)
        self.metrics = {k: tk.StringVar(value=v) for k, v in (("hz", "0 Hz"), ("samples", "0"), ("temp", "— °C"), ("errors", "0"))}
        metric_row = tk.Frame(controls, bg=PANEL); metric_row.pack(fill="x", padx=20, pady=24)
        for label, key in (("接收频率", "hz"), ("样本数", "samples"), ("温度", "temp"), ("解析异常", "errors")):
            m = tk.Frame(metric_row, bg=PANEL2, highlightbackground=LINE, highlightthickness=1); m.pack(side="left", fill="x", expand=True, padx=4)
            tk.Label(m, text=label, bg=PANEL2, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=12, pady=(12, 2))
            tk.Label(m, textvariable=self.metrics[key], bg=PANEL2, fg=TEXT, font=("Bahnschrift", 17)).pack(anchor="w", padx=12, pady=(0, 12))

        terminal = self._panel(record_body); terminal.pack(side="left", fill="both", expand=True, padx=(7, 0))
        tk.Label(terminal, text="协议终端", bg=PANEL, fg=TEXT,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=20, pady=(18, 10))
        self.log_box = tk.Text(terminal, height=18, bg="#192337", fg="#cbd6e9", insertbackground="#ffffff",
                               relief="flat", font=("Consolas", 9), padx=12, pady=10)
        self.log_box.pack(fill="both", expand=True, padx=16, pady=(0, 8)); self.log("等待连接 H7 USB CDC…")
        command = tk.Frame(terminal, bg=PANEL); command.pack(fill="x", padx=16, pady=(0, 16))
        self.command_entry = tk.Entry(command, bg=PANEL2, fg=TEXT, insertbackground=CYAN,
                                      relief="flat", highlightbackground=LINE, highlightthickness=1,
                                      font=("Consolas", 10)); self.command_entry.pack(side="left", fill="x", expand=True, ipady=8)
        self.command_entry.bind("<Return>", lambda _: self.send_entry())
        self._button(command, "发送", self.send_entry, True).pack(side="right", padx=(6, 0))

        device = self.pages["device"]
        tk.Label(device, text="设备信息", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w")
        tk.Label(device, text="当前固件能力与协议兼容范围",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(2, 14))
        info = self._panel(device); info.pack(fill="x")
        for label, value in (("设备", "DM MC02H7 / STM32H723"), ("惯性器件", "BMI088 单IMU"),
                             ("姿态算法", "VQF"), ("连接方式", "USB Device CDC"),
                             ("协议模式", "YummyIMU USE / DEBUG / BINARY"),
                             ("扩展遥测", "加速度、校正角速度、静止判断、温度")):
            row = tk.Frame(info, bg=PANEL); row.pack(fill="x", padx=24, pady=9)
            tk.Label(row, text=label, width=14, anchor="w", bg=PANEL, fg=MUTED,
                     font=("Microsoft YaHei UI", 10)).pack(side="left")
            tk.Label(row, text=value, anchor="w", bg=PANEL, fg=TEXT,
                     font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")

        self._show_page("attitude")
        self.after(50, self.draw_attitude)

    def _sensor_panel(self, parent, english, chinese, unit, accel):
        panel = self._panel(parent)
        head = tk.Frame(panel, bg=PANEL); head.pack(fill="x", padx=18, pady=(14, 4))
        left = tk.Frame(head, bg=PANEL); left.pack(side="left")
        tk.Label(left, text=english, bg=PANEL, fg=CYAN, font=("Bahnschrift", 8)).pack(anchor="w")
        tk.Label(left, text=chinese, bg=PANEL, fg=TEXT, font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        tk.Label(head, text=unit, bg=PANEL, fg=MUTED, font=("Bahnschrift", 10)).pack(side="right")
        values = tk.Frame(panel, bg=PANEL); values.pack(fill="x", padx=14, pady=5)
        variables = []
        for i, name in enumerate("XYZ"):
            box = tk.Frame(values, bg=PANEL2, highlightbackground=AXIS[i], highlightthickness=1); box.pack(side="left", fill="x", expand=True, padx=4)
            var = tk.StringVar(value="0.000" if accel else "0.00000"); variables.append(var)
            tk.Label(box, text=name, bg=PANEL2, fg=AXIS[i], font=("Bahnschrift", 10, "bold")).pack(side="left", padx=9, pady=10)
            tk.Label(box, textvariable=var, bg=PANEL2, fg=TEXT, font=("Bahnschrift", 13)).pack(side="right", padx=8)
        canvas = tk.Canvas(panel, height=320, bg="#fbfcff", bd=0, highlightbackground=LINE, highlightthickness=1); canvas.pack(fill="both", expand=True, padx=18, pady=8)
        footer = tk.StringVar(value="模长 0.000")
        tk.Label(panel, textvariable=footer, bg=PANEL, fg=MUTED, font=("Bahnschrift", 9)).pack(anchor="w", padx=18, pady=(0, 10))
        if accel: self.acc_vars, self.acc_canvas, self.acc_footer = variables, canvas, footer
        else: self.gyro_vars, self.gyro_canvas, self.gyro_footer = variables, canvas, footer
        return panel

    def scan_ports(self):
        ports = list(list_ports.comports())
        self.port_map = {f"{p.device} · {p.description}": p.device for p in ports}
        self.port_box["values"] = tuple(self.port_map)
        if ports: self.port_box.current(0)
        self.log(f"扫描到 {len(ports)} 个串口")

    def toggle_connection(self):
        if self.running: self.disconnect(); return
        display = self.port_box.get()
        if display not in self.port_map: messagebox.showwarning("未选择串口", "请先扫描并选择串口。", parent=self); return
        try:
            while not self.rx_queue.empty():
                try: self.rx_queue.get_nowait()
                except queue.Empty: break
            self.serial = serial.Serial(self.port_map[display], int(self.baud_box.get()), timeout=0.1)
            self.running = True
            threading.Thread(target=self._reader, daemon=True).start()
            self.connect_btn.configure(text="断开", bg=PANEL2, fg=TEXT)
            self.status.configure(text="●  实时连接", bg="#e9f8f2", fg="#16875d")
            self.log("已连接，切换 DEBUG 模式")
            self.send("MODE DEBUG")
            self.after(250, lambda: self.send("STATUS IMU"))
        except Exception as exc: messagebox.showerror("连接失败", str(exc), parent=self)

    def disconnect(self):
        self.running = False
        if self.serial:
            try: self.serial.close()
            except Exception: pass
        self.serial = None
        self.last_still = False
        self.still_since = None
        self.last_sensor_time = 0.0
        self.ui_dirty = True
        self.connect_btn.configure(text="连接", bg=CYAN, fg="#ffffff")
        self.status.configure(text="●  未连接", bg="#f3f6fb", fg=MUTED)

    def _reader(self):
        while self.running and self.serial:
            try:
                line = self.serial.readline()
                if line: self.rx_queue.put(line.decode("ascii", errors="replace").strip())
            except Exception as exc:
                self.rx_queue.put(("ERROR", str(exc))); break

    def _poll_queue(self):
        for _ in range(100):
            try: item = self.rx_queue.get_nowait()
            except queue.Empty: break
            if isinstance(item, tuple): self.log("串口错误：" + item[1]); self.errors += 1; self.disconnect()
            else: self.parse_line(item)
        self.after(15, self._poll_queue)

    def parse_line(self, line):
        if re.fullmatch(r"[-+\d.]+(?:,[-+\d.]+){8}", line):
            try:
                v = [float(x) for x in line.split(",")]
                self.angles = [v[0]-self.zero[0], v[1]-self.zero[1], v[2]-self.zero[2]]
                self.ui_dirty = True
            except ValueError: self.errors += 1
            return
        if line.startswith("SENSOR,"):
            try:
                v = [float(x) for x in line.split(",")[1:]]
                if len(v) < 10: raise ValueError("字段不足")
                self.accel, self.gyro = v[:3], v[3:6]
                still, gdev, adev, temp = bool(int(v[6])), v[7], v[8], v[9]
                self.rest_gyro_deviation = gdev
                self.rest_accel_deviation = adev
                self.temperature = temp
                self._set_still(still, time.monotonic())
                self._append_history(self.acc_history, self.accel); self._append_history(self.gyro_history, self.gyro)
                self.samples += 1; self.packets += 1; self.ui_dirty = True
                if self.recording: self.rows.append([datetime.now().isoformat(timespec="milliseconds"), *self.accel, *self.gyro, int(still), gdev, adev, temp])
            except (ValueError, IndexError): self.errors += 1
            return
        if line and (line.startswith("SENSOR") or re.fullmatch(r"[,+\-.\d]+", line)):
            self.errors += 1
            now = time.monotonic()
            if now - self.last_malformed_log >= 1.0:
                self.log("RX 数据帧格式异常（重复日志已限速）")
                self.last_malformed_log = now
            return
        if line: self.log("RX " + line)

    def _set_still(self, still, received_at):
        data_was_stale = self.last_sensor_time != 0.0 and received_at - self.last_sensor_time > 0.5
        if still and (not self.last_still or self.still_since is None or data_was_stale):
            self.still_since = received_at
        if not still:
            self.still_since = None
        self.last_still = still
        self.last_sensor_time = received_at

    def _render_ui(self):
        """将高频数据降采样到约 30Hz 刷新，接收与 CSV 仍保持原始频率。"""
        now = time.monotonic()
        data_fresh = self.running and self.last_sensor_time != 0.0 and now - self.last_sensor_time <= 0.5
        if self.ui_dirty:
            for var, value in zip(self.angle_vars, self.angles): var.set(f"{value:.2f}")
            for var, value in zip(self.acc_vars, self.accel): var.set(f"{value:.4f}")
            for var, value in zip(self.gyro_vars, self.gyro): var.set(f"{value:.5f}")
            self.acc_footer.set(
                f"模长 {math.sqrt(sum(x*x for x in self.accel)):.4f}  ·  静止偏差 {self.rest_accel_deviation:.3f}")
            self.gyro_footer.set(
                f"模长 {math.sqrt(sum(x*x for x in self.gyro)):.5f}  ·  静止偏差 {self.rest_gyro_deviation:.3f}")
            self.metrics["temp"].set(f"{self.temperature:.1f} °C")
            self.metrics["samples"].set(str(self.samples))
            self.draw_attitude()
            self.draw_heading()
            self.draw_chart(self.acc_canvas, self.acc_history)
            self.draw_chart(self.gyro_canvas, self.gyro_history)
            self.ui_dirty = False
        if not data_fresh:
            self.still_dot.configure(fg=RED)
            self.still_text.set("数据中断" if self.running else "等待连接")
            if not self.running:
                self.still_time.set("0.0 s")
        elif self.last_still and self.still_since:
            self.still_dot.configure(fg=CYAN)
            self.still_text.set("静止确认")
            self.still_time.set(f"{now-self.still_since:.1f} s")
        else:
            self.still_dot.configure(fg=AMBER)
            self.still_text.set("运动中")
            self.still_time.set("0.0 s")
        self.after(33, self._render_ui)

    @staticmethod
    def _append_history(history, values):
        for series, value in zip(history, values):
            series.append(value)
            if len(series) > 240: del series[0]

    def draw_chart(self, canvas, history):
        canvas.update_idletasks(); w, h = max(canvas.winfo_width(), 10), max(canvas.winfo_height(), 10)
        canvas.delete("all")
        for i in range(1, 4): canvas.create_line(0, h*i/4, w, h*i/4, fill="#173039")
        maximum = max([abs(v) for s in history for v in s] + [0.001])
        for color, series in zip(AXIS, history):
            if len(series) > 1:
                points = []
                for i, value in enumerate(series): points += [i/239*w, h/2-value/maximum*h*.44]
                canvas.create_line(*points, fill=color, width=2)

    def draw_attitude(self):
        c = self.att_canvas; c.update_idletasks(); w, h = max(c.winfo_width(), 300), max(c.winfo_height(), 200); c.delete("all")
        p, r, y = [math.radians(a) for a in self.angles]
        vertices = [(-1,-.55,-.35),(1,-.55,-.35),(1,.55,-.35),(-1,.55,-.35),(-1,-.55,.35),(1,-.55,.35),(1,.55,.35),(-1,.55,.35)]
        def rotate(v):
            x, yy, z = v
            x, yy = x*math.cos(y)-yy*math.sin(y), x*math.sin(y)+yy*math.cos(y)
            yy, z = yy*math.cos(p)-z*math.sin(p), yy*math.sin(p)+z*math.cos(p)
            x, z = x*math.cos(r)+z*math.sin(r), -x*math.sin(r)+z*math.cos(r)
            return w/2+x*80, h/2+yy*70-z*35
        pts = [rotate(v) for v in vertices]
        for face in ((0,1,2,3),(4,5,6,7),(0,1,5,4),(2,3,7,6),(1,2,6,5),(0,3,7,4)):
            c.create_polygon(*sum(([pts[i][0],pts[i][1]] for i in face),[]), fill="#dfe6f3", outline="#7788a6", width=1)
        c.create_line(w/2, h/2, w/2+115, h/2, fill=AXIS[0], width=3, arrow="last")
        c.create_line(w/2, h/2, w/2, h/2-110, fill=AXIS[2], width=3, arrow="last")
        c.create_text(28, h-24, text="X", fill=AXIS[0], font=("Bahnschrift", 11, "bold")); c.create_text(55, h-24, text="Y", fill=AXIS[1], font=("Bahnschrift", 11, "bold")); c.create_text(82, h-24, text="Z", fill=AXIS[2], font=("Bahnschrift", 11, "bold"))

    def draw_heading(self):
        c = self.heading_canvas; c.update_idletasks(); w, h = max(c.winfo_width(), 180), max(c.winfo_height(), 160); c.delete("all")
        cx, cy, radius = w / 2, h / 2 + 5, min(w, h) * 0.36
        c.create_oval(cx-radius, cy-radius, cx+radius, cy+radius, outline=LINE, width=2)
        for degree in range(0, 360, 30):
            angle = math.radians(degree - 90)
            inner = radius - (9 if degree % 90 == 0 else 5)
            c.create_line(cx+inner*math.cos(angle), cy+inner*math.sin(angle),
                          cx+radius*math.cos(angle), cy+radius*math.sin(angle), fill="#a8b5ca")
        for label, degree, color in (("N", 0, RED), ("E", 90, MUTED), ("S", 180, MUTED), ("W", 270, MUTED)):
            angle = math.radians(degree - 90)
            c.create_text(cx+(radius-20)*math.cos(angle), cy+(radius-20)*math.sin(angle),
                          text=label, fill=color, font=("Bahnschrift", 9, "bold"))
        yaw = math.radians(self.angles[2] - 90)
        c.create_line(cx, cy, cx+(radius-28)*math.cos(yaw), cy+(radius-28)*math.sin(yaw),
                      fill=CYAN, width=4, arrow="last")
        c.create_oval(cx-4, cy-4, cx+4, cy+4, fill=PANEL, outline=CYAN, width=2)

    def send(self, command):
        if not self.serial or not self.serial.is_open: self.log("未连接，无法发送命令"); return
        try: self.serial.write((command + "\r\n").encode("ascii")); self.log("TX " + command)
        except Exception as exc: self.log("发送失败：" + str(exc))

    def send_entry(self):
        command = self.command_entry.get().strip()
        if command: self.send(command); self.command_entry.delete(0, "end")

    def toggle_record(self):
        self.recording = not self.recording
        self.record_btn.configure(text="停止记录" if self.recording else "开始记录",
                                  bg=RED if self.recording else CYAN, fg="#ffffff")
        if self.recording and not self.rows: self.rows.append(["timestamp","accel_x_m_s2","accel_y_m_s2","accel_z_m_s2","gyro_x_rad_s","gyro_y_rad_s","gyro_z_rad_s","rest_detected","rest_gyro_ratio","rest_accel_ratio","temperature_c"])

    def export_csv(self):
        if len(self.rows) < 2: messagebox.showinfo("暂无数据", "请先开始记录并采集数据。", parent=self); return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", initialfile=f"BMI088_{datetime.now():%Y%m%d_%H%M%S}.csv", filetypes=(("CSV 数据", "*.csv"),))
        if path:
            with open(path, "w", newline="", encoding="utf-8-sig") as f: csv.writer(f).writerows(self.rows)
            self.log("已导出：" + path)

    def zero_attitude(self):
        self.zero = [self.zero[i] + self.angles[i] for i in range(3)]
        self.log("显示姿态已归零（未修改固件）")

    def _tick_hz(self):
        hz = self.packets - self.last_packets; self.last_packets = self.packets
        self.metrics["hz"].set(f"{hz} Hz"); self.metrics["errors"].set(str(self.errors)); self.after(1000, self._tick_hz)

    def log(self, text):
        self.log_box.insert("end", f"[{datetime.now():%H:%M:%S}] {text}\n"); self.log_box.see("end")
        line_count = int(self.log_box.index("end-1c").split(".")[0])
        if line_count > 500:
            self.log_box.delete("1.0", f"{line_count-400}.0")

    def close_app(self):
        self.disconnect(); self.destroy()


if __name__ == "__main__":
    H7Monitor().mainloop()
