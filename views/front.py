# views/front.py —— 前台蓝图（观众侧，无需登录）
"""
前台全部路由：
- 首页 '/'、影片详情 '/movies/<id>'
- 选座页 '/shows/<id>/seats'、实时报价接口 '/api/quote'
- 下单 '/orders/new'、支付页与支付动作、支付成功页
- 我的订单 '/orders'（手机号 + 姓名查询）、取消、退票

约定：
1. 视图只做“取参数 -> 调 service -> 渲染模板 / 返回 JSON”，不写 SQL、不写计价；
2. 业务错误（BizError）一律 flash 中文提示并回到原页面，保留用户已填内容，绝不出现 500；
3. 观众身份存在 session（手机号 + 姓名），查询 / 取消 / 退票都把身份传给 service 校验；
4. 支付成功页只让“刚支付的那个浏览器”看到：支付成功后把订单号记进 session 白名单。
"""
from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from services import movie_service, order_service, seat_service, showtime_service
from utils import BizError, SEAT_TYPE, fen_to_yuan

front_bp = Blueprint('front', __name__)

# session 键名：当前观众身份 / 本浏览器下过的单 / 本浏览器支付成功的单
SESSION_CUSTOMER = 'customer'
SESSION_ORDERS = 'my_orders'
SESSION_PAID = 'paid_orders'

# 场次卡片上的“语言版本”：场次表没有这个字段，按影厅类型给出展示文案
LANG_BY_HALL = {
    'IMAX': '英语 · 3D',
    'VIP': '国语 · 2D',
    'NORMAL': '国语 · 2D',
}


# ---------------- 首页展示文案（纯展示数据，不含业务规则） ----------------

# 轮播：图片全部来自 static/images/，用 img_url('banner-1') 取
BANNERS = [
    {
        'img': 'banner-1',
        'en': 'LUMIERE · CINEMA',
        'title': '每一帧都值得在大银幕上看',
        'sub': '光影影城 · 四厅联映，今夜开场',
    },
    {
        'img': 'banner-2',
        'en': 'NOW SHOWING',
        'title': '灯火与海报之间',
        'sub': '挑一部想看的电影，选一个喜欢的位置',
    },
    {
        'img': 'banner-3',
        'en': 'IMAX · DOLBY ATMOS',
        'title': '巨幕之下，身临其境',
        'sub': '2 号 IMAX 厅 · 216 个座位 · 激光放映',
    },
]

# 影厅介绍：与 init_db.py 里的 4 个影厅一一对应
HALL_INTRO = [
    {
        'cover': 'cover-1',
        'name': '1号厅 · 杜比全景声',
        'type_label': '标准厅',
        'icon': 'bi-speaker',
        'desc': '64 声道全景声环绕，声音从头顶与四周流过，把观众放进故事里。',
        'specs': ['10 行 × 14 列 · 140 座', '第 7 列后设过道', '2K 激光放映'],
    },
    {
        'cover': 'cover-2',
        'name': '2号 IMAX 厅',
        'type_label': 'IMAX',
        'icon': 'bi-badge-4k',
        'desc': '18 米巨幕顶天立地，画面多出 26% 的内容，坐进最后一排也看不完。',
        'specs': ['12 行 × 18 列 · 216 座', '第 9 列后设过道', 'IMAX 双机放映'],
    },
    {
        'cover': 'cover-3',
        'name': '3号情侣厅',
        'type_label': '情侣座',
        'icon': 'bi-heart',
        'desc': '全场双人沙发座，扶手可抬起，两个人中间没有隔阂。',
        'specs': ['6 行 × 5 对 · 60 座', '情侣座须成对选择', '可抬式扶手'],
    },
    {
        'cover': 'cover-4',
        'name': '4号 VIP 厅',
        'type_label': 'VIP',
        'icon': 'bi-gem',
        'desc': '真皮电动躺椅、独立小桌与毛毯服务，观影变成一次小憩。',
        'specs': ['5 行 × 8 列 · 40 座', '全厅 VIP 真皮躺椅', '专属入场通道'],
    },
]

