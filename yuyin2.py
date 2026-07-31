#!/usr/bin/env python3
import socket
import json
import os
import threading
from vosk import Model, KaldiRecognizer
from yy import LongSSHController

controller = LongSSHController()
# ========== 配置 ==========
MODEL_DIR   = os.path.join(os.path.dirname(__file__), "vosk-model-cn-0.22")
HOST        = '0.0.0.0'          # 监听所有网卡
PORT        = 5800               # 与客户端保持一致
CHUNK       = 512               # 字节数（16-bit = 2 字节）
RATE        = 16000              # 采样率 16 kHz

# ==========================

def run_target():
    controller.run_command("rosrun xrobot qian_jin.py")

def run_target2():
    controller.run_command("rosrun xrobot hou_tui.py")

def run_target3():
    controller.run_command("rosrun xrobot zuo_zhuan.py")

def run_target4():
    controller.run_command("rosrun xrobot you_zhuan.py")
def handle_client(conn, addr):
    print("[SERVER] 客户端连接:", addr)
    if not os.path.exists(MODEL_DIR):
        raise FileNotFoundError("离线模型目录不存在：" + MODEL_DIR)

    model = Model(MODEL_DIR)
    rec = KaldiRecognizer(model, RATE)
    rec.SetWords(False)

    try:
        while True:
            data = conn.recv(CHUNK * 2)   # 16-bit PCM
            if not data:
                break
            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result())["text"]
                print("[HEAR]", text)
                if "前进" in text:
                    run_target()
                elif "后退" in text:
                    run_target2()
                elif "左转" in text:
                    run_target3()
                elif "右转" in text:
                    run_target4()
                elif "退出" in text or "结束" in text:
                    break
    finally:
        conn.close()
        print("[SERVER] 客户端断开:", addr)

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen(5)
    print(f"[SERVER] 监听 {HOST}:{PORT}，等待客户端音频流...")

    try:
        while True:
            conn, addr = s.accept()
            # ✅ 用 lambda 把参数带进去
            threading.Thread(target=lambda: handle_client(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[SERVER] 用户中断，退出。")
    finally:
        s.close()

if __name__ == '__main__':
    main()