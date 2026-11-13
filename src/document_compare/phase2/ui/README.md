# Phase 2 UI

Streamlit reviewer: upload two PDFs, follow progress, open a finished job, browse
its findings as a section tree, step through them with old/new page images and
highlights, and ask the comparison agent.

Read first: `app.py` (shell, sidebar tabs Compare / Open job / Comparison agent,
polling), then `review.py` (summary chips, Type and Kind filters, structure tree,
viewer), `chat.py` (agent tab), `api_client.py`.

Layout: the main area shows file names (bold) and page counts, one row of count chips, the
filters, then two columns (2:3): the Structure tree (left) and the viewer (right: breadcrumb,
legend, old and new page side by side at the same width, Previous/Next in displayed order).
The tree is a Confluence-style page tree: one-line rows (ellipsis, full title in the tooltip),
a fixed chevron slot, a muted count pill at the right, 16px indent with a thin guide line,
change rows with a coloured dot and `p.N`; "Unplaced" is a last root node when non-empty.
Display inference only (the API outline is unchanged): when the engine found no hierarchy
(no section has children) and the first section's title has no section number, it is shown
as the title root with every other top-level section as its child; the root starts expanded.
An "Order" radio above the tree switches sibling order between "Document order" (default;
a caption notes when section numbers are not ascending) and "Section number" (natural sort
by the first integer in the title, unnumbered siblings after, relative order kept).
Filters are Type, Kind and a case-insensitive Search over item labels and section titles;
they apply client-side, chips show unfiltered totals, and Previous/Next follow the
filtered set. Review-queue items are returned by the API but not shown in the UI.
Opening a job or selecting an item expands the path to the selected item; other sections
stay collapsed. When a finding exists on one side only, the other side shows the page of
the same section ("section context", no highlight) from the outline's `context_old` /
`context_new` fields. The tree comes from `GET /jobs/{id}/view/outline`. The first item
in tree order is selected when a job opens. Sections are an evidence-based structure
hypothesis; the outline view is not a statement about legal meaning.

Input/output: talks only to the local HTTP API at `CRR_API_BASE` (loopback http,
default `http://127.0.0.1:8321`). Reads results; writes nothing into a job.
Session state keys: `job_id`, `selected`, `tree_open`, `tree_synced`, `root_open`, `tree_order`, `chat_history`, `chat_error`.

Dependencies: `streamlit` (optional extra `ui`) and `httpx`. Phase 1 is never
imported. No result text, question, answer or image bytes is logged. The only
raw HTML is two static style blocks (tree rows, evenly spread sidebar tabs).

Run: `streamlit run src/document_compare/phase2/ui/app.py`.
Check: `python -m unittest tests.phase2.test_ui_client tests.phase2.test_ui_smoke tests.phase2.test_viewer_outline`.
