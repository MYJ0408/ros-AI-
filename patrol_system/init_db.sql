-- 用户表
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    created DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 默认管理员账号
INSERT INTO users (username, password) VALUES ('admin', '123456');

-- 小车配置表
CREATE TABLE cars (
    port INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    area TEXT NOT NULL,
    created DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 事件记录表
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time DATETIME NOT NULL,
    port INTEGER NOT NULL,
    result INTEGER NOT NULL,
    desc TEXT,
    raw TEXT
);
