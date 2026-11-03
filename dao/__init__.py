# dao/__init__.py —— 数据访问层包
# 约定：每个 dao 模块对应一组表，只放参数化 SQL（一律 ? 占位），
#       一个函数只做一件事，返回 sqlite3.Row 或列表；
#       不做业务判断，不碰 request / session。
