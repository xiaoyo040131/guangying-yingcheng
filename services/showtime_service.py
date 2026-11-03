# services/showtime_service.py —— 场次业务：可售校验（R1）、排片校验（R9）、排片时间轴
"""
R1 可售：只售今天起 5 天内、状态为"在售"的场次；开场前 30 分钟停售。
R9 排片：同一影厅同一天场次时间段（开始 ~ 结束含 15 分钟清场）不能重叠；
        新建场次的开始时间不能早于现在；影片状态必须是热映或即将上映。
排片的写操作放在 BEGIN IMMEDIATE 事务里先查重叠再写入，避免并发排出重叠场次。
"""
from datetime import date, datetime, timedelta

import db
from config import BOOKABLE_DAYS, CLEAN_MINUTES, SCHEDULE_START, SCHEDULE_END, STOP_SALE_MINUTES
from dao import hall_dao, movie_dao, showtime_dao
from utils import (BizError, SHOWTIME_STATUS, hhmm_to_min, min_to_hhmm, parse_date,
                   WEEKDAY_CN, fen_to_yuan)

# 时间轴范围（分钟）：09:00 ~ 24:00，共 900 分钟
TIMELINE_START = hhmm_to_min(SCHEDULE_START)
TIMELINE_SPAN = hhmm_to_min(SCHEDULE_END) - TIMELINE_START


def bookable_dates():
    """可售/可排片的日期列表：今天起 5 天。"""
    today = date.today()
    out = []
    for i in range(BOOKABLE_DAYS):
        d = today + timedelta(days=i)
        out.append({
            'date': d.isoformat(),
            'label': '%d月%d日' % (d.month, d.day),
            'weekday': '今天' if i == 0 else ('明天' if i == 1 else WEEKDAY_CN[d.weekday()]),
            'is_today': i == 0,
        })
    return out


def show_start_dt(show):
    """场次开始时间（datetime）：show 需含 show_date 与 start_time。"""
    d = parse_date(show['show_date'])
    return datetime(d.year, d.month, d.day) + timedelta(minutes=hhmm_to_min(show['start_time']))


# ---------------- R1 可售校验 ----------------

def assert_bookable(show):
    """校验场次是否可售（选座 / 下单前调用），不合法抛 BizError。"""
    if show is None:
        raise BizError('场次不存在')
    if show['status'] != 'ON_SALE':
        raise BizError('该场次已停售')
    d = parse_date(show['show_date'])
    today = date.today()
    if d < today or d > today + timedelta(days=BOOKABLE_DAYS - 1):
        raise BizError('只能购买今天起 %d 天内的场次' % BOOKABLE_DAYS)
    start = show_start_dt(show)
    if start - timedelta(minutes=STOP_SALE_MINUTES) <= datetime.now():
        raise BizError('该场次已停止售票（开场前 %d 分钟停售）' % STOP_SALE_MINUTES)


def movie_showtimes(movie_id, show_date):
    """影片详情页：某天场次列表（带余票、售罄/紧张/停售标记）。"""
    parse_date(show_date)
    rows = showtime_dao.list_by_movie_date(movie_id, show_date)
    now = datetime.now()
    result = []
    for r in rows:
        capacity = r['row_count'] * r['col_count']
        sold = showtime_dao.sold_seat_count(r['id'])
        remaining = max(capacity - sold, 0)
        start = show_start_dt(r)
        result.append({
            'id': r['id'],
            'title': r['title'],
            'hall_name': r['hall_name'],
            'hall_type': r['hall_type'],
            'start_time': r['start_time'],
            'end_time': r['end_time'],
            'base_price': r['base_price'],
            'status': r['status'],
            'capacity': capacity,
            'sold': sold,
            'remaining': remaining,
            'is_stopped': (r['status'] != 'ON_SALE'
                           or start - timedelta(minutes=STOP_SALE_MINUTES) <= now),
            'is_past': start <= now,
            'is_tight': remaining < 10,
        })
    return result


# ---------------- R9 排片 ----------------

def _assert_movie_for_schedule(movie_id):
    movie = movie_dao.get_by_id(movie_id)
    if not movie:
        raise BizError('影片不存在')
    if movie['status'] not in ('SHOWING', 'COMING'):
        raise BizError('只有热映或即将上映的影片可以排片')
    return movie


