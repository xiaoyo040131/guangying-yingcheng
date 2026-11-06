# -*- coding: utf-8 -*-
"""
init_db.py —— 初始化数据库（可重复执行，每次全部重建）

流程：执行 schema.sql 建表 -> 写入演示数据（影片 / 影厅 / 座位 / 场次 / 订单 / 管理员）
-> 打印统计信息和管理员账号密码。

演示数据设计（对照需求说明第十二节）：
- 影片 8 部（poster-1~8），6 部热映、2 部即将上映；
- 影厅 4 个：1号厅 10x14（第7列后过道）、2号IMAX厅 12x18、3号情侣厅 6x10（每行5对）、4号VIP厅 5x8；
- 场次：今天起 5 天，每厅每天 4~6 场，互不重叠；
- 订单约 120 笔，覆盖全部状态；今晚某场上座率 > 80%；
  1 笔"待支付"还剩约 10 分钟、1 笔已超时；
- 演示观众：13300004444 沈知意，名下 3 笔（已检票 / 已退票 / 明天的已支付）；
- 管理员：admin / guangying2026。
"""
import os
import random
import sqlite3
from datetime import datetime, timedelta, date

from werkzeug.security import generate_password_hash

from config import DATABASE, INSTANCE_DIR, ADMIN_USERNAME, PAY_LOCK_MINUTES
# 计价统一走 services.pricing（R3），保证演示数据与线上下单价格一致
from services.pricing import price_detail, seat_price

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(BASE_DIR, 'schema.sql')
FMT = '%Y-%m-%d %H:%M:%S'

STATS = {'orders': 0, 'by_status': {}}


def s(dt):
    """datetime -> 'YYYY-MM-DD HH:MM:SS' 文本。"""
    return dt.strftime(FMT)


def ex(conn, sql, args=()):
    """执行一条写 SQL，返回 cursor。"""
    return conn.execute(sql, args)


# ---------------- 演示数据定义 ----------------

MOVIES = [
    # (片名, 英文名, 海报, 类型, 片长, 导演, 主演, 评分x10, 简介关键词, 距今上映天数, 状态)
    ('长安夜宴', 'Night Banquet of Chang\'an', 'poster-1', '剧情/历史', 148,
     '陈墨白', '沈星河 / 柳云裳 / 顾长风', 91,
     '盛唐长安，一夜宫宴，灯影幢幢之下，一场改写王朝命运的密谋正悄然展开。', -10, 'SHOWING'),
    ('雾港疑云', 'Mist Harbor Mystery', 'poster-2', '悬疑/犯罪', 127,
     '白砚秋', '陆沉 / 苏眠 / 霍青山', 87,
     '民国雾港，一声汽笛划破夜色。侦探循着煤气灯的光走进迷雾，也走进了一个无法回头的真相。', -7, 'SHOWING'),
    ('第七封信', 'The Seventh Letter', 'poster-3', '悬疑/剧情', 112,
     '温子谦', '江离 / 孟晚舟', 84,
     '旧书桌上，火漆封缄的第七封信迟迟未被拆开。烛光摇曳，写信人早已离去，读信人却无路可退。', -3, 'SHOWING'),
    ('深海回声', 'Echo of the Deep', 'poster-4', '科幻/冒险', 121,
     '蓝一鸣', '程远 / 夏澄 / 方既明', 82,
     '潜入万米深渊，潜水员听见了不属于人类的回声。发光的生物群像星空一样，在他头顶缓缓旋转。', -5, 'SHOWING'),
    ('星际归途', 'Homebound Odyssey', 'poster-5', '科幻/太空', 138,
     '韩九章', '齐司夜 / 白澜 / 黎初', 89,
     '远航二十年，飞船终于调头返航。星云在舷窗外燃烧，而地球是否还是他们记忆中的家园？', -14, 'SHOWING'),
    ('夏日微光', 'Summer Faint Light', 'poster-6', '青春/治愈', 96,
     '林一苇', '许南嘉 / 唐小棠', 78,
     '夏日的海边，少年骑单车穿过咸湿的风。那个暑假很短，短到只够喜欢上一个人。', 7, 'COMING'),
    ('山河故人', 'Mountains and Old Friends', 'poster-7', '剧情/史诗', 143,
     '顾北望', '秦岳 / 叶知秋 / 晏清', 76,
     '山河依旧，故人何在。一个旅人的背影，走过了三十年家国变迁。', 15, 'COMING'),
    ('疾速追击', 'Velocity Chase', 'poster-8', '动作/犯罪', 105,
     '郑北辰', '高峻野 / 米雅 / 老鬼', 79,
     '城市夜色中的飞车追逐，一秒都不能松油门。他要在天亮前，把真相送出这座城市。', -2, 'SHOWING'),
]

