"""German display text for the Streamlit reviewer."""

TITLE = "Dokumentenvergleich"
COMPARE = "Vergleichen"
OPEN_JOB = "Auftrag öffnen"
AGENT = "Vergleichsassistent"
OLD_PDF = "Alte PDF-Datei"
NEW_PDF = "Neue PDF-Datei"
OLD_PDF_NAME = "alt.pdf"
NEW_PDF_NAME = "neu.pdf"
EXTRACTION_NOTICE = "Beim Vergleich werden beide PDF-Dateien an den konfigurierten Extraktionsdienst gesendet."
SELECT_BOTH_PDFS = "Wählen Sie beide PDF-Dateien aus."
UPLOAD_FAILED = "Hochladen fehlgeschlagen. {error}"
JOB_ID = "Auftrags-ID"
JOB_ID_LINE = "Auftrags-ID: {job_id}"
OPEN = "Öffnen"
ENTER_JOB_ID = "Geben Sie eine Auftrags-ID ein."
STAGES_COMPLETE = "{done}/{total} Verarbeitungsschritte abgeschlossen"
OPEN_FOR_AGENT = "Öffnen Sie einen Auftrag, um den Assistenten zu nutzen."
START_HINT = "Laden Sie zwei PDF-Dateien hoch und klicken Sie auf „Vergleichen“. Alternativ können Sie einen Auftrag per ID öffnen."
STATUS_FAILED = "Der Status für Auftrag {job_id} konnte nicht geladen werden. {error}"
AGENT_PENDING = "Der Assistent ist verfügbar, sobald der Vergleich abgeschlossen ist."
JOB_FAILED = "Auftrag {job_id} ist fehlgeschlagen. {detail}"
NO_ERROR_DETAIL = "Es wurde keine Fehlerbeschreibung übermittelt."
JOB_STATE = "Auftragsstatus: {state}"
JOB_STATES = {"queued": "In der Warteschlange", "running": "Wird verarbeitet"}
UNKNOWN_JOB_STATE = "Unbekannt"
KEEP_JOB_ID = "Mit dieser Auftrags-ID können Sie das Ergebnis später erneut öffnen oder abrufen."
INVALID_API_BASE = "CRR_API_BASE muss eine gültige lokale HTTP-Adresse ohne Pfad, Abfrage oder Fragment sein."

ERRORS = {
    "timeout": "Die lokale API hat nicht rechtzeitig geantwortet und arbeitet möglicherweise noch.",
    "api_unreachable": "Die lokale API ist nicht erreichbar.",
    "invalid_arguments": "Die Anfrage enthält ungültige Angaben.",
    "invalid_artifact": "Das gespeicherte Ergebnis oder eine zugehörige Datei ist nicht lesbar.",
    "not_ready": "Das Ergebnis ist noch nicht verfügbar.",
    "not_found": "Der angeforderte Eintrag wurde nicht gefunden.",
    "source_pdf_unavailable": "Die Quell-PDF ist nicht verfügbar.",
    "chat_failed": "Der Assistent konnte keine Antwort erstellen.",
    "response_too_large": "Die Antwort überschreitet die zulässige Größe.",
}
INVALID_JOB_ID = "Die Auftrags-ID muss aus 12 Hexadezimalzeichen bestehen (0–9 und a–f)."
HTTP_ERROR = "Die lokale API meldet einen Fehler (HTTP {status})."
UNKNOWN_JOB = "Der Auftrag wurde nicht gefunden."
UNKNOWN_ERROR = "Die Anfrage ist fehlgeschlagen."

