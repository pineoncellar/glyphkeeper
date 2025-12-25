"""
数据库连接管理模块

功能：
- 创建异步数据库引擎和 Session
- 提供 get_db() 依赖注入函数
- 初始化数据库表结构
- 管理数据库连接池

用法示例：
    async with get_db() as session:
        result = await session.execute(query)
"""
