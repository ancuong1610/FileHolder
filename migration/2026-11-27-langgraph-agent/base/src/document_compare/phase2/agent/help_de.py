"""German product help shared with the comparison planner."""

from document_compare.phase2.ui import texts


HELP_DE = f"""
„{texts.TITLE}“ vergleicht eine alte und eine neue PDF-Version und zeigt die
gefundenen Unterschiede und offenen Prüffälle. Das Ergebnis dient der fachlichen
Prüfung und stellt keine rechtliche Wahrheit fest.

Die Ansicht wählen:
- „{texts.PRESET_LABELS["important"]}“ zeigt Inhaltsänderungen mit hoher
  Priorität, etwa Änderungen an Zahlen, Bedingungen oder Verneinungen.
- „{texts.PRESET_LABELS["content"]}“ zeigt Inhaltsänderungen und Verschiebungen.
- „{texts.PRESET_LABELS["review"]}“ zeigt Befunde, die geprüft werden müssen,
  auch wenn ihre Auswirkung noch unbekannt ist.
- „{texts.PRESET_LABELS["all"]}“ zeigt auch redaktionelle und noch nicht
  eingeordnete Befunde.

{texts.LEGEND}
„{texts.IMPACT_LABELS["content"]}“ kennzeichnet eine inhaltliche Änderung.
„{texts.IMPACT_LABELS["editorial"]}“ kennzeichnet Änderungen ohne erkennbaren
Einfluss auf den Inhalt, etwa bei Silbentrennung, Zeichensetzung,
Aufzählungsmarken oder Abkürzungen. Bei „{texts.IMPACT_LABELS["moved"]}“
zeigt der Befund den Ausgangs- und Zielabschnitt (aus → nach).

Blöcke bündeln aufeinanderfolgende Ergänzungen oder Löschungen, etwa
„{texts.BLOCK_SECTION["DELETED"]}“. Unter „{texts.BLOCK_MEMBERS}“ lassen sich
die einzelnen Änderungen ansehen.

Die „{texts.STRUCTURE}“ ordnet Befunde nach Abschnitten. Über „{texts.SEARCH}“
lassen sich Textausschnitte, Abschnittstitel und Befund-IDs finden.
Mit der „{texts.JOB_ID}“ lässt sich der Vergleich über „{texts.OPEN_JOB}“
später erneut öffnen. Vermutliche lokale Umstellungen durch die Lesereihenfolge
sind in der Gliederung ausgeblendet. Darauf weist die Zeile
„{texts.EXCLUDED_LOCAL_REORDERS.format(count="…")}“ hin.

Empfohlene Prüfreihenfolge: {texts.IMPORTANT} → {texts.IMPACT_LABELS["content"]}
mit offenen Prüffällen → Blöcke → Verschiebungen → {texts.IMPACT_LABELS["editorial"]}.
""".strip()
