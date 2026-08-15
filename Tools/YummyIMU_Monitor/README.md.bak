# H7 YummyIMU 上位机

## 功能

- 通过板载 USB CDC 虚拟串口连接，默认选择 921600 波特率。
- 兼容 YummyIMU 的 USE、DEBUG、BINARY 姿态协议。
- DEBUG 模式增加 SENSOR 扩展行，传输 BMI088 三轴加速度、校正后三轴角速度、VQF 静止判断、静止偏差和温度。
- 实时三维姿态、传感器曲线、静止持续时间、接收频率和 CSV 记录。
- 数据接收与 CSV 保持最高 200Hz，界面限速约 30Hz，避免高频重绘卡顿。
- 参考 YummyIMU Studio 使用浅色工作台、左侧导航和无边框自绘标题栏。
- 超过 0.5 秒未收到传感器帧时停止静止计时并显示“数据中断”。
- 不支持的 BMI270、双 IMU、INS、TDRIFT 和 YAWCAL 返回 ERROR:UNSUPPORTED，不生成虚假数据。

## 使用

1. 用工程根目录的一键编译烧录脚本烧录最新固件，重新给板子上电。
2. 打开 `dist/H7_IMU_Studio_1.1.1.exe`。
3. 扫描并选择 H7 USB CDC 对应 COM 口，点击“连接”。
4. 上位机会自动发送 `MODE DEBUG` 并开始接收传感器数据。

## 开发与打包

- 源码运行：`D:\ProgramData\miniconda3\python.exe yummyimu_monitor.py`
- EXE 使用 PyInstaller 打包；当前成品已包含 Tk 与串口依赖，不要求目标电脑安装 Python。
- `renderer`、`main.js` 等文件保留了 Electron 界面原型，日常使用以 Python EXE 为准。
- `assets/app_icon_source_v2.png` 为生成的山景源图，`app_icon.ico` 已嵌入最终 EXE。

## 文件变更记录

- 2026-08-15：创建 Electron 上位机，重点接入加速度计、陀螺仪和 VQF 静止判断；增加 CSV 导出与协议终端。
- 2026-08-15：Python 上位机分离采集频率与界面刷新频率，并增加异常日志限速。
- 2026-08-15：增加无边框浅色工作台，并修复停止收数后静止计时仍增长的问题。
