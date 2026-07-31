#!/usr/bin/env python3
import pathlib, cv2, numpy as np, json, time, struct, threading, socket, torch, os, subprocess
import sqlite3
import sys
from pathlib import Path
from flask import Flask, Response, request, jsonify, send_from_directory
from ultralytics import YOLO
from av import CodecContext, Packet
from collections import deque
from db import get_conn  # 统一数据库连接

# ================= 配置中心 =================
import json
from functools import wraps   # 用于热加载装饰器（可选）
CONFIG_FILE   = Path(__file__).parent / 'config.json'
PROMPT_FILE   = Path(__file__).parent / 'jiaoshi_prompt.txt'

def load_config():
    """若不存在则生成默认配置"""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    default = {
        "yolo_model": "model/yolov8s.pt",
        "map_pgm":    "map/da_ting11.pgm",
        "map_yaml":   "map/da_ting11.yaml"
    }
    CONFIG_FILE.write_text(json.dumps(default, ensure_ascii=False, indent=2))
    return default

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

def reload_yolo():
    """热切换 YOLO 模型（不重启服务）"""
    global model
    cfg = load_config()
    model_path = Path(__file__).parent / cfg['yolo_model']
    model = YOLO(str(model_path)).to(device)
    print(f"[INFO] YOLO 模型已热切换至 {model_path}")
# -------------------- 基础配置 --------------------
UDP_PORTS = [5007]          # 默认小车端口
MAX_FAILS = 30              # 连续丢包判离线
MAX_SUCCS = 1               # 连续收包判在线
CHECK_INTERVAL = 0.1        # 状态轮询间隔（秒）
config      = load_config()
MODEL_PATH  = Path(__file__).parent / config['yolo_model']
device = 'cuda' if torch.cuda.is_available() else 'cpu'
app = Flask(__name__)
model = YOLO(str(MODEL_PATH)).to(device)

