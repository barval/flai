# Web Search, Reading and Crawling

## 🌐 Tavily web search (per-user key)

Web search has always gone through the self-hosted **SearXNG** metasearch engine, which is free but limited: its snippets are short, popular sites dominate the results and it is easy to hit engine rate limits. Starting with v12.4 every user can attach their own **Tavily** API key — a purpose-built AI search API whose free tier grants **1000 credits per month** (a basic search costs 1 credit), returns clean, content-rich snippets and answers niche queries (repositories, documentation, price lookups) noticeably better than a generic metasearch.

**How to enable it:**

1. Create a free account at [app.tavily.com](https://app.tavily.com/home) and copy the API key (`tvly-…`).
2. In FLAI, click your **name in the header** → the profile popup opens.
3. In the «Internet search via Tavily» section paste the key and click **Add key**. The key is verified against Tavily's `/usage` endpoint before it is stored — an invalid key is rejected and never saved.
4. The popup then shows the key mask (`tvly-…xxxx`), the plan limit, the credits used this month and the credits left. A key can be deleted at any time with **Delete key**; only one key per user is allowed, so adding a second one requires deleting the first.

**How it works afterwards:**

- Every web search (router category `[-SEARCH-]`, the `web_search` tool during chat, deep analysis fetches and `/v1` chat) now queries **Tavily first** and falls back to the local SearXNG engine automatically whenever the key is missing, the provider is disabled, the key is rejected, the monthly quota is exhausted or the service is unavailable — search never breaks, it just uses the slower free path.
- A thin Tavily answer is topped up from SearXNG for free instead of spending a second credit on the same query.
- The key is stored server-side only and is never returned to the browser (only the `tvly-…xxxx` mask) and never written to logs.
- Admins see each user's remaining Tavily credits and FLAI API key count in the admin Users tab (fetched lazily and cached for 5 minutes, so opening the page does not spend credits).
- The instance stays fully functional without any Tavily key — this is an optional quality upgrade, not a requirement.

## 🕸️ Site reading & deep study (Crawl4AI)

For URLs you paste in chat and for whole-site deep-study requests, FLAI can use **Crawl4AI** sidecar (enabled with `--with-crawler` at deploy time, or by setting `CRAWL_ENABLED=true` in `.env`).

**`read_page(url)` — one URL in a real browser**

When you paste a link (e.g. a documentation page, a product listing, a GitHub issue) the `[-CRAWL-]` category is **not** triggered — instead the native `read_page` tool fires automatically (fast worker, no queue slot, no GPU). It fetches the page in a real browser (Playwright + Chromium inside the sidecar), extracts clean markdown via trafilatura, and returns it to the model. The tool is registered only when the crawler container is reachable; if the container is down, the request falls through to the normal flow and the URL is not read.

**`[-CRAWL-]` — deep site study**

When the user explicitly asks to "study this site", "analyze the whole documentation", "read everything on example.com" the router emits the `[-CRAWL-]` marker. FLAI then:

1. Crawls the starting domain (up to `CRAWL_MAX_PAGES=50`, `CRAWL_MAX_DEPTH=3`, `CRAWL_TIMEOUT_S=300`), following only same-domain links.
2. Concatenates pages as `## <url>` sections, bounded by `CRAWL_MAX_TOTAL_CHARS=1,000,000` (the per-page cap is `CRAWL_MAX_PAGE_CHARS=50,000`).
3. **Replaces** any existing user document whose filename equals the registrable domain (e.g. `docs.example.com` → `docs.example.com.txt`). The replacement deletes the old RAG entry, the file, and the DB row before writing the new one.
4. Runs the **shared document-indexing core** synchronously (the same pipeline as document uploads) — embeddings → Qdrant → RAG.
5. **Requeues reasoning** with `rag_source="crawler"`; the `file_coverage` flag ensures the fresh document reaches the prompt.
6. The resulting document appears in your **Documents panel** under the domain name, ready for ordinary RAG, web search top-up, or **Deep Analysis (RLM)**.

**Degradation**

| Situation | Behavior |
|---|---|
| Container disabled or down | `read_page` unregistered; `[-CRAWL-]` fails with a localized message (never silently falls back to light search) |
| Site refuses automated access (anti-bot) | A notice is shown and FLAI **falls back to ordinary web search** (Tavily → SearXNG) so you still get an answer |
| 0 usable pages | Localized soft error suggesting ordinary search |
| Limits reached | Clean stop; collected prefix is indexed |
| Document quota full | Localized quota error; content discarded |

**SSRF posture**

Every URL passes `app/crawler_guard.py:validate_url()` before it leaves the app: only http/https, all resolved addresses must be globally routable (no RFC 1918, loopback, link-local, 169.254.169.254, CGNAT, ULA, multicast), credentials in URLs rejected. A start URL deeper than the domain root (e.g. `https://site.com/docs/`) is confined to that path prefix — sibling sections of the portal are not crawled. Trafilatura remains the first-step extractor everywhere it runs; the crawler is the second step for pages that need a real browser. Some large shops (e.g. DNS-Shop) block datacenter IPs outright — for those FLAI automatically answers via ordinary search.

**Progress stages**

While a crawl runs you will see these streamed status labels in the chat:
- `crawl_start` — «Starting web crawl...»
- `crawl_page` (with page count) — «Crawling page N...»
- `crawl_indexing` — «Indexing crawled content...»

---
