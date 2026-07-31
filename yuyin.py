#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import json
import pyaudio
from vosk import Model, KaldiRecognizer
from yy import LongSSHController

controller = LongSSHController()
MODEL_DIR = os.path.join(os.path.dirname(__file__), "vosk-model-cn-0.22")
def run_target():
    controller.run_command("rosrun xrobot qian_jin.py")

def run_target2():
    controller.run_command("rosrun xrobot hou_tui.py")

def run_target3():
    controller.run_command("rosrun xrobot zuo_zhuan.py")

def run_target4():
    controller.run_command("rosrun xrobot you_zhuan.py")
def main():
    if not os.path.exists(MODEL_DIR):
        raise FileNotFoundError("离线模型目录不存在：" + MODEL_DIR)
    model = Model(MODEL_DIR)          # 仅本地加载，不下载
    rec = KaldiRecognizer(model, 48000)
    rec.SetWords(False)

    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16,
                    channels=1,
                    rate=48000,
                    input=True,
                    input_device_index=7,
                    frames_per_buffer=512)
    stream.start_stream()
    print("[INFO] 离线语音识别运行中，说“前进”或“退出”...")

    while True:
        if rec.AcceptWaveform(stream.read(4096, exception_on_overflow=False)):
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

    stream.stop_stream(); stream.close(); p.terminate()
    print("[INFO] 程序正常结束，全程无网络请求。")

if __name__ == "__main__":
    main()