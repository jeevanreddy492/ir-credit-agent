from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.backend.client import backend
from app.db.models import Base
from app.db.session import engine
from app.observability import setup_logging, setup_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)   # swap for Alembic once schema stabilises
    yield
    await backend.aclose()
    await engine.dispose()


app = FastAPI(title="IR Credit Request Investigation Agent", version="0.1.0", lifespan=lifespan)
setup_tracing(app)
app.include_router(router, prefix="/api/v1")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
