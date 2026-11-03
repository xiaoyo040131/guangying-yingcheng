# services/stat_service.py —— 后台仪表板统计（需求说明第六节、第十一节）
"""
提供：
- 五个状态卡数量（待支付 / 已支付 / 已检票 / 已退票 / 已取消）；
- KPI：今日票房、今日观影人次、今日平均上座率；
- 近 7 天票房柱状图数据（高度百分比由这里算好，模板不用做业务判断）；
- 影片票房排行（横向条形图宽度百分比）；
- 今日场次列表 + 每场上座率进度条。
"""
from datetime import date, timedelta

from dao import order_dao, showtime_dao
from services import order_service
from utils import ORDER_STATUS, fen_to_yuan, today_str


def dashboard():
    order_service.settle_expired()   # R5：统计前先结算过期订单，状态卡数字才准确
    today = today_str()

    # 1) 状态卡
    counts = {key: 0 for key in ORDER_STATUS}
    counts.update(order_dao.count_by_status())

    # 2) KPI：今日票房与观影人次（已支付 + 已检票，不含退票）
    rev = order_dao.day_revenue(today)

    # 3) 今日平均上座率 = 今日所有场次已售座位 / 可售座位
    shows = showtime_dao.occupancy_list(today)
    total_sold = sum(s['sold'] for s in shows)
    total_cap = sum(s['capacity'] for s in shows)
    avg_pct = round(total_sold / total_cap * 100, 1) if total_cap else 0

    # 4) 近 7 天票房
    start = date.today() - timedelta(days=6)
    rows = order_dao.revenue_last_days(start.isoformat(), today)
    by_day = {r['d']: r['revenue'] for r in rows}
    max_rev = max(list(by_day.values()) + [1])
    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        key = d.isoformat()
        revenue = by_day.get(key, 0)
        days.append({
            'date': key,
            'label': '%d/%d' % (d.month, d.day),
            'revenue': revenue,
            'revenue_yuan': fen_to_yuan(revenue),
            'height': round(revenue / max_rev * 100, 1) if revenue else 2,
            'is_today': key == today,
        })

    # 5) 影片票房排行（近 7 天）
    ranking = order_dao.movie_ranking(start.isoformat(), today, 8)
    max_rank = max([r['revenue'] or 0 for r in ranking] + [1])
    rank_rows = [{
        'title': r['title'],
        'revenue': r['revenue'] or 0,
        'revenue_yuan': fen_to_yuan(r['revenue'] or 0),
        'tickets': r['tickets'] or 0,
        'width': round((r['revenue'] or 0) / max_rank * 100, 1),
    } for r in ranking]

    # 6) 今日场次列表（含上座率进度条）
    today_shows = [{
        'id': s['id'],
        'title': s['title'],
        'hall_name': s['hall_name'],
        'start_time': s['start_time'],
        'end_time': s['end_time'],
        'status': s['status'],
        'sold': s['sold'],
        'capacity': s['capacity'],
        'pct': round(s['sold'] / s['capacity'] * 100) if s['capacity'] else 0,
    } for s in shows]

    return {
        'today': today,
        'counts': counts,
        'kpi': {
            'revenue': rev['revenue'],
            'revenue_yuan': fen_to_yuan(rev['revenue']),
            'people': rev['people'],
            'avg_pct': avg_pct,
            'shows': len(shows),
        },
        'days': days,
        'max_revenue_yuan': fen_to_yuan(max(by_day.values()) if by_day else 0),
        'ranking': rank_rows,
        'today_shows': today_shows,
    }
