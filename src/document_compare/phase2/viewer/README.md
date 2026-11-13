# Phase 2 viewer

Offline test data for the compare viewer: real PDFs whose text sits where a
document JSON's polygons say, and a seeded demo job that uses them.

Read first: `synthetic.py` — `build_synthetic_pdf` and `seed_demo_job`.

Input/output: reads `output/{old,new}_document.json` of a synthetic demo run;
writes `input/document/old.pdf`, `new.pdf` and `job_status.json` into a new job
directory under the given jobs root. Never touches an existing job directory.

Dependencies: PyMuPDF and the Phase-1 offline demo; no provider, model or
network call. Phase 1 never imports this package.

Check: `python -m unittest tests.phase2.test_viewer_synthetic`.
Manual seed: `python -m document_compare.phase2.viewer.synthetic --jobs-root PATH`.

Open a real saved workspace in the UI (copies its `output/` and the two source
PDFs into a new job; the workspace is only read; asks for missing paths):
`python -m document_compare.phase2.viewer.import_workspace --workspace workspace/EBA_STEP2 --old "input/OLD.pdf" --new "input/NEW.pdf"`.
Check: `python -m unittest tests.phase2.test_viewer_import_workspace`.

Locations and outline (`locations.py`, `outline.py`): review records are linked to
changes through `evidence.change_ids` or `source_candidate_id`; regions carry a `role`
(`element`, `table`, `cell`); `page_render.render_page` takes optional aligned `roles`
(`table` = outline only). Contract: `docs/contracts/view-endpoints.md`.
