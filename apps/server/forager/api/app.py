"""FastAPI 应用装配：路由、错误映射、生命周期、SPA 托管。"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..auth import bootstrap, require_auth
from ..config import Settings, load_settings
from ..db import Database
from ..library.cache import ImageCache, MetaCache
from ..library.downloader import Downloader
from ..sources import create_sources
from ..secrets import SecretBox, load_key
from ..source_config import SourceConfigStore
from ..sources.base import SourceError
from ..subs.scheduler import SubscriptionScheduler
from ..transport.curl import CurlError
from . import library, session, settings as settings_routes, source_auth, sources, tree

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("forager")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.data_dir)
        source_map = create_sources(settings, db)
        app.state.settings = settings
        app.state.db = db
        # 首次启动建账号：初始密码与恢复码只在日志里出现这一次
        bootstrap(db)
        app.state.sources = source_map
        app.state.source_config = SourceConfigStore(
            db, SecretBox(load_key(settings.data_dir, settings.secret_key))
        )
        # 设置页保存过的配置优先于环境变量：启动时覆盖到各源 client
        app.state.source_config.apply_all(source_map)
        app.state.meta = MetaCache(db)
        app.state.images = ImageCache(db.media_dir / ".cover-cache")
        app.state.downloader = Downloader(
            db, source_map, concurrency=settings.download_concurrency, log=log.info
        )
        app.state.scheduler = SubscriptionScheduler(
            db,
            source_map,
            app.state.downloader,
            initial=settings.sub_initial,
            tick=settings.sub_tick_sec,
            log=log.info,
        )
        await app.state.downloader.start()
        await app.state.scheduler.start()
        log.info("forager 启动：data=%s 源=%s", db.root, ", ".join(source_map))
        try:
            yield
        finally:
            await app.state.scheduler.stop()
            await app.state.downloader.stop()
            db.close()
            log.info("forager 已停止")

    app = FastAPI(title="forager", version="0.1.0", lifespan=lifespan)

    # 会话路由本身必须公开，其余业务路由统一挂 require_auth
    app.include_router(session.router)
    guard = [Depends(require_auth)]
    app.include_router(sources.router, dependencies=guard)
    app.include_router(library.router, dependencies=guard)
    app.include_router(tree.router, dependencies=guard)
    app.include_router(settings_routes.router, dependencies=guard)
    app.include_router(source_auth.router, dependencies=guard)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # ---- 统一错误出口：一律返回 {"error": ...}，与前端约定一致 ----

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(SourceError)
    async def _source_error(_request: Request, exc: SourceError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(CurlError)
    async def _curl_error(_request: Request, exc: CurlError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=exc.status or 502)

    _mount_spa(app, settings)
    return app


def _mount_spa(app: FastAPI, settings: Settings) -> None:
    """生产模式托管前端构建产物；未构建时给出明确提示而不是 404 页面。"""
    static_dir = settings.static_dir
    assets = static_dir / "assets"
    index = static_dir / "index.html"

    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if static_dir.is_dir():
            root = static_dir.resolve()
            candidate = (root / full_path).resolve()
            if candidate.is_file() and root in candidate.parents:
                return FileResponse(candidate)
            if index.is_file():
                return FileResponse(index, media_type="text/html")
        raise HTTPException(
            status_code=404,
            detail="前端未构建：先执行 pnpm build，或设置 FORAGER_STATIC_DIR",
        )


app = create_app()


__all__ = ["app", "create_app"]
