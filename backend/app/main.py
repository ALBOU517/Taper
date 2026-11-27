"""Taper — goal-driven multi-sport training engine."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import life_routes, routes, strava, study_routes
from .db import init_db

FRONTEND = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend"))



@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Taper", version="1.0.0", lifespan=lifespan,
              description="Reverse-periodized training plans, multi-sport load modelling and a workout DSL.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(routes.router)
app.include_router(strava.router)
app.include_router(life_routes.router)
app.include_router(study_routes.router)


@app.get("/api/health")
def health():
    return {"ok": True}


if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
