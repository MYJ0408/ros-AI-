#!/usr/bin/env python3
"""
教室纪律分析脚本 – 数据库版
写入 AI 原话 & 打印日志
"""
import os
import io
import torch
import pathlib
import json
import time
import logging
from PIL import Image
from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
from pathlib import Path
# -------------------- 日志 --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s"
)

# -------------------- 离线 --------------------
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# -------------------- 模型 --------------------
model_dir = pathlib.Path("Qwen2-VL-7B-Instruct").resolve()
device = "cuda" if torch.cuda.is_available() else "cpu"
model = Qwen2VLForConditionalGeneration.from_pretrained(
    str(model_dir), torch_dtype=torch.float16, local_files_only=True, device_map="auto"
)
processor = AutoProcessor.from_pretrained(str(model_dir), local_files_only=True)

# 读取外部 prompt
PROMPT_FILE = Path(__file__).with_name("jiaoshi_prompt.txt")
if not PROMPT_FILE.exists():
    # 如果不存在则生成默认
    default = (
        "你是一名管理员，你要看当前室内环境是否开灯。只看图片。\n"
        "第一步 先描述你观察到的画面（≥20字），写完换行；\n"
        "第二步 必须单独一行输出下列数字之一：\n"
        "1 能够看清周围物品，室内有开灯；\n"
        "2 四周漆黑一片，室内没有开灯；\n"
        "开始：\n描述：\n数字："
    )
    PROMPT_FILE.write_text(default, encoding='utf8')
SYS_PROMPT = PROMPT_FILE.read_text(encoding='utf8')

# -------------------- 读图片 --------------------
img_path = pathlib.Path(__file__).with_name("a").joinpath("1.jpg")
if not img_path.exists():
    logging.error("图片不存在: %s", img_path.resolve())
    exit(1)

image = Image.open(img_path).convert("RGB")
logging.info("已加载本地图片: %s", img_path.resolve())

# -------------------- 推理 --------------------
messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": SYS_PROMPT}]}]
text = processor.apply_chat_template(messages, add_generation_prompt=True)
inputs = processor(text=text, images=image, return_tensors="pt").to(device)

with torch.no_grad():
    out = model.generate(**inputs, max_new_tokens=1000, do_sample=False)

answer = processor.decode(out[0], skip_special_tokens=True)
assistant_text = answer.split("assistant")[-1].strip()

# 打印 AI 原话到日志
logging.info("----- AI 原话开始 -----")
logging.info("%s", assistant_text)
logging.info("----- AI 原话结束 -----")

# 提取末尾数字
digit = assistant_text.strip()[-1]
if digit not in {"1", "2", "3"}:
    digit = "3"
    logging.warning("末尾字符非法，已强制置 3")

logging.info("分析结果 result = %s", digit)

# -------------------- 写数据库 --------------------
from db import get_conn
import pathlib

port = int(pathlib.Path("a/last_port.txt").read_text().strip())
now = time.strftime("%Y-%m-%d %H:%M:%S")

with get_conn() as conn:
    # 1. 找刚才拍照插入的空行（raw='' 且最新）
    row = conn.execute(
        "SELECT id FROM events WHERE port=? AND raw='' ORDER BY time DESC LIMIT 1",
        (port,)
    ).fetchone()

    if row:
        # 更新这一行
        conn.execute(
            "UPDATE events SET result=?, raw=?, desc=? WHERE id=?",
            (digit, assistant_text.strip(), assistant_text.split('\n')[0][:60], row["id"])
        )
        logging.info("已更新 events.id=%s 的 AI 原话", row["id"])
    else:
        # 兜底：没有空行就新建
        conn.execute(
            "INSERT INTO events(time,port,result,desc,raw) VALUES(?,?,?,?,?)",
            (now, port, digit, assistant_text.split('\n')[0][:60], assistant_text.strip())
        )
        logging.info("已插入新事件并写入 AI 原话")
    conn.commit()
# -------------------- 写回结果文件供前端轮询 --------------------
result_file = pathlib.Path(__file__).with_name("a").joinpath("jiaoshi_result.json")
result_file.write_text(json.dumps({"result": digit, "ts": int(time.time())}), encoding='utf-8')
logging.info("已写回 jiaoshi_result.json：%s", digit)
# -------------------- 清理图片 --------------------
img_path.unlink(missing_ok=True)
logging.info("已删除 %s，运行结束", img_path)