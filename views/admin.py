# views/admin.py —— 后台蓝图（url_prefix='/admin'，全部需要登录）
"""
后台页面与接口：
- 仪表板      GET  /admin/
- 排片表      GET  /admin/schedule?date=
- 订单管理     GET  /admin/orders（标签页/搜索/日期筛选/分页）、GET /admin/orders/<订单号>
- 检票        GET  /admin/checkin?code=
- 影片管理     GET  /admin/movies、/admin/movies/new、/admin/movies/<id>/edit
- 影厅管理     GET  /admin/halls、/admin/halls/new、/admin/halls/<id>/edit、
               GET  /admin/halls/<id>/seats（座位编辑）
- 接口（fetch+JSON，未登录返回 401）：
    POST /admin/api/schedule/create
    POST /admin/api/schedule/<id>/update
    POST /admin/api/schedule/<id>/status      {action: stop|resume|cancel}
    POST /admin/api/orders/<订单号>/checkin
    POST /admin/api/orders/<订单号>/refund
    POST /admin/api/checkin                   {code}  按取票码检票
    POST /admin/api/movies/<id>/status
    POST /admin/api/halls/<id>/toggle
    POST /admin/api/halls/<id>/seats          {changes: {座位id: 类型}}

约定：视图只取参数、调 service、渲染模板或返回 JSON；所有状态改变都走 service 里的状态机函数，
业务错误（BizError）转成中文提示，页面用 flash、接口用 {ok:false, message}，绝不出现 500。
"""
import config
from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from services import (admin_service, movie_service, order_service,
                      showtime_service, stat_service)
from utils import BizError, today_str
from views.auth import login_guard

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# 订单管理标签页：key 为空表示"全部"
ORDER_TABS = [
    ('', '全部'),
    ('UNPAID', '待支付'),
    ('PAID', '已支付'),
    ('USED', '已检票'),
    ('REFUNDED', '已退票'),
    ('EXPIRED', '已超时'),
    ('CANCELLED', '已取消'),
]

# 订单轨迹动作的中文（纯展示映射）
LOG_ACTION_TEXT = {
    'create': '创建订单',
    'pay': '支付',
    'cancel': '取消',
    'expire': '超时取消',
    'refund': '退票',
    'check': '检票',
}

# 排片时间轴的范围（分钟）：09:00 ~ 24:00，交给前端做点击定位、结束时间预览
TIMELINE = {
    'start': showtime_service.TIMELINE_START,
    'span': showtime_service.TIMELINE_SPAN,
    'clean': config.CLEAN_MINUTES,
}


# ---------------- 统一登录校验 ----------------

@admin_bp.before_request
def require_login():
    """后台蓝图下所有请求统一校验登录状态（接口返回 401 JSON，页面跳登录页）。"""
    return login_guard()


# ---------------- 小工具 ----------------

def _payload():
    """接口入参：优先 JSON，退化为表单。"""
    return request.get_json(silent=True) or request.form


