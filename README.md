# 光影影城 · 在线选座购票系统

> 课程项目 · 第 6 题（★★★）　类型：按"场次 × 座位"预订 + 限时支付
> 技术栈：Python 3.10+ / Flask 3 / SQLite（标准库 sqlite3，手写 SQL）/ Bootstrap 5.3.3（本地）/ 原生 JS fetch

一家 4 个影厅的电影院的选座购票系统：观众在前台浏览热映影片，选日期选场次，在影厅座位图上选 1~4 个座位下单，锁座 15 分钟内模拟支付后得到 8 位取票码，超时未支付自动取消、座位放回；管理员在后台排片（同厅场次不重叠）、检票、退票，并查看票房与上座率。

---

## 一、功能清单

### 前台（观众，无需注册，手机号 + 姓名确认身份）

| 页面 / 功能 | 路由 | 说明 |
| --- | --- | --- |
| 首页 | `/` | 深色 Hero 轮播、快捷购票卡、正在热映海报墙、即将上映、影厅介绍、影院环境图集 |
| 影片详情 | `/movies/<id>` | 大海报 + 评分星 + 今天起 5 天日期标签 + 当天场次（余票、紧张、停售标记）；下映影片 404 |
| 选座页（核心） | `/shows/<场次id>/seats` | 深色座位图：银幕弧线、过道、普通/情侣/VIP 三色、已售/锁定/可选状态，最多选 4 个，情侣座必须成对 |
| 实时报价接口 | `/api/quote` | fetch 调用，逐座价格明细 + 合计（前端不自己算钱） |
| 确认订单 | `/orders/new`（POST） | 手机号 + 姓名 → 创建待支付订单并锁座 15 分钟 |
| 支付页 | `/orders/<订单号>/pay` | 15 分钟倒计时（后端数据库时间为准），模拟支付 |
| 支付成功页 | `/orders/<订单号>/success` | 8 位取票码（大号等宽），仅本浏览器可见 |
| 我的订单 | `/orders` | 手机号 + 姓名双匹配查询；待支付可继续支付/取消；已支付且开场前 ≥ 2 小时可退票 |
| 错误页 | 400 / 404 / 500 | 统一友好中文页面，任何用户操作不出现 500 |

### 后台（`/admin`，账号密码登录）

| 页面 / 功能 | 路由 | 说明 |
| --- | --- | --- |
| 登录 / 退出 | `/admin/login`、`/admin/logout` | 密码哈希校验；成功后 `session.clear()` 再写身份；未登录页面 302、接口 401 |
| 仪表板 | `/admin` | 状态卡 × 5、今日票房/人次/上座率 KPI、近 7 天票房纯 CSS 柱状图、影片票房排行条形图、今日场次上座率进度条 |
| 排片表（可视化） | `/admin/schedule?date=` | 影厅 × 09:00~24:00 时间轴，场次块按开始时间与时长百分比定位；点击空白新建、点块编辑/停售/取消；同厅不重叠 |
| 订单管理 | `/admin/orders` | 状态标签页 + 关键字（订单号/手机/取票码）+ 日期筛选 + 分页；详情含座位、明细、轨迹、检票/退票操作 |
| 检票 | `/admin/checkin` | 输入 8 位取票码 → 显示订单 → 确认检票（开场前 60 分钟至开场后 30 分钟） |
| 影片管理 | `/admin/movies` | 新增/编辑/改状态，海报只能从 poster-1~8 选 |
| 影厅管理 | `/admin/halls` | 名称、类型、行列数、过道列；座位编辑网格：点击切换 普通/情侣/VIP/无座位 |

---

## 二、运行方法

### 方式一：一键启动（推荐）

双击项目根目录的 **`启动项目.bat`**，它会依次：

1. `chcp 65001` 切到 UTF-8，检查 Python（未安装则提示安装 3.10+）；
2. 缺 Flask 时用清华镜像 `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "Flask>=3.0"`；
3. 没有 `instance/guanqing.db` 数据库时自动运行 `init_db.py` 生成演示数据；
4. `python app.py --open` 启动并在 1.5 秒后自动打开浏览器。

### 方式二：手动命令

```bat
cd 项目根目录
python -m pip install -r requirements.txt
python init_db.py        rem 首次运行生成数据库与演示数据（可重复执行，每次全量重建）
python app.py --open     rem 启动，浏览器访问 http://127.0.0.1:5000/
```

> 想重置演示数据：关闭服务后删除 `instance/` 文件夹，重新运行 `init_db.py`。
> 演示数据以"运行当天"为基准生成：含今天已开场的已检票订单、今晚 80%+ 上座率的场次、1 笔还剩约 10 分钟的待支付订单、1 笔已超时订单。

---

## 三、演示账号

