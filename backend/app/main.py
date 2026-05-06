from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import apps, chat, knowledge, runs, tools
from app.core.config import get_settings
from app.db.session import init_db


settings = get_settings()

app = FastAPI(title="Dify-like API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(apps.router, prefix="/api")
app.include_router(tools.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(runs.router, prefix="/api")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}
