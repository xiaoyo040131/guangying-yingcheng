# -*- coding: utf-8 -*-
"""
db.py —— 数据库访问层（最底层）

职责：
- 管理 SQLite 连接（挂在 Flask 的 g 上，一次请求一个连接）。
- 提供 query_all / query_one / execute 三个参数化查询助手。
- 提供 transaction() 上下文管理器（BEGIN IMMEDIATE），用于"下单锁座"等
  需要防并发占用的写操作：事务内先检查、再写入，冲突整单失败。

调用方向：views -> services -> dao -> db。dao 之外不允许出现 SQL。
"""
import sqlite3
from contextlib import contextmanager

from flask import g
from config import DATABASE


def get_db():
    """获取当前请求的数据库连接（没有则新建，并打开外键约束）。"""
    if 'db' not in g:
        conn = sqlite3.connect(DATABASE)
        # 查询结果用 Row，可按列名取值
        conn.row_factory = sqlite3.Row
        # SQLite 默认不强制外键，必须每个连接都打开
        conn.execute('PRAGMA foreign_keys = ON')
        g.db = conn
    return g.db


def close_db(exc=None):
    """请求结束时关闭连接（注册到 teardown_appcontext）。"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query_all(sql, args=()):
    """执行查询，返回全部行（sqlite3.Row 列表）。SQL 一律 ? 占位。"""
    cur = get_db().execute(sql, args)
    return cur.fetchall()


def query_one(sql, args=()):
    """执行查询，返回第一行（无结果返回 None）。"""
    cur = get_db().execute(sql, args)
    return cur.fetchone()


def execute(sql, args=()):
    """执行写操作，返回 cursor（可用 cursor.lastrowid / rowcount）。

    在 transaction() 事务内时不单独 commit，由事务统一提交；
    否则立即提交（简单写操作自动落库）。
    """
    db = get_db()
    cur = db.execute(sql, args)
    if not getattr(g, '_in_transaction', False):
        db.commit()
    return cur


@contextmanager
def transaction():
    """立即写事务（BEGIN IMMEDIATE）。

    用法：
        with transaction() as db:
            # 事务内：先 SELECT 检查，再 INSERT/UPDATE
            ...
    进入时加写锁（防止并发同时通过检查），正常退出提交，
    出现异常回滚并继续抛出，让上层转成友好的中文提示。
    """
    db = get_db()
    g._in_transaction = True
    db.execute('BEGIN IMMEDIATE')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        g._in_transaction = False


def init_app(app):
    """在 create_app() 中调用：请求结束自动关闭数据库连接。"""
    app.teardown_appcontext(close_db)
