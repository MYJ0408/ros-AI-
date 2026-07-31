#!/usr/bin/env python3
import sqlite3, pathlib, contextlib

DB_FILE = pathlib.Path(__file__).with_name("patrol.db")

def get_conn():
    """返回线程安全的连接对象，支持 with 语法"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return contextlib.closing(conn)