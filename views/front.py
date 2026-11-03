# views/front.py —— 前台蓝图（观众侧，无需登录）
"""
本步只实现首页 '/'，后续步骤在同一文件里补：
影片详情、选座、下单、支付、我的订单等路由。

约定：视图里不写 SQL、不写计价，只调用 services 并组织展示用的数据。
"""
from flask import Blueprint, render_template

from services import movie_service

front_bp = Blueprint('front', __name__)


# ---------------- 首页展示文案（纯展示数据，不含业务规则） ----------------

# 轮播：图片全部来自 static/images/，用 img_url('banner-1') 取
BANNERS = [
    {
        'img': 'banner-1',
        'en': 'LUMIERE · CINEMA',
        'title': '每一帧都值得在大银幕上看',
        'sub': '光影影城 · 四厅联映，今夜开场',
    },
    {
        'img': 'banner-2',
        'en': 'NOW SHOWING',
        'title': '灯火与海报之间',
        'sub': '挑一部想看的电影，选一个喜欢的位置',
    },
    {
        'img': 'banner-3',
        'en': 'IMAX · DOLBY ATMOS',
        'title': '巨幕之下，身临其境',
        'sub': '2 号 IMAX 厅 · 216 个座位 · 激光放映',
    },
]

# 影厅介绍：与 init_db.py 里的 4 个影厅一一对应
HALL_INTRO = [
    {
        'cover': 'cover-1',
        'name': '1号厅 · 杜比全景声',
        'type_label': '标准厅',
        'icon': 'bi-speaker',
        'desc': '64 声道全景声环绕，声音从头顶与四周流过，把观众放进故事里。',
        'specs': ['10 行 × 14 列 · 140 座', '第 7 列后设过道', '2K 激光放映'],
    },
    {
        'cover': 'cover-2',
        'name': '2号 IMAX 厅',
        'type_label': 'IMAX',
        'icon': 'bi-badge-4k',
        'desc': '18 米巨幕顶天立地，画面多出 26% 的内容，坐进最后一排也看不完。',
        'specs': ['12 行 × 18 列 · 216 座', '第 9 列后设过道', 'IMAX 双机放映'],
    },
    {
        'cover': 'cover-3',
        'name': '3号情侣厅',
        'type_label': '情侣座',
        'icon': 'bi-heart',
        'desc': '全场双人沙发座，扶手可抬起，两个人中间没有隔阂。',
        'specs': ['6 行 × 5 对 · 60 座', '情侣座须成对选择', '可抬式扶手'],
    },
    {
        'cover': 'cover-4',
        'name': '4号 VIP 厅',
        'type_label': 'VIP',
        'icon': 'bi-gem',
        'desc': '真皮电动躺椅、独立小桌与毛毯服务，观影变成一次小憩。',
        'specs': ['5 行 × 8 列 · 40 座', '全厅 VIP 真皮躺椅', '专属入场通道'],
    },
]

# 特色服务（圆形渐变图标 + 标题 + 说明，六列）
FEATURES = [
    {'icon': 'bi-speaker', 'title': '杜比全景声', 'desc': '64 声道沉浸音效'},
    {'icon': 'bi-brightness-high', 'title': '激光放映', 'desc': '4K 高亮画面'},
    {'icon': 'bi-heart', 'title': '情侣双人座', 'desc': '可抬式扶手沙发'},
    {'icon': 'bi-ticket-perforated', 'title': '自助取票', 'desc': '扫码 10 秒出票'},
    {'icon': 'bi-cup-straw', 'title': '现制小食', 'desc': '爆米花现爆现装'},
    {'icon': 'bi-car-front', 'title': '免费停车', 'desc': '地下车库 2 小时'},
]

# 观影服务（cover-5 ~ cover-8）
SERVICES = [
    {'img': 'cover-7', 'title': '自助取票', 'desc': '凭 8 位取票码在自助机取票，开场前 15 分钟到店即可。'},
    {'img': 'cover-5', 'title': '小食与饮品', 'desc': '现爆爆米花、手作气泡水，凭取票码在小卖部享 9 折。'},
    {'img': 'cover-6', 'title': '大堂休息区', 'desc': '提前到场可以在大堂候场，沙发区提供免费充电。'},
    {'img': 'cover-8', 'title': '观影活动', 'desc': '每周五晚设映后交流场，详情见影院大堂公告。'},
]

# 影院环境图集（3 列网格，第一张跨两行）
GALLERY = [
    {'img': 'env-1', 'caption': '夜色里的光影影城'},
    {'img': 'env-2', 'caption': '走廊与影厅入口'},
    {'img': 'env-3', 'caption': '小卖部的暖黄灯光'},
    {'img': 'env-4', 'caption': '放映室里的放映机'},
    {'img': 'env-5', 'caption': '灯暗下来的观众席'},
    {'img': 'env-6', 'caption': '大堂海报墙'},
]


# ---------------- 路由 ----------------

@front_bp.route('/')
def index():
    """首页：轮播 + 快捷购票卡 + 正在热映 + 即将上映 + 影厅 + 环境图集。"""
    movies = movie_service.listing_movies()
    return render_template(
        'front/index.html',
        banners=BANNERS,
        showing=movies['showing'],
        coming=movies['coming'],
        dates=movie_service.detail_dates(),
        halls=HALL_INTRO,
        features=FEATURES,
        services=SERVICES,
        gallery=GALLERY,
    )
