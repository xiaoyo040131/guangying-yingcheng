# services/movie_service.py —— 影片业务：前台列表 / 详情日期标签 / 后台增删改
"""
包含：
- 前台：热映、即将上映列表，影片详情（含日期标签）；
- 后台：影片新增 / 修改 / 改状态，字段后端全量校验（R9 要求影片状态合法）。
"""
from datetime import date, timedelta

from dao import movie_dao
from config import BOOKABLE_DAYS
from utils import (BizError, MOVIE_STATUS, check_int_in, parse_date, fmt_date_cn,
                   WEEKDAY_CN)

# 海报只能从已生成的 poster-1 ~ poster-8 中选（不带扩展名，img_url 自动找后缀）
POSTER_CHOICES = ['poster-%d' % i for i in range(1, 9)]


# ---------------- 前台 ----------------

def listing_movies():
    """首页海报墙：热映 + 即将上映（分两组返回）。"""
    return {
        'showing': movie_dao.list_by_status('SHOWING'),
        'coming': movie_dao.list_by_status('COMING'),
    }


def get_movie(movie_id):
    """取影片，不存在返回 None（视图据此返回 404）。"""
    return movie_dao.get_by_id(movie_id)


def detail_dates():
    """影片详情页的日期标签：今天起 5 天。"""
    today = date.today()
    days = []
    for i in range(BOOKABLE_DAYS):
        d = today + timedelta(days=i)
        days.append({
            'date': d.isoformat(),
            'label': '%d月%d日' % (d.month, d.day),
            'weekday': '今天' if i == 0 else ('明天' if i == 1 else WEEKDAY_CN[d.weekday()]),
            'is_today': i == 0,
        })
    return days


# ---------------- 后台 ----------------

def admin_list():
    """后台影片列表：全部影片（按状态排序）。"""
    return movie_dao.get_all()


def save_movie(data, movie_id=None):
    """新增 / 修改影片，返回影片 id。所有字段后端校验一遍。"""
    title = (data.get('title') or '').strip()
    if not (2 <= len(title) <= 40):
        raise BizError('片名需为 2~40 个字符')

    title_en = (data.get('title_en') or '').strip()
    if len(title_en) > 80:
        raise BizError('英文名不能超过 80 个字符')

    poster = (data.get('poster') or '').strip()
    if poster not in POSTER_CHOICES:
        raise BizError('请选择 poster-1 ~ poster-8 中的海报')

    genre = (data.get('genre') or '').strip()
    if not (1 <= len(genre) <= 30):
        raise BizError('类型需为 1~30 个字符，如"悬疑/犯罪"')

    duration = check_int_in(data.get('duration'), 60, 240, '片长需为 60~240 分钟')

    director = (data.get('director') or '').strip()
    if not (1 <= len(director) <= 50):
        raise BizError('导演需为 1~50 个字符')

    actors = (data.get('actors') or '').strip()
    if not (1 <= len(actors) <= 100):
        raise BizError('主演需为 1~100 个字符')

    # 评分输入 0~10（可带一位小数），入库时 x10 存整数
    raw_rating = str(data.get('rating') or '').strip()
    try:
        rating_x10 = int(round(float(raw_rating) * 10))
    except ValueError:
        raise BizError('评分需为 0~10 的数字')
    if not (0 <= rating_x10 <= 100):
        raise BizError('评分需在 0~10 之间')

    synopsis = (data.get('synopsis') or '').strip()
    if len(synopsis) > 500:
        raise BizError('简介不能超过 500 个字符')

    release_date = (data.get('release_date') or '').strip()
    parse_date(release_date)  # 格式校验

    status = (data.get('status') or '').strip()
    if status not in MOVIE_STATUS:
        raise BizError('影片状态不正确')

    payload = {
        'title': title, 'title_en': title_en, 'poster': poster, 'genre': genre,
        'duration': duration, 'director': director, 'actors': actors,
        'rating': rating_x10, 'synopsis': synopsis,
        'release_date': release_date, 'status': status,
    }
    if movie_id:
        movie_dao.update(movie_id, payload)
        return movie_id
    return movie_dao.insert(payload)


def set_status(movie_id, status):
    """修改影片状态（热映 / 即将上映 / 下映）。"""
    if status not in MOVIE_STATUS:
        raise BizError('影片状态不正确')
    if not movie_dao.get_by_id(movie_id):
        raise BizError('影片不存在')
    movie_dao.update_status(movie_id, status)


def date_label(d):
    """后台表格里显示 'M月D日 周X'。"""
    return fmt_date_cn(d)
