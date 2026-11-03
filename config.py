# -*- coding: utf-8 -*-
"""
config.py —— 全局配置

所有可调参数集中在这里：数据库路径、支付锁座时长、退票/检票时间窗、
座位加价、排片窗口等。SECRET_KEY 首次运行时自动生成到 instance/secret.key，
之后一直复用（保证 session 不因重启而失效）。
"""
import os
import secrets

# 项目根目录（config.py 所在目录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 运行时目录：数据库、密钥都放在 instance/（已在 .gitignore 中排除）
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')

# SQLite 数据库文件路径
DATABASE = os.path.join(INSTANCE_DIR, 'guangying.db')

# SECRET_KEY 文件路径
SECRET_KEY_FILE = os.path.join(INSTANCE_DIR, 'secret.key')


def get_secret_key():
    """首次运行时用 secrets.token_hex(32) 生成密钥并保存，之后读取复用。"""
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'r', encoding='utf-8') as f:
            key = f.read().strip()
            if key:
                return key
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, 'w', encoding='utf-8') as f:
        f.write(key)
    return key


# ---------------- 业务参数 ----------------

# 下单后锁座时长（分钟）：订单"待支付"的支付截止时间 = 创建时间 + 15 分钟
PAY_LOCK_MINUTES = 15

# 退票时间窗：开场前不足 2 小时不能退票（R7）
REFUND_BEFORE_MINUTES = 2 * 60

# 检票时间窗：开场前 60 分钟 ~ 开场后 30 分钟（R8）
CHECKIN_EARLY_MINUTES = 60
CHECKIN_LATE_MINUTES = 30

# 停售时间窗：开场前 30 分钟停止售票（R1）
STOP_SALE_MINUTES = 30

# 可售日期窗口：今天起 5 天内（R1），排片/购票都不能超出
BOOKABLE_DAYS = 5

# 排片时间轴范围与清场间隔（R9：结束时间 = 开始 + 片长 + 15 分钟清场）
SCHEDULE_START = '09:00'
SCHEDULE_END = '24:00'
CLEAN_MINUTES = 15

# 每单最多选座数
MAX_SEATS_PER_ORDER = 4

# 座位类型加价（单位：分）。R3：VIP +20 元，情侣座每座 +5 元
VIP_ADD_FEN = 2000
COUPLE_ADD_FEN = 500

# 后台管理员账号（演示数据在 init_db.py 中写入，密码 guangying2026）
ADMIN_USERNAME = 'admin'

# 每页订单条数（后台订单管理分页）
PAGE_SIZE = 10
