"""Project-authored literal adapter for four selected captured declarations.

This module is input data for AST inspection, not upstream implementation.
"""

DEFAULT_ALLOWED_METHODS = ["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE"]
TOTAL_DEFAULT = 10
DEFAULT_REMOVE_HEADERS_ON_REDIRECT = ["Cookie", "Authorization", "Proxy-Authorization"]
BACKOFF_FACTOR_DEFAULT = 0
