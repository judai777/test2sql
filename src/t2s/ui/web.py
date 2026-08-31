"""Web UI（M6）：FastAPI 单页。ADR-005：本层零业务逻辑，只做 HTTP 编排与渲染。

- POST /api/ask：一次取数问答（含结果集/SQL/轨迹）
- GET  /api/audit：审计近表（PRD US-7）
- GET  /：单页（纯内嵌 HTML/CSS/JS，零 CDN 依赖；图表用 CSS 条形图）
- Web 端 ask_user 一期降级为"按假设执行并声明假设"（PRD 未决问题 #1 的 MVP 裁剪，见 CHG-009）

用法: PYTHONPATH=src python -m t2s.ui.web --port 8000
"""
from __future__ import annotations

import argparse

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel as PydBaseModel

from t2s.config import AppConfig
from t2s.executor import ReActEngine
from t2s.executor.delegate import CoderDelegation, DelegateSqlTask
from t2s.executor.skill_registry import load_skills
from t2s.llm import EmbeddingClient, LLMClient
from t2s.manager import MemoryService, RetrievalService, SessionSummarizer
from t2s.router import RouterService
from t2s.storage import AuditLog, MemoryStore, SessionStore, open_db
from t2s.tools import ToolContext, build_registry
from t2s.tools.mcp_bridge import build_mcp_backed_registry
from t2s.tools.mcp_server import build_db_mcp_server
from t2s.tools.metadata import TABLES


class AskIn(PydBaseModel):
    question: str
    session_id: str = "web"


class SaveResultIn(PydBaseModel):
    title: str
    question: str
    sql: str
    columns: list
    rows: list


_WEB_ASK_USER = ("（Web 端暂不支持交互澄清）请基于最常见的业务口径执行，"
                 "并在回答开头显式声明所做假设。")


def build_stack(cfg: AppConfig):
    """组装双 agent 服务栈（ADR-008 D1）：data-analysis 主循环 + coder 子 agent（经 MCP）。"""
    llm = LLMClient(cfg.llm)
    skills = load_skills()
    conn = open_db(cfg.tools.memory_db_path)
    embedder = EmbeddingClient(cfg.embedding) if cfg.embedding.enabled else None
    store = MemoryStore(conn)
    retrieval = RetrievalService(tables=[(t.name, t.search_corpus) for t in TABLES],
                                 store=store, embedder=embedder)
    memory = MemoryService(store, embedder, retrieval=retrieval)

    # coder 专职 agent：MCP 服务端（绑定只读 ctx）→ 桥接注册中心 → 同一引擎底座
    server_ctx = ToolContext(db_path=cfg.tools.db_path,
                             sql_timeout_s=cfg.tools.sql_timeout_s,
                             sql_row_limit=cfg.tools.sql_row_limit)
    db_server = build_db_mcp_server(server_ctx)
    coder_registry = build_mcp_backed_registry(db_server)
    coder_engine = ReActEngine(llm, coder_registry,
                               system_prompt=skills["coder"].system_prompt)
    delegation = CoderDelegation(coder_engine, server_ctx, skills["coder"])

    registry = build_registry()
    registry.register(DelegateSqlTask(delegation))
    engine = ReActEngine(llm, registry, system_prompt=skills["data-analysis"].system_prompt)
    summarizer = SessionSummarizer(llm, SessionStore(conn), async_mode=True)
    service = RouterService(llm, engine, SessionStore(conn), AuditLog(conn),
                            memory=memory, summarizer=summarizer)
    ctx = ToolContext(db_path=cfg.tools.db_path,
                      sql_timeout_s=cfg.tools.sql_timeout_s,
                      sql_row_limit=cfg.tools.sql_row_limit,
                      ask_user=lambda q, o: _WEB_ASK_USER,
                      retriever=retrieval)
    return service, ctx


