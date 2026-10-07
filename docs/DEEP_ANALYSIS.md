# Deep Analysis Mode (RLM)

**For what?** Reading a large document and answering simple questions about it is normal RAG. Deep Analysis is for **serious work with documents**: a comparison across several contracts, finding every condition and exception in a policy, a structured report over a set of texts, verifying arithmetic across tables. Instead of one pass over a summary, the reasoning model actually *works through* the material in a loop of up to 18 steps. The per-host budget follows the resource ladder in `modules/rlm.py:_resource_step_budget()`: 24 GB+ → 18, 16 GB → 12, 12 GB → 10, 8 GB → 8, CPU/<8 GB → 6. Smaller hosts get fewer steps and still finish. The model uses three tools along the way:

| Tool | What it does |
|------|--------------|
| 🐍 `python` | executes code in an isolated sandbox — split texts, count words, extract paragraphs, search by pattern, analyze tables, solve calculations |
| 🤖 `llm` | asks a sub-model call (limited tokens) for a focused sub-result, then folds it into the main reasoning |
| 🌐 `web_fetch` | searches the web (SearXNG) for fresh facts when the answer needs them — the model is prompted to use at most 2 lookups (5 is the hard ceiling) |

Everything runs **locally** as a single task: the reasoning model stays loaded for the whole analysis, progress is streamed live («Reading documents...», «Analysis step N...»), and the result arrives with a collapsible **«Deep analysis (N steps)»** summary.

The answer always matches your language, and the actor must actually work through the selected files: a `final(answer)` call issued before any `python` corpus inspection is rejected with a corrective instruction (multi-file corpora), and the system prompt forbids answering before every corpus file has been examined — so an apartment question about two contracts won't silently report just the first one while missing the second.

**How to use it:**
1. Upload the files you want analyzed in **Documents** (PDF, DOC, DOCX, TXT, ODT, RTF, CSV, JSON, EPUB).
2. In the **Documents** panel, check the documents you want included. The checkboxes are a single picker shared with the move actions: with the 🔬 toggle **on**, checking a document marks it for Deep Analysis (it gets a green frame and a check mark, and the counter next to the toggle shows how many are selected); with the toggle **off** the same checkboxes drive document moves instead. Un-checking the toggle clears the green selection instantly, and un-checking any document removes it from the analysis set in either mode.
3. Type a question, turn on the **🔬 Deep Analysis** toggle and press **Send**.
4. *(Optional)* Attach up to 4 images as well: the multimodal model produces a detailed text description of each and every description becomes one more "document" of the analysis — so you can ask things like "match the attached warranty photos against clause 4 of the contract". Chat-attached documents join the corpus too, so the Documents-panel selection is optional when you attach files directly in the message.
5. Follow the progress stages; when the trace summary appears, expand it to see how the model got to the answer.

## Notes
- Without selected documents **and** without attachments (images or a chat-attached document), or if there is no question, the toggle is unchecked automatically and the request goes through the normal flow instead of failing.
- The analysis works on the selected documents only (no full-text search over unrelated uploads).
- To stop it: press **Cancel** — the task is checked for cancellation on every step.
