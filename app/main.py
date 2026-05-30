from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from app.routers import etl_router, auth_router
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="成本分析表 ETL 入库系统",
    version="1.0.0"
)

# 注册路由
app.include_router(auth_router, prefix="/api/v1/auth", tags=["认证服务"])
app.include_router(etl_router, prefix="/api/v1/etl", tags=["数据入库管道"])

# 挂载前端静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def read_root():
    return FileResponse("static/login.html")


@app.get("/health")
def health_check():
    """健康检查"""
    return JSONResponse({"status": "ok", "service": settings.PROJECT_NAME})