# 离线占位图（400×300 黑底白字）
PLACEHOLDER_PATH = Path("offline_placeholder.jpg")
if not PLACEHOLDER_PATH.exists():
    img = np.zeros((300, 400, 3), dtype=np.uint8)
    cv2.putText(img, "OFFLINE", (120, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
    cv2.imwrite(str(PLACEHOLDER_PATH), img)

# -------------------- RTP 处理器 --------------------
class RTPProcessor:
    def __init__(self, port: int, name="未命名", area="未设置"):
        self.port = port
        self.name, self.area = name, area
        self.active = True
        self.status = False  # 稳态在线/离线
        self.frame = None    # 最新 JPEG 帧
        self.meta = {}       # 最新元数据
        self.lock = threading.Lock()
        self.timestamps = deque(maxlen=30)
        self.fail_cnt = 0
        self.succ_cnt = 0
        # H264 解码器
        self.decoder = CodecContext.create('h264', 'r')
        self.fragment_buffer = {}
        # UDP 套接字
        self.sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        self.sock.bind(("::", self.port))
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
        self.sock.settimeout(0.5)
        # 启动线程
        threading.Thread(target=self.recv_loop, daemon=True).start()
        threading.Thread(target=self.meta_loop, daemon=True).start()

    # 即使离线也每秒推送一次元数据
    def meta_loop(self):
        while self.active:
            with self.lock:
                if not self.status:
                    self.meta = {"fps": 0, "latency_ms": -1, "detections": [], "timestamp": time.time()}
            time.sleep(1)

    # H264 解码 + YOLO 推理
    def _decode_h264(self, nalu: bytes):
        try:
            frames = self.decoder.decode(Packet(nalu))
            for av_frame in frames:
                img = av_frame.to_ndarray(format='bgr24')
                start = time.time()
                results = model.predict(img, verbose=False)[0]
                annotated = results.plot()
                latency_ms = (time.time() - start) * 1000
                # 计算 FPS
                now = time.time()
                self.timestamps.append(now)
                fps = len(self.timestamps) / (now - self.timestamps[0]) if len(self.timestamps) > 1 else 0.0
                # 组装检测结果
                detections = [
                    {"class": model.names[int(box.cls)], "confidence": float(box.conf),
                     "bbox": [int(x) for x in box.xyxy[0]]}
                    for box in results.boxes
                ]
                # 编码成 JPEG
                _, jpeg = cv2.imencode('.jpg', annotated)
                with self.lock:
                    self.frame = jpeg.tobytes()
                    self.meta = {"fps": round(fps, 2), "latency_ms": round(latency_ms, 2),
                                 "detections": detections, "timestamp": now}
                # 稳态计数
                self.succ_cnt += 1; self.fail_cnt = 0
                if self.succ_cnt >= MAX_SUCCS:
                    self.status = True
        except Exception as e:
            print(f"[{self.port}] decode err: {e}")
            self.fail_cnt += 1; self.succ_cnt = 0
            if self.fail_cnt >= MAX_FAILS:
                self.status = False

    # 接收线程
    def recv_loop(self):
        print(f"[{self.port}] 监听 RTP/H264 ...")
        while self.active:
            try:
                data, _ = self.sock.recvfrom(65536)
                if len(data) < 12: continue
                ver, pt, seq, ts, ssrc = struct.unpack('!BBHII', data[:12])
                if ((ver >> 6) & 0x03) != 2: continue
                payload = data[12:]
                # FU-A 分片重组（简化）
                if payload[0] & 0x1F == 28:
                    fu_header = payload[1]
                    start = (fu_header >> 7) & 1
                    end = (fu_header >> 6) & 1
                    nalu_type = fu_header & 0x1F
                    buf = self.fragment_buffer.setdefault(ssrc, {'buf': b'', 'type': nalu_type})
                    if start:
                        buf['buf'] = bytes([(payload[0] & 0xE0) | nalu_type]) + payload[2:]
                    else:
                        buf['buf'] += payload[2:]
                    if end:
                        nalu = buf['buf']
                        self.fragment_buffer[ssrc] = {'buf': b'', 'type': nalu_type}
                        self._decode_h264(b'\x00\x00\x00\x01' + nalu)
                else:
                    self._decode_h264(b'\x00\x00\x00\x01' + payload)
            except socket.timeout:
                self.fail_cnt += 1; self.succ_cnt = 0
                if self.fail_cnt >= MAX_FAILS:
                    self.status = False
            except Exception as e:
                print(f"[{self.port}] recv err: {e}")
                self.fail_cnt += 1; self.succ_cnt = 0
                if self.fail_cnt >= MAX_FAILS:
                    self.status = False

    # 取帧/元数据
    def get_frame(self):
        with self.lock:
            return self.frame, self.status, self.meta

    # 退出
    def close(self):
        self.active = False
        self.sock.close()


# -------------------- 全局处理器字典 --------------------
processors: dict[int, RTPProcessor] = {}

# -------------------- 工具：GC CUDA --------------------
def gc_cuda():
    while True:
        time.sleep(30)
        torch.cuda.empty_cache()


threading.Thread(target=gc_cuda, daemon=True).start()

# -------------------- Flask 路由 --------------------

# 0. 首页 – 登录页
@app.route('/')
def login_page():
    return send_from_directory('.', 'login.html')


# 1. 登录验证
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=? AND password=?", (username, password)).fetchone()
    return jsonify({"ok": bool(row)})


# 2. 登录成功后主页
@app.route('/home')
def home():
    return send_from_directory('.', 'test1.html')


# 3. 添加小车
@app.route('/add_car', methods=['POST'])
def add_car():
    try:
        data = request.get_json()
        port = int(data['port'])
        name = data.get('name', f'小车 #{port}')
        area = data.get('area', '未设置')
        if port in processors:
            return jsonify({"status": "exists"}), 200
        processors[port] = RTPProcessor(port, name, area)
        # 写库
        with get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO cars(port,name,area) VALUES(?,?,?)",
                         (port, name, area))
            conn.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# 4. 列出小车
@app.route('/list_cars')
def list_cars():
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT port,name,area FROM cars")]
    # 合并在线状态
    for r in rows:
        r["status"] = processors.get(r["port"], RTPProcessor(0)).status
    return jsonify(rows)


# 5. 删除小车
@app.route('/remove_car', methods=['POST'])
def remove_car():
    try:
        port = int(request.json['port'])
        if port not in processors:
            return jsonify({"status": "not_found", "message": f"端口 {port} 不存在"}), 404
        processors[port].close()
        del processors[port]
        # 删库
        with get_conn() as conn:
            conn.execute("DELETE FROM cars WHERE port=?", (port,))
            conn.commit()
        return jsonify({"status": "success", "message": f"已删除端口 {port}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# 6. 更新小车
@app.route('/update_car', methods=['POST'])
def update_car():
    try:
        data = request.get_json()
        port = int(data['port'])
        name = data.get('name', '')
        area = data.get('area', '')
        if port not in processors:
            return jsonify({"status": "not_found"}), 404
        processors[port].name = name
        processors[port].area = area
        # 写库
        with get_conn() as conn:
            conn.execute("UPDATE cars SET name=?, area=? WHERE port=?",
                         (name, area, port))
            conn.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# 7. 事件记录 – 分页
@app.route('/event_list')
def event_list():
    page = int(request.args.get("page", 1))
    pageSize = int(request.args.get("pageSize", 20))
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM events").fetchone()["c"]
        rows = [dict(r) for r in conn.execute(
            "SELECT time,port,result,desc FROM events ORDER BY time DESC LIMIT ? OFFSET ?",
            (pageSize, (page - 1) * pageSize))]
    return jsonify({"total": total, "list": rows})


# 8. 近 N 天事件（statistics.html 用）
@app.route('/event_list_recent')
def event_list_recent():
    from datetime import datetime, timedelta
    days = int(request.args.get("days", 3))
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT time,port,result,desc FROM events "
            "WHERE time>=? AND result IN (1,2,3) "  # ← 只保留有效结果
            "ORDER BY time DESC",
            (cutoff,))]
    return jsonify({"total": len(rows), "list": rows})


# 9. 拍照
@app.route('/snapshot', methods=['POST'])
def snapshot():
    try:
        port = int(request.json.get('port', 0))
        if port not in processors:
            return jsonify({"status": "invalid_port"}), 400
        frame, status, _ = processors[port].get_frame()
        if not status or frame is None:
            return jsonify({"status": "no_frame"}), 400
        os.makedirs("a", exist_ok=True)
        with open("a/1.jpg", "wb") as f:
            f.write(frame)
        # 记录一条“待分析”事件
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO events(time,port,result,desc,raw) VALUES(?,?,0,'拍照待分析','')",
                (time.strftime("%Y-%m-%d %H:%M:%S"), port))
            conn.commit()
        # 写 last_port
        pathlib.Path("a/last_port.txt").write_text(str(port))
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 500


