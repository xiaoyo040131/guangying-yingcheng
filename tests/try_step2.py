# tests/try_step2.py —— 第 2 步业务层验证脚本
"""
在【临时数据库】上验证业务层（不会动 instance/ 里的正式库）：
① 一次正常下单；② 同座位重复下单被拒绝；③ 一次合法状态流转（支付）；
④ 一次非法状态流转（取消已支付订单）被拒绝；⑤ 三种不同日期/时段的计价结果。

运行： python -X utf8 tests/try_step2.py
"""
import os
import sys
import sqlite3
import tempfile
from datetime import date, timedelta

# 让脚本能 import 项目根目录下的模块
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

# 关键：先把 DATABASE 指向临时库，再 import db / init_db，避免覆盖正式库
TMP_DIR = tempfile.mkdtemp(prefix='guangying_step2_')
config.DATABASE = os.path.join(TMP_DIR, 'step2.db')

import db                                     # noqa: E402
import init_db                                # noqa: E402
from flask import Flask                       # noqa: E402
from dao import order_dao, seat_dao, showtime_dao      # noqa: E402
from services import pricing, seat_service, order_service  # noqa: E402
from utils import BizError, ORDER_STATUS, fen_to_yuan      # noqa: E402

app = Flask(__name__)
db.init_app(app)

passed, failed = 0, 0


def ok(msg):
    global passed
    passed += 1
    print('  ✔ ' + msg)


def bad(msg):
    global failed
    failed += 1
    print('  ✘ ' + msg)