TYPE_LABELS = {
    "all": "Alle",
    "ADDED": "Hinzugefügt",
    "DELETED": "Gelöscht",
    "MODIFIED": "Geändert",
    "REVIEW": "Prüfung",
    "MOVED": "Verschoben",
}
KIND_LABELS = {"all": "Alle", "change": "Änderung", "table": "Tabelle", "move": "Verschiebung"}
IMPACT_LABELS = {
    "all": "Alle", "important": "Wichtig (Inhalt, hohe Priorität)", "content": "Inhalt",
    "editorial": "Redaktionell", "moved": "Verschoben",
}
IMPACT_FILTER = "Auswirkung"
EXCLUDED_UNKNOWN_IMPACT = "{count} Befunde ohne Auswirkungsangabe sind ausgeblendet."
LOCAL_REORDERS = "Lokale Umstellungen anzeigen"
EXPAND_BLOCKS = "Einzeln anzeigen"
BLOCK_SECTION = {"ADDED": "Abschnitt hinzugefügt", "DELETED": "Abschnitt gelöscht"}
BLOCK_PARAGRAPHS = {"ADDED": "{count} Absätze hinzugefügt", "DELETED": "{count} Absätze gelöscht"}
BLOCK_MEMBERS = "Änderungen im Block"
BLOCK_PAGES = "Seiten {pages}"
BLOCK_PAGE = "Seite im Block"
IMPORTANT = "Wichtig"
IMPACT_SIGNALS = {
    "modal": "Modalverb",
    "negation": "Verneinung",
    "qualifier": "Einschränkung",
    "condition": "Bedingung",
    "number": "Zahl",
    "state_marker": "Statussymbol",
    "date": "Datum",
    "reference": "Verweis",
    "added_text": "Text hinzugefügt",
    "deleted_text": "Text entfernt",
    "replaced_text": "Text ersetzt",
}
MOVE_LABEL = "{text} (aus {source} → {destination})"
UNKNOWN_ANCHOR = "—"
TABLE_CELLS_ONE = "{count} Zelle geändert"
TABLE_CELLS_MANY = "{count} Zellen geändert"
CHIP_LABELS = {
    "Changes": "Änderungen", "Tables": "Tabellen", "Added": TYPE_LABELS["ADDED"],
    "Deleted": TYPE_LABELS["DELETED"], "Modified": TYPE_LABELS["MODIFIED"],
    "Needs review": "Prüfung erforderlich",
}
SIDE_LABELS = {"old": "Alt", "new": "Neu"}
ORDER_LABELS = {"Document order": "Dokumentreihenfolge", "Section number": "Abschnittsnummer"}
ORDER = "Reihenfolge"
ORDER_NOTE = "Die Abschnitte stehen in der Reihenfolge des neuen Dokuments."
LEGEND = ("Rot = gelöscht, Grün = hinzugefügt, Orange = geändert oder verschoben, "
          "violett gestrichelt = Prüfung erforderlich (gestrichelte Umrandung = unsichere Zuordnung). "
          "Die Abzeichen Inhalt, Redaktionell und Verschoben zeigen die Auswirkung. "
          "Wichtig markiert Inhaltsänderungen mit hoher Priorität.")