# 10. 教室纪律结果（终极排查版）
@app.route('/jiaoshi_result')
def jiaoshi_result():
    import pathlib, json, subprocess, time, os, threading

    port = int(request.args.get("port", 0))
    img_file    = pathlib.Path("a/1.jpg")
    result_file = pathlib.Path("a/jiaoshi_result.json")
    has_img    = img_file.exists()
    has_result = result_file.exists()

    print(f"[DEBUG] 端口 {port}  has_img={has_img}  has_result={has_result}")

    # 0. 正在运行
    if has_result:
        data = json.loads(result_file.read_text(encoding='utf8'))
        if data.get("result") == "?":
            print("[DEBUG] 正在运行，返回请稍后")
            return jsonify({"code": 1, "msg": "请稍后"})

    # 1. 只有照片 -> 启动分析
    if has_img and not has_result:
        result_file.write_text(json.dumps({"result": "?", "ts": int(time.time())}), encoding='utf8')
        pathlib.Path("a/last_port.txt").write_text(str(port))

        def run():
            py_file = pathlib.Path(__file__).with_name("jiaoshi.py").absolute()
            log_fp  = pathlib.Path("a/jiaoshi.log").open("w", encoding="utf-8")
            try:
                print(f"[DEBUG] 准备启动子进程：python3 {py_file}")
                log_fp.write(f"[LOG] 启动命令：python3 {py_file}\n")
                # 关键：cwd + 捕获
                subprocess.run(
                    ["python3", str(py_file)],
                    check=True,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                    cwd=py_file.parent
                )
                print("[DEBUG] 子进程正常结束")
            except Exception as e:
                print(f"[DEBUG] 子进程异常：{e}")
                log_fp.write(f"[ERROR] {e}\n")
                # 让前端不再卡
                result_file.write_text(json.dumps({"result": "3", "ts": int(time.time())}), encoding='utf-8')
            finally:
                log_fp.close()

        print("[DEBUG] 启动后台线程...")
        threading.Thread(target=run, daemon=True).start()
        return jsonify({"code": 1, "msg": "请稍后"})

    # 2. 只有结果 -> 直接返回
    if not has_img and has_result:
        data = json.loads(result_file.read_text(encoding='utf8'))
        print("[DEBUG] 直接返回结果", data["result"])
        return jsonify({"code": 2, "result": data["result"]})

    # 3. 同时存在 -> 提示清理
    if has_img and has_result:
        print("[DEBUG] 需要清理缓存")
        return jsonify({"code": 9, "msg": "请先清理缓存"})

    # 4. 都没有
    print("[DEBUG] 没有图片，请先拍照")
    return jsonify({"code": 0, "msg": "请先拍照"})
