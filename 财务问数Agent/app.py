import threading
import secrets
import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent.settings import Settings, ROOT
from agent.plan_skill import PlanInput
from agent.engine import Agent
from agent.streaming import EventStream
from agent.tail_workflow import TailWorkflow

app=FastAPI(title="楼栋财务问数",docs_url=None,redoc_url=None,openapi_url=None)
_agent=None
_lock=threading.Lock()


def get_agent():
    global _agent
    with _lock:
        if _agent is None:_agent=Agent(Settings.load())
        return _agent


@app.middleware("http")
async def local_only(request:Request,call_next):
    host=request.url.hostname
    if host not in ("127.0.0.1","localhost","testserver"):
        return JSONResponse({"detail":"本地服务仅允许localhost访问"},status_code=403)
    origin=request.headers.get("origin")
    if origin and (urlparse(origin).netloc!=request.url.netloc or urlparse(origin).scheme!=request.url.scheme):
        return JSONResponse({"detail":"不允许跨站请求"},status_code=403)
    try:size=int(request.headers.get("content-length","0"))
    except ValueError:return JSONResponse({"detail":"请求长度无效"},status_code=400)
    if size>16000:
        return JSONResponse({"detail":"问题过长"},status_code=413)
    owner=request.cookies.get('finance_owner','')
    if not re.fullmatch(r'[0-9a-f]{64}',owner):owner=secrets.token_hex(32)
    request.state.memory_user=hashlib.sha256(owner.encode()).hexdigest()
    response=await call_next(request)
    response.set_cookie('finance_owner',owner,httponly=True,samesite='strict',max_age=604800,secure=False)
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Cache-Control"]="no-store"
    response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"
    return response


class ChatRequest(BaseModel):
    message:str=Field(default="",max_length=2000)
    project:str=Field(default="",max_length=150)
    building:str=Field(default="",max_length=150)
    session_id:str|None=Field(default=None,max_length=64)
    selection:str|None=Field(default=None,max_length=100)
    scope_type:str=Field(default='auto',pattern='^(auto|project|building|contract|document)$')
    entity:str=Field(default='',max_length=200)
    request_id:str|None=Field(default=None,max_length=64,pattern='^[A-Za-z0-9_-]+$')
    state_version:int|None=Field(default=None,ge=0)


@app.get("/")
def index():return FileResponse(ROOT/"web/index.html")


@app.get("/assets/{name}")
def asset(name:str):
    if name not in ("app.js","v2.js","sse.js","style.css","plan.js"):raise HTTPException(404)
    return FileResponse(ROOT/"web"/name)


@app.get("/api/status")
def status():
    try:
        a=get_agent()
        return {"ok":True,"binding":a.db.verify_binding(),"model_configured":a.model.enabled,
                "mode":"受控解析＋参数化SQL","model":a.settings.model,"engine":"初级财务查询Agent：需求状态机＋持久会话记忆",
                "memory":"SQLite结构化会话；最近6轮；24小时活跃有效期；无长期偏好库",
                "skills":[a.plan_skills.catalog()],"rules":len(a.rules.documents),"rule_sha256":a.rules.digest,
                "vector_store":"SQLite + 本地字符TF-IDF余弦检索（非语义Embedding）"}
    except Exception:
        return JSONResponse({"ok":False,"detail":"数据库绑定校验未通过，请检查Agent绑定配置、正式入口和验收指纹。"},status_code=503)


@app.post("/api/chat")
def chat(request:ChatRequest,http_request:Request):
    try:return get_agent().chat(dict(request.model_dump(),_user_id=http_request.state.memory_user))
    except ValueError as exc:raise HTTPException(400,detail=str(exc)) from None
    except Exception:raise HTTPException(503,detail="服务初始化失败，请检查配置与数据库验收记录。") from None


@app.get('/api/sessions/{session_id}')
def session_state(session_id:str,http_request:Request):
    try:
        a=get_agent();binding=a.db.verify_binding()
        state=a.memory.load(http_request.state.memory_user,session_id,binding['database_sha256'])
        return {k:state[k] for k in ('session_id','slots','constraints','output_spec','pending_questions','version','status')}
    except ValueError:raise HTTPException(404,detail='会话不存在或已过期，请新建对话。') from None


@app.post('/api/chat/stream')
def chat_stream(request:ChatRequest,http_request:Request):
    stream=EventStream(get_agent,dict(request.model_dump(),_user_id=http_request.state.memory_user))
    return StreamingResponse(stream.iterate(),media_type='text/event-stream',
                             headers={'Cache-Control':'no-cache, no-store','X-Accel-Buffering':'no'})


@app.get('/api/statements/{token}')
def download_statement(token:str):
    try:
        path=TailWorkflow().download(token)
        return FileResponse(path,filename='尾期对账单_待复核.xlsx',media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except (ValueError,OSError,KeyError):
        raise HTTPException(404,detail='文件不存在、未完成或校验未通过。') from None


@app.get('/api/plans/options')
def plan_options():
    a=get_agent()
    return {'skill':a.plan_skills.catalog(),'buildings':a.plan_skills.buildings()}


@app.post('/api/plans/check')
def plan_check(request:PlanInput):
    try:return get_agent().plan_skills.check(request.model_dump())
    except ValueError as exc:raise HTTPException(400,detail=str(exc)) from None
    except Exception:raise HTTPException(503,detail='计划核对未完成，没有自动重试。') from None


@app.get('/api/plans/tasks/{token}')
def plan_task(token:str):
    try:return get_agent().plan_skills.restore(token)
    except (ValueError,OSError,KeyError):raise HTTPException(404,detail='任务不存在或校验未通过。') from None
