from __future__ import annotations
import asyncio, json, os, subprocess, sys, time, urllib.parse, uuid
from pathlib import Path
from typing import Any
import httpx
from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport
from crr_compare.service import MAX_UPLOAD_BYTES
from agent_poc.agent import TiaComparisonAgent
from agent_poc.mcp_gateway import StdioMcpGateway, StdioReaderGateway, _unwrap as unwrap
from agent_poc.tia_client import TiaTextClient

PDF_MAGIC = b"%PDF-"
DEFAULT_API_BASE = "http://127.0.0.1:8321"


def _api_base() -> tuple[str, int]:
    raw = os.environ.get("CRR_API_BASE", DEFAULT_API_BASE).strip()
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "http":
        raise ValueError(f"CRR_API_BASE must use http scheme: {raw!r}")
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError(f"CRR_API_BASE must be loopback: {raw!r}")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError(f"CRR_API_BASE must not contain a path, query, or fragment: {raw!r}")
    port = parsed.port or 8321
    return f"{parsed.scheme}://{parsed.hostname}:{port}", port

def list_input_pdfs(input_dir: Path) -> list[Path]:
    root = Path(input_dir)
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.is_file() and p.suffix.casefold() == ".pdf"),
                  key=lambda p: p.name.casefold())

def validate_pdf_filename(input_dir: Path, filename: str, *, max_bytes: int = MAX_UPLOAD_BYTES) -> Path:
    if not filename or filename != Path(filename).name or filename.startswith("."):
        raise ValueError("filename must be a bare name")
    if Path(filename).suffix.lower() != ".pdf":
        raise ValueError("filename must end with .pdf")
    root = Path(input_dir).resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root:
        raise ValueError("path escapes the input directory")
    if not candidate.is_file():
        raise ValueError("file does not exist")
    if candidate.stat().st_size > max_bytes:
        raise ValueError("file exceeds the upload limit")
    with candidate.open("rb") as handle:
        if handle.read(len(PDF_MAGIC)) != PDF_MAGIC:
            raise ValueError("file is not a PDF")
    return candidate

def prompt_for_two_pdfs(input_dir: Path, input_fn=input) -> tuple[Path, Path]:
    available = list_input_pdfs(input_dir)
    if available:
        print("Available PDFs:")
        for pdf in available:
            print(f"  {pdf.name}")
    picked: list[Path] = []
    while len(picked) < 2:
        try:
            path = validate_pdf_filename(input_dir, input_fn(f"select PDF #{len(picked) + 1}: ").strip())
        except ValueError as exc:
            print(f"invalid: {exc}")
            continue
        if picked and path == picked[0]:
            print("invalid: choose two distinct files")
            continue
        picked.append(path)
    return picked[0], picked[1]

async def submit_and_poll(client, old_pdf_path: Path, new_pdf_path: Path, session_id: str, *, poll_seconds: float = 3.0) -> tuple[str, dict[str, Any]]:
    submitted = unwrap(await client.call_tool("submit_comparison", {
        "old_pdf_path": str(old_pdf_path), "new_pdf_path": str(new_pdf_path), "session_id": session_id}))
    payload = submitted if isinstance(submitted, dict) else json.loads(submitted)
    job_id = payload["job_id"]
    last_state = None
    last_stages = None
    while True:
        status = unwrap(await client.call_tool("check_status", {"job_id": job_id}))
        status = status if isinstance(status, dict) else json.loads(status)
        state = status.get("state")
        stages = tuple(status.get("stages_completed", []))
        if state != last_state or stages != last_stages:
            print(f"state={state} stages={list(stages)}")
            last_state, last_stages = state, stages
        if state == "done":
            return job_id, status
        if state == "failed":
            tail = status.get("error_tail") or status.get("error") or "unknown failure"
            raise RuntimeError(f"Job {job_id} failed: {tail}")
        await asyncio.sleep(poll_seconds)

def _require_credentials() -> None:
    missing: list[str] = []
    if not os.environ.get("TIA_API_KEY"):
        missing.append("TIA_API_KEY")
    if not os.environ.get("TIA_AZURE_ENDPOINT"):
        missing.append("TIA_AZURE_ENDPOINT")
    provider = os.environ.get("LLM_PROVIDER", "bbk_ai_hub").strip().lower()
    if provider == "bbk_ai_hub":
        # TIA_API_KEY already satisfies the hub requirement for the local reviewer.
        pass
    elif provider == "openai_compatible":
        if not os.environ.get("OPENAI_API_KEY"):
            missing.append("OPENAI_API_KEY")
        if not os.environ.get("LLM_MODEL"):
            missing.append("LLM_MODEL")
    else:
        raise RuntimeError(f"Unsupported LLM_PROVIDER={provider!r}; use bbk_ai_hub or openai_compatible")
    if missing:
        raise SystemExit(f"missing configuration: {', '.join(missing)} (values not printed)")

