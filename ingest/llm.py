"""
LLM backends for BuildCheck extraction.

Extraction code only talks to `LLMClient.extract_json()`, so the model behind it
can change without touching anything else. Pick a backend with an environment
variable:

    BUILDCHECK_LLM=lmstudio   (default) local model served by LM Studio
    BUILDCHECK_LLM=bedrock    Claude on Amazon Bedrock (needs AWS_BEARER_TOKEN_BEDROCK)
    BUILDCHECK_MODEL=...      model id for the chosen backend (optional for LM Studio)
"""

from __future__ import annotations

import json
import os
from typing import Protocol

from pydantic import BaseModel


class LLMClient(Protocol):
    name: str

    def extract_json(self, system: str, user: str, schema: type[BaseModel]) -> dict:
        """Return a dict shaped like `schema`. Raise ValueError if no JSON comes back."""
        ...


def coerce_json(raw) -> dict:
    """
    Return a dict whether the model gave clean JSON or reasoning text followed by JSON.
    Scans '{' positions from the end so the final answer wins over braces in the reasoning.
    For nested JSON the scan keeps widening to the outermost object of that final answer
    (the first '{' found from the end is the innermost field object, not the answer).
    """
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError(f"Unexpected model output type: {type(raw).__name__}")
    decoder = json.JSONDecoder()
    best: tuple[dict, int] | None = None          # (object, end index)
    for i in range(len(raw) - 1, -1, -1):
        if raw[i] == "{":
            try:
                obj, end = decoder.raw_decode(raw, i)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            # Only an object reaching the end of the final answer can be (a wider view of) it;
            # sibling field objects and earlier objects in the reasoning end sooner.
            if best is None or end >= best[1]:
                best = (obj, end)
    if best:
        return best[0]
    raise ValueError(f"No JSON object found in model output:\n{raw[:500]}")


class LMStudioClient:
    """Local model via the LM Studio server (Developer tab -> Start Server)."""

    name = "lmstudio"

    def __init__(self, model: str | None = None):
        import lmstudio as lms  # imported here so tests don't need LM Studio installed

        self.model = model
        self._llm = lms.llm(model) if model else lms.llm()  # None = whatever is loaded

    def extract_json(self, system: str, user: str, schema: type[BaseModel]) -> dict:
        import lmstudio as lms

        chat = lms.Chat(system)
        chat.add_user_message(user)
        result = self._llm.respond(chat, response_format=schema, config={"temperature": 0})
        return coerce_json(result.parsed)


class BedrockClient:
    """
    Claude on Amazon Bedrock. Forces a single tool call whose input schema is the
    extraction schema, so the reply is always structured JSON.
    Not exercised by the test suite; run scratch/bedrock_test.py-style checks first.
    """

    name = "bedrock"

    def __init__(self, model: str | None = None, region: str | None = None):
        import boto3

        self.model = model or os.environ.get("BUILDCHECK_MODEL")
        if not self.model:
            raise ValueError("Set BUILDCHECK_MODEL to the Bedrock model id (copy it from the model catalog).")
        self._client = boto3.client("bedrock-runtime", region_name=region or os.environ.get("AWS_REGION", "us-east-1"))

    def extract_json(self, system: str, user: str, schema: type[BaseModel]) -> dict:
        tool = {
            "toolSpec": {
                "name": "record_extraction",
                "description": "Record the values extracted from the document.",
                "inputSchema": {"json": schema.model_json_schema()},
            }
        }
        resp = self._client.converse(
            modelId=self.model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 2000},
            toolConfig={"tools": [tool], "toolChoice": {"tool": {"name": "record_extraction"}}},
        )
        for block in resp["output"]["message"]["content"]:
            if "toolUse" in block:
                return block["toolUse"]["input"]
        raise ValueError("Bedrock response contained no tool call.")


def get_client(backend: str | None = None, model: str | None = None) -> LLMClient:
    backend = (backend or os.environ.get("BUILDCHECK_LLM", "lmstudio")).lower()
    model = model or os.environ.get("BUILDCHECK_MODEL")
    if backend == "lmstudio":
        return LMStudioClient(model)
    if backend == "bedrock":
        return BedrockClient(model)
    raise ValueError(f"Unknown BUILDCHECK_LLM backend: {backend!r} (use 'lmstudio' or 'bedrock')")
