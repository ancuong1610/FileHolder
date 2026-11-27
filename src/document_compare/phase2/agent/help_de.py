"""German product help shared with the comparison planner."""

from document_compare.phase2.ui import texts


HELP_DE = f"""
„{texts.TITLE}“ vergleicht eine alte und eine neue PDF-Version und zeigt die
gefundenen Unterschiede und offenen Prüffälle.

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
„{texts.BLOCK_SECTION["DELETED"]}“.
„{texts.BLOCK_FULL_PAGE.format(page="N", action=texts.BLOCK_FULL_PAGE_ACTIONS["DELETED"], side=texts.SIDE_LABELS["old"])}“
bündelt die Löschungen einer vollständigen alten Seite. Unter
„{texts.BLOCK_MEMBERS}“ lassen sich die einzelnen Änderungen ansehen.

„{texts.PAGE_SHORT_SIDE.format(page="x", side=texts.SIDE_LABELS["old"])}“ /
„{texts.PAGE_SHORT_SIDE.format(page="y", side=texts.SIDE_LABELS["new"])}“ nennt die Seite,
zu der der Befund gehört. Im anderen Dokument markiert eine gestrichelte Linie
die entsprechende Stelle. Die Beschriftung lautet
„{texts.ANCHOR_CAPTION.format(action=texts.ANCHOR_ACTIONS["DELETED"])}“ oder
„{texts.ANCHOR_CAPTION.format(action=texts.ANCHOR_ACTIONS["ADDED"])}“.

Die „{texts.STRUCTURE}“ folgt den Abschnitten des neuen Dokuments. Abschnitte,
die es nur im alten Dokument gibt, können am Ende stehen. Über „{texts.SEARCH}“
lassen sich Textausschnitte, Abschnittstitel und Befund-IDs finden. Während der
Assistent sucht, zeigen Statuszeilen wie „{texts.CHAT_STEP_DETAILS.format(record_id="CHANGE-000001")}",
woran er gerade arbeitet.
Mit der „{texts.JOB_ID}“ lässt sich der Vergleich über „{texts.OPEN_JOB}“
später erneut öffnen. Vermutliche lokale Umstellungen durch die Lesereihenfolge
sind in der Gliederung ausgeblendet. Darauf weist die Zeile
„{texts.EXCLUDED_LOCAL_REORDERS.format(count="…")}“ hin.

Empfohlene Prüfreihenfolge: {texts.IMPORTANT} → {texts.IMPACT_LABELS["content"]}
mit offenen Prüffällen → Blöcke → Verschiebungen → {texts.IMPACT_LABELS["editorial"]}.
""".strip()
