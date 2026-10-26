# Interface Demo

Four single-page files show what FLAI looks like without installing it:

| File | Interface | Language |
|------|-----------|----------|
| `docs/demo/chat-en.html` | chat, tasks, documents, deep analysis | English |
| `docs/demo/chat-ru.html` | chat, tasks, documents, deep analysis | Russian |
| `docs/demo/admin-en.html` | admin panel, all nine tabs | English |
| `docs/demo/admin-ru.html` | admin panel, all nine tabs | Russian |

## Opening the demo

Download the file and open it in a browser. It needs no server and no network
connection: every style, script, image and video is embedded in the file.

The page starts on the login screen. The login and password fields are filled and
locked (`user` / `user` for the chat, `admin` / `admin` for the admin panel); the
"Log in" button opens the interface behind them.

## What is real and what is fictional

Real: the interface itself — the templates, CSS and JavaScript of the current release —
the hardware and model data of the machine the demo was built on, and the answers,
documents analysis, images and video the product actually produced.

Fictional: the accounts shown on the login screen, the users in the admin panel, the
documents, the requests, the backups, the cameras and the monitoring series. No real
account, chat, document or usage statistic is part of the demo.

## Why it cannot be mistaken for a running instance

Nothing in the demo sends a request. Every action — send, generate, delete, save,
download, log out — answers with a notice that this is an interface demonstration. Only
viewing works: switching between chat, tasks, documents and sessions, the admin tabs, the
theme switch and the desktop/phone switch.

The bar above the interface is not part of the product: it carries the demo label, the
desktop/phone switch and the link to the other language.

## Rebuilding the demo

The pages are generated during release preparation, after the version bump and after the
last interface change:

```bash
# 1. Back up the database first (the capture writes into it briefly).
docker exec flai-postgres pg_dump -U flai -d flai > backups/pre-work_$(date +%F_%H-%M-%S).sql

# 2. Capture the demo data from the running instance.
python -m demo.capture --lang all --base-url http://localhost:5000 --password '<demo password>' --force

# 3. Build the four pages.
python -m demo.build

# 4. The guard fails when a committed page no longer matches the templates.
pytest tests/test_demo_pages.py -q
```

The capture drives the running instance over HTTP with two throwaway accounts per
language (`demo_<lang>_chat` and `demo_<lang>_admin`, created beforehand), records the
responses, downloads the generated media and then removes the accounts. Every generated
picture is checked with the product's own multimodal model, and a picture that shows any
text is generated again (up to three attempts). If the capture is interrupted,
`python -m demo.capture --cleanup <manifest> --database-url <url>` finishes the removal;
the background tasks of those logins must be deleted explicitly, because the product's
account deletion leaves them behind.

The documents are committed as PDFs. They are regenerated only when their fictional
content changes:

```bash
python -m demo.make_documents
```

## Limits and notes

- Each page is a single file of up to 16 MB; the real ten-second video is embedded as is.
- The demo is verified in Chromium and Firefox opened from disk; Safari is not covered
  specially.
- The phone layout is the product's own responsive layout rendered in a 390 px iframe,
  not a scaled screenshot.
