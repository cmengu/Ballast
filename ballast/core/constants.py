"""Shared runtime constants for Ballast core modules.

Centralises model IDs so a version bump touches one file.
"""

# Fast/cheap model for escalation, probe, and Layer-2 evaluator LLM calls.
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# Full reasoning model for Layer-1 scoring (constraint + intent) and spec operations.
SONNET_MODEL = "claude-sonnet-4-6"
