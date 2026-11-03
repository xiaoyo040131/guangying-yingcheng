# services/seat_service.py —— 选座业务：座位图数据、选座校验（R2）、实时报价
"""
R2 选座规则：
- 每单 1~4 个座位；
- 座位必须属于该场次的影厅、且处于启用状态；
- 情侣座必须成对选择（不能只选一半）。

读座位图前先执行一次"过期结算"（R5），保证过期的待支付订单不会一直占着座。
"""
from config import MAX_SEATS_PER_ORDER
from dao import order_dao, seat_dao, showtime_dao
from services import pricing, showtime_service
from utils import BizError, SEAT_TYPE, fen_to_yuan

# 座位图状态：free 可选 / sold 已售（已支付、已检票）/ locked 锁定中（待支付）/ disabled 不可售
STATUS_TEXT = {'free': '可选', 'sold': '已售', 'locked': '锁定中', 'disabled': '不可售'}


def normalize_seat_ids(raw_ids):
    """把前端传来的座位 id 列表转成去重的整数列表。"""
    ids = []
    for x in raw_ids or []:
        try:
            v = int(x)
        except (TypeError, ValueError):
            raise BizError('座位参数不正确')
        if v not in ids:
            ids.append(v)
    return ids


def get_layout(show_id):
    """选座页数据：场次信息 + 按行组织的座位图 + 统计。"""
    from services import order_service  # 延迟导入，避免与 order_service 循环引用
    order_service.settle_expired()

    show = showtime_dao.get_by_id(show_id)
    if not show:
        raise BizError('场次不存在')
    showtime_service.assert_bookable(show)

    seats = seat_dao.list_by_hall(show['hall_id'])
    status_map = order_dao.seat_status_map(show_id)
    aisle = show['aisle_after_col']

    rows, sold, locked, free, disabled = [], 0, 0, 0, 0
    by_row = {}
    for s in seats:
        by_row.setdefault(s['row_no'], []).append(s)

    for row_no in range(1, show['row_count'] + 1):
        cells = []
        for s in by_row.get(row_no, []):
            if not s['is_enabled']:
                status = 'disabled'
                disabled += 1
            else:
                order_status = status_map.get(s['id'])
                if order_status in ('PAID', 'USED'):
                    status = 'sold'
                    sold += 1
                elif order_status == 'UNPAID':
                    status = 'locked'
                    locked += 1
                else:
                    status = 'free'
                    free += 1
            cells.append({
                'kind': 'seat',
                'id': s['id'],
                'row_no': s['row_no'],
                'col_no': s['col_no'],
                'seat_type': s['seat_type'],
                'seat_type_text': SEAT_TYPE.get(s['seat_type'], s['seat_type']),
                'partner_id': s['couple_partner_id'],
                'status': status,
                'status_text': STATUS_TEXT[status],
                'label': '%d排%d座' % (s['row_no'], s['col_no']),
            })
            # 过道：在该列之后插入一个空白单元（视觉分隔）
            if aisle and s['col_no'] == aisle:
                cells.append({'kind': 'aisle'})
        rows.append({'row_no': row_no, 'cells': cells})

    return {
        'show': show,
        'rows': rows,
        'stats': {'total': sold + locked + free, 'sold': sold, 'locked': locked,
                  'free': free, 'disabled': disabled},
        'has_couple': any(s['seat_type'] == 'COUPLE' for s in seats),
        'max_seats': MAX_SEATS_PER_ORDER,
    }


def validate_selection(show, seat_ids):
    """R2 校验：数量、归属、启用、情侣座成对。返回按行列排序的座位行列表。"""
    if not (1 <= len(seat_ids) <= MAX_SEATS_PER_ORDER):
        raise BizError('每单只能选择 1~%d 个座位' % MAX_SEATS_PER_ORDER)

    seats = seat_dao.list_by_ids(seat_ids)
    if len(seats) != len(seat_ids):
        raise BizError('座位不存在')

    selected = set(seat_ids)
    for s in seats:
        if s['hall_id'] != show['hall_id']:
            raise BizError('%d排%d座 不属于该场次的影厅' % (s['row_no'], s['col_no']))
        if not s['is_enabled']:
            raise BizError('%d排%d座 不可售' % (s['row_no'], s['col_no']))

    # 情侣座必须成对：选了其中一个，另一个也必须在已选集合里
    for s in seats:
        if s['seat_type'] == 'COUPLE':
            partner = s['couple_partner_id']
            if not partner or partner not in selected:
                raise BizError('%d排%d座 是情侣座，必须成对选择' % (s['row_no'], s['col_no']))

    return sorted(seats, key=lambda s: (s['row_no'], s['col_no']))


def quote(show_id, seat_ids):
    """实时报价（前端 fetch 调用）：座位合法性 + 逐座价格明细 + 合计。"""
    from services import order_service
    order_service.settle_expired()

    show = showtime_dao.get_by_id(show_id)
    if not show:
        raise BizError('场次不存在')
    showtime_service.assert_bookable(show)

    seats = validate_selection(show, seat_ids)
    taken = order_dao.taken_seats_detail(show['id'], [s['id'] for s in seats])
    if taken:
        labels = '、'.join('%d排%d座' % (t['row_no'], t['col_no']) for t in taken)
        raise BizError('%s 已被其他观众选走，请重新选座' % labels)

    items, total = pricing.price_detail(show['base_price'], seats)
    return {
        'items': items,
        'total': total,
        'total_yuan': fen_to_yuan(total),
        'base_price': show['base_price'],
        'base_price_yuan': fen_to_yuan(show['base_price']),
        'count': len(seats),
    }


def taken_labels(show_id, seat_ids):
    """被占座位的友好标签（下单冲突提示用）。"""
    taken = order_dao.taken_seats_detail(show_id, seat_ids)
    return '、'.join('%d排%d座' % (t['row_no'], t['col_no']) for t in taken)
