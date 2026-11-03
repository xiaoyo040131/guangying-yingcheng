# -*- coding: utf-8 -*-
"""
dao/order_dao.py —— 订单 / 订单座位 / 订单轨迹数据访问

只放参数化 SQL，不碰 request / session，不做业务判断。
状态流转的 UPDATE 都带 "AND status IN (...)" 条件并检查 rowcount，
由业务层（order_service）的状态机调用。
"""
from db import query_all, query_one, execute


# ---------------- 订单基础 ----------------

def insert(order_no, show_id, phone, customer_name, seat_count, total_price,
           status, pay_deadline, now):
    """创建订单，返回新订单 id。"""
    cur = execute(
        "INSERT INTO ticket_order (order_no, show_id, phone, customer_name, seat_count, "
        "total_price, status, pay_deadline, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_no, show_id, phone, customer_name, seat_count, total_price,
         status, pay_deadline, now, now))
    return cur.lastrowid


def get_by_id(order_id):
    """按主键查询（带影片、场次、影厅信息，页面展示用）。"""
    sql = ("SELECT o.*, st.show_date, st.start_time, st.base_price, st.status AS show_status, "
           "m.title, m.poster, h.name AS hall_name, h.type AS hall_type "
           "FROM ticket_order o "
           "JOIN showtime st ON st.id = o.show_id "
           "JOIN movie m ON m.id = st.movie_id "
           "JOIN hall h ON h.id = st.hall_id "
           "WHERE o.id = ?")
    return query_one(sql, (order_id,))


def get_by_no(order_no):
    """按订单号查询（支付页 / 详情 / 检票）。字段同 get_by_id。"""
    sql = ("SELECT o.*, st.show_date, st.start_time, st.base_price, st.status AS show_status, "
           "m.title, m.poster, h.name AS hall_name, h.type AS hall_type "
           "FROM ticket_order o "
           "JOIN showtime st ON st.id = o.show_id "
           "JOIN movie m ON m.id = st.movie_id "
           "JOIN hall h ON h.id = st.hall_id "
           "WHERE o.order_no = ?")
    return query_one(sql, (order_no,))


def list_by_identity(phone, customer_name):
    """我的订单：手机号 + 姓名 同时匹配才返回（身份条件必须进 SQL，防越权）。"""
    sql = ("SELECT o.*, st.show_date, st.start_time, m.title, m.poster, "
           "h.name AS hall_name, h.type AS hall_type "
           "FROM ticket_order o "
           "JOIN showtime st ON st.id = o.show_id "
           "JOIN movie m ON m.id = st.movie_id "
           "JOIN hall h ON h.id = st.hall_id "
           "WHERE o.phone = ? AND o.customer_name = ? "
           "ORDER BY o.created_at DESC, o.id DESC")
    return query_all(sql, (phone, customer_name))


# ---------------- 状态机流转（UPDATE ... WHERE status IN ...，检查 rowcount） ----------------

def _transition(order_id, to_status, from_statuses, now, extra_sql='', extra_args=()):
    """通用流转：UPDATE ticket_order SET status=?[, 附加字段] WHERE id=? AND status IN (...)。

    返回 rowcount；0 表示当前状态不在允许范围（状态已被别人改掉，需提示刷新）。
    """
    marks = ', '.join('?' for _ in from_statuses)
    sql = ("UPDATE ticket_order SET status = ?, updated_at = ?" + extra_sql +
           " WHERE id = ? AND status IN (" + marks + ")")
    args = [to_status, now] + list(extra_args) + [order_id] + list(from_statuses)
    cur = execute(sql, args)
    return cur.rowcount


def mark_paid(order_id, ticket_code, paid_at, now):
    """UNPAID -> PAID（R6：业务层已在事务内复查未过期）。"""
    return _transition(order_id, 'PAID', ('UNPAID',), now,
                       extra_sql=', paid_at = ?, ticket_code = ?',
                       extra_args=(paid_at, ticket_code))


def mark_cancelled(order_id, now):
    """UNPAID -> CANCELLED。"""
    return _transition(order_id, 'CANCELLED', ('UNPAID',), now)


def mark_refunded(order_id, refund_at, now):
    """PAID -> REFUNDED（R7：开场前 ≥ 2 小时，业务层校验）。"""
    return _transition(order_id, 'REFUNDED', ('PAID',), now,
                       extra_sql=', refund_at = ?', extra_args=(refund_at,))


def mark_used(order_id, checkin_at, now):
    """PAID -> USED（R8：检票时间窗，业务层校验）。"""
    return _transition(order_id, 'USED', ('PAID',), now,
                       extra_sql=', checkin_at = ?', extra_args=(checkin_at,))


