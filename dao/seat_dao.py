# -*- coding: utf-8 -*-
"""
dao/seat_dao.py —— 座位表数据访问

只放参数化 SQL，不碰 request / session，不做业务判断。
"""
from db import query_all, query_one, execute


def list_by_hall(hall_id):
    """影厅全部座位，按行、列排序（画座位图用）。"""
    return query_all(
        "SELECT * FROM seat WHERE hall_id = ? ORDER BY row_no, col_no", (hall_id,))


def list_enabled_by_hall(hall_id):
    """影厅启用的座位（选座、可售统计用）。"""
    return query_all(
        "SELECT * FROM seat WHERE hall_id = ? AND is_enabled = 1 ORDER BY row_no, col_no",
        (hall_id,))


def get_by_id(seat_id):
    return query_one("SELECT * FROM seat WHERE id = ?", (seat_id,))


def get_by_position(hall_id, row_no, col_no):
    return query_one("SELECT * FROM seat WHERE hall_id = ? AND row_no = ? AND col_no = ?",
                     (hall_id, row_no, col_no))


def count_by_hall(hall_id, only_enabled=True):
    """影厅座位总数（可售总座数）。"""
    sql = "SELECT COUNT(*) AS c FROM seat WHERE hall_id = ?"
    if only_enabled:
        sql += " AND is_enabled = 1"
    return query_one(sql, (hall_id,))['c']


def insert_many(rows):
    """批量写入座位。rows 为 (hall_id, row_no, col_no, seat_type, partner_id, is_enabled) 元组列表。

    情侣座配对 id 依赖先插入普通座位再回填，调用方（座位编辑保存）负责顺序。
    """
    sql = ("INSERT INTO seat (hall_id, row_no, col_no, seat_type, couple_partner_id, is_enabled) "
           "VALUES (?, ?, ?, ?, ?, ?)")
    db_cur = None
    for row in rows:
        db_cur = execute(sql, row)
    return db_cur


def delete_by_hall(hall_id):
    """删除影厅全部座位（座位编辑"全量重建"策略时使用，须在事务内）。"""
    return execute("DELETE FROM seat WHERE hall_id = ?", (hall_id,))


def set_enabled(seat_id, enabled):
    return execute("UPDATE seat SET is_enabled = ? WHERE id = ?", (enabled, seat_id))


def list_by_ids(seat_ids):
    """按 id 批量取座位（下单校验"座位是否属于该影厅"用）。"""
    if not seat_ids:
        return []
    marks = ', '.join('?' for _ in seat_ids)
    return query_all("SELECT * FROM seat WHERE id IN (" + marks + ") ORDER BY row_no, col_no",
                     list(seat_ids))


def update_type(seat_id, seat_type, partner_id=None, enabled=1):
    """修改座位类型 / 情侣配对 / 是否启用（座位编辑保存用）。"""
    return execute(
        "UPDATE seat SET seat_type = ?, couple_partner_id = ?, is_enabled = ? WHERE id = ?",
        (seat_type, partner_id, enabled, seat_id))
