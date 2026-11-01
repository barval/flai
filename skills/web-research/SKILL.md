---
name: web-research
description: Gather and cross-check sources before answering a factual question about the current world.
allowed-tools: web_search, read_page
---
# Web research

1. Run one `web_search` with the question's key terms; do not restate the whole sentence.
2. Open the two most relevant results with `read_page` when the snippets alone do not answer.
3. Prefer a primary source (vendor page, documentation, official statement) over a retelling.
4. When two sources disagree, say so and give both figures with their dates.
5. Answer in the user's language, and name the source you relied on.
6. If the search returns nothing usable, say that plainly instead of guessing.
