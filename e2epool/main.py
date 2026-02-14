from contextlib import asynccontextmanager

from fastapi import FastAPI

from e2epool.reconcile import reconcile_on_startup
from e2epool.routers import checkpoint, health, internal, runner, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    reconcile_on_startup()
    yield


app = FastAPI(title="e2epool", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(checkpoint.router)
app.include_router(runner.router)
app.include_router(ws.router)
app.include_router(internal.router)
