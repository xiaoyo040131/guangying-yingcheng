# -*- coding: utf-8 -*-
"""
dao/movie_dao.py —— 影片表数据访问

只放参数化 SQL（一律 ? 占位），不碰 request / session，不做业务判断。
"""
from db import query_all, query_one, execute

# 影片表全部字段（新增/修改共用，避免各处字段列表不一致）
_FIELDS = ('title', 'title_en', 'poster', 'genre', 'duration', 'director',
           'actors', 'rating', 'synopsis', 'release_date', 'status')


def get_all():
    """全部影片，按状态和上映日期排序（热映在前）。"""
    sql = ("SELECT * FROM movie "
           "ORDER BY CASE status WHEN 'SHOWING' THEN 0 WHEN 'COMING' THEN 1 ELSE 2 END, "
           "release_date DESC, id")
    return query_all(sql)


def list_by_status(status):
    """按状态取影片（SHOWING / COMING / OFF）。"""
    return query_all("SELECT * FROM movie WHERE status = ? ORDER BY rating DESC, id",
                     (status,))


def get_by_id(movie_id):
    return query_one("SELECT * FROM movie WHERE id = ?", (movie_id,))


def insert(data):
    """新建影片，data 为字段字典，返回新 id。"""
    cols = list(_FIELDS)
    sql = "INSERT INTO movie (%s) VALUES (%s)" % (
        ', '.join(cols), ', '.join('?' * len(cols)))
    cur = execute(sql, tuple(data[c] for c in cols))
    return cur.lastrowid


def update(movie_id, data):
    """修改影片，data 只包含要更新的字段。"""
    cols = [c for c in _FIELDS if c in data]
    sql = "UPDATE movie SET %s WHERE id = ?" % ', '.join(c + ' = ?' for c in cols)
    return execute(sql, tuple(data[c] for c in cols) + (movie_id,))


def update_status(movie_id, status):
    """修改影片状态（热映/即将上映/下映）。"""
    return execute("UPDATE movie SET status = ? WHERE id = ?", (status, movie_id))


def delete(movie_id):
    """删除影片（一般不用；有排片记录时会被外键拦住）。"""
    return execute("DELETE FROM movie WHERE id = ?", (movie_id,))