# 11. 清理缓存
@app.route('/clean_cache', methods=['POST'])
def clean_cache():
    pathlib.Path("a/jiaoshi_result.json").unlink(missing_ok=True)
    return jsonify({"status": "ok"})


# 12. SSE – 在线数量
@app.route('/online')
def online_stream():
    def gen():
        while True:
            n = sum(1 for p in processors.values() if p.status)
            yield f"data: {n}\n\n"
            time.sleep(CHECK_INTERVAL)
    return Response(gen(), mimetype="text/event-stream")


# 13. SSE – 各端口状态
@app.route('/status')
def status_stream():
    def gen():
        while True:
            for p, pr in processors.items():
                yield f"data: {p}:{1 if pr.status else 0}\n\n"
            time.sleep(CHECK_INTERVAL)
    return Response(gen(), mimetype="text/event-stream")


# 14. SSE – 单路 meta
@app.route('/meta/<int:port>')
def meta_stream(port):
    if port not in processors:
        return "Invalid port", 404
    def gen():
        while True:
            _, status, meta = processors[port].get_frame()
            data = {"name": processors[port].name, "area": processors[port].area, **meta}
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(0.2)
    return Response(gen(), mimetype="text/event-stream")


# 15. MJPEG 视频流
@app.route('/video_feed/<int:port>')
def video_feed(port):
    if port not in processors:
        return "Invalid port", 404
    def gen():
        while True:
            frame, status, _ = processors[port].get_frame()
            if not status or frame is None:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' +
                       PLACEHOLDER_PATH.read_bytes() + b'\r\n')
                time.sleep(1)
                continue
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.033)  # ~30fps
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')


# 16. 静态页面
@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)
# 17. 后台管理界面
@app.route('/admin')
def admin_page():
    return send_from_directory('.', 'admin.html')
# ===================== 巡航界面 =====================
import yaml
from PIL import Image
MAP_PGM     = Path(__file__).parent / config['map_pgm']    # 同目录放地图
MAP_YAML    = Path(__file__).parent / config['map_yaml']