| 角色 | 账号 | 说明 |
| --- | --- | --- |
| 管理员 | 用户名 `admin`　密码 `guangying2026` | 后台 `/admin` |
| 演示观众 | 手机号 `13300004444`　姓名 `沈知意` | 名下 3 笔订单：已检票（今天已开场）、已退票、已支付（明天），方便演示查询与退票 |

---

## 四、目录结构

```
项目根目录/
├── app.py              # create_app()：MIME 修正、img_url、CSRF、过滤器、错误页；python app.py 启动
├── config.py           # 所有可调参数（锁座时长/退票/检票窗/加价等）；SECRET_KEY 存 instance/secret.key
├── db.py               # get_db / query_all / query_one / execute / transaction()(BEGIN IMMEDIATE)
├── schema.sql          # 8 张表建表脚本（含 UNIQUE/CHECK 约束与索引）
├── init_db.py          # 建库 + 演示数据（约 137 笔订单），结束打印统计与管理员账号
├── utils.py            # BizError、状态中文字典、金额/时间换算、输入校验
├── dao/                # 数据访问层：movie/hall/seat/showtime/order/admin 共 7 文件，只放参数化 SQL
├── services/           # 业务层：pricing(唯一算价入口)/movie/showtime/seat/order/stat/admin
├── views/
│   ├── front.py        # 前台蓝图
│   ├── auth.py         # 后台登录、退出、login_required
│   └── admin.py        # 后台蓝图（url_prefix='/admin'，统一登录拦截）
├── templates/          # base.html、_macros.html、front/ × 6、admin/ × 12、errors/error.html
├── static/
│   ├── css/style.css   # 唯一样式文件（前后台共用，纯 CSS 图表）
│   ├── js/front.js     # 前台交互（选座/倒计时/实时报价，fetch + X-CSRF-Token）
│   ├── js/admin.js     # 后台交互（排片时间轴/座位编辑网格）
│   ├── images/         # WorkBuddy 生成的 JPG/PNG 图片（banner/cover/env/poster/favicon）
│   └── vendor/         # Bootstrap 5.3.3 + Bootstrap Icons 本地文件
├── tests/
│   ├── try_step2.py    # 业务层快速自检（第 2 步）
│   └── selfcheck.py    # 全量自检：需求说明第十三节全部验收测试 + 图片检查（临时库上跑）
├── docs/               # 项目规范.md、需求说明.md
├── instance/           # 运行时生成：数据库、密钥（不提交，在 .gitignore 中）
├── 启动项目.bat  ├── README.md  ├── requirements.txt  └── .gitignore
```

调用方向严格为 **views → services → dao → db**：视图不写 SQL，模板不写业务判断，dao 不碰 request/session。

---

## 五、数据表说明（8 张表）

| 表 | 说明 | 关键字段 / 约束 |
| --- | --- | --- |
| `movie` | 影片 | 评分 ×10 存整数；状态 SHOWING/COMING/OFF（CHECK） |
| `hall` | 影厅 | 名称 UNIQUE；类型 NORMAL/IMAX/VIP；行列数、过道列 |
| `seat` | 座位 | `UNIQUE(hall_id, row_no, col_no)`；类型 NORMAL/COUPLE/VIP；情侣座 `couple_partner_id` 配对 |
| `showtime` | 场次 | 日期 'YYYY-MM-DD'、时间 'HH:MM'；结束时间含 15 分钟清场；`CHECK(end_time > start_time)`；票价以分存 |
| `ticket_order` | 订单 | 订单号 UNIQUE；取票码 8 位 UNIQUE（支付后生成）；状态六枚举（CHECK）；`pay_deadline` = 创建 + 15 分钟 |
| `order_seat` | 订单座位 | **`UNIQUE(show_id, seat_id)` —— "一座一票"防重复占用的数据库兜底**；每座实付票价（分） |
| `order_log` | 订单轨迹 | 谁(operator)、何时、从什么到什么、备注；每次状态流转写一条 |
| `admin_user` | 管理员 | 只存 `generate_password_hash` 哈希 |

所有 SQL 全部 `?` 参数化；金额一律以 **分** 存 INTEGER，仅显示时换算为元（`|yuan` 过滤器）。

---

## 六、核心业务规则

