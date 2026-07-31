#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, paramiko, json   # 新增 json

# ========== 1. 改成你自己的 ======================
CAR_IP   = "localhost"
CAR_PORT = 5666                 # 新增端口
CAR_USER = "jetson"
CAR_PASS = "12345678"
# 小车端路径保持不变
CAR_JSON = "/home/jetson/ai-robot/catkin_ws/src/xrobot/config/Parameter.json"
# =================================================


def main() -> None:
    local_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Parameter.json")
    if not os.path.isfile(local_json):
        print("[ERROR] 同目录下找不到 Parameter.json：", local_json)
        input("按回车退出...")
        sys.exit(1)

    print("[INFO] 正在连接 {}@{} …".format(CAR_USER, CAR_IP))
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(CAR_IP, port=CAR_PORT, username=CAR_USER, password=CAR_PASS)

        print("[INFO] 上传 {} -> {}:{}".format(local_json, CAR_IP, CAR_JSON))
        with ssh.open_sftp() as sftp:
            sftp.put(local_json, CAR_JSON)

        print("[INFO] 完成！")
    except Exception as e:
        print("[ERROR]", e)
    finally:
        ssh.close()

if __name__ == "__main__":
    main()