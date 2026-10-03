import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config
from .routes import router

logger = logging.getLogger("api")

app = FastAPI(title="AI News Brief API", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins(),
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    # Log the type only: messages can carry connection strings.
    logger.error("Unhandled error on %s: %s", request.url.path, type(exc).__name__)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


app.include_router(router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
