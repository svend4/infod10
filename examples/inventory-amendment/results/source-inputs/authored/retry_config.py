"""Manually authored literals derived from urllib3 Retry 2.5.0.

This is an adapter for selected defaults, not upstream urllib3 source,
not a runnable client and not proof of urllib3 runtime behavior.
"""
DEFAULT_ALLOWED_METHODS = {"HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE"}
TOTAL_DEFAULT = 10
