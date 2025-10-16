# Cloudscape RAG Server — BM25 / Runtime-Offline

An **offline-deployable** Cloudscape documentation retrieval service:

Browsertrix crawl → Generate WACZ → (Optional) Generate TypeDoc Markdown with Node/Docker → **Page-level** SQLite FTS5 index (BM25) → MCP HTTP server for querying.

* Runs **fully offline** (no model required at query time).
* Results are grouped into four **buckets**: `components.api`, `components.usage`, `patterns`, `typedoc`.
* Each bucket is ranked separately using FTS5 `bm25()`, then **normalized** to `[0,1]` (where `1` is best).

---

## Quick Start

### 0) Requirements

* Docker (for Browsertrix, TypeDoc, and server build)
* Python 3.10+

### 1) Crawl (requires internet access)

```bash
make crawl
```

Output: `data/wacz/collections/cloudscape/<timestamp>.wacz`
(Generated with `--text` and `--generateWACZ`, containing `pages/pages.jsonl` and `pages/extraPages.jsonl`.)

> To use a fixed filename, copy the latest WACZ to `data/wacz/cloudscape.wacz` after generation.

### 2) Generate TypeDoc Markdown (optional but recommended)

Converts npm packages (default: `@cloudscape-design/components`) into Markdown, included in the index:

```bash
make typedoc
```

Output: `data/typedoc_md/**`

### 3) Build the Index (offline)

```bash
make index
```

* Reads from `data/wacz/.../cloudscape.wacz` (or timestamped file) and `data/typedoc_md/**`
* Produces `build/index.db` (SQLite + FTS5)

### 4) Run the MCP HTTP Server (offline)

```bash
make build
make run
# Default: http://localhost:8000
```

---

## API

* `GET /healthz` → `"ok"`
* `GET /page?url=<exact-url>` → Returns full page (`id/url/title/text`)
* `POST /search`

  * **Request Body:**

    ```json
    {
      "q": "Flashbar",
      "k_components": 1,   // API & Usage results (default: 1 each)
      "k_patterns": 5,     // Patterns (default: 5)
      "k_typedoc": 3       // TypeDoc (default: 3)
    }
    ```

  * **Response (excerpt):**

    ```json
    {
      "query": "Flashbar",
      "components": {
        "api":    [ { "url": "...?tabId=api", "score": 1.0, "text_preview": "...", "text_len": 7784 } ],
        "usage":  [ { "url": "...?tabId=usage", "score": 1.0, "text_preview": "...", "text_len": 9644 } ]
      },
      "patterns": [ { "url": "https://cloudscape.design/patterns/...", "score": 0.73, "text_preview": "..." } ],
      "typedoc":  [ { "url": "typedoc://cloudscape-design__components/...", "score": 0.92, "text_preview": "..." } ]
    }
    ```

> **Tip:** For AI agents, a good default is `k_components=1`, `k_patterns=3`, `k_typedoc=2` for a balanced amount of context.

---

## cURL Examples

### Query “Flashbar” (default: API/Usage = 1, Patterns = 5, TypeDoc = 3)

```bash
curl -s -X POST http://localhost:8000/search \
  -H 'content-type: application/json' \
  -d '{"q":"Flashbar"}' | jq
```

### Only Components (disable Patterns/TypeDoc)

```bash
curl -s -X POST http://localhost:8000/search \
  -H 'content-type: application/json' \
  -d '{"q":"Flashbar", "k_components":2, "k_patterns":0, "k_typedoc":0}' | jq
```

### Phrase Search (FTS5 MATCH supports quotes and AND/OR)

```bash
curl -s -X POST http://localhost:8000/search \
  -H 'content-type: application/json' \
  -d '{"q":"\"status indicator\" AND color", "k_components":1, "k_patterns":3, "k_typedoc":0}' | jq
```

### Get full page（use /page?url=...）

```bash
curl -s "http://localhost:8000/page?url=https://cloudscape.design/components/flashbar/?tabId=api" \
  | jq
```

---

## Common Makefile Targets

* `make crawl` → Crawl with Browsertrix, output WACZ (`pages.jsonl`, `extraPages.jsonl`)
* `make typedoc` → Generate TypeDoc Markdown via Node/Docker (`data/typedoc_md/**`)
* `make index` → Build **page-level** SQLite FTS5 index from WACZ + Markdown
* `make build` → Build Docker image with MCP server
* `make run` → Run MCP HTTP server with `build/index.db`

---

## Computation Principles & Indexing Rules

1. **Page-Level Indexing**

   * Indexes whole pages from Cloudscape + TypeDoc Markdown (not fragmented by sentence/paragraph).
   * Agent-friendly: one hit = one page of context.

2. **URL Normalization & Buckets**

   * `components.api`: `.../components/<name>/?tabId=api`
   * `components.usage`: `.../components/<name>/?tabId=usage`
   * `patterns`: `.../patterns/**` (no tab split)
   * `typedoc`: From npm packages like `@cloudscape-design/components` (`typedoc://...`)

   > Noise pages (e.g., `tabId=playground/testing` or `?example=`) are **excluded**.

3. **Content Cleaning (Denoise)**

   * Removes cookie banners, footers, long sidebar menus.
   * Previews prioritize **Properties / Usage / Guidelines**.

4. **BM25 Ranking & Normalization**

   * FTS5 `bm25()` (lower = better).
   * Min-max normalized per bucket to `[0,1]`, exposed as `score` (higher = better).

5. **Previews**

   * `/search` returns `text_preview` aligned to keywords/section headers (with highlights).
   * For full text, use `/page?url=...`.

---

## Configuration

Optional `.env`:

```ini
SEEDS="https://cloudscape.design/get-started/ https://cloudscape.design/components/ https://cloudscape.design/patterns/"
COLLECTION=cloudscape
OUT_DIR=data/wacz
NODE_IMAGE=node:20-bookworm-slim   ; # Used by TypeDoc container
```

---

## Troubleshooting

* Empty `/search` results → Ensure `build/index.db` exists with `pages` / `pages_fts`, and `--text` was used during crawl.
* Previews still include noise → Re-run `make index` (latest indexer improves cleaning).
* TypeDoc too noisy → Manually ignore very short `.md` files or reduce `k_typedoc`.

---

## Suggested Prompt for AI Agents

> You are a **Cloudscape Design System assistant**. When the user asks about a component or pattern:
>
> 1. Call `POST /search` with `{"q": "<user query>", "k_components": 1, "k_patterns": 3, "k_typedoc": 2}`.
> 2. Start with `components.api[0]` and `components.usage[0]` `text_preview`. Fetch full text via `/page?url=...` if needed.
> 3. For usage guidance, best practices, or UX guidelines, check the `patterns` bucket.
> 4. For precise types/interfaces/events, check the `typedoc` bucket.
> 5. When composing answers:
>    * Cite sources using the returned `url`.
>    * Integrate in the order **API → Usage → Patterns → TypeDoc**.
>    * Use only retrieved content—no speculation.
>    * Deduplicate redundant content, keeping the most specific/operational.
>    * If no exact match, state “Not explicitly described in Cloudscape documentation” and provide closest reference.
> 6. End with a “References” list of 1–4 URLs.
>
> Queries may use keywords or phrases (e.g., `"Flashbar"`, `"\"status indicator\" AND color"`). If results are too broad, start with `components.api` and `usage`; expand to `patterns` if more context is needed.

