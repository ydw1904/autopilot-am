"""Shared application state — read by sidebar, written by page modules."""

APP: dict = {
    'chrome_launched':  False,
    'planner_running':  False,
    'circuits':         [],
}
