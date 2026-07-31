#!/usr/bin/env python3
"""
Windows 版本 JPEG/H264 RTP 流转发器
替代 ROS 版本，使用 OpenCV 从摄像头/视频文件获取视频流
"""

import socket
import sys
import struct
import random
import threading
import time
import argparse
from fractions import Fraction

import numpy as np
import cv2
import av

# ---------------- 参数 ----------------
# 修改这里：默认改为本地 IP，不再是 zzkj1004.com
UDP_HOST = "127.0.0.1"  # ← 改成你想要的默认地址：127.0.0.1 或 192.168.x.x
UDP_PORT = 5007
MTU = 1100
RTP_PAYLOAD_TYPE = 96
RTP_HEADER_FORMAT = '!BBHII'

# ---------------- 全局状态 ----------------
rtp_sequence = random.randint(0, 0xFFFF)
rtp_timestamp = random.randint(0, 0xFFFFFFFF)
rtp_ssrc = random.randint(0, 0xFFFFFFFF)

first_frame = True
sps_pps_cache = None
frame_count = 0
start_time = None
lock = threading.Lock()


# ---------------- 工具函数 ----------------
def create_rtp_header(pt, seq, ts, ssrc, marker=0):
    """创建 RTP 头部"""
    version_p_x_cc = 0x80
    second_byte = (marker << 7) | pt
    return struct.pack(RTP_HEADER_FORMAT, version_p_x_cc, second_byte, seq, ts, ssrc)


def fragment_nalu(nalu):
    """将大的 NALU 分片为 FU-A 格式"""
    fragments = []
    nalu_type = nalu[0] & 0x1F
    nalu_header = nalu[0]
    offset = 1

    while offset < len(nalu):
        left = len(nalu) - offset
        frag_size = min(MTU - 12 - 2, left)  # 12字节RTP头 + 2字节FU头
        is_last = (offset + frag_size >= len(nalu))

        fu_indicator = (nalu_header & 0xE0) | 28  # FU-A type = 28
        fu_header = ((offset == 1) << 7) | (is_last << 6) | nalu_type

        fragments.append(bytes([fu_indicator, fu_header]) + nalu[offset:offset + frag_size])
        offset += frag_size

    return fragments


def send_nalu(sock, nalu):
    """发送 NALU 单元，自动处理分片"""
    global rtp_sequence, rtp_timestamp

    with lock:
        nalu_type = nalu[4] & 0x1F if len(nalu) > 4 else nalu[0] & 0x1F
        print(f"[发送] NALU 类型: {nalu_type}, 长度: {len(nalu)} bytes")

        if len(nalu) > MTU - 12:
            # 需要分片
            frags = fragment_nalu(nalu)
            for i, frag in enumerate(frags):
                is_last = (i == len(frags) - 1)
                hdr = create_rtp_header(RTP_PAYLOAD_TYPE, rtp_sequence, rtp_timestamp, rtp_ssrc, marker=int(is_last))
                sock.send(hdr + frag)
                rtp_sequence = (rtp_sequence + 1) & 0xFFFF
        else:
            # 直接发送
            hdr = create_rtp_header(RTP_PAYLOAD_TYPE, rtp_sequence, rtp_timestamp, rtp_ssrc, marker=1)
            sock.send(hdr + nalu)
            rtp_sequence = (rtp_sequence + 1) & 0xFFFF


# ---------------- 编码器设置 ----------------
def setup_codec(width=640, height=480, fps=30):
    """初始化 H.264 编码器"""
    global sps_pps_cache

    c = av.CodecContext.create('h264', 'w')
    c.width = width
    c.height = height
    c.pix_fmt = 'yuv420p'
    c.framerate = fps
    c.time_base = Fraction(1, fps)
    c.bit_rate = 50000  # 平均码率 50kbps
    c.gop_size = 20  # 关键帧间隔

    # x264 参数配置
    c.options = {
        'tune': 'zerolatency',
        'preset': 'superfast',
        'crf': '28',
        'profile': 'baseline',
        'x264-params': ('repeat-headers=1:annexb=1:'
                        'keyint=20:min-keyint=20:scenecut=0:'
                        'bframes=0:ref=1:qpmax=35:'
                        'vbv-maxrate=500:vbv-bufsize=800')
    }
    c.open()

    # 生成 SPS/PPS
    dummy = np.zeros((height, width, 3), np.uint8)
    dummy_frame = av.VideoFrame.from_ndarray(dummy, format='bgr24')

    sps_pps = bytearray()
    for pkt in c.encode(dummy_frame):
        sps_pps.extend(bytes(pkt))

    sps_pps_cache = bytes(sps_pps) if sps_pps else None

    print(f"[初始化] 编码器: {width}x{height}@{fps}fps")
    print(f"[初始化] SPS/PPS: {len(sps_pps)} bytes")
    print(f"[初始化] 目标码率: {c.bit_rate / 1000:.1f} kbps")

    return c


def update_codec_if_needed(codec, new_width, new_height):
    """动态调整编码器分辨率"""
    if codec.width != new_width or codec.height != new_height:
        print(f"[调整] 分辨率变更: {codec.width}x{codec.height} -> {new_width}x{new_height}")

        # 重新创建编码器
        codec.width = new_width
        codec.height = new_height
        codec.open()

        # 重新生成 SPS/PPS
        dummy = np.zeros((new_height, new_width, 3), np.uint8)
        dummy_frame = av.VideoFrame.from_ndarray(dummy, format='bgr24')

        sps_pps = bytearray()
        for pkt in codec.encode(dummy_frame):
            sps_pps.extend(bytes(pkt))

        global sps_pps_cache
        sps_pps_cache = bytes(sps_pps) if sps_pps else None

        return True
    return False