- **R1 可售**：只售今天起 5 天内、状态"在售"的场次；开场前 30 分钟停售。
- **R2 选座**：每单 1~4 个座位；必须属于该场次影厅且启用；情侣座必须成对选择。
- **R3 计价**：每座票价 = 场次基础票价 + 座位加价（VIP +20 元，情侣座每座 +5 元）；总价逐座相加。唯一算价入口在 `services/pricing.py`，演示数据与线上下单共用同一套。
- **R4 下单锁座**：`BEGIN IMMEDIATE` 事务内 先过期结算 → 再查占用 → 再写订单与 `order_seat`；并发冲突由 UNIQUE 约束兜底，整单失败并提示具体哪个座位被选走。
- **R5 超时释放**：读座位图 / 订单列表 / 下单之前，先把"支付截止时间已过"的待支付订单批量改为已超时并删除 `order_seat`（条件 UPDATE + 轨迹，操作人 system）。
- **R6 支付**：只有未过期的待支付订单可支付（事务内再查一次截止时间）；支付后生成不重复的 8 位取票码。
- **R7 退票**：已支付且开场前 ≥ 2 小时可退；退票删除 `order_seat` 释放座位。
- **R8 检票**：已支付订单在 [开场前 60 分钟, 开场后 30 分钟] 内可检票；不能重复检票。
- **R9 排片**：同一影厅同一天场次时间段（含清场）不能重叠（事务内先查重叠再写入）；开始时间不能早于当前时间；影片须为热映或即将上映。

---

## 七、订单状态机

| 动作 | 起始状态 | 目标状态 | 触发者 | 副作用 |
| --- | --- | --- | --- | --- |
| 下单 create | — | UNPAID 待支付 | 观众 | 锁座（写 order_seat） |
| pay 支付 | UNPAID（未过期） | PAID 已支付 | 观众 | 生成取票码 |
| cancel 取消 | UNPAID | CANCELLED 已取消 | 观众 | 删除 order_seat 释放座位 |
| expire 超时 | UNPAID（已过期） | EXPIRED 已超时 | 系统 | 删除 order_seat 释放座位 |
| refund 退票 | PAID | REFUNDED 已退票 | 观众 / 管理员 | 删除 order_seat（R7） |
| check 检票 | PAID | USED 已检票 | 管理员 | 记录检票时间（R8） |

实现于 `services/order_service.py` 的 `TRANSITIONS`：更新一律
`UPDATE … SET status=? WHERE id=? AND status IN (允许的起始状态)` 并检查 `rowcount == 1`，每次流转写一条 `order_log`。

---

## 八、自测方法

### 1. 全量自检（推荐，约 1 分钟）

```bat
python -X utf8 tests\selfcheck.py
```

脚本把数据库指向临时文件（不动 `instance/` 正式库），用 `test_client()` + 业务层调用跑完需求说明第十三节全部验收测试：

1. 页面可访问；下映影片详情 404；停售场次不能进入选座；
2. 选 5 个座位 / 只选情侣座一半 / 选外厅座位 —— 被拒绝；
3. 普通座 / VIP 座 / 情侣座计价正确（并对照真实订单总价）；
4. **10 个线程同时抢同一个座位，只成功 1 单**；交集座位组后提交整单失败；
5. 超时结算：状态变已超时、座位释放、不能再支付；
6. 支付生成 8 位唯一取票码；开场前不足 2 小时不能退票（对照：满 2 小时可退并释放座位）；
7. 检票时间窗外被拒、不能重复检票（对照：窗口内正常检票）；
8. 同厅重叠排片失败、不同厅同一时间可以；
9. 身份不匹配查不到、不能取消/退别人的票、后台未登录 302/401、缺 CSRF 返回 400、XSS 被转义；
10. 首页引用的每一张图片都返回 200；
11. banner / cover / env 图片全部存在且均为 JPG / PNG，目录里无 SVG。

每项打印 ✔ / ✘，最后输出 **"全部 N 项检查通过"**（退出码 0）。

### 2. 业务层快速自检

```bat
python -X utf8 tests\try_step2.py
```

### 3. 手工演示路线（验收答辩用）

1. 首页 → 影片详情 → 选一场明天的场次进选座页，选 2 座下单；
2. 支付页看到 15 分钟倒计时 → 模拟支付 → 成功页拿到 8 位取票码；
3. 用 `13300004444 / 沈知意` 查"我的订单"，演示已支付订单退票、待支付订单取消；
4. 后台登录 `admin / guangying2026`：排片表点空白新建一场（再重叠建一场看拒绝）→ 订单管理搜索 → 检票页输入刚才的取票码检票；
5. 仪表板看今日票房、近 7 天柱状图与上座率进度条。

### 4. 安全机制速查

- SQL 全参数化，无任何拼接用户输入；
- CSRF：所有 POST 表单带 `_csrf`，fetch 带 `X-CSRF-Token`，缺失/不匹配统一 400；
- Jinja2 自动转义，禁用 `|safe`；JS 一律 `textContent`/`createElement`；
- 前台身份 = 手机号 + 姓名双匹配（SQL 带身份条件）；后台统一登录拦截；
- 管理员密码哈希存储；登录成功 `session.clear()`；跳转只允许 `/admin` 开头。
