# services/order_service.py —— 订单业务（核心）
"""
职责：
- 状态机（项目规范第 4 节第 5 条）：TRANSITIONS 定义全部合法流转；
  更新一律 "UPDATE ... WHERE id=? AND status IN (...)" 并检查 rowcount；
  每次流转写一条 order_log（谁、何时、从什么到什么、备注）。
- R4 下单锁座：BEGIN IMMEDIATE 事务内 先过期结算 -> 再查占用 -> 再写入；
  order_seat 的 UNIQUE(show_id, seat_id) 兜底，冲突整单失败并提示具体座位。
- R5 超时释放：所有读座位图 / 订单列表 / 下单之前先结算过期订单。
- R6 支付：事务内复查截止时间，支付成功生成 8 位不重复取票码。
- R7 退票：已支付且开场前 >= 2 小时，退票删除 order_seat 释放座位。
- R8 检票：已支付订单在 [开场前 60 分钟, 开场后 30 分钟] 内可检票。
"""
import functools
import random
import sqlite3
from datetime import datetime, timedelta

import db
from config import (CHECKIN_EARLY_MINUTES, CHECKIN_LATE_MINUTES,
                    PAY_LOCK_MINUTES, REFUND_BEFORE_MINUTES)
from dao import order_dao, showtime_dao
from services import pricing, seat_service, showtime_service
from utils import BizError, ORDER_STATUS, check_name, check_phone, now_str

# 状态机：动作 -> (允许的起始状态元组, 目标状态, 按钮文字)
TRANSITIONS = {
    'create': ((), 'UNPAID', '确认下单'),
    'pay': (('UNPAID',), 'PAID', '立即支付'),
    'cancel': (('UNPAID',), 'CANCELLED', '取消订单'),
    'expire': (('UNPAID',), 'EXPIRED', '超时取消'),
    'refund': (('PAID',), 'REFUNDED', '申请退票'),
    'check': (('PAID',), 'USED', '确认检票'),
}