PAGE_DISPLAY_FAILED = "Das Seitenbild konnte nicht angezeigt werden."
PAGE_COUNT_ONE = " · {count} Seite"
PAGE_COUNT_MANY = " · {count} Seiten"
TYPE_FILTER = "Änderungstyp"
KIND_FILTER = "Kategorie"
SEARCH = "Suche"
SEARCH_HINT = "Änderungen, Abschnitte oder IDs suchen"
FLAGGED_ONLY = "Nur mit Prüfbedarf"
PAGE_SHORT = "S. {page}"
UNTITLED = "(ohne Titel)"
STRUCTURE = "Gliederung"
NO_MATCHING_SECTIONS = "Keine passenden Abschnitte oder Änderungen gefunden."
NO_SECTION_STRUCTURE = "Für diesen Auftrag wurde keine Gliederung gefunden."
UNPLACED = "Nicht zugeordnet"
LLM_OPINION = "Einschätzung des Sprachmodells · beratend"
NO_LLM_REASON = "Keine Begründung angegeben."
NO_EVALUATOR = "Kein Bewertungsmodell angegeben"
EVIDENCE_REFS = "Berücksichtigte Belege: {refs}"
LLM_OUTCOMES = {
    "REAL_CHANGE": "Tatsächliche Änderung", "ARTIFACT": "Artefakt", "UNCERTAIN": "Unsicher",
}
UNKNOWN_LLM_OUTCOME = "Unbekannte Einstufung"
REVIEW_NOTE = "Prüfung erforderlich — {reason}"
NO_REVIEW_REASON = "Der Grund wurde nicht näher angegeben"
REVIEW_REASONS = {
    "Possible match on the other side not confirmed": "Mögliche Entsprechung auf der anderen Seite unbestätigt",
    "Block of content moved": "Ein Inhaltsblock wurde verschoben",
    "Short or ambiguous change — LLM could not decide": "Kurze oder mehrdeutige Änderung ohne eindeutige Modelleinschätzung",
    "Duplicate exact assignment": "Identischer Text mehrfach vorhanden – Zuordnung unklar",
    "Duplicate exact residual": "Identischer Text mehrfach vorhanden – ohne Gegenstück",
    "Modified with review evidence": "Geänderter Absatz mit Prüfhinweisen aus der Extraktion",
    "Hyphenation wrap artifact": "Vermutlich nur Silbentrennung am Zeilenende",
    "List label only difference": "Nur die Aufzählungsmarke unterscheidet sich",
    "Single glyph difference": "Ein einzelnes Zeichen unterscheidet sich (möglicher Erkennungsfehler)",
    "Unresolved table group": "Tabellenzuordnung ungeklärt",
    "Added table candidate": "Möglicherweise neue Tabelle",
    "Resegmentation suspect": "Möglicherweise geteilter oder zusammengeführter Absatz",
    "Beyond aligned coverage": "Außerhalb des abgeglichenen Bereichs",
    "Structural heading boundary": "Grenze an einer Überschrift verschoben",
    "Needs review": NO_REVIEW_REASON,
    "review item": TYPE_LABELS["REVIEW"],
}
DETAILS_FAILED = "Die Details konnten nicht geladen werden. {error}"
NO_LOCATIONS = "Die Seitenpositionen sind nicht verfügbar. {error}"
RENDER_FAILED = "Das Seitenbild ({side}) konnte nicht erstellt werden. {error}"
PAGE = "{side} · Seite {page}"
SECTION_CONTEXT = " · Abschnittskontext"
NO_PAGE_LOCATION = "{side} · keine Seitenposition"
PAGE_IMAGE_UNAVAILABLE = "{side} · Seite {page} (Bild nicht verfügbar)"
SOURCE_PDF_UNAVAILABLE = "Die Quell-PDF ist für diesen Auftrag nicht verfügbar. Daher fehlen die Seitenbilder."
PREVIOUS = "Zurück"
NEXT = "Weiter"
CHANGE_ID = "Änderungs-ID: {record_id}"
RESIZE_STRUCTURE = "Breite der Gliederung ändern"
RESIZE_HINT = "Ziehen zum Ändern der Breite. Doppelklicken zum Zurücksetzen."
RESULT_FAILED = "Das Ergebnis für Auftrag {job_id} konnte nicht geladen werden. {error}"
NO_MATCHING_FINDINGS = "Keine Befunde entsprechen diesen Filtern."
NO_FINDINGS = "Dieses Ergebnis enthält keine Befunde."

AGENT_NOT_CONFIGURED = "Der Assistent ist auf diesem Rechner nicht konfiguriert."
CHAT_HINT = "Frage zum Vergleich stellen (Textauszüge werden an das Modell gesendet)"
CHAT_PENDING = "Die Anfrage wird verarbeitet …"
LIMITATIONS = "Einschränkungen: {items}"
LIMITATION_LABELS = {
    "job_not_done": "Auftrag läuft noch",
    "invalid_artifact": "Ergebnisdatei ungültig",
    "review_open": "Prüfungen offen",
    "table_reviews_open": "Tabellenprüfungen offen",
    "llm_advisory_present": "Beratende Modelleinschätzung vorhanden",
    "document_identity_missing": "Dokumentidentität fehlt",
    "response_too_large": "Antwort zu umfangreich",
    "old_extraction_validation_missing": "Extraktionsprüfung für das alte Dokument fehlt",
    "new_extraction_validation_missing": "Extraktionsprüfung für das neue Dokument fehlt",
    "old_extraction_validation_unreadable": "Extraktionsprüfung für das alte Dokument nicht lesbar",
    "new_extraction_validation_unreadable": "Extraktionsprüfung für das neue Dokument nicht lesbar",
    "old_extraction_requires_review": "Extraktion des alten Dokuments benötigt Prüfung",
    "new_extraction_requires_review": "Extraktion des neuen Dokuments benötigt Prüfung",
}


def api_error(code: str, status: int | None = None) -> str:
    if code in ERRORS:
        return ERRORS[code]
    return HTTP_ERROR.format(status=status) if status is not None else UNKNOWN_ERROR
