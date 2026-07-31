import sqlite3
import pathlib

# 数据库文件路径
DB_FILE = pathlib.Path(__file__).with_name("patrol.db")

# 如果数据库已存在，先删掉（调试阶段方便）
if DB_FILE.exists():
    DB_FILE.unlink()

# 连接数据库（会自动创建）
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# 创建用户表
cursor.execute('''
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    created DATETIME DEFAULT CURRENT_TIMESTAMP
);
''')

# 插入默认管理员
cursor.execute("INSERT INTO users (username, password) VALUES ('admin', '123456')")

# 创建小车表
cursor.execute('''
CREATE TABLE cars (
    port INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    area TEXT NOT NULL,
    created DATETIME DEFAULT CURRENT_TIMESTAMP
);
''')

# 创建事件记录表
cursor.execute('''
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time DATETIME NOT NULL,
    port INTEGER NOT NULL,
    result INTEGER NOT NULL,
    desc TEXT,
    raw TEXT
);
''')

# 保存并关闭
conn.commit()
conn.close()

print("✅ 数据库创建完成：", DB_FILE.resolve())