HALLS = [
    # (名称, 类型, 行, 列, 过道列(其后), 基础票价下限/上限)
    ('1号厅', 'NORMAL', 10, 14, 7, (4200, 4800)),
    ('2号IMAX厅', 'IMAX', 12, 18, 9, (6500, 7200)),
    ('3号情侣厅', 'NORMAL', 6, 10, None, (5600, 6200)),
    ('4号VIP厅', 'VIP', 5, 8, None, (8600, 9400)),
]

# 演示观众（固定手机号 + 姓名）
DEMO_PHONE, DEMO_NAME = '13300004444', '沈知意'


def gen_names(n):
    """生成 n 个不重复的中文姓名（避免"张三李四"式演示数据）。"""
    family = ['林', '周', '陈', '徐', '韩', '许', '苏', '程', '秦', '叶',
              '孟', '江', '方', '杜', '傅', '顾', '白', '陆', '沈', '宋']
    given = ['晚晴', '慕云', '嘉树', '若鸿', '东临', '南嘉', '之遥', '亦寒', '云舟', '见山',
             '星野', '清越', '知非', '望舒', '照野', '怀瑾', '疏影', '闻笛', '既白', '青临',
             '风眠', '雨时', '未央', '长晏', '折柳', '向晚', '闻笙', '觉夏', '平川', '逾明']
    used, names = set(), []
    while len(names) < n:
        nm = random.choice(family) + random.choice(given)
        if nm in used:
            continue
        used.add(nm)
        names.append(nm)
    return names


def gen_customers(n=40):
    """生成 n 组演示观众（手机号, 姓名），手机号不重复。"""
    names = gen_names(n)
    phones, used = [], set()
    while len(phones) < n:
        p = '1' + random.choice('3456789') + ''.join(random.choice('0123456789') for _ in range(9))
        if p not in used:
            used.add(p)
            phones.append(p)
    return list(zip(phones, names))


# ---------------- 选座辅助 ----------------

def build_units(hall_seats):
    """把座位整理成"选择单元"：普通/VIP 座一个单元一个座，情侣座两个座一个单元。
    这样选座时天然保证情侣座成对出现。"""
    units, seen = [], set()
    for st in hall_seats:
        if st['id'] in seen:
            continue
        if st['seat_type'] == 'COUPLE' and st['partner_id']:
            units.append((st['id'], st['partner_id']))
            seen.add(st['partner_id'])
        else:
            units.append((st['id'],))
    return units


def pick_seats(units, occupied, n, tries=400):
    """随机挑 n 个可用座位（情侣座成对），全部空闲才返回；失败返回 None。"""
    for _ in range(tries):
        ids, chosen = [], set()
        while len(ids) < n:
            u = random.choice(units)
            if u in chosen or len(ids) + len(u) > n:
                continue
            chosen.add(u)
            ids.extend(u)
        if len(ids) != n:
            continue
        if any(i in occupied for i in ids):
            continue
        return ids
    return None


# ---------------- 订单生成 ----------------

