from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import App, AppTool
from app.schemas import AppCreate, AppUpdate, DEFAULT_WORKFLOW_SPEC


def create_app(db: Session, payload: AppCreate) -> App:
    app = App(
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        model_provider=payload.model_provider,
        model_name=payload.model_name,
        temperature=payload.temperature,
        top_p=payload.top_p,
        max_tokens=payload.max_tokens,
        workflow_spec=payload.workflow_spec or DEFAULT_WORKFLOW_SPEC,
    )
    db.add(app)
    db.flush()
    db.add(AppTool(app_id=app.id, tool_name="query_order", enabled=True))
    db.commit()
    db.refresh(app)
    return app


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
