from __future__ import annotations
import os
import logging
from typing import Any


def response_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "response", "output_text", "answer"):
            if isinstance(value.get(key), str):
                return value[key]
    return str(value)

logger = logging.getLogger(__name__)

class TiaTextClient:
    """Thin adapter matching the working Stage-13 TIA integration."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise RuntimeError("Missing TIA_API_KEY. Put it in the process environment (or a local `.env`).")
        self.api_key = api_key
        self.model = os.getenv("LLM_MODEL", "").strip()
        logger.warning("TIA agent model configured as: %s", self.model)
        if not self.model:
            raise RuntimeError(
                "Missing LLM_MODEL. Add LLM_MODEL=<approved-model> to .env."
            )
        

    def invoke(self, prompt: str, system_message: str) -> str:
        import bbk_ai_hub_sdk

        value = bbk_ai_hub_sdk.invoke_text_api(
            self.api_key,
            prompt, 
            system_message,
            model = self.model)
        # Log for LLM answer
        # logger.warning("TIA raw response type: %s", type(value).__name__)

        return response_text(value)

