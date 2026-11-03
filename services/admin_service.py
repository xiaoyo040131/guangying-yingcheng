# services/admin_service.py —— 后台账号、影厅与座位编辑业务
"""
包含：
- 管理员登录校验（密码只存哈希，用 werkzeug.security 校验）；
- 影厅管理（名称唯一、类型、行列数、过道、启用状态）；
- 座位编辑：把网格上的座位在 普通 / 情侣 / VIP / 无座位 之间切换，
  保存时自动把"相邻的情侣座"两两配对，落单的自动改回普通座并提示。
"""
import db
from dao import admin_dao, hall_dao, seat_dao, showtime_dao
from utils import BizError, HALL_TYPE, SEAT_TYPE, check_int_in
from werkzeug.security import check_password_hash


# ---------------- 登录 ----------------

def authenticate(username, password):
    """校验管理员账号密码，成功返回管理员行，失败抛 BizError。"""
    username = (username or '').strip()
    password = password or ''
    if not username or not password:
        raise BizError('请输入账号和密码')
    user = admin_dao.get_by_username(username)
    if not user or not check_password_hash(user['password_hash'], password):
        # 统一提示，不区分"账号不存在"和"密码错误"
        raise BizError('账号或密码不正确')
    return user


# ---------------- 影厅管理 ----------------

def list_halls():
    """影厅列表（带座位数统计）。"""
    halls = hall_dao.get_all()
    return [{
        'hall': h,
        'seat_total': seat_dao.count_by_hall(h['id'], only_enabled=False),
        'seat_enabled': seat_dao.count_by_hall(h['id'], only_enabled=True),
        'show_count': showtime_dao.count_by_hall(h['id']),
    } for h in halls]


def get_hall(hall_id):
    hall = hall_dao.get_by_id(hall_id)
    if not hall:
        raise BizError('影厅不存在')
    return hall


def save_hall(data, hall_id=None):
    """新增 / 修改影厅。已有排片的影厅不允许改行数、列数（座位会对不上）。"""
    name = (data.get('name') or '').strip()
    if not (2 <= len(name) <= 20):
        raise BizError('影厅名称需为 2~20 个字符')

    htype = (data.get('type') or '').strip()
    if htype not in HALL_TYPE:
        raise BizError('影厅类型不正确')

    rows = check_int_in(data.get('row_count'), 1, 26, '行数需为 1~26')
    cols = check_int_in(data.get('col_count'), 1, 30, '列数需为 1~30')

    raw_aisle = str(data.get('aisle_after_col') or '').strip()
    if raw_aisle in ('', '0', 'None'):
        aisle = None
    else:
        aisle = check_int_in(raw_aisle, 1, cols - 1, '过道列号需在 1~%d 之间（或留空表示无过道）' % (cols - 1))

    enabled = 1 if str(data.get('is_enabled', '1')) in ('1', 'on', 'true', 'True') else 0

    if hall_dao.name_exists(name, exclude_id=hall_id):
        raise BizError('影厅名称「%s」已存在' % name)

    payload = {'name': name, 'type': htype, 'row_count': rows, 'col_count': cols,
               'aisle_after_col': aisle, 'is_enabled': enabled}

    if hall_id:
        hall = get_hall(hall_id)
        if (rows != hall['row_count'] or cols != hall['col_count']) \
                and showtime_dao.count_by_hall(hall_id) > 0:
            raise BizError('该影厅已有 %d 个场次，不能修改行数/列数'
                           % showtime_dao.count_by_hall(hall_id))
        hall_dao.update(hall_id, payload)
        return hall_id

    return hall_dao.insert(payload)


def toggle_hall(hall_id, enabled):
    """启用 / 停用影厅（停用后不再出现在前台与排片下拉中）。"""
    get_hall(hall_id)
    hall_dao.set_enabled(hall_id, 1 if enabled else 0)


# ---------------- 座位编辑 ----------------

def seat_grid(hall_id):
    """座位编辑网格：行 × 列，每格是座位（含类型与启用状态）。"""
    hall = get_hall(hall_id)
    seats = seat_dao.list_by_hall(hall_id)
    index = {(s['row_no'], s['col_no']): s for s in seats}
    grid = []
    for r in range(1, hall['row_count'] + 1):
        cells = []
        for c in range(1, hall['col_count'] + 1):
            s = index.get((r, c))
            cells.append({
                'id': s['id'] if s else None,
                'row_no': r,
                'col_no': c,
                'seat_type': s['seat_type'] if s else 'NONE',
                'is_enabled': s['is_enabled'] if s else 0,
                'is_aisle': bool(hall['aisle_after_col'] and c == hall['aisle_after_col']),
            })
        grid.append({'row_no': r, 'cells': cells})
    return {'hall': hall, 'grid': grid}


def save_seats(hall_id, changes):
    """保存座位类型。

    changes: {seat_id: 'NORMAL' | 'COUPLE' | 'VIP' | 'NONE'}
    保存后逐行重新配对情侣座：相邻的两个情侣座配成一对，落单的自动改回普通座。
    返回被改回普通座的座位数（供页面提示）。
    """
    hall = get_hall(hall_id)
    seats = seat_dao.list_by_hall(hall_id)
    if not seats:
        raise BizError('该影厅还没有座位数据')

    with db.transaction():
        for s in seats:
            code = changes.get(str(s['id'])) or changes.get(s['id'])
            if not code:
                continue
            if code == 'NONE':
                # 无座位：保留记录但标记不可售，避免破坏历史订单
                seat_dao.update_type(s['id'], s['seat_type'], None, 0)
            elif code in SEAT_TYPE:
                seat_dao.update_type(s['id'], code, None, 1)
            else:
                raise BizError('座位类型不正确')

        # 重新配对：逐行把启用中的情侣座按列号排序，相邻（列差 1）才配对
        unpaired = 0
        fresh = seat_dao.list_by_hall(hall_id)
        by_row = {}
        for s in fresh:
            by_row.setdefault(s['row_no'], []).append(s)
        for row_no, row_seats in by_row.items():
            couples = [s for s in row_seats if s['is_enabled'] and s['seat_type'] == 'COUPLE']
            couples.sort(key=lambda x: x['col_no'])
            i = 0
            while i < len(couples) - 1:
                a, b = couples[i], couples[i + 1]
                if b['col_no'] == a['col_no'] + 1:
                    seat_dao.update_type(a['id'], 'COUPLE', b['id'], 1)
                    seat_dao.update_type(b['id'], 'COUPLE', a['id'], 1)
                    i += 2
                else:
                    seat_dao.update_type(a['id'], 'NORMAL', None, 1)
                    unpaired += 1
                    i += 1
            if i == len(couples) - 1:            # 该行剩下一个落单的情侣座
                seat_dao.update_type(couples[i]['id'], 'NORMAL', None, 1)
                unpaired += 1
    return {'hall': hall, 'unpaired': unpaired}
