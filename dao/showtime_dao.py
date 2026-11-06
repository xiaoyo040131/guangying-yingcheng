# -*- coding: utf-8 -*-
"""
dao/showtime_dao.py —— 场次表数据访问

只放参数化 SQL，不碰 request / session，不做业务判断。
时间比较依赖 'HH:MM' 零填充字符串比较（'09:30' < '22:10' < '24:00'）。
"""
from db import query_all, query_one, execute

# 场次 + 影片 + 影厅联查的公共 SELECT（页面展示几乎都要带片名、厅名）
_SELECT = """
SELECT st.*, m.title, m.title_en, m.poster, m.duration, m.genre, m.rating,
       h.name AS hall_name, h.type AS hall_type, h.row_count, h.col_count,
       h.aisle_after_col
FROM showtime st
JOIN movie m ON m.id = st.movie_id
JOIN hall  h ON h.id = st.hall_id
"""


def get_by_id(show_id):
    """单个场次（带影片、影厅信息）。"""
    return query_one(_SELECT + " WHERE st.id = ?", (show_id,))


def list_by_movie_date(movie_id, show_date):
    """某影片某天的全部在售/停售场次（影片详情页）。"""
    sql = _SELECT + (" WHERE st.movie_id = ? AND st.show_date = ? "
                     "AND st.status != 'CANCELLED' ORDER BY st.start_time")
    return query_all(sql, (movie_id, show_date))


def list_by_hall_date(hall_id, show_date):
    """某影厅某天的场次（排片时间轴）。"""
    sql = _SELECT + " WHERE st.hall_id = ? AND st.show_date = ? ORDER BY st.start_time"
    return query_all(sql, (hall_id, show_date))


def list_by_date(show_date):
    """某天全部场次（仪表板"今日场次"）。"""
    sql = _SELECT + " WHERE st.show_date = ? AND st.status != 'CANCELLED' ORDER BY h.id, st.start_time"
    return query_all(sql, (show_date,))


def list_dates_range(start_date, end_date):
    """日期区间内的场次（仪表板近 7 天票房需要场次日期维度）。"""
    sql = _SELECT + " WHERE st.show_date BETWEEN ? AND ? ORDER BY st.show_date, st.start_time"
    return query_all(sql, (start_date, end_date))


def overlap_count(hall_id, show_date, start_time, end_time, exclude_id=None):
    """同厅同天（未取消）场次中与 [start, end) 时间段重叠的场次数量。

    重叠判定：已有.start < 新.end 且 已有.end > 新.start（区间相交）。
    业务层在 BEGIN IMMEDIATE 事务内调用它做排片冲突检查。
    """
    sql = ("SELECT COUNT(*) AS c FROM showtime "
           "WHERE hall_id = ? AND show_date = ? AND status != 'CANCELLED' "
           "AND start_time < ? AND end_time > ?")
    args = [hall_id, show_date, end_time, start_time]
    if exclude_id is not None:
        sql += " AND id != ?"
        args.append(exclude_id)
    return query_one(sql, args)['c']


def insert(movie_id, hall_id, show_date, start_time, end_time, base_price):
    """新建场次，返回新 id。"""
    cur = execute(
        "INSERT INTO showtime (movie_id, hall_id, show_date, start_time, end_time, base_price, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'ON_SALE')",
        (movie_id, hall_id, show_date, start_time, end_time, base_price))
    return cur.lastrowid


def update(show_id, movie_id, start_time, end_time, base_price):
    """编辑场次（换影片、改时间、改票价）。"""
    return execute(
        "UPDATE showtime SET movie_id = ?, start_time = ?, end_time = ?, base_price = ? "
        "WHERE id = ?",
        (movie_id, start_time, end_time, base_price, show_id))


def set_status(show_id, status):
    """修改场次状态（停售 / 取消 / 恢复在售）。"""
    return execute("UPDATE showtime SET status = ? WHERE id = ?", (status, show_id))


def sold_seat_count(show_id):
    """场次已售座位数（order_seat 里还占着的，即未取消/未超时/未退票的）。"""
    return query_one("SELECT COUNT(*) AS c FROM order_seat WHERE show_id = ?",
                     (show_id,))['c']


def recent_show_ids(days=7):
    """近 N 天的场次 id 列表（清理演示数据等场景备用）。"""
    return query_all("SELECT id FROM showtime ORDER BY id")


def occupancy_list(show_date):
    """某天全部未取消场次 + 已售座位数 + 可售座位数（仪表板/排片用）。

    已售 = order_seat 中还占着的座位（取消/超时/退票时已删除对应记录）。
    """
    sql = """
    SELECT st.id, st.start_time, st.end_time, st.status, st.base_price,
           m.title, m.duration, h.id AS hall_id, h.name AS hall_name, h.type AS hall_type,
           (SELECT COUNT(*) FROM order_seat os WHERE os.show_id = st.id) AS sold,
           (SELECT COUNT(*) FROM seat s WHERE s.hall_id = st.hall_id AND s.is_enabled = 1) AS capacity
    FROM showtime st
    JOIN movie m ON m.id = st.movie_id
    JOIN hall h  ON h.id = st.hall_id
    WHERE st.show_date = ? AND st.status != 'CANCELLED'
    ORDER BY h.id, st.start_time
    """
    return query_all(sql, (show_date,))


def count_by_status(show_date):
    """某天各状态场次数量（排片页统计）。"""
    rows = query_all(
        "SELECT status, COUNT(*) AS c FROM showtime WHERE show_date = ? GROUP BY status",
        (show_date,))
    return {r['status']: r['c'] for r in rows}


def count_by_hall(hall_id):
    """某影厅的有效场次数（已取消的不算：改行/列数前用它判断是否已有排片）。"""
    return query_one("SELECT COUNT(*) AS c FROM showtime "
                     "WHERE hall_id = ? AND status != 'CANCELLED'",
                     (hall_id,))['c']
