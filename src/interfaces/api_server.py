"""
FastAPI 接口服务
提供 LightRAG 的 HTTP API 接口

待完善
"""
import os
import shutil
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..core import get_logger, get_settings, PROJECT_ROOT
from ..memory.RAG_engine import RAGEngine, get_rag_engine
from ..ingestion.loader import ingest_file, ingest_text
from ..agents.search import SearchAgent, SearchResult

logger = get_logger(__name__)

# 临时文件目录
TEMP_DIR = PROJECT_ROOT / "temp"


# ============================================
# Pydantic 模型
# ============================================

class QueryRequest(BaseModel):
    """查询请求"""
    question: str = Field(..., description="用户问题", min_length=1)
    mode: str = Field(default="hybrid", description="查询模式: local/global/hybrid/mix/naive")
    top_k: int = Field(default=60, description="返回的相关文档数量", ge=1, le=200)
    prompt_template: Optional[str] = Field(default=None, description="提示词模板名称")


class QueryResponse(BaseModel):
    """查询响应"""
    answer: str
    mode: str
    question: str


class IngestTextRequest(BaseModel):
    """文本摄入请求"""
    text: str = Field(..., description="要摄入的文本内容", min_length=1)


class IngestResponse(BaseModel):
    """摄入响应"""
    success: bool
    message: str
    filename: Optional[str] = None


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    rag_initialized: bool
    version: str = "1.0.0"


# ============================================
# FastAPI 应用
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("API 服务启动中...")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    
    # 预初始化 RAG 引擎 (可选，首次请求时也会初始化)
    try:
        await get_rag_engine()
        logger.info("RAG 引擎预初始化完成")
    except Exception as e:
        logger.warning(f"RAG 引擎预初始化失败 (将在首次请求时重试): {e}")
    
    yield
    
    # 关闭时
    logger.info("API 服务关闭中...")
    try:
        engine = await RAGEngine.get_instance()
        await engine.close()
    except Exception as e:
        logger.error(f"关闭 RAG 引擎失败: {e}")
    
    # 清理临时文件
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR, ignore_errors=True)


app = FastAPI(
    title="GlyphKeeper API",
    description="LightRAG 知识库 API 服务",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# API 端点
# ============================================

@app.get("/health", response_model=HealthResponse, tags=["系统"])
async def health_check():
    """健康检查"""
    try:
        engine = await RAGEngine.get_instance()
        initialized = engine.is_initialized
    except:
        initialized = False
    
    return HealthResponse(
        status="healthy" if initialized else "degraded",
        rag_initialized=initialized
    )


@app.post("/query", response_model=QueryResponse, tags=["查询"])
async def query_knowledge(request: QueryRequest):
    """
    查询知识库
    
    - **question**: 用户问题
    - **mode**: 查询模式
        - `local`: 局部搜索，侧重实体关系
        - `global`: 全局搜索，侧重主题概念
        - `hybrid`: 混合模式 (推荐)
        - `mix`: 组合多种结果
        - `naive`: 朴素搜索
    - **top_k**: 返回的相关文档数量
    """
    try:
        agent = SearchAgent()
        result = await agent.query(
            question=request.question,
            mode=request.mode,
            top_k=request.top_k,
            prompt_template=request.prompt_template
        )
        
        return QueryResponse(
            answer=result.answer,
            mode=result.mode,
            question=result.question
        )
        
    except Exception as e:
        logger.error(f"查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/query", response_model=QueryResponse, tags=["查询"])
async def query_knowledge_get(
    q: str = Query(..., description="用户问题", min_length=1),
    mode: str = Query(default="hybrid", description="查询模式"),
    top_k: int = Query(default=60, description="返回数量", ge=1, le=200)
):
    """GET 方式查询知识库"""
    request = QueryRequest(question=q, mode=mode, top_k=top_k)
    return await query_knowledge(request)


@app.post("/upload", response_model=IngestResponse, tags=["摄入"])
async def upload_document(file: UploadFile = File(...)):
    """
    上传并摄入文档
    
    支持格式: .txt, .md, .pdf
    """
    # 检查文件类型
    suffix = Path(file.filename).suffix.lower()
    if suffix not in [".txt", ".md", ".pdf"]:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix}。支持: .txt, .md, .pdf"
        )
    
    # 保存临时文件
    temp_path = TEMP_DIR / f"upload_{file.filename}"
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # 摄入文件
        success = await ingest_file(temp_path)
        
        return IngestResponse(
            success=success,
            message="文档已成功摄入知识库" if success else "文档摄入失败",
            filename=file.filename
        )
        
    except Exception as e:
        logger.error(f"上传文件处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
    finally:
        # 清理临时文件
        if temp_path.exists():
            temp_path.unlink()


@app.post("/ingest/text", response_model=IngestResponse, tags=["摄入"])
async def ingest_text_endpoint(request: IngestTextRequest):
    """直接摄入文本内容"""
    try:
        success = await ingest_text(request.text)
        
        return IngestResponse(
            success=success,
            message="文本已成功摄入知识库" if success else "文本摄入失败"
        )
        
    except Exception as e:
        logger.error(f"文本摄入失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/modes", tags=["系统"])
async def list_query_modes():
    """列出所有可用的查询模式"""
    return {
        "modes": [
            {"name": "local", "description": "局部搜索，侧重实体关系，适合具体问题"},
            {"name": "global", "description": "全局搜索，侧重主题概念，适合概览性问题"},
            {"name": "hybrid", "description": "混合模式，平衡 local 和 global (推荐)"},
            {"name": "mix", "description": "组合多种结果，返回更全面的答案"},
            {"name": "naive", "description": "朴素搜索，简单快速"},
        ]
    }


@app.get("/templates", tags=["系统"])
async def list_prompt_templates():
    """列出所有可用的提示词模板"""
    return {"templates": list(SearchAgent.PROMPT_TEMPLATES.keys())}


# ============================================
# 启动函数
# ============================================

def run_server(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """
    启动 API 服务器
    
    Args:
        host: 监听地址
        port: 监听端口
        reload: 是否启用热重载
    """
    import uvicorn
    
    settings = get_settings()
    
    # 从配置读取服务器设置
    api_config = getattr(settings, 'api_server', None)
    if api_config:
        host = api_config.get('host', host)
        port = api_config.get('port', port)
        reload = api_config.get('reload', reload)
    
    logger.info(f"启动 API 服务器: http://{host}:{port}")
    
    uvicorn.run(
        "src.interfaces.api_server:app",
        host=host,
        port=port,
        reload=reload
    )


if __name__ == "__main__":
    run_server()
