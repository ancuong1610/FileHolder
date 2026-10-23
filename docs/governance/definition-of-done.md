# Definition of done

Done means the selected task has enough evidence for its actual risk. It is not
universal release approval, a claim of semantic accuracy or proof that an agent
followed these instructions.

## Evidence by task

- **Question/inspection** — answer with the owning source, observed evidence and
  unknowns. No edit is expected.
- **Micro-fix** — the intended small change, the smallest relevant check and a
  concise diff report.
- **Defect repair** — a reproduced failure or saved evidence, the traced producer,
  a regression that failed before the repair, and the focused check after.
- **Feature/change** — acceptance criteria, focused tests, affected phase or
  integration check, contract/reader impact and preserved user work.
- **Evaluation** — dataset/workspace identity, OLD/NEW orientation, exact command
  and counts, separated regression/extraction/semantic/production claims.
- **Handover** — actual diff inspection, `git diff --check`, exact checks/results,
  documentation impact and skipped or unverified cases.

## Always report

1. Changed files or the explicit statement that nothing changed.
2. Exact commands and results, including failures and skips; do not report a
   worker summary as evidence.
3. Documentation impact from the diff, or why no document needed an edit.
4. Confidentiality/permission boundaries observed, and any client, provider or
   Windows check that remains unverified.
5. The next user-visible action.

Do not commit, push, deploy or publish without explicit authorization. A passing
suite proves the tested contracts and regressions, not universal comparison
accuracy or native Copilot/GitLab Duo loading.