# 特色服务（圆形渐变图标 + 标题 + 说明，六列）
FEATURES = [
    {'icon': 'bi-speaker', 'title': '杜比全景声', 'desc': '64 声道沉浸音效'},
    {'icon': 'bi-brightness-high', 'title': '激光放映', 'desc': '4K 高亮画面'},
    {'icon': 'bi-heart', 'title': '情侣双人座', 'desc': '可抬式扶手沙发'},
    {'icon': 'bi-ticket-perforated', 'title': '自助取票', 'desc': '扫码 10 秒出票'},
    {'icon': 'bi-cup-straw', 'title': '现制小食', 'desc': '爆米花现爆现装'},
    {'icon': 'bi-car-front', 'title': '免费停车', 'desc': '地下车库 2 小时'},
]

# 观影服务（cover-5 ~ cover-8）
SERVICES = [
    {'img': 'cover-7', 'title': '自助取票', 'desc': '凭 8 位取票码在自助机取票，开场前 15 分钟到店即可。'},
    {'img': 'cover-5', 'title': '小食与饮品', 'desc': '现爆爆米花、手作气泡水，凭取票码在小卖部享 9 折。'},
    {'img': 'cover-6', 'title': '大堂休息区', 'desc': '提前到场可以在大堂候场，沙发区提供免费充电。'},
    {'img': 'cover-8', 'title': '观影活动', 'desc': '每周五晚设映后交流场，详情见影院大堂公告。'},
]

# 影院环境图集（3 列网格，第一张跨两行）
GALLERY = [
    {'img': 'env-1', 'caption': '夜色里的光影影城'},
    {'img': 'env-2', 'caption': '走廊与影厅入口'},
    {'img': 'env-3', 'caption': '小卖部的暖黄灯光'},
    {'img': 'env-4', 'caption': '放映室里的放映机'},
    {'img': 'env-5', 'caption': '灯暗下来的观众席'},
    {'img': 'env-6', 'caption': '大堂海报墙'},
]


# ---------------- 内部小工具（session 身份 / 座位参数） ----------------

def _current_customer():
    """当前浏览器里记住的观众身份，返回 (phone, name) 或 (None, None)。"""
    customer = session.get(SESSION_CUSTOMER) or {}
    return customer.get('phone'), customer.get('name')


def _remember_customer(phone, name):
    """查询 / 下单成功后记住身份，后续“我的订单”与取消退票都靠它。"""
    session[SESSION_CUSTOMER] = {'phone': phone, 'name': name}


def _remember_order(order_no):
    """把订单号记进 session（本浏览器下过的单，最多留 20 条）。"""
    orders = [no for no in (session.get(SESSION_ORDERS) or []) if no != order_no]
    orders.insert(0, order_no)
    session[SESSION_ORDERS] = orders[:20]


def _remember_paid(order_no):
    """支付成功白名单：支付成功页只认这份名单（换浏览器看不到取票码）。"""
    paid = [no for no in (session.get(SESSION_PAID) or []) if no != order_no]
    paid.insert(0, order_no)
    session[SESSION_PAID] = paid[:20]


def _can_view_order(order):
    """能否查看该订单：本浏览器下过，或 session 里的手机号 + 姓名与订单一致。"""
    if order['order_no'] in (session.get(SESSION_ORDERS) or []):
        return True
    phone, name = _current_customer()
    return bool(phone) and order['phone'] == phone and order['customer_name'] == name


def _parse_seat_ids(raw):
    """把 '12,13,20' 这类字符串解析成去重的座位 id 列表（非法片段直接忽略）。"""
    ids = []
    for chunk in str(raw or '').split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = int(chunk)
        except ValueError:
            continue
        if value not in ids:
            ids.append(value)
    return ids


def _seat_page(show_id, selected_ids=None, phone='', name='', flash_error=True):
    """渲染选座页；场次不可售时提示并回首页（下单失败时也复用它保留已填内容）。"""
    try:
        layout = seat_service.get_layout(show_id)
    except BizError as exc:
        if flash_error:
            flash(str(exc), 'warning')
        return redirect(url_for('front.index'))

    return render_template(
        'front/seats.html',
        layout=layout,
        show=layout['show'],
        rows=layout['rows'],
        stats=layout['stats'],
        has_couple=layout['has_couple'],
        max_seats=layout['max_seats'],
        selected_ids=selected_ids or [],
        form_phone=phone,
        form_name=name,
    )