# ---------------- 视频处理 ----------------
def process_frame(codec, sock, frame_bgr):
    """处理单帧图像并发送"""
    global first_frame, rtp_timestamp, frame_count

    try:
        h, w = frame_bgr.shape[:2]

        # 检查是否需要调整编码器
        if update_codec_if_needed(codec, w, h):
            first_frame = True  # 强制重新发送 SPS/PPS

        # 转换为 PyAV Frame
        frame = av.VideoFrame.from_ndarray(frame_bgr, format='bgr24')

        # 首帧发送 SPS/PPS
        if first_frame and sps_pps_cache:
            first_frame = False
            send_nalu(sock, sps_pps_cache)
            print(f"[首帧] 已发送 SPS/PPS ({len(sps_pps_cache)} bytes)")

        # 编码并发送
        for pkt in codec.encode(frame):
            send_nalu(sock, bytes(pkt))

        # 更新时间戳 (90kHz 时钟，假设 30fps -> 3000 增量)
        with lock:
            rtp_timestamp = (rtp_timestamp + 3000) & 0xFFFFFFFF
            frame_count += 1

            # 每30秒打印一次统计
            if frame_count % 300 == 0:
                elapsed = time.time() - start_time
                fps = frame_count / elapsed
                print(f"[统计] 已发送 {frame_count} 帧, 平均 {fps:.1f} fps")

    except Exception as e:
        print(f"[错误] 处理帧失败: {e}")


# ---------------- 采集循环 ----------------
def capture_from_camera(codec, sock, device_id=0, width=640, height=480, fps=30):
    """从摄像头采集视频"""
    print(f"[摄像头] 正在打开设备 {device_id}...")

    # Windows 下使用 DirectShow 后端
    cap = cv2.VideoCapture(device_id, cv2.CAP_DSHOW)

    if not cap.isOpened():
        print(f"[错误] 无法打开摄像头 {device_id}")
        # 尝试默认后端
        cap = cv2.VideoCapture(device_id)
        if not cap.isOpened():
            print(f"[错误] 所有后端都无法打开摄像头")
            return False

    # 设置分辨率
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)

    # 获取实际参数
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

    print(f"[摄像头] 实际参数: {actual_w}x{actual_h} @ {actual_fps:.1f}fps")

    global start_time
    start_time = time.time()

    print("[开始] 按 'q' 退出，按 's' 截图")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[警告] 读取帧失败")
            time.sleep(0.01)
            continue

        # 显示预览 (可选，可注释掉以减少CPU占用)
        cv2.imshow('Preview (Press q to quit)', frame)

        # 处理并发送
        process_frame(codec, sock, frame)

        # 检查按键
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            filename = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(filename, frame)
            print(f"[截图] 已保存 {filename}")

    cap.release()
    cv2.destroyAllWindows()
    return True


# ---------------- 网络连接 ----------------
def create_udp_socket(host, port):
    """创建 UDP 套接字并连接到服务器"""
    print(f"[网络] 正在连接 {host}:{port}...")

    # 尝试解析地址 (支持 IPv4/IPv6)
    for fam, _, _, _, addr in socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_DGRAM):
        try:
            sock = socket.socket(fam, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            if fam == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)

            sock.bind(('', 0))  # 绑定任意本地端口
            sock.connect(addr)

            proto = "IPv6" if fam == socket.AF_INET6 else "IPv4"
            print(f"[网络] 已连接 ({proto}): {addr}")
            return sock

        except Exception as e:
            print(f"[警告] 连接失败 ({addr}): {e}")
            continue

    return None


# ---------------- 主程序 ----------------
def main():
    parser = argparse.ArgumentParser(description='Windows RTP Video Streamer')
    # 修改这里：默认值从命令行参数改为直接使用上面的 UDP_HOST 常量
    parser.add_argument('--host', default=UDP_HOST, help=f'目标服务器地址 (默认: {UDP_HOST})')
    parser.add_argument('--port', type=int, default=UDP_PORT, help=f'目标端口 (默认: {UDP_PORT})')
    parser.add_argument('--device', type=int, default=0, help='摄像头设备ID')
    parser.add_argument('--width', type=int, default=640, help='视频宽度')
    parser.add_argument('--height', type=int, default=480, help='视频高度')
    parser.add_argument('--fps', type=int, default=30, help='采集帧率')

    args = parser.parse_args()

    # 创建 UDP 连接
    sock = create_udp_socket(args.host, args.port)
    if sock is None:
        print("[致命错误] 无法创建网络连接")
        sys.exit(1)

    # 初始化编码器
    codec = setup_codec(args.width, args.height, args.fps)

    # 先发一次 SPS/PPS
    if sps_pps_cache:
        send_nalu(sock, sps_pps_cache)
        print("[启动] 已预发送 SPS/PPS")

    # 开始采集
    try:
        success = capture_from_camera(codec, sock, args.device, args.width, args.height, args.fps)
        if not success:
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n[中断] 用户停止")
    finally:
        sock.close()
        print("[结束] 已关闭连接")


if __name__ == '__main__':
    main()