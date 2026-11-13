# Phase 2 UI

Streamlit reviewer: upload two PDFs, follow progress, open a finished job, step
through findings with old/new page images and highlights, and ask the chat panel.

Read first: `app.py` (shell, upload, open job, polling), then `review.py`
(findings list and page viewer), `chat.py` (sidebar chat), `api_client.py`.

Input/output: talks only to the local HTTP API at `CRR_API_BASE` (loopback http,
default `http://127.0.0.1:8321`). Reads results; writes nothing into a job.
Session state keys: `job_id`, `selected`, `old_page`, `new_page`, `chat_history`.

Dependencies: `streamlit` (optional extra `ui`) and `httpx`. Phase 1 is never
imported. No result text, question, answer or image bytes is logged.

Run: `streamlit run src/document_compare/phase2/ui/app.py`.
Check: `python -m unittest tests.phase2.test_ui_client tests.phase2.test_ui_smoke`.