def create_app(cfg: AppConfig | None = None, service=None, ctx=None) -> FastAPI:
    cfg = cfg or AppConfig.load()
    if service is None or ctx is None:
        service, ctx = build_stack(cfg)

    app = FastAPI(title="Test2SQL", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGE

    @app.post("/api/ask")
    def ask(body: AskIn) -> JSONResponse:
        answer = service.handle(body.question, session_id=body.session_id, user_id="web", ctx=ctx)
        return JSONResponse({
            "question": body.question,
            "content": answer.content,
            "sql": answer.sql,
            "row_count": answer.row_count,
            "result": answer.result,
            "stop_reason": answer.stop_reason,
            "steps": len(answer.steps),
            "total_tokens": answer.total_tokens,
            "elapsed_ms": answer.elapsed_ms,
        })

    @app.get("/api/audit")
    def audit(limit: int = 20) -> JSONResponse:
        return JSONResponse([e.model_dump() for e in service.audit.recent(limit)])

    # ---------- 记忆管理（M8 确认制 + 结果表格记忆） ----------

    @app.get("/api/memory/candidates")
    def candidates() -> JSONResponse:
        if service.memory is None:
            return JSONResponse([])
        return JSONResponse([p.model_dump() for p in service.memory.candidate_pairs()])

    @app.post("/api/memory/confirm")
    def confirm(body: dict) -> JSONResponse:
        ok = service.memory.confirm_pair(int(body["pair_id"])) if service.memory else False
        return JSONResponse({"ok": ok})

    @app.post("/api/memory/save-result")
    def save_result(body: SaveResultIn) -> JSONResponse:
        if service.memory is None:
            return JSONResponse({"ok": False})
        rid = service.memory.save_result(body.title, body.question, body.sql,
                                         body.columns, body.rows)
        return JSONResponse({"ok": True, "id": rid})

    return app


_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>Test2SQL · 自然语言取数</title>
<style>
  :root { --line:#e3e7ee; --ink:#1c2330; --sub:#66707f; --brand:#2456d6; }
  * { box-sizing:border-box; margin:0; }
  body { font:15px/1.6 "Segoe UI","Microsoft YaHei",sans-serif; color:var(--ink);
         background:#f4f6fa; max-width:880px; margin:0 auto; padding:28px 20px 60px; }
  h1 { font-size:22px; margin-bottom:4px; } .sub { color:var(--sub); font-size:13px; margin-bottom:20px; }
  .bar { display:flex; gap:10px; } input { flex:1; padding:11px 14px; border:1px solid var(--line);
         border-radius:9px; font-size:15px; } button { padding:11px 22px; border:0; border-radius:9px;
         background:var(--brand); color:#fff; font-size:15px; cursor:pointer; } button:disabled { opacity:.55; }
  .card { background:#fff; border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-top:16px; }
  .meta { color:var(--sub); font-size:12.5px; margin-top:10px; }
  .answer { white-space:pre-wrap; }
  .sql { background:#0f1626; color:#d7e3ff; padding:12px 14px; border-radius:8px;
         font:12.5px/1.5 Consolas,monospace; overflow-x:auto; margin-top:12px; white-space:pre-wrap; }
  table { border-collapse:collapse; margin-top:12px; font-size:13.5px; width:100%; }
  th,td { border:1px solid var(--line); padding:6px 10px; text-align:left; }
  th { background:#f0f3f9; }
  .chart { margin-top:14px; } .row { display:flex; align-items:center; margin:5px 0; gap:8px; }
  .lbl { width:170px; font-size:12.5px; color:var(--sub); text-align:right;
         overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .track { flex:1; background:#eef1f7; border-radius:5px; height:19px; }
  .fill { height:19px; border-radius:5px; background:linear-gradient(90deg,#4a7bf7,#2456d6);
          min-width:2px; } .val { font-size:12px; color:var(--sub); width:90px; }
  .badge { display:inline-block; padding:2px 9px; border-radius:20px; font-size:12px;
           background:#e8f0ff; color:var(--brand); margin-left:8px; }
  .err { color:#b42318; } .save { border:1px solid var(--line); background:#fff;
         border-radius:6px; padding:2px 10px; font-size:12px; cursor:pointer; margin-left:8px; }
</style></head><body>
<h1>Test2SQL <span class="badge" id="badge">就绪</span></h1>
<div class="sub">证券业务自然语言取数 · 只读 · 全链路审计</div>
<div class="bar"><input id="q" placeholder="例：上个月各营业部的成交额排行" autofocus>
<button id="go">查询</button></div>
<div class="card" id="out" style="display:none">
  <div class="answer" id="content"></div>
  <div class="sql" id="sql" style="display:none"></div>
  <table id="table" style="display:none"></table>
  <div class="chart" id="chart"></div>
  <div class="meta" id="meta"></div>
</div>
<script>
const $ = id => document.getElementById(id);
let lastData = null;
function esc(s){ const d=document.createElement('div'); d.textContent=s==null?'':String(s); return d.innerHTML; }
function render(data){
  lastData = data;
  $('out').style.display='block';
  $('content').innerHTML = '<div class="'+(data.stop_reason.startsWith('blocked')||data.stop_reason==='error'?'err':'')+'">'+esc(data.content)+'</div>';
  $('sql').style.display = data.sql ? 'block':'none'; $('sql').textContent = data.sql||'';
  const res = data.result, tb = $('table'), chart = $('chart');
  tb.style.display='none'; chart.innerHTML='';
  if (res && res.columns && res.columns.length && res.rows && res.rows.length){
    tb.style.display='table';
    tb.innerHTML = '<tr>'+res.columns.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr>'
      + res.rows.slice(0,20).map(r=>'<tr>'+r.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('');
    if (res.columns.length>=2 && res.rows.every(r=>typeof r[1]==='number')){
      const rows = res.rows.slice(0,12);
      const max = Math.max(...rows.map(r=>Math.abs(r[1])))||1;
      chart.innerHTML = '<div style="font-size:12.5px;color:var(--sub);margin-bottom:6px">图表（前 12 行）</div>'
        + rows.map(r=>'<div class="row"><div class="lbl" title="'+esc(r[0])+'">'+esc(r[0])+'</div>'
          +'<div class="track"><div class="fill" style="width:'+(Math.abs(r[1])/max*100).toFixed(1)+'%"></div></div>'
          +'<div class="val">'+esc(r[1])+'</div></div>').join('');
    }
  }
  $('meta').innerHTML = 'stop='+data.stop_reason+' · 步数 '+data.steps+' · tokens '+data.total_tokens
    +' · '+(data.elapsed_ms/1000).toFixed(1)+'s'+(data.row_count!=null?' · 返回 '+data.row_count+' 行':'')
    +(data.sql&&data.result?' <button class="save" onclick="saveResult()">💾 保存此结果到记忆</button>':'');
}
async function saveResult(){
  if(!lastData||!lastData.sql) return;
  await fetch('/api/memory/save-result',{method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({title:(lastData.question||'').slice(0,40),question:lastData.question||'',
      sql:lastData.sql,columns:(lastData.result&&lastData.result.columns)||[],rows:(lastData.result&&lastData.result.rows)||[]})});
  $('badge').textContent='已存入记忆 ✓'; setTimeout(()=>$('badge').textContent='就绪',2000);
}
async function ask(){
  const q = $('q').value.trim(); if(!q) return;
  $('go').disabled = true; $('badge').textContent = '查询中…';
  try {
    const r = await fetch('/api/ask', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question:q, session_id:'web-'+Date.now()%100000})});
    render(await r.json());
  } catch(e){ $('out').style.display='block'; $('content').innerHTML = '<span class="err">请求失败：'+esc(e)+'</span>'; }
  $('go').disabled = false; $('badge').textContent = '就绪';
}
$('go').onclick = ask;
$('q').addEventListener('keydown', e=>{ if(e.key==='Enter') ask(); });
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Test2SQL Web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    cfg = AppConfig.load()
    if not cfg.llm.api_key:
        print("未配置 T2S_LLM_API_KEY：复制 .env.example 为 .env 并填入真实 key。")
        return 1
    import uvicorn
    uvicorn.run(create_app(cfg), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