def _assert_hall_for_schedule(hall_id):
    hall = hall_dao.get_by_id(hall_id)
    if not hall:
        raise BizError('影厅不存在')
    if not hall['is_enabled']:
        raise BizError('该影厅已停用，不能排片')
    return hall


def _assert_schedule_date(show_date):
    d = parse_date(show_date)
    today = date.today()
    if d < today or d > today + timedelta(days=BOOKABLE_DAYS - 1):
        raise BizError('排片日期需在今天起 %d 天内' % BOOKABLE_DAYS)
    return d


def _calc_end_time(start_time, duration):
    """结束时间 = 开始 + 片长 + 15 分钟清场，且不得超过 24:00。"""
    start_min = hhmm_to_min(start_time)
    if start_min < TIMELINE_START or start_min >= 24 * 60:
        raise BizError('开始时间需在 %s ~ 24:00 之间' % SCHEDULE_START)
    end_min = start_min + int(duration) + CLEAN_MINUTES
    if end_min > 24 * 60:
        raise BizError('结束时间会超过 24:00（片长 %d 分钟 + %d 分钟清场），请选择更早的开始时间'
                       % (duration, CLEAN_MINUTES))
    return min_to_hhmm(end_min)


def _check_price(base_price):
    """票价单位统一为"分"：1~300 元 = 100~30000 分（视图层负责把"元"换算成分）。"""
    try:
        price = int(base_price)
    except (TypeError, ValueError):
        raise BizError('票价需为整数')
    if not (100 <= price <= 30000):
        raise BizError('票价需在 1~300 元之间')
    return price


def create_showtime(movie_id, hall_id, show_date, start_time, base_price):
    """新建场次。事务内先查同厅同天重叠，再写入（R9）。"""
    movie = _assert_movie_for_schedule(movie_id)
    hall = _assert_hall_for_schedule(hall_id)
    _assert_schedule_date(show_date)
    start_time = (start_time or '').strip()
    hhmm_to_min(start_time)
    end_time = _calc_end_time(start_time, movie['duration'])
    price = _check_price(base_price)

    d = parse_date(show_date)
    start_dt = datetime(d.year, d.month, d.day) + timedelta(minutes=hhmm_to_min(start_time))
    if start_dt <= datetime.now():
        raise BizError('开始时间不能早于当前时间')

    with db.transaction():
        if showtime_dao.overlap_count(hall['id'], show_date, start_time, end_time) > 0:
            raise BizError('%s 在 %s ~ %s 已有场次，同一影厅的场次不能重叠'
                           % (hall['name'], start_time, end_time))
        show_id = showtime_dao.insert(movie['id'], hall['id'], show_date,
                                      start_time, end_time, price)
    return showtime_dao.get_by_id(show_id)


def update_showtime(show_id, movie_id, start_time, base_price):
    """编辑场次：已有售出票的场次只允许改票价（改时间会让已售座位对不上）。"""
    show = showtime_dao.get_by_id(show_id)
    if not show:
        raise BizError('场次不存在')
    if show['status'] == 'CANCELLED':
        raise BizError('已取消的场次不能编辑')

    movie = _assert_movie_for_schedule(movie_id)
    start_time = (start_time or '').strip()
    hhmm_to_min(start_time)
    end_time = _calc_end_time(start_time, movie['duration'])
    price = _check_price(base_price)

    sold = showtime_dao.sold_seat_count(show_id)
    if sold > 0 and (int(movie_id) != show['movie_id'] or start_time != show['start_time']):
        raise BizError('该场次已有 %d 个座位售出，只能修改票价，不能改影片或时间' % sold)

    d = parse_date(show['show_date'])
    start_dt = datetime(d.year, d.month, d.day) + timedelta(minutes=hhmm_to_min(start_time))
    if start_dt <= datetime.now():
        raise BizError('开始时间不能早于当前时间')

    with db.transaction():
        if showtime_dao.overlap_count(show['hall_id'], show['show_date'],
                                      start_time, end_time, exclude_id=show_id) > 0:
            raise BizError('该影厅在这一时间段已有其他场次，场次不能重叠')
        showtime_dao.update(show_id, movie['id'], start_time, end_time, price)
    return showtime_dao.get_by_id(show_id)