def expire_overdue(now_str):
    """R5 过期结算：把支付截止时间已过的待支付订单批量改为 EXPIRED。

    返回被结算的订单 id 列表（业务层据此删除 order_seat 并写轨迹）。
    """
    rows = query_all(
        "SELECT id FROM ticket_order WHERE status = 'UNPAID' AND pay_deadline < ?",
        (now_str,))
    if not rows:
        return []
    ids = [r['id'] for r in rows]
    marks = ', '.join('?' for _ in ids)
    execute("UPDATE ticket_order SET status = 'EXPIRED', updated_at = ? "
            "WHERE id IN (" + marks + ")", [now_str] + ids)
    return ids


# ---------------- 订单座位 ----------------

def insert_order_seat(order_id, show_id, seat_id, price):
    """锁座写入一条订单座位。UNIQUE(show_id, seat_id) 冲突时抛 IntegrityError，
    由业务层捕获转成"座位已被选走"的中文提示。"""
    return execute(
        "INSERT INTO order_seat (order_id, show_id, seat_id, price) VALUES (?, ?, ?, ?)",
        (order_id, show_id, seat_id, price))


def delete_order_seats(order_id):
    """删除订单的全部座位记录（取消 / 超时 / 退票时释放座位）。"""
    return execute("DELETE FROM order_seat WHERE order_id = ?", (order_id,))


def seat_ids_of_order(order_id):
    """订单占用的座位 id 列表。"""
    rows = query_all("SELECT seat_id FROM order_seat WHERE order_id = ?", (order_id,))
    return [r['seat_id'] for r in rows]


def sold_seat_ids(show_id):
    """场次当前已被占用的座位 id 列表（画座位图"已售/锁定"用）。"""
    rows = query_all("SELECT seat_id FROM order_seat WHERE show_id = ?", (show_id,))
    return [r['seat_id'] for r in rows]


def seats_of_order(order_id):
    """订单座位明细（带座位位置信息，详情页展示）。"""
    sql = ("SELECT os.price, s.row_no, s.col_no, s.seat_type "
           "FROM order_seat os JOIN seat s ON s.id = os.seat_id "
           "WHERE os.order_id = ? ORDER BY s.row_no, s.col_no")
    return query_all(sql, (order_id,))


def seat_id_taken(show_id, seat_id):
    """座位是否已被占（UNIQUE 约束前的友好检查，事务内再查一次）。"""
    return query_one("SELECT id FROM order_seat WHERE show_id = ? AND seat_id = ?",
                     (show_id, seat_id)) is not None


# ---------------- 订单轨迹 ----------------

