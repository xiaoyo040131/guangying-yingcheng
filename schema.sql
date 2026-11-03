-- ============================================================
-- schema.sql —— 光影影城建表脚本
-- 约定：
--   金额一律以"分"存 INTEGER；日期 'YYYY-MM-DD'；时间 'HH:MM'；
--   时间戳 'YYYY-MM-DD HH:MM:SS'；布尔 0/1 加 CHECK。
--   order_seat 的 UNIQUE(show_id, seat_id) 是"一座一票"的防重复占用兜底约束。
-- ============================================================

PRAGMA foreign_keys = ON;

-- 先删后建，保证 init_db.py 可重复执行
DROP TABLE IF EXISTS order_log;
DROP TABLE IF EXISTS order_seat;
DROP TABLE IF EXISTS ticket_order;
DROP TABLE IF EXISTS showtime;
DROP TABLE IF EXISTS seat;
DROP TABLE IF EXISTS hall;
DROP TABLE IF EXISTS movie;
DROP TABLE IF EXISTS admin_user;

-- 影片表：评分以 x10 整数存（91 显示 9.1）
CREATE TABLE movie (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,                          -- 片名
    title_en     TEXT    DEFAULT '',                        -- 英文名
    poster       TEXT    NOT NULL,                          -- 海报文件名（不带扩展名，poster-1~8）
    genre        TEXT    NOT NULL,                          -- 类型，如 "悬疑/犯罪"
    duration     INTEGER NOT NULL CHECK (duration BETWEEN 60 AND 240),   -- 片长（分钟）
    director     TEXT    NOT NULL,                          -- 导演
    actors       TEXT    NOT NULL,                          -- 主演
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 0 AND 100),      -- 评分 x10
    synopsis     TEXT    NOT NULL DEFAULT '',               -- 简介
    release_date TEXT    NOT NULL,                          -- 上映日期 'YYYY-MM-DD'
    status       TEXT    NOT NULL CHECK (status IN ('SHOWING','COMING','OFF'))  -- 热映/即将上映/下映
);
CREATE INDEX idx_movie_status ON movie(status);

-- 影厅表
CREATE TABLE hall (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,               -- 厅名，如 "1号厅"
    type            TEXT    NOT NULL CHECK (type IN ('NORMAL','IMAX','VIP')),
    row_count       INTEGER NOT NULL CHECK (row_count > 0),
    col_count       INTEGER NOT NULL CHECK (col_count > 0),
    aisle_after_col INTEGER,                               -- 过道位置（该列后为过道，NULL=无过道）
    is_enabled      INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1))
);

-- 座位表：每个影厅的每个座位一行；情侣座通过 couple_partner_id 配对
CREATE TABLE seat (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    hall_id           INTEGER NOT NULL REFERENCES hall(id),
    row_no            INTEGER NOT NULL CHECK (row_no > 0),
    col_no            INTEGER NOT NULL CHECK (col_no > 0),
    seat_type         TEXT    NOT NULL CHECK (seat_type IN ('NORMAL','COUPLE','VIP')),
    couple_partner_id INTEGER REFERENCES seat(id),          -- 情侣座另一半（可空）
    is_enabled        INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
    UNIQUE (hall_id, row_no, col_no)                         -- 同一厅同行同列只有一个座位
);
CREATE INDEX idx_seat_hall ON seat(hall_id);

-- 场次表：结束时间已含 15 分钟清场；排片不重叠由业务层在事务内校验
CREATE TABLE showtime (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_id   INTEGER NOT NULL REFERENCES movie(id),
    hall_id    INTEGER NOT NULL REFERENCES hall(id),
    show_date  TEXT    NOT NULL,                            -- 'YYYY-MM-DD'
    start_time TEXT    NOT NULL,                            -- 'HH:MM'（零填充）
    end_time   TEXT    NOT NULL CHECK (end_time > start_time AND end_time <= '24:00'),
    base_price INTEGER NOT NULL CHECK (base_price > 0),    -- 基础票价（分），座位类型另加价
    status     TEXT    NOT NULL DEFAULT 'ON_SALE'
               CHECK (status IN ('ON_SALE','STOPPED','CANCELLED'))
);
CREATE INDEX idx_showtime_hall_date ON showtime(hall_id, show_date);
CREATE INDEX idx_showtime_movie     ON showtime(movie_id, show_date);

-- 订单表：手机号 + 姓名同时匹配确认身份
CREATE TABLE ticket_order (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no     TEXT    NOT NULL UNIQUE,                   -- 订单号，展示用
    show_id      INTEGER NOT NULL REFERENCES showtime(id),
    phone        TEXT    NOT NULL CHECK (length(phone) = 11),
    customer_name TEXT   NOT NULL,
    seat_count   INTEGER NOT NULL CHECK (seat_count BETWEEN 1 AND 4),
    total_price  INTEGER NOT NULL CHECK (total_price > 0),  -- 总价（分）
    ticket_code  TEXT    UNIQUE,                            -- 8 位取票码，支付成功后生成（可空）
    status       TEXT    NOT NULL
                 CHECK (status IN ('UNPAID','PAID','CANCELLED','EXPIRED','REFUNDED','USED')),
    pay_deadline TEXT    NOT NULL,                          -- 支付截止 = 创建 + 15 分钟（超时结算依据）
    paid_at      TEXT,                                      -- 支付时间
    checkin_at   TEXT,                                      -- 检票时间
    refund_at    TEXT,                                      -- 退票时间
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE INDEX idx_order_phone   ON ticket_order(phone, customer_name);
CREATE INDEX idx_order_status  ON ticket_order(status);
CREATE INDEX idx_order_show    ON ticket_order(show_id);
CREATE INDEX idx_order_deadline ON ticket_order(pay_deadline);

-- 订单座位表：★ UNIQUE(show_id, seat_id) 防重复占用 —— 同一场次同一座位
-- 最多只能出现在一笔订单里（含当前锁座中/已支付/已检票的订单），数据库层兜底。
CREATE TABLE order_seat (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES ticket_order(id),
    show_id  INTEGER NOT NULL REFERENCES showtime(id),
    seat_id  INTEGER NOT NULL REFERENCES seat(id),
    price    INTEGER NOT NULL CHECK (price > 0),            -- 该座实付票价（分，含座位类型加价）
    UNIQUE (show_id, seat_id)
);
CREATE INDEX idx_order_seat_order ON order_seat(order_id);

-- 订单轨迹表：每次状态流转记一条（谁、何时、从什么到什么、备注）
CREATE TABLE order_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES ticket_order(id),
    action      TEXT    NOT NULL,                           -- create/pay/cancel/expire/refund/check
    from_status TEXT    NOT NULL DEFAULT '-',
    to_status   TEXT    NOT NULL,
    operator    TEXT    NOT NULL,                           -- 手机号后4位 / admin / system
    remark      TEXT    DEFAULT '',
    created_at  TEXT    NOT NULL
);
CREATE INDEX idx_log_order ON order_log(order_id);

-- 管理员表：密码只存哈希（werkzeug.security 生成）
CREATE TABLE admin_user (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