def change_status(show_id, action):
    """场次状态操作：stop 停售 / resume 恢复售票 / cancel 取消。"""
    show = showtime_dao.get_by_id(show_id)
    if not show:
        raise BizError('场次不存在')

    if action == 'stop':
        if show['status'] == 'CANCELLED':
            raise BizError('已取消的场次不能再停售')
        showtime_dao.set_status(show_id, 'STOPPED')
        return '场次已停售'

    if action == 'resume':
        if show['status'] == 'CANCELLED':
            raise BizError('已取消的场次不能恢复售票')
        if show_start_dt(show) - timedelta(minutes=STOP_SALE_MINUTES) <= datetime.now():
            raise BizError('该场次已过停售时间，不能恢复售票')
        showtime_dao.set_status(show_id, 'ON_SALE')
        return '场次已恢复售票'

    if action == 'cancel':
        sold = showtime_dao.sold_seat_count(show_id)
        if sold > 0:
            raise BizError('该场次已有 %d 个座位售出，不能删除，只能停售' % sold)
        showtime_dao.set_status(show_id, 'CANCELLED')
        return '场次已取消'

    raise BizError('未知的场次操作')


# ---------------- 排片时间轴（后台可视化） ----------------

def schedule_view(show_date):
    """排片表数据：行 = 影厅，横轴 = 09:00~24:00，场次块按分钟定位。

    返回结构：
        {'date', 'dates', 'halls': [{'hall', 'blocks': [...]}], 'stats'}
    每个 block 含 left / width 百分比，模板直接写进 style。
    """
    parse_date(show_date)
    halls = hall_dao.list_enabled()
    shows = showtime_dao.occupancy_list(show_date)

    by_hall = {}
    for s in shows:
        by_hall.setdefault(s['hall_id'], []).append(s)

    rows, total_sold, total_cap = [], 0, 0
    for hall in halls:
        blocks = []
        for s in by_hall.get(hall['id'], []):
            left = (hhmm_to_min(s['start_time']) - TIMELINE_START) / TIMELINE_SPAN * 100
            width = (hhmm_to_min(s['end_time']) - hhmm_to_min(s['start_time'])) / TIMELINE_SPAN * 100
            pct = round(s['sold'] / s['capacity'] * 100) if s['capacity'] else 0
            blocks.append({
                'id': s['id'],
                'title': s['title'],
                'start_time': s['start_time'],
                'end_time': s['end_time'],
                'status': s['status'],
                'status_text': SHOWTIME_STATUS.get(s['status'], s['status']),
                'sold': s['sold'],
                'capacity': s['capacity'],
                'pct': pct,
                'left': round(left, 2),
                'width': round(width, 2),
                'base_price': s['base_price'],
                'price_yuan': fen_to_yuan(s['base_price']),
                'duration': s['duration'],
            })
            total_sold += s['sold']
            total_cap += s['capacity']
        rows.append({'hall': hall, 'blocks': blocks})

    # 时间轴刻度：每 2 小时一格
    ticks = []
    for m in range(TIMELINE_START, hhmm_to_min(SCHEDULE_END) + 1, 120):
        ticks.append({'label': min_to_hhmm(m),
                      'left': (m - TIMELINE_START) / TIMELINE_SPAN * 100})

    d = parse_date(show_date)
    return {
        'date': show_date,
        'date_label': '%d月%d日 %s' % (d.month, d.day, WEEKDAY_CN[d.weekday()]),
        'dates': bookable_dates(),
        'halls': rows,
        'ticks': ticks,
        'stats': {
            'shows': len(shows),
            'sold': total_sold,
            'capacity': total_cap,
            'pct': round(total_sold / total_cap * 100) if total_cap else 0,
            'status_count': showtime_dao.count_by_status(show_date),
        },
    }


def schedule_movie_options():
    """排片弹窗的影片下拉：热映 + 即将上映。"""
    return [m for m in movie_dao.get_all() if m['status'] in ('SHOWING', 'COMING')]
