# services/__init__.py —— 业务层包
# 约定：services 负责校验、计价、事务、状态机、统计；
#       只调用 dao，不直接写 SQL；所有失败抛 utils.BizError("中文提示")。
