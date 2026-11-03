# -*- coding: utf-8 -*-
"""
dao/admin_dao.py —— 管理员表数据访问

只放参数化 SQL。密码哈希的生成/校验用 werkzeug.security，在业务层完成。
"""
from db import query_one, execute


def get_by_username(username):
    return query_one("SELECT * FROM admin_user WHERE username = ?", (username,))


def insert(username, password_hash, now):
    """写入管理员（init_db 演示数据用）。"""
    cur = execute(
        "INSERT INTO admin_user (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, password_hash, now))
    return cur.lastrowid


def update_password(username, password_hash):
    return execute("UPDATE admin_user SET password_hash = ? WHERE username = ?",
                   (password_hash, username))
