# views/auth.py —— 后台登录 / 退出 / 登录校验
"""
后台身份相关的全部逻辑都放在这里：
- GET  /admin/login  登录页
- POST /admin/login  校验账号密码（密码用 werkzeug 的哈希校验，账号不存在与密码错误给同一提示）
- POST /admin/logout 退出（只接受 POST，页面用表单提交，避免被链接触发）
- login_required 装饰器 + login_guard()：未登录时普通页面 302 到登录页，接口请求返回 401 JSON

安全要点（项目规范第 4 节第 9 条）：
1. 登录成功后先 session.clear() 再写入身份，防止会话固定攻击；
2. 登录后的跳转地址只允许以 /admin 开头，避免开放重定向；
3. 未登录访问任何后台接口一律 401，前端据此提示"登录已过期"。
"""
import functools

from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from services import admin_service
from utils import BizError

auth_bp = Blueprint('auth', __name__, url_prefix='/admin')

# 登录页地址（避免在 next 里指回登录页造成死循环）
LOGIN_PATH = '/admin/login'


# ---------------- 工具 ----------------

def _wants_json():
    """判断当前请求是否"要 JSON"：后台接口、fetch 请求都算。"""
    return (request.path.startswith('/admin/api')
            or request.is_json
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest')


def _current_target():
    """当前访问地址（含查询串），用于登录后跳回原页面。"""
    target = request.path or '/admin'
    if request.query_string:
        target += '?' + request.query_string.decode('utf-8', 'ignore')
    return target


def safe_next(target):
    """只允许跳转到 /admin 开头的站内地址（防止开放重定向）。"""
    target = (target or '').strip()
    if not target.startswith('/admin'):
        return None
    if target.startswith('//') or target.startswith('/admin/login'):
        return None
    return target


def login_guard():
    """登录校验的公共实现：已登录返回 None；未登录返回跳转或 401 响应。

    admin 蓝图用 before_request 调用它做统一拦截，login_required 装饰器内部也用它。
    """
    if session.get('admin_id'):
        return None
    if _wants_json():
        return jsonify({'ok': False, 'message': '登录已过期，请重新登录'}), 401
    return redirect(url_for('auth.login', next=_current_target()))


def login_required(view):
    """视图装饰器：未登录时普通页面跳登录页，接口请求返回 401 JSON。"""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        resp = login_guard()
        if resp is not None:
            return resp
        return view(*args, **kwargs)
    return wrapped


# ---------------- 登录 / 退出 ----------------

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """登录页与登录提交。

    账号不存在和密码错误返回同一句"账号或密码不正确"，不泄露账号是否存在。
    """
    next_url = safe_next(request.values.get('next'))
    username = ''

    # 已登录就直接进后台，不用再看登录页
    if session.get('admin_id'):
        return redirect(next_url or url_for('admin.dashboard'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        try:
            user = admin_service.authenticate(username, password)
        except BizError as exc:
            flash(str(exc), 'error')
            return render_template('admin/login.html', next_url=next_url,
                                   username=username), 200

        # 先清空会话再写身份：旧会话里的任何内容都不带进后台
        session.clear()
        session['admin_id'] = user['id']
        session['admin_username'] = user['username']
        flash('欢迎回来，%s' % user['username'], 'success')
        return redirect(next_url or url_for('admin.dashboard'))

    return render_template('admin/login.html', next_url=next_url, username=username)


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """退出登录：只接受 POST（页面侧边栏用表单提交，带 _csrf）。"""
    session.clear()
    flash('已退出登录，下次见。', 'success')
    return redirect(url_for('auth.login'))