def _db_guard(func):
    """把数据库"锁等待超时"等异常转成中文提示，保证用户操作不出现 500。"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if 'locked' in msg or 'busy' in msg:
                raise BizError('当前下单人数较多，系统繁忙，请稍后重试')
            raise BizError('数据操作失败，请稍后重试')
    return wrapper


# ---------------- R5 过期结算 ----------------

def _settle_expired_in_tx(now=None):
    """事务内版本：把支付截止时间已过的待支付订单改为已超时，删座位，写轨迹。

    调用方必须已经在 db.transaction() 里（不能嵌套 BEGIN）。
    返回本次结算的订单数。
    """
    now = now or now_str()
    ids = order_dao.expire_overdue(now)          # 条件 UPDATE：status='UNPAID' AND pay_deadline < now
    for oid in ids:
        order_dao.delete_order_seats(oid)         # 释放座位
        order_dao.add_log(oid, 'expire', 'UNPAID', 'EXPIRED', 'system',
                          '超时未支付，系统自动释放座位', now)
    return len(ids)


@_db_guard
def settle_expired():
    """独立事务版本：读座位图、订单列表、下单之前都先调用一次。"""
    with db.transaction():
        return _settle_expired_in_tx()


# ---------------- 订单号 / 取票码 ----------------

def _gen_order_no():
    """订单号：GY + 时间 + 4 位随机，重复则重试。"""
    for _ in range(30):
        no = 'GY' + datetime.now().strftime('%Y%m%d%H%M%S') + str(random.randint(1000, 9999))
        if not order_dao.order_no_exists(no):
            return no
    raise BizError('订单号生成失败，请重试')


def _gen_ticket_code():
    """8 位数字取票码（首位非 0），UNIQUE 约束保证不重复。"""
    for _ in range(50):
        code = str(random.randint(10000000, 99999999))
        if not order_dao.ticket_code_exists(code):
            return code
    raise BizError('取票码生成失败，请重试')


# ---------------- R4 下单锁座 ----------------

@_db_guard
def create_order(show_id, seat_ids, phone, name):
    """创建待支付订单并锁座。返回订单行（含影片/场次信息）。"""
    phone = check_phone(phone)
    name = check_name(name)

    show = showtime_dao.get_by_id(show_id)
    if not show:
        raise BizError('场次不存在')
    showtime_service.assert_bookable(show)          # R1

    seat_ids = seat_service.normalize_seat_ids(seat_ids)
    seats = seat_service.validate_selection(show, seat_ids)   # R2

    now_dt = datetime.now()
    now = now_dt.strftime('%Y-%m-%d %H:%M:%S')
    deadline = (now_dt + timedelta(minutes=PAY_LOCK_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')

    try:
        with db.transaction():                      # BEGIN IMMEDIATE：进入即加写锁
            _settle_expired_in_tx(now)              # R5：事务内先结算过期订单
            taken = order_dao.taken_seats_detail(show['id'], [s['id'] for s in seats])
            if taken:                               # 再检查：可能刚被别人买走
                labels = '、'.join('%d排%d座' % (t['row_no'], t['col_no']) for t in taken)
                raise BizError('%s 已被其他观众选走，整单下单失败，请重新选座' % labels)

            items, total = pricing.price_detail(show['base_price'], seats)   # R3
            order_no = _gen_order_no()
            order_id = order_dao.insert(order_no, show['id'], phone, name,
                                        len(seats), total, 'UNPAID', deadline, now)
            for it in items:
                order_dao.insert_order_seat(order_id, show['id'], it['seat_id'], it['price'])
            order_dao.add_log(order_id, 'create', '-', 'UNPAID', phone[-4:], '下单锁座', now)
    except sqlite3.IntegrityError:
        # UNIQUE(show_id, seat_id) 兜底：并发时后写入的一方整单回滚
        raise BizError('座位刚刚被其他观众选走，整单下单失败，请重新选座')

    return order_dao.get_by_id(order_id)


# ---------------- 查询 ----------------

def _show_start(order_row):
    return showtime_service.show_start_dt(order_row)


def _flags(order_row, now=None):
    """订单可执行的操作标记（页面按钮显示用）。"""
    now = now or datetime.now()
    status = order_row['status']
    start = _show_start(order_row)
    deadline = datetime.strptime(order_row['pay_deadline'], '%Y-%m-%d %H:%M:%S')
    return {
        'can_pay': status == 'UNPAID' and deadline > now,
        'can_cancel': status == 'UNPAID',
        'can_refund': status == 'PAID' and start - now >= timedelta(minutes=REFUND_BEFORE_MINUTES),
        'can_checkin': (status == 'PAID'
                        and start - timedelta(minutes=CHECKIN_EARLY_MINUTES) <= now
                        <= start + timedelta(minutes=CHECKIN_LATE_MINUTES)),
        'pay_remain_seconds': max(int((deadline - now).total_seconds()), 0)
                              if status == 'UNPAID' else 0,
    }


def get_order_detail(order_no, phone=None, name=None):
    """订单详情（含座位明细、轨迹、可执行操作）。

    传了 phone/name 就做身份校验：不匹配返回 None（不暴露"订单存在但不是你的"）。
    """
    settle_expired()                                # R5
    order = order_dao.get_by_no(order_no)
    if not order:
        return None
    if phone is not None and (order['phone'] != phone or order['customer_name'] != name):
        return None
    return {
        'order': order,
        'seats': order_dao.seats_of_order(order['id']),
        'logs': order_dao.logs_of_order(order['id']),
        'flags': _flags(order),
    }


def list_my_orders(phone, name):
    """我的订单：手机号 + 姓名同时匹配（身份条件写进 SQL，防越权）。"""
    phone = check_phone(phone)
    name = check_name(name)
    settle_expired()                                # R5：列表里不会出现已过期却仍显示"待支付"的单
    rows = order_dao.list_by_identity(phone, name)
    now = datetime.now()
    result = []
    for r in rows:
        seats = order_dao.seats_of_order(r['id'])
        result.append({
            'order': r,
            'seats': seats,
            'flags': _flags(r, now),
            'status_text': ORDER_STATUS.get(r['status'], r['status']),
        })
    return result


# ---------------- 状态流转 ----------------

@_db_guard
def pay(order_no):
    """R6 支付：只有未过期的待支付订单可以支付；事务内复查截止时间。"""
    now = now_str()
    with db.transaction():
        _settle_expired_in_tx(now)                  # 先结算，已超时的单会被改成 EXPIRED
        order = order_dao.get_by_no(order_no)
        if not order:
            raise BizError('订单不存在')
        if order['status'] != 'UNPAID':
            raise BizError('订单当前为「%s」，不能支付'
                           % ORDER_STATUS.get(order['status'], order['status']))
        if order['pay_deadline'] <= now:
            raise BizError('订单已超过 %d 分钟支付时限，座位已释放，请重新选座' % PAY_LOCK_MINUTES)
        code = _gen_ticket_code()
        rowcount = order_dao.mark_paid(order['id'], code, now, now)
        if rowcount != 1:
            raise BizError('订单状态已变化，请刷新后重试')
        order_dao.add_log(order['id'], 'pay', 'UNPAID', 'PAID', order['phone'][-4:],
                          '模拟支付成功，取票码 %s' % code, now)
    return order_dao.get_by_no(order_no)


@_db_guard
def cancel(order_no, phone=None, name=None):
    """取消待支付订单：删除 order_seat 释放座位（R5 状态机）。"""
    now = now_str()
    with db.transaction():
        order = order_dao.get_by_no(order_no)
        if not order:
            raise BizError('订单不存在')
        if phone is not None and (order['phone'] != phone or order['customer_name'] != name):
            raise BizError('订单不存在或身份不匹配')
        rowcount = order_dao.mark_cancelled(order['id'], now)
        if rowcount != 1:
            raise BizError('订单当前为「%s」，不能取消'
                           % ORDER_STATUS.get(order['status'], order['status']))
        order_dao.delete_order_seats(order['id'])
        order_dao.add_log(order['id'], 'cancel', 'UNPAID', 'CANCELLED',
                          (phone or order['phone'])[-4:], '观众主动取消，座位已释放', now)
    return order_dao.get_by_no(order_no)


@_db_guard
def refund(order_no, phone=None, name=None, operator='观众'):
    """R7 退票：已支付且开场前 >= 2 小时；退票删除 order_seat 释放座位。"""
    now_dt = datetime.now()
    now = now_dt.strftime('%Y-%m-%d %H:%M:%S')
    with db.transaction():
        order = order_dao.get_by_no(order_no)
        if not order:
            raise BizError('订单不存在')
        if phone is not None and (order['phone'] != phone or order['customer_name'] != name):
            raise BizError('订单不存在或身份不匹配')
        if order['status'] != 'PAID':
            raise BizError('订单当前为「%s」，只有已支付订单可以退票'
                           % ORDER_STATUS.get(order['status'], order['status']))
        start = _show_start(order)
        if start - now_dt < timedelta(minutes=REFUND_BEFORE_MINUTES):
            raise BizError('开场前不足 2 小时，无法退票（开场时间 %s %s）'
                           % (order['show_date'], order['start_time']))
        rowcount = order_dao.mark_refunded(order['id'], now, now)
        if rowcount != 1:
            raise BizError('订单状态已变化，请刷新后重试')
        order_dao.delete_order_seats(order['id'])
        order_dao.add_log(order['id'], 'refund', 'PAID', 'REFUNDED', operator,
                          '退票成功，座位已释放', now)
    return order_dao.get_by_no(order_no)


def checkin_by_code(code, operator=None):
    """后台检票：按 8 位取票码找订单并检票（R8 时间窗）。"""
    code = (code or '').strip()
    if not (code.isdigit() and len(code) == 8):
        raise BizError('请输入 8 位数字取票码')
    order = order_dao.find_by_ticket_code(code)
    if not order:
        raise BizError('未找到该取票码对应的订单')
    return checkin_order(order['id'], operator=operator)


@_db_guard
def checkin_order(order_id, operator=None):
    """R8 检票：已支付订单在 [开场前 60 分钟, 开场后 30 分钟] 内可检票，且不能重复检票。"""
    now_dt = datetime.now()
    now = now_dt.strftime('%Y-%m-%d %H:%M:%S')
    with db.transaction():
        order = order_dao.get_by_id(order_id)
        if not order:
            raise BizError('订单不存在')
        if order['status'] == 'USED':
            raise BizError('该订单已检票，不能重复检票')
        if order['status'] != 'PAID':
            raise BizError('订单当前为「%s」，只有已支付订单可以检票'
                           % ORDER_STATUS.get(order['status'], order['status']))
        start = _show_start(order)
        early = start - timedelta(minutes=CHECKIN_EARLY_MINUTES)
        late = start + timedelta(minutes=CHECKIN_LATE_MINUTES)
        if not (early <= now_dt <= late):
            raise BizError('当前不在检票时间内（开场前 %d 分钟至开场后 %d 分钟，本场开场 %s %s）'
                           % (CHECKIN_EARLY_MINUTES, CHECKIN_LATE_MINUTES,
                              order['show_date'], order['start_time']))
        rowcount = order_dao.mark_used(order['id'], now, now)
        if rowcount != 1:
            raise BizError('订单状态已变化，请刷新后重试')
        order_dao.add_log(order['id'], 'check', 'PAID', 'USED', operator or 'admin',
                          '检票入场', now)
    return order_dao.get_by_id(order_id)


# ---------------- 后台订单列表 ----------------

def admin_orders(status=None, keyword=None, date_str=None, page=1, page_size=10):
    """后台订单管理：状态标签 + 搜索 + 日期筛选 + 分页。"""
    settle_expired()                                # R5
    if status and status not in ORDER_STATUS:
        status = None
    keyword = (keyword or '').strip() or None
    offset = (page - 1) * page_size
    rows = order_dao.admin_list(status, keyword, date_str, offset, page_size)
    total = order_dao.admin_count(status, keyword, date_str)
    pages = max((total + page_size - 1) // page_size, 1)
    return {
        'orders': rows,
        'total': total,
        'page': page,
        'pages': pages,
        'status': status,
        'keyword': keyword or '',
        'date': date_str or '',
        'status_count': order_dao.count_by_status(),
    }
