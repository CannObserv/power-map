"""FastAPI application entry point."""

from fastapi import FastAPI

from src.core.logging import configure_logging

configure_logging()

app = FastAPI(title="power-map", version="0.1.0")
