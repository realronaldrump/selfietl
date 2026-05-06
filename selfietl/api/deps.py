from __future__ import annotations

from fastapi import Request

from selfietl.config import AppConfig
from selfietl.db import Database


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_db(request: Request) -> Database:
    return request.app.state.db
