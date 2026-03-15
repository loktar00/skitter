"""
Workflow Recorder — parses agent CLI stream-json output to extract
Playwright MCP tool calls and convert them into replayable workflow steps.
"""

from __future__ import annotations

import json
from typing import Any

from workflow_models import WorkflowStep

# MCP tool name prefix
_PREFIX = "mcp__playwright__browser_"

# Actions that are for AI decision-making, not replay
_SKIP_ACTIONS = {"snapshot", "take_screenshot", "console_messages", "network_requests", "install"}


def parse_stream_line(line: str) -> dict | None:
    """Parse a single stream-json line, return dict or None if unparseable."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def extract_tool_call(content_block: dict) -> tuple[str, dict[str, Any]] | None:
    """
    Extract (action, params) from a tool_use content block.
    Returns None if not a Playwright MCP tool or should be skipped.
    """
    if content_block.get("type") != "tool_use":
        return None

    tool_name = content_block.get("name", "")
    if not tool_name.startswith(_PREFIX):
        return None

    action = tool_name[len(_PREFIX):]
    if action in _SKIP_ACTIONS:
        return None

    params = content_block.get("input", {})
    return action, params


def stream_to_steps(lines: list[str]) -> list[WorkflowStep]:
    """
    Parse stream-json lines and extract Playwright tool calls as WorkflowStep list.

    Expected format per line:
    {"type":"assistant","message":{"content":[{"type":"tool_use","name":"mcp__playwright__browser_click","input":{...}}]}}
    """
    steps: list[WorkflowStep] = []
    seq = 0

    for line in lines:
        data = parse_stream_line(line)
        if data is None:
            continue

        # Only process assistant messages with tool_use
        if data.get("type") != "assistant":
            continue

        message = data.get("message", {})
        content_blocks = message.get("content", [])

        for block in content_blocks:
            result = extract_tool_call(block)
            if result is None:
                continue

            action, params = result
            seq += 1

            # Build description from params
            description = _build_description(action, params)

            steps.append(WorkflowStep(
                seq=seq,
                action=action,
                params=params,
                description=description,
            ))

    return steps


def _build_description(action: str, params: dict[str, Any]) -> str:
    """Build a human-readable description for a step."""
    if action == "navigate":
        return f"Navigate to {params.get('url', '?')}"
    if action == "click":
        return f"Click {params.get('element', params.get('ref', '?'))}"
    if action == "type":
        text = params.get("text", "")
        preview = text[:40] + ("..." if len(text) > 40 else "")
        return f"Type '{preview}'"
    if action == "fill_form":
        count = len(params.get("fields", []))
        return f"Fill {count} form field(s)"
    if action == "select_option":
        return f"Select option in {params.get('element', '?')}"
    if action == "press_key":
        return f"Press {params.get('key', '?')}"
    if action == "hover":
        return f"Hover over {params.get('element', params.get('ref', '?'))}"
    if action == "wait_for":
        if params.get("text"):
            return f"Wait for text: {params['text']}"
        if params.get("textGone"):
            return f"Wait for text gone: {params['textGone']}"
        if params.get("time"):
            return f"Wait {params['time']}s"
        return "Wait"
    if action == "evaluate":
        return "Run JavaScript"
    if action == "file_upload":
        return "Upload file(s)"
    if action == "drag":
        return f"Drag {params.get('startElement', '?')} to {params.get('endElement', '?')}"
    if action == "handle_dialog":
        return f"{'Accept' if params.get('accept') else 'Dismiss'} dialog"
    if action == "navigate_back":
        return "Go back"
    if action == "tabs":
        return f"Tab action: {params.get('action', '?')}"
    if action == "resize":
        return f"Resize to {params.get('width')}x{params.get('height')}"
    if action == "close":
        return "Close browser"
    return f"{action}"