def build_temp_db():
    """建表 + 写入演示数据（与 init_db.py 完全同一套逻辑）。"""
    conn = sqlite3.connect(config.DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    with open(init_db.SCHEMA_PATH, encoding='utf-8') as f:
        conn.executescript(f.read())
    init_db.seed(conn)
    conn.commit()
    conn.close()


def pick_free_seats(show_id, n):
    """挑 n 个空闲的普通座（演示下单用）。"""
    show = showtime_dao.get_by_id(show_id)
    seats = seat_dao.list_by_hall(show['hall_id'])
    occupied = set(order_dao.sold_seat_ids(show_id))
    picked = []
    for s in seats:
        if s['is_enabled'] and s['seat_type'] == 'NORMAL' and s['id'] not in occupied:
            picked.append(s['id'])
            if len(picked) == n:
                return picked
    return picked


def find_show(prefer_day=1):
    """找一个明天（或之后）有足够空位的场次。"""
    for offset in range(prefer_day, 5):
        d = (date.today() + timedelta(days=offset)).isoformat()
        for s in showtime_dao.list_by_date(d):
            if len(pick_free_seats(s['id'], 2)) == 2:
                return s
    return None


def main():
    print('=' * 60)
    print('第 2 步 业务层自检')
    print('临时数据库:', config.DATABASE)
    print('=' * 60)

    build_temp_db()

    with app.app_context():
        # ---------------- ① 正常下单 ----------------
        print('\n[1] 正常下单')
        show = find_show(1)
        if not show:
            bad('没有可用场次，无法演示下单')
            return
        seats = pick_free_seats(show['id'], 2)
        print('    场次: %s %s %s-%s  《%s》 基础票价 %s 元'
              % (show['show_date'], show['hall_name'], show['start_time'],
                 show['end_time'], show['title'], fen_to_yuan(show['base_price'])))
        try:
            order = order_service.create_order(show['id'], seats, '13800138000', '林晚晴')
            detail = order_service.get_order_detail(order['order_no'])
            labels = '、'.join('%d排%d座' % (x['row_no'], x['col_no']) for x in detail['seats'])
            print('    订单号 %s  座位 %s  总价 %s 元  状态 %s'
                  % (order['order_no'], labels, fen_to_yuan(order['total_price']),
                     ORDER_STATUS[order['status']]))
            if order['status'] == 'UNPAID' and order['total_price'] > 0 and len(detail['seats']) == 2:
                ok('下单成功，已锁座并生成待支付订单')
            else:
                bad('下单结果不符合预期')
        except BizError as e:
            bad('下单被意外拒绝：%s' % e)
            return

        # ---------------- ② 同座位重复下单 ----------------
        print('\n[2] 对同一场次同一批座位再下一单')
        try:
            order_service.create_order(show['id'], seats, '13900139000', '周慕云')
            bad('重复占座竟然成功了（防重复占用失效）')
        except BizError as e:
            print('    被拒绝：%s' % e)
            if '选走' in str(e):
                ok('重复占座被拒绝，并提示了具体座位')
            else:
                bad('拒绝原因不是"座位已被选走"')

        # ---------------- ③ 合法状态流转：支付 ----------------
        print('\n[3] 合法状态流转：待支付 -> 已支付')
        try:
            paid = order_service.pay(order['order_no'])
            print('    订单 %s 状态 %s，取票码 %s'
                  % (paid['order_no'], ORDER_STATUS[paid['status']], paid['ticket_code']))
            logs = order_dao.logs_of_order(paid['id'])
            print('    轨迹: ' + ' -> '.join('%s(%s)' % (l['action'], l['to_status']) for l in logs))
            if paid['status'] == 'PAID' and paid['ticket_code'] and len(paid['ticket_code']) == 8:
                ok('支付成功，生成 8 位取票码，并写入轨迹')
            else:
                bad('支付结果不符合预期')
        except BizError as e:
            bad('支付被意外拒绝：%s' % e)
            paid = None

        # ---------------- ④ 非法状态流转：取消已支付订单 ----------------
        print('\n[4] 非法状态流转：取消一笔已支付订单')
        if paid:
            try:
                order_service.cancel(paid['order_no'], '13800138000', '林晚晴')
                bad('已支付订单竟然被取消了（状态机失效）')
            except BizError as e:
                print('    被拒绝：%s' % e)
                ok('状态机拦截了非法流转')
        else:
            bad('没有已支付订单，跳过非法流转演示')

        # 再补一个合法取消：新下一单再取消
        print('    —— 对照：新下一单后取消（合法）')
        try:
            seats2 = pick_free_seats(show['id'], 2)
            tmp = order_service.create_order(show['id'], seats2, '13700137000', '陈嘉树')
            cancelled = order_service.cancel(tmp['order_no'], '13700137000', '陈嘉树')
            freed = order_dao.seat_ids_of_order(cancelled['id'])
            print('    订单 %s 状态 %s，占座记录 %d 条'
                  % (cancelled['order_no'], ORDER_STATUS[cancelled['status']], len(freed)))
            if cancelled['status'] == 'CANCELLED' and len(freed) == 0:
                ok('取消成功且座位已释放')
            else:
                bad('取消后座位没有释放')
        except BizError as e:
            bad('合法取消失败：%s' % e)

        # ---------------- ⑤ 三种日期/时段的计价 ----------------
        print('\n[5] 计价：三种不同日期 / 时段')
        demo_seats = [
            {'id': 1, 'row_no': 3, 'col_no': 5, 'seat_type': 'NORMAL'},
            {'id': 2, 'row_no': 3, 'col_no': 6, 'seat_type': 'VIP'},
            {'id': 3, 'row_no': 4, 'col_no': 1, 'seat_type': 'COUPLE'},
        ]
        # 每天各挑一场：第 1 天取最后一场（晚场）、第 2 天取中间一场、第 3 天取第一场（早场）
        picked_shows = []
        for offset, which in ((0, 'last'), (1, 'middle'), (2, 'first')):
            d = (date.today() + timedelta(days=offset)).isoformat()
            shows = showtime_dao.list_by_date(d)
            if not shows:
                continue
            if which == 'last':
                picked_shows.append(shows[-1])
            elif which == 'middle':
                picked_shows.append(shows[len(shows) // 2])
            else:
                picked_shows.append(shows[0])

        for s in picked_shows:
            items, total = pricing.price_detail(s['base_price'], demo_seats)
            print('    %s %s-%s《%s》%s 基础票价 %s 元'
                  % (s['show_date'], s['start_time'], s['end_time'], s['title'],
                     s['hall_name'], fen_to_yuan(s['base_price'])))
            for it in items:
                print('       %s %s + 加价 %s = %s 元'
                      % (it['seat_label'], it['seat_type'],
                         fen_to_yuan(it['surcharge']), fen_to_yuan(it['price'])))
            print('       合计: %s 元' % fen_to_yuan(total))
        if len(picked_shows) == 3:
            ok('三种日期/时段计价均正确（普通 +0 / VIP +20 / 情侣 +5）')
        else:
            bad('演示场次不足三种日期')

    print('\n' + '=' * 60)
    print('通过 %d 项，失败 %d 项' % (passed, failed))
    print('临时数据库目录：%s（可删除）' % TMP_DIR)
    print('=' * 60)
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
