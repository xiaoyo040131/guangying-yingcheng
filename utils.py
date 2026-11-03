# -*- coding: utf-8 -*-
"""
utils.py —— 公共工具

包含：
- BizError：业务异常。校验失败抛出，视图层捕获后 flash 给用户（保证不出现 500）。
- 各类状态码 -> 中文 的字典（订单、影片、影厅、座位、场次）。
- 金额换算（分 <-> 元）、时间换算（'HH:MM' <-> 分钟）。
- 手机号、姓名、日期等输入校验（后端统一校验一切输入）。
"""
import re
from datetime import datetime, date


class BizError(Exception):
    """业务错误：消息为中文提示，直接展示给用户。"""
    pass


# ---------------- 状态中文字典 ----------------

# 订单状态（需求说明第九节状态机）
ORDER_STATUS = {
    'UNPAID': '待支付',
    'PAID': '已支付',
    'CANCELLED': '已取消',
    'EXPIRED': '已超时',
    'REFUNDED': '已退票',
    'USED': '已检票',
}

# 影片状态
MOVIE_STATUS = {
    'SHOWING': '热映',
    'COMING': '即将上映',
    'OFF': '下映',
}

# 影厅类型
HALL_TYPE = {
    'NORMAL': '普通厅',
    'IMAX': 'IMAX厅',
    'VIP': 'VIP厅',
}

# 座位类型
SEAT_TYPE = {
    'NORMAL': '普通座',
    'COUPLE': '情侣座',
    'VIP': 'VIP座',
}

# 场次状态
SHOWTIME_STATUS = {
    'ON_SALE': '在售',
    'STOPPED': '停售',
    'CANCELLED': '已取消',
}

# 星期几的中文显示
WEEKDAY_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


# ---------------- 时间与日期 ----------------

def now_str():
    """当前时间，存库格式 'YYYY-MM-DD HH:MM:SS'。"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today_str():
    """今天日期，'YYYY-MM-DD'。"""
    return date.today().isoformat()


def parse_date(s):
    """把 'YYYY-MM-DD' 解析为 date，失败抛 BizError。"""
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        raise BizError('日期格式不正确')


def parse_datetime(s):
    """把 'YYYY-MM-DD HH:MM:SS' 解析为 datetime，失败抛 BizError。"""
    try:
        return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        raise BizError('时间格式不正确')


def hhmm_to_min(s):
    """'HH:MM' -> 当天第几分钟（'24:00' -> 1440）。"""
    try:
        h, m = s.split(':')
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        raise BizError('时间格式不正确')
    if not (0 <= h <= 24 and 0 <= m <= 59) or h * 60 + m > 24 * 60:
        raise BizError('时间格式不正确')
    return h * 60 + m


def min_to_hhmm(m):
    """分钟 -> 'HH:MM'（零填充，支持 '24:00'）。"""
    return '%02d:%02d' % (m // 60, m % 60)


def add_minutes(hhmm, minutes):
    """'HH:MM' 加若干分钟，返回 'HH:MM'（可能得到 '24:00' 之内或之后，由调用方校验上限）。"""
    return min_to_hhmm(hhmm_to_min(hhmm) + minutes)


def fmt_date_cn(d):
    """'YYYY-MM-DD' -> 'M月D日 周X'，用于页面日期标签。"""
    dt = parse_date(d)
    return '%d月%d日 %s' % (dt.month, dt.day, WEEKDAY_CN[dt.weekday()])


# ---------------- 金额 ----------------

def fen_to_yuan(fen):
    """分 -> 元（字符串，两位小数），模板过滤器用。"""
    try:
        return '%.2f' % (int(fen) / 100)
    except (ValueError, TypeError):
        return '0.00'


def rating_str(rating_x10):
    """评分以 x10 整数存库（如 91），显示为 '9.1'。"""
    try:
        return '%.1f' % (int(rating_x10) / 10)
    except (ValueError, TypeError):
        return '0.0'


# ---------------- 输入校验（后端必须校验一切输入） ----------------

_PHONE_RE = re.compile(r'^1[3-9]\d{9}$')


def check_phone(phone):
    """校验手机号：1[3-9] 开头的 11 位数字。"""
    if not phone or not _PHONE_RE.match(str(phone).strip()):
        raise BizError('请输入正确的 11 位手机号')
    return str(phone).strip()


def check_name(name):
    """校验姓名：去掉首尾空格后 2~20 个字符。"""
    name = (name or '').strip()
    if not (2 <= len(name) <= 20):
        raise BizError('请输入 2~20 个字符的姓名')
    return name


def check_limit_date(d, field='日期'):
    """校验日期字段格式合法。"""
    parse_date(d)
    return d


def check_int_in(value, low, high, msg):
    """校验整数在 [low, high] 范围内，否则抛 BizError(msg)。"""
    try:
        v = int(value)
    except (ValueError, TypeError):
        raise BizError(msg)
    if not (low <= v <= high):
        raise BizError(msg)
    return v
