# views/__init__.py —— 视图包
"""
views 只负责：接收请求参数 -> 调用 services -> 渲染模板 / 返回 JSON。
不写 SQL，不做业务判断（校验、计价、事务都在 services 里）。
"""
