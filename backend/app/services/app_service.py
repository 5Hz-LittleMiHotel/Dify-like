from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import App, AppTool
from app.schemas import AppCreate, AppUpdate, DEFAULT_WORKFLOW_SPEC


def create_app(db: Session, payload: AppCreate) -> App:  # db 负责数据库交互，payload 负责描述“要创建什么应用”
    app = App(  # 先根据 payload 里的字段，组装一个新的 App ORM 对象
        name=payload.name,  # 应用名称
        description=payload.description,  # 应用描述
        system_prompt=payload.system_prompt,  # 该应用的 system prompt
        model_provider=payload.model_provider,  # 模型提供方，例如 mock
        model_name=payload.model_name,  # 模型名称
        temperature=payload.temperature,  # 采样温度
        top_p=payload.top_p,  # top_p 参数
        max_tokens=payload.max_tokens,  # 最大输出 token 数
        workflow_spec=payload.workflow_spec or DEFAULT_WORKFLOW_SPEC,  # 如果没有传 workflow，就使用默认 workflow
    )
    db.add(app)  # 把这个 App 对象加入当前数据库会话，准备写入数据库
    db.flush()  # 先把 App 刷到数据库，生成 app.id，后面创建 AppTool 时要用这个 id
    db.add(AppTool(app_id=app.id, tool_name="query_order", enabled=True))  # 默认给新应用绑定一个 query_order 工具
    db.commit()  # 提交事务，把 App 和 AppTool 一起真正写入数据库
    """
    以上做法不是不能一次性做，而是当前写法更清晰、更显式。也可以通过 relationship/cascade 组织成另一种写法。
    """
    db.refresh(app)  # 从数据库重新读取 app，确保拿到最新状态
    return app  # 返回创建好的应用对象


def list_apps(db: Session) -> list[App]:
    return list(db.scalars(select(App).order_by(App.created_at.desc())))


def get_app(db: Session, app_id: str) -> App | None:
    return db.get(App, app_id)


def update_app(db: Session, app: App, payload: AppUpdate) -> App:
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(app, key, value)
    db.commit()
    db.refresh(app)
    return app


def delete_app(db: Session, app: App) -> None:
    db.delete(app)
    db.commit()


def set_app_tools(db: Session, app_id: str, tool_names: list[str]) -> list[AppTool]:
    existing = {tool.tool_name: tool for tool in db.scalars(select(AppTool).where(AppTool.app_id == app_id))}
    for tool in existing.values():
        tool.enabled = tool.tool_name in tool_names
    for tool_name in tool_names:
        if tool_name not in existing:
            db.add(AppTool(app_id=app_id, tool_name=tool_name, enabled=True))
    db.commit()
    return list(db.scalars(select(AppTool).where(AppTool.app_id == app_id).order_by(AppTool.tool_name)))


def get_enabled_tool_names(db: Session, app_id: str) -> list[str]:
    rows = db.scalars(
        select(AppTool).where(AppTool.app_id == app_id, AppTool.enabled.is_(True)).order_by(AppTool.tool_name)
    )
    return [row.tool_name for row in rows]
