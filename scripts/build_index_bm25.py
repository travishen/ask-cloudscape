#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Page-level BM25 indexer for Cloudscape:
- Reads pages.jsonl + extraPages.jsonl from a .wacz file or extracted dir
- Components: keep only tabId=api|usage (drop playground/testing/example)
- Patterns: keep all pages under /patterns/**
- TypeDoc: optionally ingest Markdown from a directory (--typedoc)
- Stores one row per canonical URL in `pages`, and indexes text into FTS5 `pages_fts`
"""

import argparse, json, zipfile, sqlite3, sys, re
from pathlib import Path
from typing import List, Dict, Iterable, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from tqdm import tqdm

# ---------- URL canonicalization (for website pages) ----------
def canonicalize_url(url: str) -> Tuple[str, str, bool]:
    """
    Return (canonical_url, section, keep)
      section ∈ {"components_api","components_usage","components_other","patterns","typedoc","other"}
      keep: whether this URL should be kept (False means drop)
    """
    try:
        u = urlparse(url)
    except Exception:
        return url, "other", True

    host = u.netloc or ""
    path = u.path or ""
    query = u.query or ""
    if "cloudscape.design" not in host:
        return url, "other", True

    q = dict(parse_qsl(query, keep_blank_values=True))
    # Components
    if path.startswith("/components/"):
        tab = (q.get("tabId") or "").lower()
        if "example" in q or tab in {"playground", "testing"}:
            return url, "components_other", False  # drop noisy variants
        if tab in {"api", "usage"}:
            canon = urlunparse((u.scheme, host, path, "", urlencode({"tabId": tab}), ""))
            return canon, f"components_{tab}", True
        # overview (no/other tab)
        canon = urlunparse((u.scheme, host, path, "", "", ""))
        return canon, "components_other", True

    # Patterns
    if path.startswith("/patterns/"):
        canon = urlunparse((u.scheme, host, path, "", query, ""))
        return canon, "patterns", True

    # Others
    return url, "other", True

# ---------- JSONL helpers ----------
def _load_jsonl_lines(lines: Iterable[str], out: List[Dict]):
    for line in lines:
        try:
            if isinstance(line, (bytes, bytearray)):
                line = line.decode("utf-8", errors="ignore")
            obj = json.loads(line)
            if "url" in obj:
                out.append(obj)
        except Exception:
            continue

def read_pages_from_wacz(src: Path) -> List[Dict]:
    pages: List[Dict] = []
    targets = ["pages/pages.jsonl", "pages/extraPages.jsonl"]
    with zipfile.ZipFile(src, "r") as z:
        found_any = False
        for name in targets:
            try:
                with z.open(name) as f:
                    _load_jsonl_lines(f, pages)
                    found_any = True
            except KeyError:
                continue
        if not found_any:
            raise FileNotFoundError(f"{src} 缺少 pages.jsonl/extraPages.jsonl（抓站需帶 --text --generateWACZ）")
    return pages

def read_pages_from_dir(src: Path) -> List[Dict]:
    pages: List[Dict] = []
    found_any = False
    for name in ["pages/pages.jsonl", "pages/extraPages.jsonl"]:
        p = src / name
        if p.exists():
            with p.open("r", encoding="utf-8", errors="ignore") as f:
                _load_jsonl_lines(f, pages)
                found_any = True
    if not found_any:
        raise FileNotFoundError(f"{src} 缺少 pages.jsonl/extraPages.jsonl（抓站需帶 --text）")
    return pages

# ---------- TypeDoc ingestion ----------
def read_typedoc_md(root: Path) -> List[Dict]:
    """Return list of dicts: {url,title,text,section='typedoc'} from Markdown tree."""
    docs: List[Dict] = []
    if not root or not root.exists():
        return docs
    for p in root.rglob("*.md"):
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
            url = f"typedoc://{p.relative_to(root).as_posix()}"
            title = p.stem.replace("-", " ").title()
            docs.append({"url": url, "title": f"TypeDoc: {title}", "text": txt, "section": "typedoc"})
        except Exception:
            continue
    return docs

# ---------- cleaning ----------
COOKIE_BLOCK_RE = re.compile(
    r"(?:We use cookies|Select your cookie preferences|Customize cookie preferences|AWS Cookie Notice)"
    r".{0,2000}?(?:Accept all|Save preferences|Cookie preferences)",
    re.I | re.S,
)

# 這段在 clean_text() 的最後加一個「從導航跳到正文」的邏輯
NAV_START_RE = re.compile(
    r"(?:^|\n)(?:Get\s*started|Foundation|Components|Patterns|Demos|GitHub)(?:[\s\S]{0,800})?"
    r"(?:\n\s*(?:On this section|Development guidelines|General guidelines|Properties|Usage)\b)",
    re.I,
)

def clean_text(text: str) -> str:
    if not text:
        return ""
    # 去 cookie
    text = re.sub(COOKIE_BLOCK_RE, " ", text)
    # 砍頁尾雜訊行（保留你原本的 FOOTER_LINE_RE 流程）
    ...
    # 壓縮空白
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    # 如果偵測到頂部的導航膠囊，直接把 preview 起點推進到第一個正文段
    m = NAV_START_RE.search(text)
    if m:
        text = text[m.end():].lstrip()

    return text

# ---------- SQLite ----------
def create_schema(db: sqlite3.Connection):
    db.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY,
            url TEXT UNIQUE,
            title TEXT,
            text TEXT,
            section TEXT
        );
    """)
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(text, content='');")
    db.commit()

def upsert_page(db: sqlite3.Connection, url: str, title: str, text: str, section: str) -> int:
    db.execute("INSERT OR REPLACE INTO pages(url, title, text, section) VALUES (?,?,?,?)",
               (url, title, text, section))
    rowid = db.execute("SELECT id FROM pages WHERE url=?", (url,)).fetchone()[0]
    db.execute("DELETE FROM pages_fts WHERE rowid=?", (rowid,))
    db.execute("INSERT INTO pages_fts(rowid, text) VALUES (?,?)", (rowid, text))
    return rowid

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wacz", required=True, help="Path to .wacz 或 WACZ 資料夾")
    ap.add_argument("--db", default="build/index.db", help="輸出 SQLite DB 路徑")
    ap.add_argument("--typedoc", default="", help="（可選）TypeDoc Markdown 根目錄")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    src = Path(args.wacz)
    typedoc_root = Path(args.typedoc) if args.typedoc else None
    out_path = Path(args.db)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(str(out_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL;")
    db.execute("PRAGMA synchronous=NORMAL;")
    create_schema(db)

    # Website pages from WACZ
    records = read_pages_from_dir(src) if src.is_dir() else read_pages_from_wacz(src)
    page_map: Dict[str, Dict] = {}
    stats = {"kept": 0, "dropped": 0, "components_api": 0, "components_usage": 0,
             "components_other": 0, "patterns": 0, "typedoc": 0, "other": 0}

    for rec in records:
        raw_url = (rec.get("url") or "").strip()
        title = (rec.get("title") or "").strip() or raw_url
        text = clean_text(rec.get("text") or "")
        if not raw_url or not text:
            continue

        canon, section, keep = canonicalize_url(raw_url)
        if not keep:
            stats["dropped"] += 1
            continue

        cur = page_map.get(canon)
        if (cur is None) or (len(text) > len(cur["text"])):
            page_map[canon] = {"url": canon, "title": title, "text": text, "section": section}

    # Optional: TypeDoc pages (stored as typedoc:// URLs)
    if typedoc_root:
        for d in read_typedoc_md(typedoc_root):
            url = d["url"]
            cur = page_map.get(url)
            if (cur is None) or (len(d["text"]) > len(cur["text"])):
                page_map[url] = d

    # write
    total_pages = 0
    for d in tqdm(page_map.values(), desc="Index pages"):
        upsert_page(db, d["url"], d["title"], d["text"], d["section"])
        total_pages += 1
        stats[d["section"]] = stats.get(d["section"], 0) + 1
        stats["kept"] += 1
    db.commit()

    try:
        db.execute("INSERT INTO pages_fts(pages_fts) VALUES ('optimize');")
        db.commit()
    except sqlite3.DatabaseError:
        pass

    if args.verbose:
        for k in ["kept","dropped","components_api","components_usage","components_other","patterns","typedoc","other"]:
            print(f"[stats] {k}: {stats.get(k,0)}")
        print(f"[stats] total rows in pages: {db.execute('SELECT COUNT(*) FROM pages').fetchone()[0]}")

    print(f"Indexed {total_pages} pages -> {out_path}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