def load_map_config():
    with open(MAP_YAML, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    return cfg['origin'][:2], float(cfg['resolution'])

MAP_ORIGIN, MAP_RES = load_map_config()

def pgm_to_png_bytes(pgm_path=None):
    pgm_path = pgm_path or MAP_PGM  # 允许传入新路径
    img = Image.open(pgm_path).convert('RGB')
    _, buf = cv2.imencode('.png', np.asarray(img))
    return buf.tobytes()

@app.route('/cruise')
def cruise_page():
    return send_from_directory('.', 'cruise.html')

@app.route('/map_info')
def map_info():
    """给前端：原点 + 分辨率"""
    return jsonify(origin=MAP_ORIGIN, res=MAP_RES)

@app.route('/cruise_feed')
def cruise_feed():
    def gen():
        while True:
            # 🔥 每次都读当前配置里的地图
            cfg = load_config()
            current_pgm = Path(__file__).parent / cfg['map_pgm']
            frame = pgm_to_png_bytes(current_pgm)
            yield (b'--frame\r\n'
                   b'Content-Type: image/png\r\n\r\n' + frame + b'\r\n')
            time.sleep(1)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/admin/api/user', methods=['POST'])
def add_user():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"status": "error", "msg": "用户名和密码不能为空"}), 400
    try:
        with get_conn() as conn:
            conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            conn.commit()
        return jsonify({"status": "ok"})
    except sqlite3.IntegrityError:
        return jsonify({"status": "error", "msg": "用户名已存在"}), 400
# -------------------- 后台管理 API --------------------
from flask import request

# ================ 后台配置 API =================
@app.route('/admin/api/config', methods=['GET'])
def get_cfg():
    return jsonify(load_config())

@app.route('/admin/api/config', methods=['POST'])
def save_cfg_api():
    cfg = request.get_json()
    save_config(cfg)
    try:
        reload_yolo()
        # 🔥 重载地图
        global MAP_ORIGIN, MAP_RES
        MAP_ORIGIN, MAP_RES = load_map_config()
        print(f"[INFO] 地图已热切换至 {cfg['map_pgm']}")
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)}), 400
    return jsonify({"status": "ok"})

@app.route('/admin/api/prompt', methods=['GET'])
def get_prompt():
    if not PROMPT_FILE.exists():
        # 首次生成默认 prompt
        default_prompt = (
            "你是一名管理员，你要看当前室内环境是否开灯。只看图片。\n"
            "第一步 先描述你观察到的画面（≥20字），写完换行；\n"
            "第二步 必须单独一行输出下列数字之一：\n"
            "1 能够看清周围物品，室内有开灯；\n"
            "2 四周漆黑一片，室内没有开灯；\n"
            "开始：\n描述：\n数字："
        )
        PROMPT_FILE.write_text(default_prompt, encoding='utf8')
    return jsonify({"prompt": PROMPT_FILE.read_text(encoding='utf8')})

@app.route('/admin/api/prompt', methods=['POST'])
def save_prompt_api():
    prompt = request.get_json()['prompt']
    PROMPT_FILE.write_text(prompt, encoding='utf8')
    return jsonify({"status": "ok"})

@app.route('/admin/api/files')
def list_files():
    """浏览本地模型/地图文件"""
    sub_dir  = request.args.get('dir', 'model')        # model / map
    ext      = request.args.get('ext', '.pt')
    root     = Path(__file__).parent / sub_dir
    files    = [str(f.relative_to(root.parent)) for f in root.rglob(f'*{ext}') if f.is_file()]
    return jsonify(files)
# 身份验证装饰器（可选，但强烈建议）
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 这里可以添加简单的会话检查或更复杂的权限验证
        # 例如：if not session.get('is_admin'): return jsonify({"error": "Forbidden"}), 403
        return f(*args, **kwargs)

    return decorated_function
@app.route('/admin/api/user/<int:user_id>', methods=['DELETE'])
# @admin_required          # 如果加了装饰器先去掉测一把
def admin_api_delete_user(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    return jsonify({"status": "success"})

@app.route('/admin/api/users')
# @admin_required
def admin_api_users():
    """获取用户列表"""
    with get_conn() as conn:
        users = [dict(r) for r in conn.execute("SELECT id, username FROM users").fetchall()]
    return jsonify(users)


@app.route('/admin/api/events')
# @admin_required
def admin_api_events():
    """获取所有事件（带过滤和分页）"""
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("pageSize", 50))
    port_filter = request.args.get("port", type=int)

    query = "SELECT * FROM events"
    params = []
    if port_filter:
        query += " WHERE port = ?"
        params.append(port_filter)
    query += " ORDER BY time DESC LIMIT ? OFFSET ?"
    params.extend([page_size, (page - 1) * page_size])

    with get_conn() as conn:
        events = [dict(r) for r in conn.execute(query, params).fetchall()]
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    return jsonify({"list": events, "total": total})


