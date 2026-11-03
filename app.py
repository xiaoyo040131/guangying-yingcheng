# app.py —— 光影影城 · 在线选座购票系统 应用入口
"""
create_app() 负责把整个应用装配起来：
- 修正 MIME 类型（Windows 注册表常被其它软件改坏，会导致 js/css/图片类型错误）；
- 注册 img_url()：模板里取图的唯一入口，jpg / png 自动识别，换图不用改代码；
- 注册数据库（db.init_app）与前台蓝图；
- 统一 CSRF 校验（所有 POST 都要带 _csrf 隐藏字段或 X-CSRF-Token 请求头）；
- 注入模板全局变量、过滤器、站点信息；
- 404 / 400 / 500 统一渲染 errors/error.html；
- python app.py 启动，带 --open 时 1.5 秒后自动打开浏览器。

调用方向：views -> services -> dao -> db。本文件只做“装配”，不写业务逻辑。
"""
import hmac
import mimetypes
import os
import secrets
import sys
import threading
import webbrowser
from datetime import date

# ---- 必须在创建 app 之前修正 MIME 类型（项目规范第 4 节第 12 条）----
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("font/woff2", ".woff2")

from flask import (Flask, abort, render_template, request, session, url_for)

import config
import db
from utils import (HALL_TYPE, MOVIE_STATUS, ORDER_STATUS, SEAT_TYPE,
                   SHOWTIME_STATUS, fen_to_yuan, rating_str)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(BASE_DIR, 'static', 'images')

# 图片查找顺序：数据库里只存不带扩展名的名字，这里按顺序找第一个存在的文件
IMG_EXTS = ('.jpg', '.jpeg', '.png', '.webp')

# 站点信息（模板全局变量，前台 / 后台共用）
SITE = {
    'name': '光影影城',
    'slogan': '每一帧都值得在大银幕上看',
    'en': 'LUMIERE · CINEMA',
    'icon': 'bi-film',
    'phone': '0571-8866 6699',
    'address': '杭州市西湖区文三路 128 号 光影中心 5 层',
    'open_time': '每日 09:00 - 24:00',
}

# 前台导航（href 用站点内路径，active 由 nav_active 决定）
NAV_ITEMS = [
    {'key': 'home', 'label': '首页', 'href': '/'},
    {'key': 'movies', 'label': '正在热映', 'href': '/#showing'},
    {'key': 'halls', 'label': '影厅与设备', 'href': '/#halls'},
    {'key': 'gallery', 'label': '影院环境', 'href': '/#gallery'},
    {'key': 'orders', 'label': '我的订单', 'href': '/orders'},
]


def img_url(name):
    """按名字取 static/images/ 下的图片地址。

    数据库里存 'poster-1'（不带扩展名），生成工具给的是 jpg 还是 png 都能显示：
    去掉扩展名后按 .jpg -> .jpeg -> .png -> .webp 找第一个存在的文件。
    找不到时退回 .jpg（页面表现为裂图，便于发现漏图），不抛异常。
    """
    if not name:
        return ''
    name = str(name).strip()
    stem, ext = os.path.splitext(name)
    # 调用方已经写了扩展名且文件存在，直接用
    if ext and os.path.exists(os.path.join(IMAGES_DIR, name)):
        return url_for('static', filename='images/' + name)
    stem = stem or name
    for suffix in IMG_EXTS:
        if os.path.exists(os.path.join(IMAGES_DIR, stem + suffix)):
            return url_for('static', filename='images/' + stem + suffix)
    return url_for('static', filename='images/' + stem + '.jpg')


def csrf_token():
    """取当前会话的 CSRF 令牌，没有就生成一个（模板里用来填隐藏字段）。"""
    token = session.get('_csrf')
    if not token:
        token = secrets.token_hex(16)
        session['_csrf'] = token
    return token


def _nav_active():
    """根据当前路径返回导航高亮键，模板里只做 nav_active == 'xxx' 的比较。"""
    path = request.path or '/'
    if path == '/':
        return 'home'
    if path.startswith('/movies') or path.startswith('/shows'):
        return 'movies'
    if path.startswith('/orders'):
        return 'orders'
    return ''


