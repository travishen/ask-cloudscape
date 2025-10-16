# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, List
import os, sqlite3, textwrap, re, hashlib, logging

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

# ---------- Config ----------
DB_PATH = os.getenv("DB_PATH", "build/index.db")
MAX_PREVIEW = int(os.getenv("MAX_PREVIEW_CHARS", "220"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cloudscape-mcp")

# ---------- MCP ----------
mcp = FastMCP(
    "cloudscape",
    instructions="Tools for AWS Cloudscape UI RAG: search buckets and fetch page content."
)

# ---------- DB helpers ----------
_db: sqlite3.Connection | None = None
def db() -> sqlite3.Connection:
    global _db
    if _db is None:
        if not os.path.exists(DB_PATH):
            raise RuntimeError(f"DB not found: {DB_PATH}. Run `make index` or mount the DB.")
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
    return _db

def _short(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return textwrap.shorten(s, width=MAX_PREVIEW, placeholder=" …")

def _sha10(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:10]

def _bucket(url: str) -> str:
    u = url.lower()
    if "cloudscape.design/components/" in u and "tabid=api" in u:   return "components.api"
    if "cloudscape.design/components/" in u and "tabid=usage" in u: return "components.usage"
    if "cloudscape.design/patterns/" in u:                          return "patterns"
    if u.startswith("typedoc://"):                                  return "typedoc"
    return "other"

def _fts(q: str, limit: int) -> List[sqlite3.Row]:
    sql = """
    SELECT p.id, p.url, p.title, p.text, bm25(pages_fts) AS rank
    FROM pages_fts
    JOIN pages p ON p.rowid = pages_fts.rowid
    WHERE pages_fts MATCH ?
    ORDER BY rank ASC
    LIMIT ?
    """
    return list(db().execute(sql, (q, limit)))

# ---------- MCP tools ----------
@mcp.tool()
def search(q: str, k_components: int = 1, k_patterns: int = 5, k_typedoc: int = 3) -> Dict[str, Any]:
    superset = _fts(q, limit=max(50, k_components*6 + k_patterns*6 + k_typedoc*6))
    buckets = {"components.api": [], "components.usage": [], "patterns": [], "typedoc": []}
    for r in superset:
        hit = {"url": r["url"], "title": r["title"], "text_preview": _short(r["text"]), "text_len": len(r["text"] or "")}
        b = _bucket(hit["url"])
        if b in buckets:
            buckets[b].append(hit)

    def top(xs, k): return xs[:max(0, k)] if k >= 0 else []
    return {
        "query": q,
        "components": {
            "api":   top(buckets["components.api"],   k_components),
            "usage": top(buckets["components.usage"], k_components),
        },
        "patterns": top(buckets["patterns"], k_patterns),
        "typedoc":  top(buckets["typedoc"],  k_typedoc),
        "used_rag": True,
        "pack_id": _sha10(q),
    }

@mcp.tool()
def page(url: str) -> Dict[str, Any]:
    row = db().execute("SELECT id,url,title,text FROM pages WHERE url=? LIMIT 1", (url,)).fetchone()
    if not row and url.startswith("typedoc://"):
        row = db().execute("SELECT id,url,title,text FROM pages WHERE url=? LIMIT 1", (url.replace("typedoc://",""),)).fetchone()
    if not row:
        return {"error": "NOT_FOUND", "url": url, "used_rag": True}
    return {"id": row["id"], "url": row["url"], "title": row["title"], "text": row["text"], "used_rag": True, "pack_id": _sha10(row["url"])}

# ---------- ASGI (SSE MCP) ----------
async def health(_request):
    ok = os.path.exists(DB_PATH)
    return JSONResponse({"ok": ok, "db_path": DB_PATH})

# Build FastMCP's SSE app with its default endpoints:
#   GET  /sse            (event stream)
#   POST /messages/      (backchannel)
sse_app = mcp.sse_app()  # no custom paths => defaults to /sse and /messages/

# Compose the parent Starlette app.
# Mount SSE app at ROOT so its own routes (/sse, /messages/) are exact.
app = Starlette()
app.mount("/", sse_app)
app.add_route("/health", health, methods=["GET"])

# CORS so MCP Inspector (browser) can preflight/connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],                 # tighten if you need
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))
