# -*- coding: utf-8 -*-
"""
tests/selfcheck.py —— 全量自检脚本（项目规范第 6 节）

把 config.DATABASE 指向临时文件（绝不动 instance/ 正式库），用 Flask test_client()
并直接调用业务层，跑完需求说明第十三节的全部验收测试，另加两项图片检查：
    ① 首页引用的每一张图片都返回 200；
    ② static/images/ 下的 banner / cover / env 图片都是 .jpg / .png，不存在 .svg。

验收项与需求说明第十三节一一对应：
    [1] 页面可访问；下映影片详情 404；停售场次不能进入选座。
    [2] 选 5 个座位 / 只选情侣座一半 / 选不属于该影厅的座位 —— 被拒绝。
    [3] 计价：普通座、VIP 座、情侣座票价正确。
    [4] 10 个线程同时抢同一个座位，只成功 1 个订单；座位组有交集时后提交整单失败。
    [5] 超时：截止时间改到过去 -> 结算后已超时、座位释放、不能再支付。
    [6] 支付后有 8 位唯一取票码；开场前不足 2 小时不能退票（并对照：超过 2 小时可退）。
    [7] 检票时间窗外被拒；不能重复检票（并对照：窗口内可正常检票）。
    [8] 排片：同厅时间重叠创建失败；不同厅同一时间可以。
    [9] 身份不匹配查不到；不能退/取消别人的票；后台未登录重定向；缺 CSRF 返回 400；XSS 被转义。

运行： python -X utf8 tests/selfcheck.py
全部通过时最后打印"全部 N 项检查通过"，退出码 0；任何一项失败退出码 1。
"""
import os
import re
import sys
import sqlite3
import tempfile
import threading
from datetime import datetime, timedelta, date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ---- 关键：先把 DATABASE 指向临时库，再 import db / app，避免覆盖正式库 ----
import config
TMP_DIR = tempfile.mkdtemp(prefix='guangying_selfcheck_')
config.DATABASE = os.path.join(TMP_DIR, 'selfcheck.db')

import db                                                        # noqa: E402
import init_db                                                   # noqa: E402
from app import app                                              # noqa: E402
from dao import hall_dao, movie_dao, order_dao, seat_dao, showtime_dao  # noqa: E402
from services import (movie_service, order_service, pricing,     # noqa: E402
                      seat_service, showtime_service)
from utils import (ORDER_STATUS, BizError, hhmm_to_min,          # noqa: E402
                   min_to_hhmm, fen_to_yuan)

IMAGES_DIR = os.path.join(ROOT, 'static', 'images')
FMT = '%Y-%m-%d %H:%M:%S'

passed, failed = 0, 0


def ok(msg):
    global passed
    passed += 1
    print('    ✔ ' + msg)


def bad(msg):
    global failed
    failed += 1
    print('    ✘ ' + msg)


def check(cond, ok_msg, bad_msg):
    """cond 为真记通过，否则记失败（bad_msg 带上原因线索）。"""
    if cond:
        ok(ok_msg)
    else:
        bad(bad_msg)


def expect_biz(func, keyword, ok_msg, bad_msg):
    """断言 func() 抛出 BizError，且提示里包含 keyword（keyword 为 None 时不限）。"""
    try:
        func()
    except BizError as e:
        if keyword is None or keyword in str(e):
            ok(ok_msg + '（提示：%s）' % e)
        else:
            bad(bad_msg + '（提示关键词不对：%s）' % e)
        return True
    except Exception as e:                                   # 不该出现的其它异常
        bad(bad_msg + '（抛出了非业务异常：%r）' % e)
        return False
    bad(bad_msg + '（竟然没有抛出业务异常）')
    return False


# ---------------- 临时数据库与直查工具 ----------------

