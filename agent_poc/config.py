from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


POC_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = POC_ROOT.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(POC_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    api_base: str = os.getenv("CRR_API_BASE", "http://127.0.0.1:8321").rstrip("/")
    backend: str = os.getenv("CRR_POC_BACKEND", "mock").strip().lower()
    tia_api_key: str = os.getenv("TIA_API_KEY", "").strip()
    max_steps: int = int(os.getenv("CRR_AGENT_MAX_STEPS", "5"))
    result_limit: int = int(os.getenv("CRR_AGENT_RESULT_LIMIT", "8"))
    mcp_server_file: Path = Path(
        os.getenv(
            "CRR_MCP_SERVER_FILE",
            str(PROJECT_ROOT / "crr_compare" / "mcp_server.py"),
        )
    ).resolve()
    mock_report: Path = POC_ROOT / "mock_data" / "comparison_result.json"


def get_settings() -> Settings:
    settings = Settings()
    if settings.backend not in {"api", "mcp", "mock"}:
        raise ValueError("CRR_POC_BACKEND must be 'api', 'mcp', or 'mock'.")
    if settings.max_steps < 1 or settings.max_steps > 10:
        raise ValueError("CRR_AGENT_MAX_STEPS must be between 1 and 10.")
    if settings.result_limit < 1 or settings.result_limit > 50:
        raise ValueError("CRR_AGENT_RESULT_LIMIT must be between 1 and 50.")
    return settings