def make_order(conn, show, seats, status, phone, name, created, deadline,
               paid=None, checkin=None, refund=None, now=None):
    """写一笔订单（订单 + order_seat + 轨迹）。seats 为座位 id 列表；选座失败返回 None。"""
    if not seats:
        return None
    seat_map = SEAT_BY_ID
    base = show['base_price']
    # 调用正式计价函数：每座 = 基础票价 + 座位类型加价，总价逐座相加
    items, total = price_detail(base, [seat_map[i] for i in seats])

    # 订单号：GY + 创建时间 + 4 位随机，保证唯一
    while True:
        order_no = 'GY' + created.strftime('%Y%m%d%H%M%S') + str(random.randint(1000, 9999))
        if order_no not in ORDER_NOS:
            ORDER_NOS.add(order_no)
            break

    # 已支付 / 已检票 / 已退票的订单必须有 8 位取票码（R6：支付成功后生成）
    code = gen_ticket_code(conn) if status in ('PAID', 'USED', 'REFUNDED') else None

    cur = ex(conn, """INSERT INTO ticket_order (order_no, show_id, phone, customer_name,
                   seat_count, total_price, ticket_code, status, pay_deadline, paid_at, checkin_at,
                   refund_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (order_no, show['id'], phone, name, len(seats), total, code, status,
               s(deadline), s(paid) if paid else None,
               s(checkin) if checkin else None, s(refund) if refund else None,
               s(created), s(now or datetime.now())))
    order_id = cur.lastrowid

    price = {it['seat_id']: it['price'] for it in items}
    for i in seats:
        ex(conn, "INSERT INTO order_seat (order_id, show_id, seat_id, price) VALUES (?,?,?,?)",
           (order_id, show['id'], i, price[i]))
        OCCUPIED[show['id']].add(i)

    # 轨迹：先"下单"，再按最终状态补动作日志
    op = phone[-4:]
    add_log(conn, order_id, 'create', '-', 'UNPAID', op, '下单锁座', created)
    if status in ('PAID', 'USED', 'REFUNDED'):
        add_log(conn, order_id, 'pay', 'UNPAID', 'PAID', op, '模拟支付成功', paid)
    if status == 'USED':
        add_log(conn, order_id, 'check', 'PAID', 'USED', 'admin', '检票入场', checkin)
    elif status == 'REFUNDED':
        # R7：退票释放座位 —— 删除 order_seat
        ex(conn, "DELETE FROM order_seat WHERE order_id = ?", (order_id,))
        for i in seats:
            OCCUPIED[show['id']].discard(i)
        add_log(conn, order_id, 'refund', 'PAID', 'REFUNDED', op, '开场前退票', refund)
    elif status == 'CANCELLED':
        ex(conn, "DELETE FROM order_seat WHERE order_id = ?", (order_id,))
        for i in seats:
            OCCUPIED[show['id']].discard(i)
        add_log(conn, order_id, 'cancel', 'UNPAID', 'CANCELLED', op, '观众主动取消', deadline)
    STATS['orders'] += 1
    STATS['by_status'][status] = STATS['by_status'].get(status, 0) + 1
    return order_id


def make_unseated_order(conn, show, status, phone, name, created, deadline, now):
    """写一笔不占座的订单（已超时：座位早已释放）。"""
    while True:
        order_no = 'GY' + created.strftime('%Y%m%d%H%M%S') + str(random.randint(1000, 9999))
        if order_no not in ORDER_NOS:
            ORDER_NOS.add(order_no)
            break
    ex(conn, """INSERT INTO ticket_order (order_no, show_id, phone, customer_name,
                seat_count, total_price, status, pay_deadline, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
       (order_no, show['id'], phone, name, 2, seat_price(show['base_price'], 'NORMAL') * 2, status,
        s(deadline), s(created), s(now)))
    order_id = ex(conn, "SELECT last_insert_rowid() AS id").fetchone()['id']
    add_log(conn, order_id, 'create', '-', 'UNPAID', phone[-4:], '下单锁座', created)
    if status == 'EXPIRED':
        add_log(conn, order_id, 'expire', 'UNPAID', 'EXPIRED', 'system', '超时未支付，系统释放座位', deadline)
    STATS['orders'] += 1
    STATS['by_status'][status] = STATS['by_status'].get(status, 0) + 1
    return order_id


def add_log(conn, order_id, action, frm, to, operator, remark, when):
    ex(conn, """INSERT INTO order_log (order_id, action, from_status, to_status,
                operator, remark, created_at) VALUES (?,?,?,?,?,?,?)""",
       (order_id, action, frm, to, operator, remark, s(when)))


def gen_ticket_code(conn):
    """生成不重复的 8 位取票码（8 位数字，首位非 0）。"""
    while True:
        code = str(random.randint(10000000, 99999999))
        row = conn.execute("SELECT id FROM ticket_order WHERE ticket_code = ?", (code,)).fetchone()
        if row is None:
            return code


# ---------------- 种子数据 ----------------

def seed(conn):
    global SEAT_BY_ID, OCCUPIED, ORDER_NOS, UNITS_BY_HALL, HALL_SEAT_COUNT

    random.seed(20261102)
    now = datetime.now()
    today = date.today()
    ORDER_NOS = set()

    # ---- 管理员 ----
    ex(conn, "INSERT INTO admin_user (username, password_hash, created_at) VALUES (?,?,?)",
       (ADMIN_USERNAME, generate_password_hash('guangying2026'), s(now)))

    # ---- 影片 ----
    movie_ids = {}
    for (title, ten, poster, genre, dur, director, actors, rating,
         synopsis, offset, status) in MOVIES:
        release = (today + timedelta(days=offset)).isoformat()
        cur = ex(conn, """INSERT INTO movie (title, title_en, poster, genre, duration,
                     director, actors, rating, synopsis, release_date, status)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 (title, ten, poster, genre, dur, director, actors, rating,
                  synopsis, release, status))
        movie_ids[title] = cur.lastrowid
    showing = [(m[0], m[4]) for m in MOVIES if m[10] == 'SHOWING']  # (片名, 片长)

    # ---- 影厅与座位 ----
    hall_ids, SEAT_BY_ID, UNITS_BY_HALL, HALL_SEAT_COUNT = {}, {}, {}, {}
    for (name, htype, rows, cols, aisle, (pmin, pmax)) in HALLS:
        cur = ex(conn, """INSERT INTO hall (name, type, row_count, col_count,
                     aisle_after_col, is_enabled) VALUES (?,?,?,?,?,1)""",
                 (name, htype, rows, cols, aisle))
        hall_id = cur.lastrowid
        hall_ids[name] = hall_id

        # 情侣厅：每行 5 对情侣座（10 列，两两配对）
        if name == '3号情侣厅':
            for r in range(1, rows + 1):
                for c in range(1, cols + 1):
                    cur = ex(conn, """INSERT INTO seat (hall_id, row_no, col_no,
                             seat_type, couple_partner_id, is_enabled) VALUES (?,?,?,'COUPLE',NULL,1)""",
                             (hall_id, r, c))
                    SEAT_BY_ID[cur.lastrowid] = {'id': cur.lastrowid, 'hall_id': hall_id,
                                                 'row_no': r, 'col_no': c,
                                                 'seat_type': 'COUPLE', 'partner_id': None}
            # 回填配对：奇数列与右侧偶数列互为伴侣
            for r in range(1, rows + 1):
                for c in range(1, cols + 1, 2):
                    a = conn.execute("SELECT id FROM seat WHERE hall_id=? AND row_no=? AND col_no=?",
                                     (hall_id, r, c)).fetchone()['id']
                    b = conn.execute("SELECT id FROM seat WHERE hall_id=? AND row_no=? AND col_no=?",
                                     (hall_id, r, c + 1)).fetchone()['id']
                    ex(conn, "UPDATE seat SET couple_partner_id=? WHERE id=?", (b, a))
                    ex(conn, "UPDATE seat SET couple_partner_id=? WHERE id=?", (a, b))
                    SEAT_BY_ID[a]['partner_id'] = b
                    SEAT_BY_ID[b]['partner_id'] = a
        else:
            stype = 'VIP' if htype == 'VIP' else 'NORMAL'
            for r in range(1, rows + 1):
                for c in range(1, cols + 1):
                    cur = ex(conn, """INSERT INTO seat (hall_id, row_no, col_no,
                             seat_type, couple_partner_id, is_enabled) VALUES (?,?,?,?,NULL,1)""",
                             (hall_id, r, c, stype))
                    SEAT_BY_ID[cur.lastrowid] = {'id': cur.lastrowid, 'hall_id': hall_id,
                                                 'row_no': r, 'col_no': c,
                                                 'seat_type': stype, 'partner_id': None}

    # 情侣座座位记录补上 hall_id 字段后，按厅重建选择单元
    for row in SEAT_BY_ID.values():
        row.setdefault('hall_id', None)
    for hid in hall_ids.values():
        rows_of_hall = conn.execute(
            "SELECT id, hall_id FROM seat WHERE hall_id=?", (hid,)).fetchall()
        for r in rows_of_hall:
            SEAT_BY_ID[r['id']]['hall_id'] = hid
        UNITS_BY_HALL[hid] = build_units(
            [v for v in SEAT_BY_ID.values() if v['hall_id'] == hid])
        HALL_SEAT_COUNT[hid] = sum(1 for v in SEAT_BY_ID.values() if v['hall_id'] == hid)

    # ---- 场次：今天起 5 天，每厅每天 4~6 场，时间不重叠 ----
    WINDOW_START = 9 * 60 + 30          # 09:30 起排
    WINDOW_END = 23 * 60 + 55           # 23:55 前结束（含清场）
    shows = []
    for (name, htype, rows, cols, aisle, (pmin, pmax)) in HALLS:
        hid = hall_ids[name]
        for d in range(5):  # 今天 + 未来 4 天
            day = today + timedelta(days=d)
            n = random.randint(4, 6)
            # 选影片与片长，保证能排进当天窗口（不够就换成短片）
            picks = random.choices(showing, k=n)
            while sum(dur + 15 for _, dur in picks) > (WINDOW_END - WINDOW_START - n):
                picks[random.randrange(n)] = random.choice(showing)
            total = sum(dur + 15 for _, dur in picks)
            gap = (WINDOW_END - WINDOW_START - total) // n
            t = WINDOW_START
            for (title, dur) in picks:
                end = t + dur + 15  # 结束时间已含 15 分钟清场
                # 周末票价上浮 5 元，随机微调 1 元档
                price = pmax - (pmin - pmax) // 2 + random.choice([0, 100, -100])
                if day.weekday() >= 5:
                    price += 500
                cur = ex(conn, """INSERT INTO showtime (movie_id, hall_id, show_date,
                             start_time, end_time, base_price, status) VALUES (?,?,?,?,?,?,'ON_SALE')""",
                         (movie_ids[title], hid, day.isoformat(),
                          '%02d:%02d' % (t // 60, t % 60),
                          '%02d:%02d' % (end // 60, end % 60), price))
                shows.append({'id': cur.lastrowid, 'movie': title, 'hall_id': hid,
                              'date': day, 'start_min': t, 'end_min': end, 'dur': dur,
                              'base_price': price})
                t = end + gap

    OCCUPIED = {sh['id']: set() for sh in shows}

    def start_dt(sh):
        return datetime(sh['date'].year, sh['date'].month, sh['date'].day) \
            + timedelta(minutes=sh['start_min'])

    # ---- 找到"今晚要排满 80%+"的场次：今天 19:00 后的场次，优先 1 号厅 ----
    today_shows = [sh for sh in shows if sh['date'] == today]
    future_today = [sh for sh in today_shows if start_dt(sh) > now]
    tonight = None
    h1 = [sh for sh in future_today
          if sh['start_min'] >= 19 * 60 and sh['hall_id'] == hall_ids['1号厅']]
    if h1:
        tonight = h1[-1]
    else:
        late = [sh for sh in future_today if sh['start_min'] >= 19 * 60]
        tonight = late[-1] if late else (future_today[-1] if future_today else None)

    customers = gen_customers(40)
    started_shows = [sh for sh in shows if start_dt(sh) <= now]

    for sh in shows:
        st_dt = start_dt(sh)
        hall_all_couple = all(len(u) == 2 for u in UNITS_BY_HALL[sh['hall_id']])

        def rand_n():
            # 情侣厅只能选偶数个座位（2 或 4）
            return random.choice([2, 4]) if hall_all_couple else random.randint(1, 4)

        if sh is tonight:
            # 今晚演示场次：上座率 > 80%
            target = int(HALL_SEAT_COUNT[sh['hall_id']] * 0.85)
            guard = 800
            while len(OCCUPIED[sh['id']]) < target and guard > 0:
                guard -= 1
                created = now - timedelta(minutes=random.randint(60, 4320))
                paid = created + timedelta(minutes=random.randint(1, 9))
                make_order(conn, sh, pick_seats(UNITS_BY_HALL[sh['hall_id']],
                                                OCCUPIED[sh['id']], rand_n()),
                           'PAID', *random.choice(customers), created,
                           created + timedelta(minutes=PAY_LOCK_MINUTES),
                           paid=paid, now=now)
            continue

        if st_dt <= now:
            # 今天已开场的场次：保证有已检票订单（多数 USED，少量 PAID）
            for _ in range(2):
                status = 'USED' if random.random() < 0.75 else 'PAID'
                created = st_dt - timedelta(minutes=random.randint(90, 3000))
                paid = created + timedelta(minutes=random.randint(1, 9))
                kw = {}
                if status == 'USED':
                    # 检票时间窗：开场前 60 分钟 ~ 开场后 30 分钟
                    ci = st_dt - timedelta(minutes=random.randint(5, 55))
                    if ci < paid:
                        ci = paid + timedelta(minutes=1)
                    kw['checkin'] = ci
                make_order(conn, sh, pick_seats(UNITS_BY_HALL[sh['hall_id']],
                                                OCCUPIED[sh['id']], rand_n()),
                           status, *random.choice(customers), created,
                           created + timedelta(minutes=PAY_LOCK_MINUTES),
                           paid=paid, now=now, **kw)
        else:
            # 未来场次：随机一些已支付订单
            if random.random() < 0.3:
                for _ in range(random.randint(1, 2)):
                    created = now - timedelta(minutes=random.randint(30, 2000))
                    make_order(conn, sh, pick_seats(UNITS_BY_HALL[sh['hall_id']],
                                                    OCCUPIED[sh['id']], rand_n()),
                               'PAID', *random.choice(customers), created,
                               created + timedelta(minutes=PAY_LOCK_MINUTES),
                               paid=created + timedelta(minutes=random.randint(1, 9)), now=now)
            # 已取消订单：不占座（写入后即删除 order_seat）
            if random.random() < 0.15:
                created = now - timedelta(minutes=random.randint(30, 1000))
                make_order(conn, sh, pick_seats(UNITS_BY_HALL[sh['hall_id']],
                                                OCCUPIED[sh['id']], rand_n()),
                           'CANCELLED', *random.choice(customers), created,
                           created + timedelta(minutes=PAY_LOCK_MINUTES), now=now)
            # 已退票订单：只对开场 2 小时以后的场次生成（保持与 R7 一致）
            if random.random() < 0.10 and st_dt - timedelta(hours=2) >= now:
                created = now - timedelta(minutes=random.randint(200, 1500))
                refund = now - timedelta(minutes=random.randint(5, 100))
                if refund <= created:
                    refund = created + timedelta(minutes=5)
                make_order(conn, sh, pick_seats(UNITS_BY_HALL[sh['hall_id']],
                                                OCCUPIED[sh['id']], rand_n()),
                           'REFUNDED', *random.choice(customers), created,
                           created + timedelta(minutes=PAY_LOCK_MINUTES),
                           paid=created + timedelta(minutes=random.randint(1, 5)),
                           refund=refund, now=now)

    # ---- 补足：每种状态至少 2 条 ----
    def count_status(st):
        return conn.execute("SELECT COUNT(*) c FROM ticket_order WHERE status=?", (st,)).fetchone()['c']

    future_pool = [sh for sh in shows if start_dt(sh) > now + timedelta(hours=3)]
    for st, need in (('CANCELLED', 2), ('REFUNDED', 2), ('PAID', 2), ('USED', 2), ('EXPIRED', 2)):
        while count_status(st) < need and future_pool:
            sh = random.choice(future_pool)
            created = now - timedelta(minutes=random.randint(60, 1000))
            if st == 'EXPIRED':
                make_unseated_order(conn, sh, 'EXPIRED', *random.choice(customers),
                                    created, now - timedelta(minutes=random.randint(5, 30)), now)
            else:
                n = random.choice([2, 4]) if all(len(u) == 2 for u in UNITS_BY_HALL[sh['hall_id']]) \
                    else random.randint(1, 3)
                make_order(conn, sh, pick_seats(UNITS_BY_HALL[sh['hall_id']],
                                                OCCUPIED[sh['id']], n),
                           st, *random.choice(customers), created,
                           created + timedelta(minutes=PAY_LOCK_MINUTES),
                           paid=created + timedelta(minutes=random.randint(1, 5)),
                           refund=now - timedelta(minutes=random.randint(5, 60)) if st == 'REFUNDED' else None,
                           now=now)

    # ---- 演示观众 沈知意：已检票（今天已开场）/ 已退票（明天）/ 已支付（明天）----
    if started_shows:
        sh = started_shows[0]
        created = now - timedelta(hours=random.randint(3, 20))
        paid = created + timedelta(minutes=3)
        ci = max(start_dt(sh) - timedelta(minutes=30), paid + timedelta(minutes=1))
        make_order(conn, sh, pick_seats(UNITS_BY_HALL[sh['hall_id']], OCCUPIED[sh['id']], 2),
                   'USED', DEMO_PHONE, DEMO_NAME, created,
                   created + timedelta(minutes=PAY_LOCK_MINUTES),
                   paid=paid, checkin=ci, now=now)
    tmr = [sh for sh in shows if sh['date'] == today + timedelta(days=1)]
    if tmr:
        sh1, sh2 = tmr[0], tmr[min(3, len(tmr) - 1)]
        created = now - timedelta(hours=random.randint(5, 30))
        make_order(conn, sh1, pick_seats(UNITS_BY_HALL[sh1['hall_id']], OCCUPIED[sh1['id']], 2),
                   'REFUNDED', DEMO_PHONE, DEMO_NAME, created,
                   created + timedelta(minutes=PAY_LOCK_MINUTES),
                   paid=created + timedelta(minutes=2),
                   refund=now - timedelta(minutes=random.randint(10, 60)), now=now)
        created = now - timedelta(hours=random.randint(2, 10))
        make_order(conn, sh2, pick_seats(UNITS_BY_HALL[sh2['hall_id']], OCCUPIED[sh2['id']], 2),
                   'PAID', DEMO_PHONE, DEMO_NAME, created,
                   created + timedelta(minutes=PAY_LOCK_MINUTES),
                   paid=created + timedelta(minutes=4), now=now)

    # ---- 1 笔待支付还剩约 10 分钟（优先今天接下来要开的场次；深夜跑脚本时退到明天）----
    upcoming_today = [sh for sh in future_today if start_dt(sh) > now + timedelta(minutes=30)]
    if not upcoming_today:
        upcoming_today = [sh for sh in shows if sh['date'] == today + timedelta(days=1)]
    if upcoming_today:
        sh = upcoming_today[0]
        created = now - timedelta(minutes=5)
        make_order(conn, sh, pick_seats(UNITS_BY_HALL[sh['hall_id']], OCCUPIED[sh['id']], 2),
                   'UNPAID', *random.choice(customers), created,
                   created + timedelta(minutes=PAY_LOCK_MINUTES), now=now)
        # 另一笔普通观众的待支付
        created = now - timedelta(minutes=3)
        make_order(conn, sh, pick_seats(UNITS_BY_HALL[sh['hall_id']], OCCUPIED[sh['id']],
                                        random.choice([1, 2]) if not all(
                                            len(u) == 2 for u in UNITS_BY_HALL[sh['hall_id']]) else 2),
                   'UNPAID', *random.choice(customers), created,
                   created + timedelta(minutes=PAY_LOCK_MINUTES), now=now)
    # ---- 1 笔已超时（不占座）----
    if future_pool:
        make_unseated_order(conn, future_pool[0], 'EXPIRED', *random.choice(customers),
                            now - timedelta(minutes=30), now - timedelta(minutes=15), now)

    conn.commit()


def print_report():
    """运行结束打印统计。"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    q = lambda sql, a=(): [dict(r) for r in conn.execute(sql, a).fetchall()]
    print('=' * 52)
    print('光影影城 数据库初始化完成')
    print('=' * 52)
    for t in ('movie', 'hall', 'seat', 'showtime', 'ticket_order', 'order_seat', 'order_log', 'admin_user'):
        c = conn.execute(f'SELECT COUNT(*) c FROM {t}').fetchone()['c']
        print(f'  {t:14s} {c:>6} 条')
    print('-' * 52)
    names = {'UNPAID': '待支付', 'PAID': '已支付', 'CANCELLED': '已取消',
             'EXPIRED': '已超时', 'REFUNDED': '已退票', 'USED': '已检票'}
    for r in q("SELECT status, COUNT(*) c FROM ticket_order GROUP BY status"):
        print(f"  订单 {names.get(r['status'], r['status']):>4} {r['c']:>4} 笔")
    print('-' * 52)
    print('  管理员账号: admin    密码: guangying2026')
    print('  演示观众:   13300004444  沈知意（名下含已检票/已退票/已支付各一笔）')
    print('  待支付演示订单（约剩 10 分钟）:')
    for r in q("SELECT order_no, phone, customer_name, pay_deadline FROM ticket_order "
               "WHERE status='UNPAID' ORDER BY pay_deadline LIMIT 2"):
        print(f"    订单号 {r['order_no']}  {r['phone']} {r['customer_name']}  "
              f"支付截止 {r['pay_deadline']}")
    print('=' * 52)
    conn.close()


if __name__ == '__main__':
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    with open(SCHEMA_PATH, encoding='utf-8') as f:
        conn.executescript(f.read())
    seed(conn)
    conn.close()
    print_report()
