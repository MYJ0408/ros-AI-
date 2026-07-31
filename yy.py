#!/usr/bin/env python3
import paramiko
import time

CAR_USER = "jetson"
CAR_PASS = "12345678"
TUNNEL_PORT = 5666

class LongSSHController:
    def __init__(self):
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect("localhost", port=TUNNEL_PORT, username=CAR_USER, password=CAR_PASS)
        self.shell = self.ssh.invoke_shell()
        self.send("source /opt/ros/noetic/setup.bash")
        self.send("source /home/jetson/ai-robot/catkin_ws/devel/setup.bash")

    def send(self, cmd):
        self.shell.send(cmd + "\n")
        time.sleep(0.1)

    def run_command(self, cmd):
        self.send(cmd)

if __name__ == "__main__":
    controller = LongSSHController()
    while True:
        cmd = input("输入命令（如：rosrun xrobot zuo_zhuan.py）：")
        controller.run_command(cmd)