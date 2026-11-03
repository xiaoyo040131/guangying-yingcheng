# services/pricing.py —— R3 计价规则（全系统唯一的算价入口）
"""
每座票价 = 场次基础票价 + 座位类型加价：
    普通座 +0，情侣座 +5 元，VIP 座 +20 元（金额单位：分）
总价 = 逐座相加。

init_db.py 生成演示订单时也调用这里的函数，保证"演示数据"与"线上下单"
用的是同一套计价逻辑，不会出现两套价格。
"""
from config import VIP_ADD_FEN, COUPLE_ADD_FEN
from utils import BizError

# 座位类型加价（分）
SEAT_SURCHARGE = {
    'NORMAL': 0,
    'COUPLE': COUPLE_ADD_FEN,
    'VIP': VIP_ADD_FEN,
}


def seat_price(base_price, seat_type):
    """单个座位票价（分）= 基础票价 + 座位类型加价。"""
    if seat_type not in SEAT_SURCHARGE:
        raise BizError('座位类型不正确')
    return int(base_price) + SEAT_SURCHARGE[seat_type]


def price_detail(base_price, seats):
    """逐座计价。

    seats: 座位行列表，每行需含 id / row_no / col_no / seat_type
    返回 (明细列表, 总价分)：
        明细项 = {seat_id, row_no, col_no, seat_type, seat_label, surcharge, price}
    """
    items = []
    total = 0
    for st in seats:
        surcharge = SEAT_SURCHARGE.get(st['seat_type'])
        if surcharge is None:
            raise BizError('座位类型不正确')
        price = int(base_price) + surcharge
        items.append({
            'seat_id': st['id'],
            'row_no': st['row_no'],
            'col_no': st['col_no'],
            'seat_type': st['seat_type'],
            'seat_label': '%d排%d座' % (st['row_no'], st['col_no']),
            'surcharge': surcharge,
            'price': price,
        })
        total += price
    return items, total


def total_price(base_price, seats):
    """只取总价（分）。"""
    return price_detail(base_price, seats)[1]
