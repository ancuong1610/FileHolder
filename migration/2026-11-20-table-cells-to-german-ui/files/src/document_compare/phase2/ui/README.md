# Phase 2 UI

The Streamlit reviewer uses German interface labels. Upload two PDFs, follow progress, open a finished job, browse
its findings as a section tree, step through them with old/new page images and
highlights, and ask the comparison agent.

Read first: `app.py` (shell, sidebar tabs Vergleichen / Auftrag öffnen / Vergleichsassistent,
polling), then `review.py` (summary chips, filters, structure tree,
viewer), `chat.py` (agent tab), `texts.py` (German labels), and `api_client.py`.

Layout: the main area shows file names (bold) and page counts, one row of count chips, the
filters, then two columns (2:3): the Structure tree (left) and the viewer (right: breadcrumb,
legend, old and new page side by side at the same width, Previous/Next in displayed order).
The tree is a Confluence-style page tree: one-line rows (ellipsis, full title in the tooltip),
a fixed chevron slot, a muted count pill at the right, 16px indent with a thin guide line,
change rows with a coloured dot and `S. N`; "Nicht zugeordnet" is a last root node when non-empty.
Display inference only (the API outline is unchanged): when the engine found no hierarchy
(no section has children) and the first section has neither a TOC rank nor a recognizable section label, it is shown
as the title root with every other top-level section as its child; the root starts expanded.
The order control above the tree offers "Dokumentreihenfolge" (default) and
"Abschnittsnummer". The latter sorts each sibling group first by linked TOC rank
from the new document or, when absent, the old document, then by natural label
(e.g. `AT 1` before `AT 4.3` before `BT 1`). Unlabelled sections follow in
reading order. A caption appears in document order when the alternative would
reorder sections.
Filters are "Änderungstyp", "Kategorie", "Suche" over item labels, IDs and section
titles, and "Nur mit Prüfbedarf". Filters apply client-side. The badges show
unfiltered totals, and "Zurück" and "Weiter" follow the filtered set.
Items flagged for review show a purple "Prüfung" tag after the type dot and a
`Prüfung erforderlich — <reason>` line under the breadcrumb. Review records that match no change
are tree items of their own (purple dot). Their page highlights are purple and dashed.
Opening a job or selecting an item expands the path to the selected item; other sections
stay collapsed. When a finding exists on one side only, the other side shows the page of
the same section ("Abschnittskontext", no highlight) from the outline's `context_old` /
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

Panel width: the divider between the structure tree and the page viewer can be
dragged (or moved with the arrow keys when focused); width is kept per browser
in `localStorage` (`dc_tree_width`), limited to 260 px – 65 % of the row, and a
double-click resets it. It is a small script injected through
`streamlit.components.v1.html` (no new dependency); on narrow screens where the
columns stack it switches itself off.