def _as_int(value, message):
    """把入参转成整数，失败给中文提示。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BizError(message)


def _price_fen(value):
    """票价输入单位是"元"，数据库存"分"。转换后由 service 统一校验范围。"""
    try:
        yuan = float(str(value).strip())
    except (TypeError, ValueError):
        raise BizError('票价需为数字（单位：元）')
    return int(round(yuan * 100))


def _operator():
    """当前操作人（写订单轨迹用）。"""
    return session.get('admin_username') or 'admin'


def _fail(exc):
    """业务错误 -> 400 + 中文提示（前端用 textContent 显示）。"""
    return jsonify({'ok': False, 'message': str(exc)}), 400


# ---------------- 仪表板 ----------------

@admin_bp.route('/', strict_slashes=False)
def dashboard():
    """仪表板：状态卡 + 今日 KPI + 近 7 天票房 + 影片排行 + 今日场次上座率。

    strict_slashes=False：/admin 与 /admin/ 都能直接命中本视图，
    未登录时才能立刻 302 到登录页，而不是先被 308 重定向到 /admin/。
    """
    data = stat_service.dashboard()
    return render_template('admin/dashboard.html', active='dashboard', d=data)


# ---------------- 排片表 ----------------

@admin_bp.route('/schedule')
def schedule():
    """排片时间轴：行 = 影厅，横轴 = 09:00~24:00。"""
    date_str = (request.args.get('date') or '').strip() or today_str()
    try:
        sv = showtime_service.schedule_view(date_str)
    except BizError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('admin.schedule'))

    movies = [{'id': m['id'], 'title': m['title'], 'duration': m['duration'],
               'status': m['status']}
              for m in showtime_service.schedule_movie_options()]
    return render_template('admin/schedule.html', active='schedule',
                           sv=sv, movies=movies, timeline=TIMELINE)


# ---------------- 订单管理 ----------------

@admin_bp.route('/orders')
def orders():
    """订单列表：状态标签页 + 关键词搜索 + 日期筛选 + 分页（翻页保留条件）。"""
    status = (request.args.get('status') or '').strip()
    keyword = (request.args.get('keyword') or '').strip()
    date_str = (request.args.get('date') or '').strip()
    page = request.args.get('page', type=int) or 1
    if page < 1:
        page = 1

    result = order_service.admin_orders(status=status or None, keyword=keyword,
                                        date_str=date_str or None,
                                        page=page, page_size=config.PAGE_SIZE)

    counts = result['status_count']
    total_all = sum(counts.values())
    tabs = [{
        'key': key,
        'label': label,
        'count': total_all if not key else counts.get(key, 0),
    } for key, label in ORDER_TABS]

    args = {'status': result['status'] or '', 'keyword': result['keyword'],
            'date': result['date']}
    return render_template('admin/orders.html', active='orders', r=result,
                           tabs=tabs, args=args)


@admin_bp.route('/orders/<order_no>')
def order_detail(order_no):
    """订单详情：座位、明细、轨迹与可执行的操作（检票 / 退票）。"""
    detail = order_service.get_order_detail(order_no)
    if not detail:
        abort(404)
    return render_template('admin/order_detail.html', active='orders',
                           detail=detail, action_text=LOG_ACTION_TEXT)


# ---------------- 检票 ----------------

@admin_bp.route('/checkin')
def checkin():
    """检票页：输入 8 位取票码 -> 显示订单 -> 确认检票（R8 时间窗由 service 判定）。

    说明：这里用后台订单检索（订单号 / 手机号 / 取票码模糊匹配）找到这一单，
    再取详情，这样视图层不直接访问 dao，保持 views -> services 的调用方向。
    """
    code = (request.args.get('code') or '').strip()
    detail, error = None, None

    if code:
        if not (code.isdigit() and len(code) == 8):
            error = '请输入 8 位数字取票码'
        else:
            found = order_service.admin_orders(keyword=code, page=1, page_size=20)
            row = next((x for x in found['orders'] if x['ticket_code'] == code), None)
            if row:
                detail = order_service.get_order_detail(row['order_no'])
            else:
                error = '未找到该取票码对应的订单'
    if error:
        flash(error, 'error')

    return render_template('admin/checkin.html', active='checkin',
                           code=code, detail=detail)


# ---------------- 影片管理 ----------------

@admin_bp.route('/movies')
def movies():
    """影片列表：海报、类型、时长、评分、状态。"""
    return render_template('admin/movies.html', active='movies',
                           movies=movie_service.admin_list())


@admin_bp.route('/movies/new', methods=['GET', 'POST'])
def movie_new():
    """新增影片。"""
    if request.method == 'POST':
        try:
            movie_service.save_movie(request.form)
        except BizError as exc:
            flash(str(exc), 'error')
            return render_template('admin/movie_form.html', active='movies',
                                   movie=None, form=request.form,
                                   posters=movie_service.POSTER_CHOICES), 200
        flash('影片已添加', 'success')
        return redirect(url_for('admin.movies'))

    return render_template('admin/movie_form.html', active='movies',
                           movie=None, form={}, posters=movie_service.POSTER_CHOICES)


@admin_bp.route('/movies/<int:movie_id>/edit', methods=['GET', 'POST'])
def movie_edit(movie_id):
    """编辑影片。"""
    movie = movie_service.get_movie(movie_id)
    if not movie:
        abort(404)

    if request.method == 'POST':
        try:
            movie_service.save_movie(request.form, movie_id=movie_id)
        except BizError as exc:
            flash(str(exc), 'error')
            return render_template('admin/movie_form.html', active='movies',
                                   movie=movie, form=request.form,
                                   posters=movie_service.POSTER_CHOICES), 200
        flash('影片信息已保存', 'success')
        return redirect(url_for('admin.movies'))

    return render_template('admin/movie_form.html', active='movies',
                           movie=movie, form={}, posters=movie_service.POSTER_CHOICES)


# ---------------- 影厅管理 ----------------

@admin_bp.route('/halls')
def halls():
    """影厅列表：名称、类型、行列、座位数、场次数、启用状态。"""
    return render_template('admin/halls.html', active='halls',
                           halls=admin_service.list_halls())


@admin_bp.route('/halls/new', methods=['GET', 'POST'])
def hall_new():
    """新增影厅。"""
    if request.method == 'POST':
        try:
            hall_id = admin_service.save_hall(request.form)
        except BizError as exc:
            flash(str(exc), 'error')
            return render_template('admin/hall_form.html', active='halls',
                                   hall=None, form=request.form), 200
        flash('影厅已创建，请继续编辑座位图', 'success')
        return redirect(url_for('admin.seat_editor', hall_id=hall_id))

    return render_template('admin/hall_form.html', active='halls', hall=None, form={})


@admin_bp.route('/halls/<int:hall_id>/edit', methods=['GET', 'POST'])
def hall_edit(hall_id):
    """编辑影厅基础信息。"""
    hall = admin_service.get_hall(hall_id)

    if request.method == 'POST':
        try:
            admin_service.save_hall(request.form, hall_id=hall_id)
        except BizError as exc:
            flash(str(exc), 'error')
            return render_template('admin/hall_form.html', active='halls',
                                   hall=hall, form=request.form), 200
        flash('影厅信息已保存', 'success')
        return redirect(url_for('admin.halls'))

    return render_template('admin/hall_form.html', active='halls', hall=hall, form={})


@admin_bp.route('/halls/<int:hall_id>/seats')
def seat_editor(hall_id):
    """座位编辑页：在网格上点选座位类型（普通 / 情侣 / VIP / 无座位）。"""
    data = admin_service.seat_grid(hall_id)
    return render_template('admin/seat_editor.html', active='halls', g=data)


# ---------------- 接口：排片 ----------------

@admin_bp.route('/api/schedule/create', methods=['POST'])
def api_schedule_create():
    """新建场次（R9：同厅不重叠、开始不早于现在、影片须热映/即将上映）。"""
    data = _payload()
    try:
        show = showtime_service.create_showtime(
            _as_int(data.get('movie_id'), '请选择影片'),
            _as_int(data.get('hall_id'), '影厅不正确'),
            (data.get('show_date') or '').strip(),
            (data.get('start_time') or '').strip(),
            _price_fen(data.get('price')))
    except BizError as exc:
        return _fail(exc)
    return jsonify({'ok': True, 'id': show['id'],
                    'message': '场次已创建：%s - %s' % (show['start_time'], show['end_time'])})


@admin_bp.route('/api/schedule/<int:show_id>/update', methods=['POST'])
def api_schedule_update(show_id):
    """编辑场次：已有售出票的场次只能改票价。"""
    data = _payload()
    try:
        show = showtime_service.update_showtime(
            show_id,
            _as_int(data.get('movie_id'), '请选择影片'),
            (data.get('start_time') or '').strip(),
            _price_fen(data.get('price')))
    except BizError as exc:
        return _fail(exc)
    return jsonify({'ok': True, 'id': show['id'],
                    'message': '场次已更新：%s - %s' % (show['start_time'], show['end_time'])})


@admin_bp.route('/api/schedule/<int:show_id>/status', methods=['POST'])
def api_schedule_status(show_id):
    """场次状态操作：stop 停售 / resume 恢复售票 / cancel 取消场次。"""
    data = _payload()
    action = (data.get('action') or '').strip()
    try:
        message = showtime_service.change_status(show_id, action)
    except BizError as exc:
        return _fail(exc)
    return jsonify({'ok': True, 'message': message})


# ---------------- 接口：订单 ----------------

@admin_bp.route('/api/orders/<order_no>/checkin', methods=['POST'])
def api_order_checkin(order_no):
    """后台检票（R8：开场前 60 分钟 ~ 开场后 30 分钟，不能重复检票）。"""
    detail = order_service.get_order_detail(order_no)
    if not detail:
        return jsonify({'ok': False, 'message': '订单不存在'}), 404
    try:
        order_service.checkin_order(detail['order']['id'], operator=_operator())
    except BizError as exc:
        return _fail(exc)
    return jsonify({'ok': True, 'message': '检票成功，请放行入场'})


@admin_bp.route('/api/orders/<order_no>/refund', methods=['POST'])
def api_order_refund(order_no):
    """后台退票（R7：开场前 >= 2 小时；退票释放座位）。"""
    try:
        order_service.refund(order_no, operator='管理员 %s' % _operator())
    except BizError as exc:
        return _fail(exc)
    return jsonify({'ok': True, 'message': '退票成功，座位已释放'})


@admin_bp.route('/api/checkin', methods=['POST'])
def api_checkin():
    """按取票码检票（检票页的"确认检票"按钮）。"""
    data = _payload()
    try:
        order = order_service.checkin_by_code(data.get('code'), operator=_operator())
    except BizError as exc:
        return _fail(exc)
    return jsonify({'ok': True, 'message': '检票成功：%s 已入场' % order['order_no'],
                    'order_no': order['order_no']})


# ---------------- 接口：影片 / 影厅 ----------------

@admin_bp.route('/api/movies/<int:movie_id>/status', methods=['POST'])
def api_movie_status(movie_id):
    """切换影片状态（热映 / 即将上映 / 下映）。"""
    data = _payload()
    status = (data.get('status') or '').strip()
    try:
        movie_service.set_status(movie_id, status)
    except BizError as exc:
        return _fail(exc)
    return jsonify({'ok': True, 'message': '影片状态已更新'})


@admin_bp.route('/api/halls/<int:hall_id>/toggle', methods=['POST'])
def api_hall_toggle(hall_id):
    """启用 / 停用影厅（停用后不再出现在前台与排片下拉中）。"""
    data = _payload()
    enabled = str(data.get('is_enabled', '1')) in ('1', 'true', 'True', 'on')
    try:
        admin_service.toggle_hall(hall_id, enabled)
    except BizError as exc:
        return _fail(exc)
    return jsonify({'ok': True,
                    'message': '影厅已%s' % ('启用' if enabled else '停用')})


@admin_bp.route('/api/halls/<int:hall_id>/seats', methods=['POST'])
def api_hall_seats(hall_id):
    """保存座位类型：changes = {座位id: NORMAL|COUPLE|VIP|NONE}。

    保存后 service 会重新配对情侣座，落单的自动改回普通座并在返回值里告诉页面。
    """
    data = _payload()
    changes = data.get('changes') or {}
    if not isinstance(changes, dict):
        return jsonify({'ok': False, 'message': '座位数据格式不正确'}), 400
    try:
        result = admin_service.save_seats(hall_id, changes)
    except BizError as exc:
        return _fail(exc)

    message = '座位已保存'
    if result['unpaired']:
        message += '；有 %d 个情侣座落单，已自动改回普通座' % result['unpaired']
    return jsonify({'ok': True, 'message': message, 'unpaired': result['unpaired']})