# ---------------- 路由：首页 / 影片详情 ----------------

@front_bp.route('/')
def index():
    """首页：轮播 + 快捷购票卡 + 正在热映 + 即将上映 + 影厅 + 环境图集。"""
    movies = movie_service.listing_movies()
    return render_template(
        'front/index.html',
        banners=BANNERS,
        showing=movies['showing'],
        coming=movies['coming'],
        dates=movie_service.detail_dates(),
        halls=HALL_INTRO,
        features=FEATURES,
        services=SERVICES,
        gallery=GALLERY,
    )


@front_bp.route('/movies/<int:movie_id>')
def movie_detail(movie_id):
    """影片详情：大海报 + 信息 + 今天起 5 天日期标签 + 当天场次卡片。"""
    movie = movie_service.get_movie(movie_id)
    # 下映影片详情页直接 404（验收测试第 1 条）
    if not movie or movie['status'] == 'OFF':
        abort(404)

    dates = movie_service.detail_dates()
    valid_dates = [d['date'] for d in dates]
    show_date = (request.args.get('date') or '').strip()
    if show_date not in valid_dates:
        show_date = valid_dates[0]

    shows = showtime_service.movie_showtimes(movie_id, show_date)
    for item in shows:
        # 语言版本：场次表没有该字段，按影厅类型给出展示文案
        item['lang'] = LANG_BY_HALL.get(item['hall_type'], '国语 · 2D')

    rating = int(movie['rating'] or 0)
    return render_template(
        'front/movie_detail.html',
        movie=movie,
        dates=dates,
        show_date=show_date,
        shows=shows,
        full_stars=min(rating // 20, 5),
        half_star=1 if rating % 20 >= 10 and rating // 20 < 5 else 0,
    )


# ---------------- 路由：选座 / 实时报价 ----------------

@front_bp.route('/shows/<int:show_id>/seats')
def show_seats(show_id):
    """选座页。?seats=1,2 用于下单失败后回填已选座位。"""
    selected = _parse_seat_ids(request.args.get('seats') or '')
    return _seat_page(show_id, selected)


@front_bp.route('/api/quote')
def api_quote():
    """实时报价接口（fetch 调用）：座位合法性 + 逐座价格 + 合计，单位元。"""
    show_id = request.args.get('show_id', type=int)
    seat_ids = _parse_seat_ids(request.args.get('seat_ids') or '')
    try:
        data = seat_service.quote(show_id, seat_ids)
    except BizError as exc:
        # 业务错误返回 400 + 中文提示，前端用 textContent 显示
        return jsonify({'ok': False, 'message': str(exc)}), 400

    items = [{
        'seat_id': it['seat_id'],
        'label': it['seat_label'],
        'seat_type': it['seat_type'],
        'seat_type_text': SEAT_TYPE.get(it['seat_type'], it['seat_type']),
        'surcharge_yuan': fen_to_yuan(it['surcharge']),
        'price_yuan': fen_to_yuan(it['price']),
    } for it in data['items']]

    return jsonify({
        'ok': True,
        'items': items,
        'count': data['count'],
        'total_yuan': data['total_yuan'],
        'base_price_yuan': data['base_price_yuan'],
    })


# ---------------- 路由：下单 / 支付 ----------------

@front_bp.route('/orders/new', methods=['POST'])
def order_new():
    """确认订单（POST）：手机号 + 姓名 -> 创建待支付订单并锁座 -> 跳到支付页。"""
    show_id = request.form.get('show_id', type=int)
    seat_ids = _parse_seat_ids(request.form.get('seat_ids') or '')
    phone = (request.form.get('phone') or '').strip()
    name = (request.form.get('name') or '').strip()

    try:
        order = order_service.create_order(show_id, seat_ids, phone, name)
    except BizError as exc:
        # 回到选座页，保留手机号 / 姓名 / 已选座位（业务错误不出现 500）
        flash(str(exc), 'error')
        return _seat_page(show_id, seat_ids, phone, name, flash_error=False)

    _remember_order(order['order_no'])
    _remember_customer(order['phone'], order['customer_name'])
    flash('下单成功，座位已为你保留 15 分钟，请尽快完成支付', 'success')
    return redirect(url_for('front.order_pay', order_no=order['order_no']))


@front_bp.route('/orders/<order_no>/pay')
def order_pay(order_no):
    """支付页：15 分钟倒计时 + 订单明细 + 模拟支付按钮。"""
    detail = order_service.get_order_detail(order_no)
    if not detail:
        abort(404)
    if not _can_view_order(detail['order']):
        flash('请先用下单时的手机号和姓名查询订单，再继续支付', 'warning')
        return redirect(url_for('front.orders'))

    return render_template('front/pay.html', **detail)


@front_bp.route('/orders/<order_no>/pay', methods=['POST'])
def order_pay_do(order_no):
    """模拟支付（POST）：后端再校验一次截止时间，成功生成 8 位取票码。"""
    detail = order_service.get_order_detail(order_no)
    if not detail:
        abort(404)
    if not _can_view_order(detail['order']):
        flash('订单不存在或身份不匹配', 'error')
        return redirect(url_for('front.orders'))

    try:
        order_service.pay(order_no)
    except BizError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('front.order_pay', order_no=order_no))

    _remember_order(order_no)
    _remember_paid(order_no)
    flash('支付成功，凭取票码到自助机取票', 'success')
    return redirect(url_for('front.order_success', order_no=order_no))


