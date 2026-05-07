from collections.abc import Generator  # 导入 Generator 类型，用来标注会生成数据库会话的函数

from sqlalchemy import create_engine  # 导入创建数据库引擎的函数
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker  # 导入 ORM 基类、会话类型和会话工厂

from app.core.config import get_settings  # 导入读取配置的函数


class Base(DeclarativeBase):  # 定义 SQLAlchemy 的声明式基类，所有模型都要继承它
    pass  # 这里不需要额外内容，先保留一个空基类


settings = get_settings()  # 读取环境变量和 .env，拿到数据库等配置
engine = create_engine(settings.database_url, pool_pre_ping=True)  # 按数据库连接串创建引擎，预先检测连接是否可用
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)  # 创建会话工厂，后面用它生成 Session 对象


def get_db() -> Generator[Session, None, None]:  # 定义一个 FastAPI 依赖函数，返回数据库会话
    db = SessionLocal()  # 用会话工厂创建一个新的数据库会话
    try:  # 确保函数退出时一定会执行清理
        yield db  # 把会话交给 FastAPI 使用
    finally:  # 无论正常返回还是发生异常，都会执行这里
        db.close()  # 关闭会话，释放连接资源


def init_db() -> None:  # 定义数据库初始化函数，启动时会调用
    # 导入模型文件，让 SQLAlchemy 注册所有表定义
    from app.db import models  # noqa: F401
    """
    # noqa = “no quality assurance”，告诉代码检查器这行先别报错
    # F401 = “导入了但没使用”
    # 表面上看 models 没被直接用到，所以 lint 会报“未使用导入”。
    # 但它其实是为了触发模型注册，让 Base.metadata.create_all() 能看到所有表定义，
    # 所以这个导入是有副作用的，不是多余的。
    """
    Base.metadata.create_all(bind=engine)  # 根据已注册的模型元数据，自动创建缺失的数据表
