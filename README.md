# 智能巡检系统（ros-AI）

基于 ROS 机器人与 AI 视觉的智能巡检系统，提供**实时视频巡检、YOLO 目标检测、大模型图像分析、车辆管理、事件记录与数据统计**的一体化 Web 平台。

## 功能特性

- **实时视频巡检**：接收巡检小车通过 UDP 发送的 H.264 视频流，服务端解码后进行 AI 推理，Web 页面以 MJPEG 方式实时显示检测结果
- **AI 视觉识别**：基于 Ultralytics YOLO，支持 `yolov8s`（通用检测）、`yolov8n`（轻量检测）、`yolo11s-pose`（人体姿态估计），可在运行中热切换模型
- **大模型智能分析**：集成 Qwen2-VL-7B 多模态大模型（离线运行），自动分析教室/环境画面，判断“有人 / 无人 / 有动物”并写入日志与数据库
- **巡航路线配置**：PGM 地图坐标拾取界面，可设置巡航路径点与车速，并通过 SSH 一键下发至小车
- **车辆管理**：多车辆在线状态实时监控（UDP 心跳检测，自动判定在线/离线），支持增删改车辆与分区
- **事件记录与统计**：异常检测事件自动入库（SQLite），提供事件查询与数据统计页面
- **语音控制（可选）**：基于 Vosk 离线中文语音识别，通过 SSH 远程控制小车前进 / 后退 / 左转 / 右转
- **用户登录**：后台管理系统带账号登录与权限控制

## 技术栈

| 分类 | 技术 |
| --- | --- |
| 后端 | Python 3、Flask |
| AI 检测 | Ultralytics YOLO、PyTorch、OpenCV |
| 视频处理 | PyAV（H.264 解码）、UDP/RTP、MJPEG |
| 多模态大模型 | Qwen2-VL-7B-Instruct（transformers，离线） |
| 语音识别 | Vosk 离线中文模型、PyAudio |
| 远程控制 | Paramiko（SSH 隧道） |
| 数据库 | SQLite |
| 前端 | HTML + JavaScript（登录、后台、巡航、统计等页面） |
| 车端系统 | ROS Noetic（`rosrun xrobot ...` 指令） |

## 目录结构

```
AiInspection/
├── patrol_system/               # 巡检主系统
│   ├── TestAI.py                # Flask 主服务：视频流解码 + YOLO 推理 + Web API
│   ├── jiaoshi.py               # 教室画面分析（Qwen2-VL 离线判断有人/无人）
│   ├── jiaoshi_prompt.txt       # 大模型分析提示词
│   ├── db.py                    # SQLite 统一连接
│   ├── build_db.py              # 初始化数据库（默认管理员 admin / 123456）
│   ├── init_db.sql              # 数据库建表脚本（users / cars / events）
│   ├── patrol.db                # SQLite 数据库文件
│   ├── config.json              # YOLO 模型、地图配置
│   ├── Parameter.json           # 巡航参数（地图、路径点、速度）
│   ├── Path_Settings.py         # 通过 SSH 上传巡航参数到小车
│   ├── *.html                   # 登录、后台、车辆管理、巡航、事件、统计页面
│   ├── map/                     # 地图（da_ting11 大厅、waimian 室外，pgm + yaml）
│   ├── model/                   # YOLO 模型文件（.pt）
│   └── a/                       # 日志与结果输出（jiaoshi.log 等）
├── yy.py                        # SSH 长连接控制器（连接小车执行 ROS 指令）
├── yuyin.py                     # 离线语音识别（本地麦克风，控制小车）
├── yuyin2.py                    # 离线语音识别（TCP 服务端，端口 5800）
├── jie_ma.py                    # 阿里云 DNS 动态域名（DDNS）更新脚本
└── README.md
```

## 环境依赖

- Python 3.9 / 3.10
- 主要依赖：`flask`、`ultralytics`、`torch`、`opencv-python`、`av`、`numpy`、`paramiko`、`vosk`、`pyaudio`、`transformers`、`Pillow`、`requests`、`aliyun-python-sdk-core`、`aliyun-python-sdk-alidns`
- 模型文件（需自行准备）：
  - `patrol_system/model/`：`yolov8s.pt`、`yolov8n.pt`、`yolo11s-pose.pt`
  - `Qwen2-VL-7B-Instruct` 本地模型目录（`jiaoshi.py` 使用）
  - `vosk-model-cn-0.22` 离线中文语音模型（`yuyin.py` / `yuyin2.py` 使用）

## 快速开始

```bash
# 1. 初始化数据库（会创建 patrol.db，默认管理员 admin / 123456）
python patrol_system/build_db.py

# 2. 启动巡检主服务
python patrol_system/TestAI.py
# 浏览器访问 http://localhost:5000

# 3. 登录后台，在“车辆管理”中添加巡检小车（端口、名称、区域）

# 4. 小车端将 H.264 视频通过 UDP 推送到服务端口（默认 5007）

# 5. （可选）设置巡航参数并下发到小车
python patrol_system/Path_Settings.py
```

## 配置文件说明

| 文件 | 说明 |
| --- | --- |
| `patrol_system/config.json` | 巡检服务配置：YOLO 模型路径、地图文件路径 |
| `patrol_system/Parameter.json` | 巡航参数：地图名称、路径坐标点 `xy`、巡航速度 `speed` |
| `patrol_system/Path_Settings.py` | 小车 SSH 连接参数（默认 `localhost:5666`，用户 `jetson`），将 `Parameter.json` 上传到小车 |
| `yy.py` | SSH 长连接参数（同上），执行 `rosrun xrobot ...` 控制指令 |

## 端口说明

| 端口 | 用途 |
| --- | --- |
| 5000 | Flask Web 服务（巡检平台） |
| 5007 | UDP 视频流接收（默认小车） |
| 5800 | 离线语音识别 TCP 服务（yuyin2.py） |
| 5666 | 小车 SSH 隧道端口 |

## 注意事项

- 小车端需要 ROS Noetic 环境，并运行对应的 xrobot 节点与视频推流程序
- `jie_ma.py` 中阿里云 `ACCESS_KEY_ID` / `ACCESS_KEY_SECRET` 等为占位配置，使用前请替换为自己的密钥
- `Parameter.json` 中的地图名与 `map/` 目录需保持一致，确保小车端存在对应地图文件
- 模型文件体积较大（约 47 MB），推送仓库时网络不稳定可重试或使用代理
