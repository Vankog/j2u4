"""API clients for Jira and Tempo."""

import time

import requests

ACCOUNT_FIELD = "customfield_10048"

# Transient HTTP statuses worth retrying. 4xx is deliberately excluded —
# auth/permission/not-found errors don't fix themselves on retry.
_TRANSIENT_STATUS = {500, 502, 503, 504}
_RETRY_ATTEMPTS = 3
_BACKOFF_SECONDS = (1, 3)  # sleeps between attempts 1->2 and 2->3


class ApiError(Exception):
    """User-friendly API error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _handle_api_error(response: requests.Response, service: str) -> str:
    """Convert HTTP errors to user-friendly messages."""
    status = response.status_code

    messages = {
        401: f"{service}: Authentication failed. Check your API token!",
        403: f"{service}: Access denied. Check your permissions or API token!",
        404: f"{service}: Resource not found. Check the URL in config.json!",
        429: f"{service}: Too many requests. Wait a moment and try again.",
        500: f"{service}: Server error. The service may be temporarily unavailable.",
        502: f"{service}: Bad gateway. The service may be temporarily unavailable.",
        503: f"{service}: Service unavailable. Try again later.",
    }

    return messages.get(status, f"{service}: HTTP {status} - {response.reason}")


def _get_with_retry(
    url: str,
    service: str,
    *,
    auth=None,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: int = 30,
    _sleep=time.sleep,
) -> requests.Response:
    """GET with retry on transient failures (5xx, connection, timeout).

    Retries up to _RETRY_ATTEMPTS total with exponential backoff. Returns
    the Response on success OR on a final non-transient status (2xx/3xx/4xx
    are returned immediately so the caller can decide what to do with
    them). Raises ApiError only when every attempt failed at the network
    level (connection/timeout) — a 5xx on the last attempt is returned so
    the caller can produce a status-specific error message.

    The _sleep parameter is injectable for tests; production code uses
    time.sleep.
    """
    last_response: requests.Response | None = None
    last_network_error: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            last_response = requests.get(
                url,
                auth=auth,
                headers=headers,
                params=params,
                timeout=timeout,
            )
            last_network_error = None
        except requests.exceptions.ConnectionError as e:
            last_network_error = e
            last_response = None
        except requests.exceptions.Timeout as e:
            last_network_error = e
            last_response = None
        else:
            if last_response.status_code not in _TRANSIENT_STATUS:
                return last_response
        if attempt < _RETRY_ATTEMPTS - 1:
            _sleep(_BACKOFF_SECONDS[attempt])

    if last_response is not None:
        # Final attempt got a transient 5xx — hand it back so the caller
        # can build a status-specific ApiError via _handle_api_error.
        return last_response
    if isinstance(last_network_error, requests.exceptions.Timeout):
        raise ApiError(
            f"{service}: Connection timed out after {_RETRY_ATTEMPTS} attempts."
        )
    raise ApiError(
        f"{service}: Cannot connect after {_RETRY_ATTEMPTS} attempts. Check your network!"
    )


class JiraClient:
    """Client for Jira REST API."""

    def __init__(self, config: dict):
        self.base_url = config["jira"]["base_url"]
        self.email = config["jira"]["user_email"]
        self.token = config["jira"]["api_token"]

    def get_my_account_id(self) -> str:
        """Get the current user's Jira account ID."""
        r = _get_with_retry(
            f"{self.base_url}/rest/api/3/myself",
            "Jira",
            auth=(self.email, self.token),
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if not r.ok:
            raise ApiError(_handle_api_error(r, "Jira"), r.status_code)
        return r.json()["accountId"]

    def get_issue_details(self, issue_id: int) -> dict | None:
        """Fetch issue details (key, summary, account field).

        Best-effort: returns None on any non-200 (404 = no permission to read
        this issue, common for cross-team tickets) and on persistent network
        failures. Callers must handle None as "summary unavailable" without
        crashing.
        """
        try:
            r = _get_with_retry(
                f"{self.base_url}/rest/api/3/issue/{issue_id}",
                "Jira",
                auth=(self.email, self.token),
                headers={"Accept": "application/json"},
                params={"fields": f"key,summary,{ACCOUNT_FIELD}"},
                timeout=10,
            )
        except ApiError:
            return None
        if r.status_code == 200:
            return r.json()
        return None


class TempoClient:
    """Client for Tempo REST API."""

    def __init__(self, config: dict):
        self.token = config["tempo"]["api_token"]

    def _paginated_get(self, url: str, params: dict) -> list[dict]:
        """GET a paginated Tempo endpoint, return concatenated results."""
        results: list[dict] = []
        while url:
            r = _get_with_retry(
                url,
                "Tempo",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
                params=params,
                timeout=30,
            )
            if not r.ok:
                raise ApiError(_handle_api_error(r, "Tempo"), r.status_code)
            data = r.json()
            results.extend(data.get("results", []))
            url = (data.get("metadata") or {}).get("next")
            params = {}  # pagination URLs already carry params
        return results

    def fetch_worklogs(
        self, account_id: str, date_from: str, date_to: str
    ) -> list[dict]:
        """Fetch worklogs for a user within a date range."""
        return self._paginated_get(
            f"https://api.tempo.io/4/worklogs/user/{account_id}",
            {"from": date_from, "to": date_to, "limit": 1000},
        )

    def fetch_accounts(self) -> list[dict]:
        """Fetch all Tempo accounts the token has access to."""
        return self._paginated_get(
            "https://api.tempo.io/4/accounts",
            {"limit": 200},
        )

    def fetch_worklogs_by_account(
        self, account_key: str, date_from: str, date_to: str
    ) -> list[dict]:
        """Fetch worklogs for a Tempo account within a date range. The Tempo
        account is implicit in the URL — no Jira issue lookup needed to
        resolve the account."""
        return self._paginated_get(
            f"https://api.tempo.io/4/worklogs/account/{account_key}",
            {"from": date_from, "to": date_to, "limit": 1000},
        )

    def get_worklog(self, worklog_id: int) -> dict | None:
        """Direct lookup of a single worklog by id. Used by orphan detection
        (verify a Unit4 marker is truly gone in Tempo, not just outside the
        fetched range) and by ticket-name resolution (recover the Jira issue
        id when the Unit4 row scan only yielded the WL marker).

        Returns:
            The worklog dict on HTTP 200.
            None if Tempo says it does not exist (HTTP 404).

        Raises:
            ApiError on any other failure (network, auth, 5xx) so the
            caller can decide to abort instead of mistakenly treating
            a transient error as a missing worklog.
        """
        r = _get_with_retry(
            f"https://api.tempo.io/4/worklogs/{worklog_id}",
            "Tempo",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        raise ApiError(_handle_api_error(r, "Tempo"), r.status_code)

    def worklog_exists(self, worklog_id: int) -> bool:
        """Thin wrapper around get_worklog() for callers that only need
        existence, not the data."""
        return self.get_worklog(worklog_id) is not None