def add_log(order_id, action, from_status, to_status, operator, remark, now):
    """写一条状态流转日志。"""
    return execute(
        "INSERT INTO order_log (order_id, action, from_status, to_status, operator, remark, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (order_id, action, from_status, to_status, operator, remark, now))


def logs_of_order(order_id):
    """订单轨迹列表（按时间正序）。"""
    return query_all("SELECT * FROM order_log WHERE order_id = ? ORDER BY id", (order_id,))


# ---------------- 取票码 ----------------

def ticket_code_exists(code):
    """8 位取票码是否已存在（生成时循环重试保证唯一）。"""
    return query_one("SELECT id FROM ticket_order WHERE ticket_code = ?", (code,)) is not None


# ---------------- 后台订单管理（列表 / 搜索 / 筛选 / 分页） ----------------

def admin_list(status=None, keyword=None, date_str=None, offset=0, limit=10):
    """后台订单列表：状态标签页 + 关键词（订单号/手机号/取票码）+ 日期筛选 + 分页。"""
    where, args = [], []
    if status:
        where.append("o.status = ?")
        args.append(status)
    if keyword:
        kw = '%' + keyword + '%'
        where.append("(o.order_no LIKE ? OR o.phone LIKE ? OR o.ticket_code LIKE ?)")
        args += [kw, kw, kw]
    if date_str:
        where.append("st.show_date = ?")
        args.append(date_str)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    sql = ("SELECT o.*, st.show_date, st.start_time, m.title, h.name AS hall_name "
           "FROM ticket_order o "
           "JOIN showtime st ON st.id = o.show_id "
           "JOIN movie m ON m.id = st.movie_id "
           "JOIN hall h ON h.id = st.hall_id"
           + where_sql + " ORDER BY o.created_at DESC, o.id DESC LIMIT ? OFFSET ?")
    return query_all(sql, args + [limit, offset])


def admin_count(status=None, keyword=None, date_str=None):
    """后台订单列表对应的总条数（分页用）。"""
    where, args = [], []
    if status:
        where.append("o.status = ?")
        args.append(status)
    if keyword:
        kw = '%' + keyword + '%'
        where.append("(o.order_no LIKE ? OR o.phone LIKE ? OR o.ticket_code LIKE ?)")
        args += [kw, kw, kw]
    if date_str:
        where.append("st.show_date = ?")
        args.append(date_str)
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    sql = ("SELECT COUNT(*) AS c FROM ticket_order o "
           "JOIN showtime st ON st.id = o.show_id" + where_sql)
    return query_one(sql, args)['c']


# ---------------- 统计（仪表板） ----------------

def count_by_status():
    """各状态订单数量。返回 {状态: 条数}。"""
    rows = query_all("SELECT status, COUNT(*) AS c FROM ticket_order GROUP BY status")
    return {r['status']: r['c'] for r in rows}


def day_revenue(date_str):
    """某天的票房（分）与人次：只统计已支付 / 已检票（不含退票）。"""
    sql = ("SELECT COALESCE(SUM(o.total_price), 0) AS revenue, "
           "COALESCE(SUM(o.seat_count), 0) AS people "
           "FROM ticket_order o JOIN showtime st ON st.id = o.show_id "
           "WHERE st.show_date = ? AND o.status IN ('PAID','USED')")
    return query_one(sql, (date_str,))


def revenue_last_days(start_date, end_date):
    """日期区间内每天的票房：[(日期, 票房分), ...]，纯 CSS 柱状图数据源。"""
    sql = ("SELECT st.show_date AS d, SUM(o.total_price) AS revenue "
           "FROM ticket_order o JOIN showtime st ON st.id = o.show_id "
           "WHERE st.show_date BETWEEN ? AND ? AND o.status IN ('PAID','USED') "
           "GROUP BY st.show_date ORDER BY st.show_date")
    return query_all(sql, (start_date, end_date))


def movie_ranking(start_date, end_date, limit=8):
    """日期区间内影片票房排行：[(片名, 票房分, 票数), ...]，横向条形图数据源。"""
    sql = ("SELECT m.title, SUM(o.total_price) AS revenue, SUM(o.seat_count) AS tickets "
           "FROM ticket_order o "
           "JOIN showtime st ON st.id = o.show_id "
           "JOIN movie m ON m.id = st.movie_id "
           "WHERE st.show_date BETWEEN ? AND ? AND o.status IN ('PAID','USED') "
           "GROUP BY m.id ORDER BY revenue DESC LIMIT ?")
    return query_all(sql, (start_date, end_date, limit))


def find_by_ticket_code(code):
    """取票码查订单（后台检票页）。"""
    sql = ("SELECT o.*, st.show_date, st.start_time, m.title, m.poster, "
           "h.name AS hall_name, h.type AS hall_type "
           "FROM ticket_order o "
           "JOIN showtime st ON st.id = o.show_id "
           "JOIN movie m ON m.id = st.movie_id "
           "JOIN hall h ON h.id = st.hall_id "
           "WHERE o.ticket_code = ?")
    return query_one(sql, (code,))


def order_no_exists(order_no):
    """订单号是否已存在（生成时避免重复）。"""
    return query_one("SELECT id FROM ticket_order WHERE order_no = ?", (order_no,)) is not None


def seat_status_map(show_id):
    """场次内每个已占座位的订单状态：{seat_id: 'UNPAID'/'PAID'/'USED'}。

    座位图据此区分"锁定中（待支付，带锁图标）"与"已售"。
    """
    rows = query_all(
        "SELECT os.seat_id, o.status FROM order_seat os "
        "JOIN ticket_order o ON o.id = os.order_id WHERE os.show_id = ?", (show_id,))
    return {r['seat_id']: r['status'] for r in rows}


def taken_seats_detail(show_id, seat_ids):
    """指定座位中已被占用的明细（下单前检查，便于提示"具体哪个座位被选走"）。"""
    if not seat_ids:
        return []
    marks = ', '.join('?' for _ in seat_ids)
    sql = ("SELECT s.row_no, s.col_no, o.status FROM order_seat os "
           "JOIN seat s ON s.id = os.seat_id "
           "JOIN ticket_order o ON o.id = os.order_id "
           "WHERE os.show_id = ? AND os.seat_id IN (" + marks + ") ORDER BY s.row_no, s.col_no")
    return query_all(sql, [show_id] + list(seat_ids))