@front_bp.route('/orders/<order_no>/success')
def order_success(order_no):
    """支付成功页：8 位取票码。只允许刚支付的那个浏览器查看（session 白名单）。"""
    if order_no not in (session.get(SESSION_PAID) or []):
        abort(404)

    detail = order_service.get_order_detail(order_no)
    if not detail:
        abort(404)
    return render_template('front/success.html', **detail)


# ---------------- 路由：我的订单 / 取消 / 退票 ----------------

@front_bp.route('/orders', methods=['GET', 'POST'])
def orders():
    """我的订单：手机号 + 姓名查询（POST），或展示 session 里已确认身份的订单列表（GET）。"""
    phone, name = '', ''
    items, searched = [], False

    if request.method == 'POST':
        phone = (request.form.get('phone') or '').strip()
        name = (request.form.get('name') or '').strip()
        try:
            items = order_service.list_my_orders(phone, name)
        except BizError as exc:
            flash(str(exc), 'error')
        else:
            # 只有“手机号 + 姓名同时匹配到订单”才算确认身份；
            # 查不到时不要覆盖浏览器里已经确认过的身份（否则姓名打错一次就把自己踢下线）
            if items:
                _remember_customer(phone, name)
            searched = True
    else:
        phone, name = _current_customer()
        phone, name = phone or '', name or ''
        if phone and name:
            try:
                items = order_service.list_my_orders(phone, name)
            except BizError:
                session.pop(SESSION_CUSTOMER, None)
                phone, name = '', ''
            else:
                searched = True

    return render_template('front/orders.html', orders=items, phone=phone,
                           name=name, searched=searched)


@front_bp.route('/orders/<order_no>/cancel', methods=['POST'])
def order_cancel(order_no):
    """取消待支付订单（身份取自 session，Service 里再做一次匹配）。"""
    phone, name = _current_customer()
    if not phone:
        flash('请先用手机号和姓名查询订单', 'warning')
        return redirect(url_for('front.orders'))

    try:
        order_service.cancel(order_no, phone, name)
    except BizError as exc:
        flash(str(exc), 'error')
    else:
        flash('订单已取消，座位已释放', 'success')
    return redirect(url_for('front.orders'))


@front_bp.route('/orders/<order_no>/refund', methods=['POST'])
def order_refund(order_no):
    """退票（R7：已支付且开场前 ≥ 2 小时）。"""
    phone, name = _current_customer()
    if not phone:
        flash('请先用手机号和姓名查询订单', 'warning')
        return redirect(url_for('front.orders'))

    try:
        order_service.refund(order_no, phone, name, operator='观众')
    except BizError as exc:
        flash(str(exc), 'error')
    else:
        flash('退票成功，座位已释放，票款将原路退回', 'success')
    return redirect(url_for('front.orders'))