@app.route('/admin/api/event/<int:event_id>', methods=['DELETE'])
# @admin_required
def admin_api_delete_event(event_id):
    """删除单条事件"""
    with get_conn() as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
    return jsonify({"status": "success"})


@app.route('/admin/api/stats')
# @admin_required
def admin_api_stats():
    """获取系统统计信息"""
    with get_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        car_count = conn.execute("SELECT COUNT(*) FROM cars").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        latest_event = conn.execute("SELECT MAX(time) FROM events").fetchone()[0]

    return jsonify({
        "user_count": user_count,
        "car_count": car_count,
        "event_count": event_count,
        "latest_event": latest_event
    })
# =================  上传轨迹专用  =================
# 1. 修改接口路由与处理函数
UPLOAD_TMP_JSON = Path(__file__).parent / 'Parameter.json'   # 代替 xy.txt

@app.route('/admin/api/parameter_json', methods=['POST'])
def api_parameter_json():
    """把前端发来的 {map:xx, xy:[{x:1,y:2},...]} 合并到现有 Parameter.json，保留 speed 等字段"""
    try:
        data = request.get_json()
        if not data or 'map' not in data or 'xy' not in data:
            return jsonify({"status":"error","msg":"字段缺失"}), 400

        # 1. 读旧配置（没有就新建带 speed=2）
        if UPLOAD_TMP_JSON.exists():
            old_cfg = json.loads(UPLOAD_TMP_JSON.read_text(encoding='utf8'))
        else:
            old_cfg = {'speed': 2}

        # 2. 只覆盖 map/xy，其余原封不动
        old_cfg['map'] = data['map']
        old_cfg['xy']  = data['xy']

        # 3. 写盘
        UPLOAD_TMP_JSON.write_text(json.dumps(old_cfg, ensure_ascii=False, indent=2), encoding='utf8')
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"status":"error","msg":str(e)}), 400

@app.route('/admin/api/do_upload', methods=['POST'])
def api_do_upload():
    """后台直接调用 Path_Settings.py 完成 SCP"""
    try:
        # 如果 Path_Settings.py 与 TestAI.py 不在同目录，自行改路径
        proc = subprocess.run([sys.executable, "Path_Settings.py"],
                              cwd=Path(__file__).parent,
                              capture_output=True,
                              text=True)
        if proc.returncode == 0:
            return jsonify({"msg":"SCP 上传成功！"})
        else:
            return jsonify({"msg":"SCP 失败："+proc.stderr}) , 500
    except Exception as e:
        return jsonify({"msg":str(e)}) , 500
@app.route('/Parameter.json', methods=['PUT'])
def update_speed():
    cfg = request.get_json()          # 前端已把 speed 改掉
    Path('Parameter.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    return jsonify(status='ok')
# -------------------- 启动 --------------------
if __name__ == '__main__':
    with get_conn() as conn:
        # 预置默认小车5007
        conn.execute("INSERT OR IGNORE INTO cars(port,name,area) VALUES(5007,'默认小车','11楼大厅')")
        conn.commit()
        # 从数据库加载所有小车
        rows = conn.execute("SELECT port, name, area FROM cars").fetchall()
    # 为每个小车创建RTPProcessor实例
    for row in rows:
        port = row['port']
        name = row['name']
        area = row['area']
        # 避免重复创建（如果已存在则跳过）
        if port not in processors:
            processors[port] = RTPProcessor(port, name, area)
    print(">>> 数据库版 TestAI 启动完毕，访问 http://localhost:5000")
    app.run(host='::', port=5000, threaded=True,use_reloader=False)