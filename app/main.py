# -*- coding: utf-8 -*-
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import os, sqlite3, re
from urllib.parse import urlparse, parse_qs

DB_PATH = os.environ.get("DB_PATH", "index.db")
app = FastAPI(title="Cloudscape RAG (BM25-only, page-level + typedoc)")

_db = None
def get_db() -> sqlite3.Connection:
    """Singleton SQLite connection with row dicts."""
    global _db
    if _db is None:
        if not os.path.exists(DB_PATH):
            raise RuntimeError(f"DB not found at {DB_PATH}. Did you build the index?")
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
    return _db


# -------------------- Models --------------------
class SearchBody(BaseModel):
    q: str
    # components.api 與 components.usage 各取 Top-K
    k_components: int = 1
    # patterns 取 Top-K
    k_patterns: int = 5
    # typedoc 取 Top-K
    k_typedoc: int = 3


# -------------------- Utilities --------------------
SECTION_ANCHORS = re.compile(
    r"\n\s*(Properties|API|Usage|Development guidelines|Guidelines|Overview)\s*\n",
    re.I,
)

def make_preview(text: str, query: str, size: int = 800, url: str | None = None) -> str:
    """
    產生貼近重點的預覽：
      1) 若為 components 的 API/Usage 頁，優先定位到章節標題 (Properties/Usage/Guidelines)
      2) 否則以第一個關鍵詞為中心裁切
      3) 仍無法命中則回傳前 size 字
    並將第一個關鍵詞以 **bold** 標出，方便 Agent 快速掃描。
    """
    if not text:
        return ""

    # 1) Components 的 API/Usage 頁，先錨到章節區域，避開頁首導覽
    if url:
        try:
            u = urlparse(url)
            tab = (parse_qs(u.query or "").get("tabId", [""])[0] or "").lower()
            if u.path.startswith("/components/") and tab in {"api", "usage"}:
                m = SECTION_ANCHORS.search(text)
                if m:
                    i = m.start()
                    half = size // 2
                    start = max(0, i - half)
                    end = min(len(text), start + size)
                    chunk = text[start:end]
                    # 同時嘗試把關鍵詞加粗
                    key_pat = _first_keyword_pattern(query)
                    if key_pat:
                        chunk = key_pat.sub(lambda x: f"**{x.group(0)}**", chunk)
                    return chunk
        except Exception:
            pass

    # 2) 關鍵詞貼齊
    key_pat = _first_keyword_pattern(query)
    if key_pat:
        m2 = key_pat.search(text)
        if m2:
            i = m2.start()
            half = size // 2
            start = max(0, i - half)
            end = min(len(text), start + size)
            chunk = text[start:end]
            return key_pat.sub(lambda x: f"**{x.group(0)}**", chunk)

    # 3) 回退：前 size 字
    return text[:size]


def _first_keyword_pattern(query: str):
    """從 query 中擷取一個可用關鍵詞並回傳不分大小寫的 regex pattern；若無則回 None。"""
    q = (query or "").strip()
    if not q:
        return None
    m = re.search(r'([A-Za-z0-9][A-Za-z0-9\-_/]+)', q)
    key = (m.group(1) if m else q).strip()
    if not key:
        return None
    try:
        return re.compile(re.escape(key), re.I)
    except re.error:
        return None


def _norm_scores(rows, query: str):
    """將同一桶的 bm25 分數正規化為 0..1，並生成貼近關鍵字/章節的預覽片段。"""
    if not rows:
        return []
    ranks = [float(r["rank"]) for r in rows]
    mn, mx = min(ranks), max(ranks)

    def norm(x: float) -> float:
        if mx - mn < 1e-9:
            return 1.0
        return 1.0 - (x - mn) / (mx - mn)

    out = []
    for r in rows:
        text = r["text"] or ""
        out.append({
            "id": int(r["id"]),
            "url": r["url"],
            "title": r["title"],
            "section": r["section"],
            "text_preview": make_preview(text, query, 1200, r["url"]),
            "text_len": len(text),
            "score": norm(float(r["rank"])),
            "bm25_raw": float(r["rank"]),
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


# -------------------- Endpoints --------------------
@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/")
def root():
    return {
        "service": "cloudscape-rag-bm25",
        "mode": "page-level",
        "buckets": ["components.api", "components.usage", "patterns", "typedoc"],
        "endpoints": ["/search", "/page?url=...", "/healthz"],
    }


@app.get("/page")
def get_page(url: str = Query(..., description="Exact URL to fetch full page text")):
    db = get_db()
    row = db.execute("SELECT id, url, title, text FROM pages WHERE url=?", (url,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": int(row["id"]), "url": row["url"], "title": row["title"], "text": row["text"]}


@app.post("/search")
def search(body: SearchBody):
    """
    預設策略（符合你的使用層級）：
      1) Components API：只查 section='components_api'，取最相關 k_components
      2) Components Usage：只查 section='components_usage'，取最相關 k_components
      3) Patterns：只查 section='patterns'，取最相關 k_patterns
      4) TypeDoc：只查 section='typedoc'，取最相關 k_typedoc
    備註：每桶各自正規化分數與排序；需要全文用 /page?url=...
    """
    db = get_db()

    # Components - API
    rows_api = db.execute(
        """
        SELECT p.id, p.url, p.title, p.text, p.section, bm25(pages_fts) AS rank
        FROM pages p
        JOIN pages_fts ON p.id = pages_fts.rowid
        WHERE pages_fts MATCH ? AND p.section='components_api'
        ORDER BY rank ASC
        LIMIT ?
        """,
        (body.q, int(body.k_components)),
    ).fetchall()

    # Components - Usage
    rows_usage = db.execute(
        """
        SELECT p.id, p.url, p.title, p.text, p.section, bm25(pages_fts) AS rank
        FROM pages p
        JOIN pages_fts ON p.id = pages_fts.rowid
        WHERE pages_fts MATCH ? AND p.section='components_usage'
        ORDER BY rank ASC
        LIMIT ?
        """,
        (body.q, int(body.k_components)),
    ).fetchall()

    # Patterns
    rows_patterns = db.execute(
        """
        SELECT p.id, p.url, p.title, p.text, p.section, bm25(pages_fts) AS rank
        FROM pages p
        JOIN pages_fts ON p.id = pages_fts.rowid
        WHERE pages_fts MATCH ? AND p.section='patterns'
        ORDER BY rank ASC
        LIMIT ?
        """,
        (body.q, int(body.k_patterns)),
    ).fetchall()

    # TypeDoc
    rows_typedoc = db.execute(
        """
        SELECT p.id, p.url, p.title, p.text, p.section, bm25(pages_fts) AS rank
        FROM pages p
        JOIN pages_fts ON p.id = pages_fts.rowid
        WHERE pages_fts MATCH ? AND p.section='typedoc'
        ORDER BY rank ASC
        LIMIT ?
        """,
        (body.q, int(body.k_typedoc)),
    ).fetchall()

    return {
        "query": body.q,
        "components": {
            "api": _norm_scores(rows_api, body.q),
            "usage": _norm_scores(rows_usage, body.q),
        },
        "patterns": _norm_scores(rows_patterns, body.q),
        "typedoc": _norm_scores(rows_typedoc, body.q),
    }