def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _start_api(env: dict[str, str], cwd: Path, api_base: str, port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "crr_compare.service:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env, cwd=str(cwd))
    deadline = time.time() + 30
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"API process exited early (code {proc.returncode})")
            try:
                if httpx.get(f"{api_base}/openapi.json", timeout=1.0).status_code == 200:
                    return proc
            except httpx.HTTPError:
                time.sleep(0.3)
        raise RuntimeError("API did not become ready")
    except Exception:
        _stop_process(proc)
        raise

def format_summary(summary: dict[str, Any]) -> str:
    lines = ["Current Document Health"]
    progress = summary.get("progress", {})
    completed = len(progress.get("stages_completed", []))
    duration = progress.get("duration_seconds")
    lines.append(f"  progress: {completed}/15 stages, state={progress.get('state', 'unknown')}, duration={duration}s")
    old_doc = summary.get("documents", {}).get("old", {})
    new_doc = summary.get("documents", {}).get("new", {})
    lines.append(f"  pages: old={old_doc.get('pages')}, new={new_doc.get('pages')}")
    lines.append(f"  elements: old={old_doc.get('elements')}, new={new_doc.get('elements')}")
    extraction = summary.get("extraction", {})
    old_extr = extraction.get("old", {})
    new_extr = extraction.get("new", {})
    lines.append(f"  extraction: verdict={extraction.get('verdict')}, old={old_extr.get('status')}/{old_extr.get('warning_count', 0)}/{old_extr.get('issue_count', 0)}, new={new_extr.get('status')}/{new_extr.get('warning_count', 0)}/{new_extr.get('issue_count', 0)}")
    changes = summary.get("changes", {})
    lines.append(f"  changes: modified={changes.get('modified', 0)}, added={changes.get('added', 0)}, deleted={changes.get('deleted', 0)}, table={changes.get('table', 0)}")
    disp = summary.get("disposition", {})
    lines.append(f"  disposition: accepted={disp.get('accepted', 0)}, review={disp.get('review', 0)}, suppressed={disp.get('suppressed', 0)}")
    matching = summary.get("matching", {})
    lines.append(f"  matching: unchanged={matching.get('unchanged')}, modified={matching.get('modified')}, old_unmatched={matching.get('old_unmatched')}, new_unmatched={matching.get('new_unmatched')}")
    tables = summary.get("tables", {})
    lines.append(f"  tables: changes={tables.get('changes', 0)}, unresolved={tables.get('unresolved_reviews', 0)}")
    evaluator = summary.get("evaluator", {})
    lines.append(f"  evaluator: provider={evaluator.get('provider')}, mode={evaluator.get('mode')}")
    return "\n".join(lines)

async def main(root: Path | None = None) -> None:
    if root is None:
        root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    api_base, api_port = _api_base()
    _require_credentials()
    input_dir = root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    child_env = {
        **os.environ,
        "FASTMCP_CHECK_FOR_UPDATES": "off",
        "CRR_API_BASE": api_base,
        "CRR_MCP_UPLOAD_ROOT": str(input_dir),
        "CRR_PROJECT_ROOT": str(root / "workspace"),
        "CRR_RUN_LLM": "real",
        "PYTHONPATH": os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH", "")])),
    }

    api = _start_api(child_env, root, api_base, api_port)
    server_file = str(root / "crr_compare" / "mcp_server.py")
    reader_server_file = str(root / "crr_compare" / "reader_mcp_server.py")
    transport = PythonStdioTransport(server_file, env=child_env, cwd=str(root))
    reader_transport = PythonStdioTransport(reader_server_file, env=child_env, cwd=str(root))
    try:
        async with Client(transport) as client:
            old_pdf, new_pdf = prompt_for_two_pdfs(input_dir)
            session_id = uuid.uuid4().hex
            job_id, status = await submit_and_poll(client, old_pdf, new_pdf, session_id)
            summary = unwrap(await client.call_tool("retrieve_summary", {"job_id": job_id}))
            summary = summary if isinstance(summary, dict) else json.loads(summary)
            print(format_summary(summary))
            print(f"redline: {api_base}/jobs/{job_id}/artifacts/comparison_redline.pdf")

            loop = asyncio.get_running_loop()
            tia = TiaTextClient(os.environ["TIA_API_KEY"])
            # Q&A uses a separate reader MCP surface: exactly summarize/search/details.
            async with Client(reader_transport) as reader_client:
                reader_gateway = StdioReaderGateway(reader_client, loop)
                agent = TiaComparisonAgent(tia, reader_gateway, max_steps=5, result_limit=8)
                while True:
                    question = await asyncio.to_thread(input, "you> ")
                    if question.strip() in {"quit", "exit"}:
                        break
                    answer = await asyncio.to_thread(agent.ask, question, job_id)
                    print("agent:", answer)
    finally:
        _stop_process(api)