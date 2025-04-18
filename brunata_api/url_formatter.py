"""HTTPX Log Formatter."""

from logging import Formatter, LogRecord


class RedactQueryParams(Formatter):
  """Redact sensitive query parameters from log messages."""

  @staticmethod
  def _filter(s: str) -> str:
    """Find URL."""
    url_start_index = s.find("http")
    if url_start_index == -1:
      return s
    url_end_index = s.find(" ", url_start_index)
    url = s[url_start_index:url_end_index]
    """ Find query parameters """
    query_index = url.find("?")
    if query_index == -1:
      return s
    query = url[query_index + 1:]
    # Find sensitive query parameters
    sensitive_params = [
      "code_challenge",
      "redirect_uri",
      "csrf_token",
      "tx",
    ]
    params = dict(item.split for item in query.split("&"))
    redacted_params = {k: "***" if k in sensitive_params else v for k, v in params.items()}
    # Redact query parameters
    return s.replace(
      query,
      "&".join(f"{k}={v}" for k, v in redacted_params.items()),
    )

  def format(self, record: LogRecord) -> str:
    """Format log message."""
    return self._filter(Formatter.format(self, record))