def create_app():
    """应用工厂：装配配置、数据库、蓝图、过滤器、错误页。"""
    app = Flask(__name__)
    app.config.from_object(config)
    # 密钥保存在 instance/secret.key，首次运行自动生成
    app.config['SECRET_KEY'] = config.get_secret_key()
    # 开发阶段不要让浏览器缓存 css/js，改完刷新就能看到
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    # 开发阶段模板改动立即生效（生产部署时去掉即可）
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['JSON_AS_ASCII'] = False

    db.init_app(app)

    # ---------------- 蓝图 ----------------
    # 前台蓝图本步注册；auth / admin 蓝图在第 5 步实现后再注册
    from views.front import front_bp
    app.register_blueprint(front_bp)

    # ---------------- 模板全局函数与过滤器 ----------------
    app.jinja_env.globals['img_url'] = img_url
    app.jinja_env.globals['csrf_token'] = csrf_token
    # 状态中文字典（模板里 {{ ORDER_STATUS[o.status] }}）
    app.jinja_env.globals.update({
        'ORDER_STATUS': ORDER_STATUS,
        'MOVIE_STATUS': MOVIE_STATUS,
        'HALL_TYPE': HALL_TYPE,
        'SEAT_TYPE': SEAT_TYPE,
        'SHOWTIME_STATUS': SHOWTIME_STATUS,
    })

    @app.template_filter('yuan')
    def filter_yuan(fen):
        """金额过滤器：分 -> '128.00'（模板里 {{ o.total_price|yuan }} 元）。"""
        return fen_to_yuan(fen)

    @app.template_filter('rating')
    def filter_rating(value):
        """评分过滤器：91 -> '9.1'。"""
        return rating_str(value)

    @app.template_filter('mask_phone')
    def filter_mask_phone(phone):
        """手机号脱敏：13800138000 -> 138****8000。"""
        s = str(phone or '')
        return s[:3] + '****' + s[-4:] if len(s) == 11 else s

    @app.template_filter('split')
    def filter_split(value, sep='/'):
        """字符串切分：'悬疑/犯罪' -> ['悬疑', '犯罪']（用于标签胶囊）。"""
        if value is None:
            return []
        return [part.strip() for part in str(value).split(sep) if part.strip()]

    # ---------------- 站点信息注入 ----------------
    @app.context_processor
    def inject_site():
        return {
            'site': SITE,
            'nav_items': NAV_ITEMS,
            'nav_active': _nav_active(),
            'current_year': date.today().year,
        }

    # ---------------- CSRF 统一校验 ----------------
    @app.before_request
    def verify_csrf():
        """所有写请求统一校验令牌，缺失或不匹配一律 400（项目规范第 4 节第 10 条）。"""
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return None
        expected = session.get('_csrf')
        sent = (request.headers.get('X-CSRF-Token')
                or request.form.get('_csrf')
                or request.values.get('_csrf'))
        if not sent and request.is_json:
            payload = request.get_json(silent=True) or {}
            sent = payload.get('_csrf')
        if not expected or not sent or not hmac.compare_digest(str(expected), str(sent)):
            abort(400, description='表单已过期，请刷新页面后重试')
        return None

    # ---------------- 错误页 ----------------
    @app.errorhandler(400)
    def handle_400(err):
        return render_template('errors/error.html', code=400,
                               title='请求无效',
                               message=getattr(err, 'description', '') or '提交的数据不正确，请返回重试。'), 400

    @app.errorhandler(404)
    def handle_404(err):
        return render_template('errors/error.html', code=404,
                               title='页面走丢了',
                               message='这场戏不在排片里，换个页面看看吧。'), 404

    @app.errorhandler(405)
    def handle_405(err):
        return render_template('errors/error.html', code=405,
                               title='请求方式不对',
                               message='该地址不支持这种操作方式。'), 405

    @app.errorhandler(500)
    def handle_500(err):
        # 兜底：任何未预料的异常都渲染友好页面，不把堆栈甩给用户
        app.logger.exception('服务器内部错误: %s', err)
        return render_template('errors/error.html', code=500,
                               title='放映机卡带了',
                               message='服务器出了点小问题，请稍后重试。'), 500

    return app


app = create_app()


def _open_browser():
    """延迟一点点再打开，避免浏览器比服务先起来。"""
    try:
        webbrowser.open('http://127.0.0.1:5000/')
    except Exception:
        pass


if __name__ == '__main__':
    if '--open' in sys.argv:
        threading.Timer(1.5, _open_browser).start()
    # 默认关闭自动重载：避免调试模式下起两个进程、浏览器被打开两次
    app.run(host='127.0.0.1', port=5000, debug='--debug' in sys.argv)
