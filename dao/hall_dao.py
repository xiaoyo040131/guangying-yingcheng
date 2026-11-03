# -*- coding: utf-8 -*-
"""
dao/hall_dao.py —— 影厅表数据访问

只放参数化 SQL，不碰 request / session，不做业务判断。
"""
from db import query_all, query_one, execute

_FIELDS = ('name', 'type', 'row_count', 'col_count', 'aisle_after_col', 'is_enabled')


def get_all():
    """全部影厅，按 id 排序（1号厅 ~ 4号厅）。"""
    return query_all("SELECT * FROM hall ORDER BY id")


def list_enabled():
    """启用中的影厅（前台展示、排片下拉框用）。"""
    return query_all("SELECT * FROM hall WHERE is_enabled = 1 ORDER BY id")


def get_by_id(hall_id):
    return query_one("SELECT * FROM hall WHERE id = ?", (hall_id,))


def name_exists(name, exclude_id=None):
    """厅名是否已被占用（UNIQUE 校验前的友好检查）。"""
    if exclude_id is None:
        return query_one("SELECT id FROM hall WHERE name = ?", (name,)) is not None
    return query_one("SELECT id FROM hall WHERE name = ? AND id != ?",
                     (name, exclude_id)) is not None


def insert(data):
    cols = [c for c in _FIELDS if c in data]
    sql = "INSERT INTO hall (%s) VALUES (%s)" % (
        ', '.join(cols), ', '.join('?' * len(cols)))
    cur = execute(sql, tuple(data[c] for c in cols))
    return cur.lastrowid


def update(hall_id, data):
    cols = [c for c in _FIELDS if c in data]
    sql = "UPDATE hall SET %s WHERE id = ?" % ', '.join(c + ' = ?' for c in cols)
    return execute(sql, tuple(data[c] for c in cols) + (hall_id,))


def set_enabled(hall_id, enabled):
    """启用 / 停用影厅。"""
    return execute("UPDATE hall SET is_enabled = ? WHERE id = ?", (enabled, hall_id))