def build_temp_db():
    """建表 + 写入演示数据（与 init_db.py 完全同一套逻辑，跑在临时库上）。"""
    conn = sqlite3.connect(config.DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    with open(init_db.SCHEMA_PATH, encoding='utf-8') as f:
        conn.executescript(f.read())
    init_db.seed(conn)
    conn.commit()
    conn.close()


def sq(sql, args=()):
    """测试用的直接 SQL 查询/修改（只操作临时库，用于构造边界条件）。"""
    conn = sqlite3.connect(config.DATABASE)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.commit()
    conn.close()
    return rows


def order_status(order_no):
    return sq("SELECT status FROM ticket_order WHERE order_no=?", (order_no,))[0]['status']


def seat_count_of_show(show_id, seat_id):
    return sq("SELECT COUNT(*) c FROM order_seat WHERE show_id=? AND seat_id=?",
              (show_id, seat_id))[0]['c']


# ---------------- 挑场次 / 挑座位 ----------------

def pick_free_seats(show_id, n, seat_type='NORMAL'):
    """挑 n 个空闲座位（默认普通座），不够就返回已挑到的部分。"""
    show = showtime_dao.get_by_id(show_id)
    seats = seat_dao.list_by_hall(show['hall_id'])
    occupied = set(order_dao.sold_seat_ids(show_id))
    picked = []
    for s in seats:
        if (s['is_enabled'] and (seat_type is None or s['seat_type'] == seat_type)
                and s['id'] not in occupied):
            picked.append(s['id'])
            if len(picked) == n:
                return picked
    return picked


def find_show(day_offset=1, need_free=None, seat_type='NORMAL', exclude_ids=()):
    """在 day_offset 那天找一个有空位的在售场次（exclude_ids 用于避开已被测试改过时间的场次）。"""
    d = (date.today() + timedelta(days=day_offset)).isoformat()
    for s in showtime_dao.list_by_date(d):
        if s['id'] in exclude_ids or s['status'] != 'ON_SALE':
            continue
        if need_free is None or len(pick_free_seats(s['id'], need_free, seat_type)) == need_free:
            return s
    return None


def find_couple_show():
    """找一个情侣厅的在售场次（明天起往后找）。"""
    for offset in range(1, 5):
        d = (date.today() + timedelta(days=offset)).isoformat()
        for s in showtime_dao.list_by_date(d):
            seats = seat_dao.list_by_hall(s['hall_id'])
            if any(x['seat_type'] == 'COUPLE' for x in seats):
                return s
    return None


def couple_pair_of(show):
    """取该场次影厅里一对空闲情侣座 (座位id, 伴侣id)，找不到返回 (None, None)。"""
    seats = seat_dao.list_by_hall(show['hall_id'])
    occupied = set(order_dao.sold_seat_ids(show['id']))
    for s in seats:
        if (s['seat_type'] == 'COUPLE' and s['couple_partner_id'] and s['is_enabled']
                and s['id'] not in occupied and s['couple_partner_id'] not in occupied):
            return s['id'], s['couple_partner_id']
    return None, None


def set_show_start(show_id, minutes_from_now):
    """把场次的开始时间改到"现在 + minutes_from_now"（仅临时库，用于构造退票/检票边界）。

    结束时间 = 开始 + 片长 + 15 分钟清场，避免违反 CHECK(end_time > start_time)。
    """
    row = sq("SELECT m.duration d FROM showtime s JOIN movie m ON m.id=s.movie_id "
             "WHERE s.id=?", (show_id,))[0]
    dt = datetime.now() + timedelta(minutes=minutes_from_now)
    start_min = dt.hour * 60 + dt.minute
    end_min = min(start_min + row['d'] + 15, 24 * 60)
    sq("UPDATE showtime SET show_date=?, start_time=?, end_time=? WHERE id=?",
       (dt.date().isoformat(), min_to_hhmm(start_min), min_to_hhmm(end_min), show_id))


# ---------------- 各验收项 ----------------

def test_1_pages(client):
    """[验收1] 页面可访问；下映影片详情 404；停售场次不能进入选座。"""
    print('\n[1] 页面可访问 / 下映影片 404 / 停售场次拦截')

    r = client.get('/')
    check(r.status_code == 200, '首页 / 返回 200', '首页返回 %d' % r.status_code)
    r = client.get('/orders')
    check(r.status_code == 200, '我的订单 /orders 返回 200', '/orders 返回 %d' % r.status_code)
    r = client.get('/admin/login')
    check(r.status_code == 200, '后台登录页 /admin/login 返回 200',
          '/admin/login 返回 %d' % r.status_code)

    # 正在热映的影片详情可访问
    showing = movie_dao.list_by_status('SHOWING')
    movie = showing[0]
    r = client.get('/movies/%d' % movie['id'])
    check(r.status_code == 200, '热映影片《%s》详情返回 200' % movie['title'],
          '热映影片详情返回 %d' % r.status_code)

    # 下映影片详情 404：临时把一部热映影片改成"下映"，测完恢复
    movie_service.set_status(movie['id'], 'OFF')
    r = client.get('/movies/%d' % movie['id'])
    check(r.status_code == 404, '下映影片详情返回 404', '下映影片详情返回 %d' % r.status_code)
    movie_service.set_status(movie['id'], 'SHOWING')
    r = client.get('/movies/%d' % movie['id'])
    check(r.status_code == 200, '恢复热映后详情重新可访问', '恢复后详情返回 %d' % r.status_code)

    # 停售场次不能进入选座：把一场次改为停售，访问选座页应被重定向回首页
    show = find_show(day_offset=1, need_free=1)
    showtime_dao.set_status(show['id'], 'STOPPED')
    r = client.get('/shows/%d/seats' % show['id'])
    check(r.status_code == 302 and '/shows/' not in r.headers.get('Location', ''),
          '停售场次访问选座页被重定向（%s）' % r.status_code,
          '停售场次选座页返回 %d，未拦截' % r.status_code)
    showtime_dao.set_status(show['id'], 'ON_SALE')
    r = client.get('/shows/%d/seats' % show['id'])
    check(r.status_code == 200, '恢复在售后选座页可访问', '恢复后选座页返回 %d' % r.status_code)


def test_2_selection_rules():
    """[验收2] 选 5 个座位 / 只选情侣座一半 / 选不属于该影厅的座位 —— 被拒绝。"""
    print('\n[2] 选座规则校验（R2）')

    # ① 选 5 个座位被拒
    show = find_show(day_offset=1, need_free=5)
    if show is None:
        bad('没有找到带 5 个空座的场次，无法测试超量选座')
        return
    five = pick_free_seats(show['id'], 5)
    expect_biz(lambda: order_service.create_order(show['id'], five, '13811112222', '测试观众'),
               '1~4', '一次选 5 个座位被拒绝', '选 5 个座位未被拒绝')

    # ② 只选情侣座的一半被拒
    couple_show = find_couple_show()
    if couple_show:
        a, b = couple_pair_of(couple_show)
        if a:
            expect_biz(lambda: order_service.create_order(couple_show['id'], [a],
                                                          '13811112222', '测试观众'),
                       '成对', '只选情侣座一半被拒绝', '只选情侣座一半未被拒绝')
            # 对照：成对选择是合法的（这笔单后面还会用来对计价）
            ok_couple = None
            try:
                ok_couple = order_service.create_order(couple_show['id'], [a, b],
                                                       '13811113333', '情侣观众')
            except BizError as e:
                bad('情侣座成对选择竟然失败：%s' % e)
            if ok_couple:
                ok('情侣座成对选择成功（订单 %s）' % ok_couple['order_no'])
        else:
            bad('情侣厅没有空闲情侣座对，无法测试')
    else:
        bad('没有找到情侣厅场次，无法测试情侣座规则')

    # ③ 选不属于该影厅的座位被拒
    foreign = sq("SELECT id FROM seat WHERE hall_id != ? AND is_enabled=1 LIMIT 1",
                 (show['hall_id'],))[0]['id']
    one = pick_free_seats(show['id'], 1)
    expect_biz(lambda: order_service.create_order(show['id'], one + [foreign],
                                                  '13811114444', '测试观众'),
               '不属于', '选入外厅座位被拒绝', '选入外厅座位未被拒绝')


def test_3_pricing():
    """[验收3] 计价：普通座、VIP 座、情侣座票价正确（R3）。"""
    print('\n[3] 计价（R3：普通 +0 / VIP +20 元 / 情侣 +5 元每座）')

    # 普通座：票价 = 基础票价
    show = find_show(day_offset=1, need_free=2)
    seat_id = pick_free_seats(show['id'], 1)[0]
    seat = seat_dao.list_by_ids([seat_id])[0]
    items, total = pricing.price_detail(show['base_price'], [seat])
    check(items[0]['price'] == show['base_price'] and items[0]['surcharge'] == 0,
          '普通座票价 = 基础票价 %s 元，加价 0' % fen_to_yuan(show['base_price']),
          '普通座计价错误：应为 %d 分，实际 %d 分' % (show['base_price'], items[0]['price']))

    # 用真实下单复核总价：订单总价应等于逐座计价之和
    order = order_service.create_order(show['id'], [seat_id], '13811115555', '计价观众')
    check(order['total_price'] == show['base_price'],
          '普通座 1 张订单总价 = %s 元，与计价规则一致' % fen_to_yuan(order['total_price']),
          '订单总价 %d 分与计价结果不符' % order['total_price'])

    # VIP 座：基础票价 + 20 元
    vip_show = None
    for offset in range(1, 5):
        d = (date.today() + timedelta(days=offset)).isoformat()
        for s in showtime_dao.list_by_date(d):
            if len(pick_free_seats(s['id'], 1, 'VIP')) == 1:
                vip_show = s
                break
        if vip_show:
            break
    if vip_show:
        vip_id = pick_free_seats(vip_show['id'], 1, 'VIP')[0]
        vip_seat = seat_dao.list_by_ids([vip_id])[0]
        items, _ = pricing.price_detail(vip_show['base_price'], [vip_seat])
        expect = vip_show['base_price'] + 2000
        check(items[0]['price'] == expect,
              'VIP 座票价 = %s + 20 = %s 元' % (fen_to_yuan(vip_show['base_price']),
                                               fen_to_yuan(expect)),
              'VIP 座计价错误：应为 %d 分，实际 %d 分' % (expect, items[0]['price']))
    else:
        bad('没有找到带空 VIP 座的场次')

    # 情侣座：每座基础票价 + 5 元（复用验收 2 里下的情侣座订单核对总价）
    couple_show = find_couple_show()
    if couple_show:
        a, _b = couple_pair_of(couple_show)
        if a:
            expect_each = couple_show['base_price'] + 500
            items, total2 = pricing.price_detail(couple_show['base_price'],
                                                 seat_dao.list_by_ids([a, _b]))
            check(total2 == expect_each * 2 and items[0]['surcharge'] == 500,
                  '情侣座每座 = %s + 5 = %s 元，两座合计 %s 元'
                  % (fen_to_yuan(couple_show['base_price']),
                     fen_to_yuan(expect_each), fen_to_yuan(total2)),
                  '情侣座计价错误：合计应为 %d 分，实际 %d 分' % (expect_each * 2, total2))


def test_4_concurrency():
    """[验收4] 10 线程抢同一座位只成功 1 单；座位组有交集时后提交整单失败。"""
    print('\n[4] 并发抢座（10 线程同一座位 + 交集座位组）')

    show = find_show(day_offset=2, need_free=1)
    if show is None:
        bad('没有找到有空位的场次，无法做并发测试')
        return
    seat_id = pick_free_seats(show['id'], 1)[0]
    seat = seat_dao.list_by_ids([seat_id])[0]
    print('    场次 #%d《%s》抢 %d排%d座（10 个线程同时下单）'
          % (show['id'], show['title'], seat['row_no'], seat['col_no']))

    results = [None] * 10

    def worker(i):
        # 每个线程独立的 app 上下文 = 独立的数据库连接，模拟 10 个真实并发用户
        with app.app_context():
            try:
                order_service.create_order(show['id'], [seat_id],
                                           '138%08d' % i, '并发观众%d' % i)
                results[i] = ('ok', None)
            except BizError as e:
                results[i] = ('biz', str(e))
            except Exception as e:
                results[i] = ('exc', repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    succeed = sum(1 for r in results if r and r[0] == 'ok')
    rejected = sum(1 for r in results if r and r[0] == 'biz')
    crashed = sum(1 for r in results if r and r[0] == 'exc')
    print('    结果：成功 %d / 业务拒绝 %d / 异常 %d' % (succeed, rejected, crashed))
    check(succeed == 1 and crashed == 0,
          '10 线程抢同一座位：恰好成功 1 单，其余 9 单被业务拒绝，无异常',
          '并发结果不对：成功 %d / 拒绝 %d / 异常 %d' % (succeed, rejected, crashed))
    check(seat_count_of_show(show['id'], seat_id) == 1,
          '该座位在 order_seat 中只有 1 条占用记录（UNIQUE 兜底生效）',
          '该座位占用记录数 != 1')

    # 交集座位组：先到者得，后提交的整单失败（哪怕它还包含没人抢的座位）
    more = pick_free_seats(show['id'], 3)
    if len(more) == 3:
        s2, s3, s4 = more
        try:
            order_service.create_order(show['id'], [s2, s3], '13822220001', '先到观众')
            ok('观众甲选 %s 成功锁座' % ('、'.join(str(x) for x in [s2, s3])))
        except BizError as e:
            bad('观众甲正常下单失败：%s' % e)
        expect_biz(lambda: order_service.create_order(show['id'], [s3, s4],
                                                      '13822220002', '后到观众'),
                   '选走', '观众乙与甲有交集（座位 %d），整单失败' % s3,
                   '有交集的订单未被整单拒绝')
        check(seat_count_of_show(show['id'], s4) == 0,
              '整单失败：没人抢的座位 %d 也没有被写入占用' % s4,
              '失败订单的其它座位被意外占用')
    else:
        bad('空位不足 3 个，无法测试交集座位组')


def test_5_expire():
    """[验收5] 超时：截止时间改到过去 -> 结算后已超时、座位释放、不能再支付。"""
    print('\n[5] 超时结算（R5）')

    show = find_show(day_offset=2, need_free=1)
    seat_id = pick_free_seats(show['id'], 1)[0]
    order = order_service.create_order(show['id'], [seat_id], '13833330001', '超时观众')
    check(seat_count_of_show(show['id'], seat_id) == 1,
          '下单后座位被锁定', '下单后座位未被锁定')

    # 直接把支付截止时间改到过去（模拟 15 分钟前下的单一直没支付）
    sq("UPDATE ticket_order SET pay_deadline='2020-01-01 00:00:00' WHERE order_no=?",
       (order['order_no'],))
    order_service.settle_expired()                       # R5：过期结算
    check(order_status(order['order_no']) == 'EXPIRED',
          '结算后订单状态 = 已超时（EXPIRED）',
          '结算后订单状态 = %s' % order_status(order['order_no']))
    check(seat_count_of_show(show['id'], seat_id) == 0,
          '座位已释放（order_seat 已删除）', '座位未释放')
    expect_biz(lambda: order_service.pay(order['order_no']),
               '不能支付', '超时订单不能再支付', '超时订单竟然还能支付')


def test_6_ticket_and_refund():
    """[验收6] 支付生成 8 位唯一取票码；开场前不足 2 小时不能退票（对照：满 2 小时可退）。"""
    print('\n[6] 取票码唯一 + 退票时间窗（R6 / R7）')

    # ① 支付 -> 8 位取票码；并核对全库取票码唯一
    show = find_show(day_offset=1, need_free=1)
    seat_id = pick_free_seats(show['id'], 1)[0]
    order = order_service.create_order(show['id'], [seat_id], '13844440001', '取票观众')
    paid = order_service.pay(order['order_no'])
    code = paid['ticket_code']
    check(code and len(code) == 8 and code.isdigit(),
          '支付成功，生成 8 位取票码 %s' % code, '取票码不合法：%r' % code)

    rows = sq("SELECT ticket_code FROM ticket_order WHERE ticket_code IS NOT NULL")
    codes = [r['ticket_code'] for r in rows]
    check(len(codes) == len(set(codes)) and all(len(c) == 8 and c.isdigit() for c in codes),
          '全库 %d 个取票码互不重复且均为 8 位数字' % len(codes),
          '取票码存在重复或格式错误')

    # ② 对照：明天场次开场前远超 2 小时，可以正常退票并释放座位
    refunded = order_service.refund(order['order_no'], '13844440001', '取票观众')
    check(refunded['status'] == 'REFUNDED',
          '开场前超过 2 小时退票成功（已退票）', '合法退票失败')
    check(seat_count_of_show(show['id'], seat_id) == 0,
          '退票后座位已释放', '退票后座位未释放')

    # ③ 开场前不足 2 小时不能退票：换一场（避免用到上面已测过的场次），把开始时间改到 90 分钟后
    soon_show = find_show(day_offset=1, need_free=1, exclude_ids=(show['id'],))
    set_show_start(soon_show['id'], 90)
    seat_id2 = pick_free_seats(soon_show['id'], 1)[0]
    order2 = order_service.create_order(soon_show['id'], [seat_id2], '13844440002', '急退观众')
    order_service.pay(order2['order_no'])
    expect_biz(lambda: order_service.refund(order2['order_no'], '13844440002', '急退观众'),
               '不足 2 小时', '开场前 90 分钟退票被拒绝（R7）', '不足 2 小时竟然能退票')
    check(order_status(order2['order_no']) == 'PAID',
          '被拒绝的退票不改变订单状态（仍为已支付）',
          '退票被拒后订单状态变成 %s' % order_status(order2['order_no']))


def test_7_checkin():
    """[验收7] 检票时间窗外被拒；不能重复检票（对照：窗口内可正常检票）。"""
    print('\n[7] 检票时间窗与重复检票（R8）')

    # ① 时间窗外：明天场次的已支付订单（距开场远超 60 分钟）不能检票
    show = find_show(day_offset=1, need_free=1)
    seat_id = pick_free_seats(show['id'], 1)[0]
    order = order_service.create_order(show['id'], [seat_id], '13855550001', '检票观众')
    order_service.pay(order['order_no'])
    expect_biz(lambda: order_service.checkin_order(order['id'], operator='admin'),
               '检票时间', '开场前 60 分钟之外检票被拒绝', '时间窗外竟然能检票')

    # ② 窗口内：换一场（避开上面测过的场次），把开始时间改到 35 分钟后开场
    #   （在前 60 分钟 ~ 后 30 分钟窗口内）
    soon_show = find_show(day_offset=1, need_free=1, exclude_ids=(show['id'],))
    set_show_start(soon_show['id'], 35)
    seat_id2 = pick_free_seats(soon_show['id'], 1)[0]
    order2 = order_service.create_order(soon_show['id'], [seat_id2], '13855550002', '入场观众')
    paid2 = order_service.pay(order2['order_no'])
    used = order_service.checkin_order(paid2['id'], operator='admin')
    check(used['status'] == 'USED',
          '窗口内检票成功（已检票）', '窗口内检票失败')
    # ③ 不能重复检票
    expect_biz(lambda: order_service.checkin_order(paid2['id'], operator='admin'),
               '已检票', '重复检票被拒绝', '同一订单竟然能重复检票')


def test_8_schedule():
    """[验收8] 排片：同厅时间重叠创建失败；不同厅同一时间可以。"""
    print('\n[8] 排片不重叠（R9）')

    show = find_show(day_offset=1, need_free=1)
    movie_id = show['movie_id']

    # ① 同一影厅同一时间段（与现有场次重叠）-> 失败
    expect_biz(lambda: showtime_service.create_showtime(
        movie_id, show['hall_id'], show['show_date'], show['start_time'], 5000),
        '重叠', '同厅重叠场次创建被拒绝', '同厅重叠场次竟然创建成功')

    # ② 不同影厅同一时间 -> 成功。演示数据里每个厅每天都排 4~6 场、几乎占满
    #    09:30~23:55，其它厅在当天往往没有能放下整场电影的空档，所以先在
    #    【临时库】里把另一个厅当天的场次全部置为"已取消"（腾空该厅，不影响
    #    被测业务代码），再在完全相同的时间创建场次 —— 证明重叠校验只看本厅。
    other_hall = None
    for hall in hall_dao.list_enabled():
        if hall['id'] != show['hall_id']:
            other_hall = hall
            break
    sq("UPDATE showtime SET status='CANCELLED' WHERE hall_id=? AND show_date=?",
       (other_hall['id'], show['show_date']))
    try:
        created = showtime_service.create_showtime(
            movie_id, other_hall['id'], show['show_date'], show['start_time'], 5000)
        ok('不同影厅（%s）在同一时间（%s %s）创建场次成功 —— 重叠校验只针对同一影厅'
           % (created['hall_name'], created['show_date'], created['start_time']))
        showtime_service.change_status(created['id'], 'cancel')   # 清理测试场次
        ok('测试用新场次已取消，不影响后续检查')
    except BizError as e:
        bad('不同影厅同一时间创建场次失败（不应发生）：%s' % e)


def test_9_identity_csrf_xss(client):
    """[验收9] 身份不匹配查不到；不能退/取消别人的票；后台未登录重定向；
    缺 CSRF 返回 400；XSS 被转义。"""
    print('\n[9] 身份与安全（越权 / CSRF / XSS / 后台登录）')

    # 先拿一份带 CSRF 令牌的会话（首页模板里有 <meta name="csrf-token">）
    r = client.get('/')
    html = r.get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html)
    if not m:
        bad('首页没有找到 csrf-token，无法继续安全测试')
        return
    token = m.group(1)

    # ① 缺 CSRF 的 POST -> 400
    show = find_show(day_offset=1, need_free=1)
    seat_id = pick_free_seats(show['id'], 1)[0]
    r = client.post('/orders/new', data={
        'show_id': show['id'], 'seat_ids': str(seat_id),
        'phone': '13911112222', 'name': '<img src=x>'})
    check(r.status_code == 400, '缺 CSRF 令牌的 POST 被拒（400）',
          '缺 CSRF 的 POST 返回 %d' % r.status_code)

    # ② 带 CSRF 正常下单：姓名里塞 XSS，随后检查支付页转义
    r = client.post('/orders/new', data={
        'show_id': show['id'], 'seat_ids': str(seat_id), '_csrf': token,
        'phone': '13911112222', 'name': '<img src=x>'})
    check(r.status_code == 302, '带 CSRF 的下单成功（302 跳支付页）',
          '带 CSRF 的下单返回 %d' % r.status_code)
    order_no = r.headers.get('Location', '').rstrip('/').split('/')[-2] \
        if '/pay' in r.headers.get('Location', '') else None
    if order_no:
        r = client.get('/orders/%s/pay' % order_no)
        page = r.get_data(as_text=True)
        check('&lt;img src=x&gt;' in page and '<img src=x>' not in page,
              'XSS 内容被 Jinja2 自动转义（显示为文本而非图片标签）',
              'XSS 未被转义，页面包含原始 <img> 标签')
    else:
        bad('下单跳转地址里没解析到订单号，XSS 转义检查未执行')

    # ③ 身份不匹配查不到（手机号 + 姓名必须同时匹配）
    result = order_service.list_my_orders('13911112222', '沈知意')
    check(result == [], '手机号对、姓名不对 -> 查不到任何订单',
          '姓名不匹配竟然查到了 %d 笔订单' % len(result))
    if order_no:
        detail = order_service.get_order_detail(order_no, '13911112222', '另一个人')
        check(detail is None, '订单详情身份不匹配 -> 返回 None（不泄露存在性）',
              '身份不匹配竟然能拿到订单详情')

    # ④ 不能取消/退别人的票：观众丙的订单，用浏览器里（乙的身份）去取消 -> 原样保留
    show_b = find_show(day_offset=1, need_free=1)
    seat_b = pick_free_seats(show_b['id'], 1)[0]
    order_c = order_service.create_order(show_b['id'], [seat_b], '13722223333', '何雨时')
    r = client.post('/orders/%s/cancel' % order_c['order_no'],
                    data={'_csrf': token})              # session 里的身份是 13911112222
    check(r.status_code == 302 and order_status(order_c['order_no']) == 'UNPAID',
          '取消别人的订单被拒绝，订单状态未变',
          '越权取消竟生效，订单状态 = %s' % order_status(order_c['order_no']))

    # ⑤ 不能退别人的票（service 层身份校验）：观众丁的已支付订单
    show_d = find_show(day_offset=2, need_free=1)
    seat_d = pick_free_seats(show_d['id'], 1)[0]
    order_d = order_service.create_order(show_d['id'], [seat_d], '13633334444', '赵听澜')
    order_service.pay(order_d['order_no'])
    expect_biz(lambda: order_service.refund(order_d['order_no'],
                                            '13911112222', '冒名顶替'),
               '身份不匹配', '用别人的身份退票被拒绝', '越权退票未被拒绝')
    check(order_status(order_d['order_no']) == 'PAID',
          '越权退票未改变订单状态', '越权退票后订单状态 = %s' % order_status(order_d['order_no']))

    # ⑥ 后台未登录：页面 302 到登录页；接口请求（fetch POST，带 CSRF 头但未登录）返回 401 JSON
    r = client.get('/admin')
    check(r.status_code == 302 and '/admin/login' in r.headers.get('Location', ''),
          '未登录访问 /admin 被重定向到登录页', '未登录访问 /admin 返回 %d' % r.status_code)
    r = client.post('/admin/api/checkin', data={'code': '12345678'},
                    headers={'X-CSRF-Token': token})
    check(r.status_code == 401, '未登录调用后台接口返回 401（登录已过期）',
          '未登录接口返回 %d' % r.status_code)

    # ⑦ 管理员登录成功 -> 仪表板可访问
    r = client.get('/admin/login')
    token2_m = re.search(r'name="csrf-token" content="([0-9a-f]+)"',
                         r.get_data(as_text=True))
    r = client.post('/admin/login', data={
        'username': 'admin', 'password': 'guangying2026',
        '_csrf': token2_m.group(1) if token2_m else token})
    check(r.status_code == 302, '管理员账号密码正确，登录成功（302）',
          '管理员登录返回 %d' % r.status_code)
    r = client.get('/admin')
    check(r.status_code == 200, '登录后仪表板返回 200', '登录后 /admin 返回 %d' % r.status_code)
    r = client.get('/admin/schedule?date=%s'
                   % (date.today() + timedelta(days=1)).isoformat())
    check(r.status_code == 200, '登录后排片表返回 200', '排片表返回 %d' % r.status_code)


def test_images(client, home_html):
    """图片检查：① 首页引用的每张图片都返回 200；② banner/cover/env 都是 JPG/PNG、无 SVG。"""
    print('\n[10] 图片检查 ①：首页引用的每一张图片都返回 200')
    refs = sorted(set(re.findall(r'/static/images/[^"\'\)]+', home_html)))
    check(len(refs) > 0, '首页共引用 %d 张图片' % len(refs), '首页没有引用任何图片')
    broken = []
    for ref in refs:
        r = client.get(ref)
        if r.status_code != 200:
            broken.append('%s -> %d' % (ref, r.status_code))
    if broken:
        bad('有图片无法访问：' + '；'.join(broken))
    else:
        ok('首页引用的全部 %d 张图片均返回 200' % len(refs))

    print('\n[11] 图片检查 ②：banner / cover / env 均为 JPG / PNG，目录里没有 SVG')
    files = os.listdir(IMAGES_DIR)
    svg = [f for f in files if f.lower().endswith('.svg')]
    check(not svg, 'static/images/ 下不存在任何 .svg 文件',
          '发现 SVG 文件：%s' % '、'.join(svg))
    missing, wrong_ext = [], []
    required = (['banner-%d' % i for i in range(1, 4)]
                + ['cover-%d' % i for i in range(1, 9)]
                + ['env-%d' % i for i in range(1, 7)])
    for name in required:
        hit = None
        for f in files:
            stem, ext = os.path.splitext(f)
            if stem == name and ext.lower() in ('.jpg', '.jpeg', '.png'):
                hit = f
                break
        if hit is None:
            if any(f.startswith(name + '.') for f in files):
                wrong_ext.append(name)
            else:
                missing.append(name)
    if missing:
        bad('缺少图片：%s' % '、'.join(missing))
    elif wrong_ext:
        bad('以下图片不是 JPG/PNG：%s' % '、'.join(wrong_ext))
    else:
        ok('banner × 3 / cover × 8 / env × 6 全部存在且均为 JPG / PNG')


# ---------------- 主流程 ----------------

def main():
    print('=' * 64)
    print('光影影城 全量自检（需求说明第十三节 + 图片检查）')
    print('临时数据库: %s' % config.DATABASE)
    print('=' * 64)

    build_temp_db()
    print('临时库已生成（演示数据与 init_db.py 同一套逻辑）\n')

    client = app.test_client()

    # 验收 1：页面可访问（顺便把首页 HTML 留给图片检查用）
    with app.app_context():
        test_1_pages(client)
    home_html = client.get('/').get_data(as_text=True)

    # 验收 2~8：业务层（统一在 app 上下文里调用 service）
    with app.app_context():
        test_2_selection_rules()
        test_3_pricing()
        test_4_concurrency()
        test_5_expire()
        test_6_ticket_and_refund()
        test_7_checkin()
        test_8_schedule()

    # 验收 9：身份与安全（test_client + service）
    with app.app_context():
        test_9_identity_csrf_xss(client)

    # 图片两项检查
    test_images(client, home_html)

    print('\n' + '=' * 64)
    if failed == 0:
        print('全部 %d 项检查通过' % passed)
    else:
        print('通过 %d 项，失败 %d 项（详见上方 ✘ 条目）' % (passed, failed))
    print('临时数据库目录：%s（可整目录删除）' % TMP_DIR)
    print('=' * 64)
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
