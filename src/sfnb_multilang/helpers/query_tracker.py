"""Implicit cross-language query ID capture for Snowflake Workspace Notebooks.

Hooks into the ServerConnection query listener to capture every query ID
executed through the shared Snowpark session.  User-originated SQL cell
queries are distinguished from internal housekeeping queries by the
presence of ``dataframe_uuid`` in the listener kwargs.

IPython ``pre_run_cell`` / ``post_run_cell`` hooks bracket cell execution
so captured queries can be attributed to specific cells and associated
with the ``dataframe_N`` variables that the Workspace creates for SQL
cell results.

Usage (automatic -- called by setup_notebook):

    from query_tracker import install_query_tracker
    install_query_tracker(session)

Public API (after installation):

    from query_tracker import nb_last_query_id, nb_query_id
    qid = nb_last_query_id()
    qid = nb_query_id(dataframe="dataframe_4")
    qid = nb_query_id(cell=3)
"""
from __future__ import annotations

import threading
from typing import Optional


_tracker: Optional["QueryTracker"] = None

_nb_queries: dict = {
    "cells": {},
    "dataframes": {},
    "all": [],
}


class QueryTracker:
    """ServerConnection query listener that captures query IDs.

    Register an instance via ``session._conn.add_query_listener(tracker)``.
    The ServerConnection calls ``tracker._notify(query_record, **kwargs)``
    for every query executed through the session.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cell_buffer: list[dict] = []
        self._known_dfs: set[str] = set()
        self._current_cell: int | None = None

    def _notify(self, query_record, **kwargs):
        """Called by ServerConnection for every query execution."""
        try:
            qid = (
                getattr(query_record, "sfqid", None)
                or getattr(query_record, "query_id", None)
            )
            sql = (
                getattr(query_record, "query", None)
                or getattr(query_record, "sql_text", None)
            )
            if qid is None:
                try:
                    qid = str(query_record)
                except BaseException:
                    qid = "<unknown>"
            if sql is None:
                sql = ""
            entry = {
                "query_id": qid,
                "sql": sql,
                "is_user_query": "dataframe_uuid" in kwargs,
                "dataframe_uuid": kwargs.get("dataframe_uuid"),
            }
            with self._lock:
                self._cell_buffer.append(entry)
        except BaseException:
            pass

    def _pre_run_cell(self, info):
        """IPython pre_run_cell callback -- flush orphans then snapshot.

        Any queries sitting in the buffer were produced by non-Python cells
        (e.g. SQL cells) that never trigger post_run_cell.  Flush them to
        ``_nb_queries['all']`` so they are permanently available via
        ``nb_last_query_id()`` before the buffer is cleared.
        """
        try:
            ip = _get_ipython()
            if ip is None:
                return

            current_dfs = {
                k for k in list(ip.user_ns.keys())
                if k.startswith("dataframe_")
            }
            new_dfs = current_dfs - self._known_dfs

            with self._lock:
                orphaned = list(self._cell_buffer)
                self._cell_buffer.clear()
                self._current_cell = None

            for entry in orphaned:
                df_name = None
                if entry["is_user_query"] and new_dfs:
                    df_name = sorted(new_dfs, key=_df_sort_key)[-1]
                    new_dfs.discard(df_name)
                record = {
                    "query_id": entry["query_id"],
                    "sql": entry["sql"],
                    "cell": None,
                    "dataframe": df_name,
                }
                _nb_queries["all"].append(record)
                if df_name:
                    _nb_queries["dataframes"][df_name] = entry["query_id"]

            self._known_dfs = current_dfs
        except BaseException:
            self._known_dfs = set()

    def _post_run_cell(self, result):
        """IPython post_run_cell callback -- commit captured queries."""
        try:
            self._post_run_cell_inner(result)
        except BaseException:
            pass

    def _post_run_cell_inner(self, result):
        cell_num = getattr(result, "execution_count", None)

        ip = _get_ipython()
        new_dfs: set[str] = set()
        if ip is not None:
            try:
                current_dfs = {
                    k for k in list(ip.user_ns.keys())
                    if k.startswith("dataframe_")
                }
                new_dfs = current_dfs - self._known_dfs
            except Exception:
                pass

        with self._lock:
            user_queries = [e for e in self._cell_buffer if e["is_user_query"]]
            all_queries = list(self._cell_buffer)
            self._cell_buffer.clear()

        if not user_queries and not all_queries:
            return

        if user_queries:
            uq = user_queries[-1]
            df_name = None
            if len(new_dfs) == 1:
                df_name = next(iter(new_dfs))
            elif len(new_dfs) > 1:
                df_name = sorted(new_dfs, key=_df_sort_key)[-1]

            record = {
                "query_id": uq["query_id"],
                "sql": uq["sql"],
                "cell": cell_num,
                "dataframe": df_name,
            }
            _nb_queries["all"].append(record)
            if cell_num is not None:
                _nb_queries["cells"][cell_num] = record
            if df_name:
                _nb_queries["dataframes"][df_name] = uq["query_id"]
            return

        if all_queries:
            last = all_queries[-1]
            record = {
                "query_id": last["query_id"],
                "sql": last["sql"],
                "cell": cell_num,
                "dataframe": None,
            }
            _nb_queries["all"].append(record)
            if cell_num is not None:
                _nb_queries["cells"][cell_num] = record


def _df_sort_key(name: str) -> int:
    """Extract numeric suffix from dataframe_N for sorting."""
    try:
        return int(name.split("_", 1)[1])
    except (IndexError, ValueError):
        return 0


def _get_ipython():
    """Safe IPython accessor."""
    try:
        import IPython
        return IPython.get_ipython()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def nb_last_query_id() -> str | None:
    """Return the query ID of the most recent query, or None.

    Checks the in-flight buffer first (queries from the current cell or
    from non-Python cells that haven't been flushed yet), then falls back
    to committed queries from previous cells.  This ensures SQL cell
    queries are visible immediately without waiting for a Python cell
    lifecycle to flush them.
    """
    if _tracker is not None:
        with _tracker._lock:
            if _tracker._cell_buffer:
                return _tracker._cell_buffer[-1]["query_id"]
    if _nb_queries["all"]:
        return _nb_queries["all"][-1]["query_id"]
    return None


def nb_query_id(
    cell: int | None = None,
    dataframe: str | None = None,
) -> str | None:
    """Look up a query ID by cell execution count or dataframe name.

    Parameters
    ----------
    cell : int, optional
        IPython execution count (the In[N] number).
    dataframe : str, optional
        The ``dataframe_N`` variable name assigned by the Workspace.

    Returns
    -------
    str or None
        The Snowflake query ID, or None if not found.
    """
    if dataframe is not None:
        return _nb_queries["dataframes"].get(dataframe)
    if cell is not None:
        rec = _nb_queries["cells"].get(cell)
        return rec["query_id"] if rec else None
    return nb_last_query_id()


def get_registry() -> dict:
    """Return a shallow copy of the query registry for inspection."""
    return {
        "cells": dict(_nb_queries["cells"]),
        "dataframes": dict(_nb_queries["dataframes"]),
        "all": list(_nb_queries["all"]),
    }


def install_query_tracker(session) -> QueryTracker | None:
    """Install the query tracker on the Snowpark session.

    Registers a ``QueryTracker`` as a query listener on the session's
    ``ServerConnection`` and installs IPython cell hooks.  Safe to call
    multiple times -- subsequent calls are no-ops.

    Parameters
    ----------
    session : snowflake.snowpark.Session
        The active Snowpark session.

    Returns
    -------
    QueryTracker or None
        The installed tracker, or None if installation failed.
    """
    global _tracker

    if _tracker is not None:
        return _tracker

    # Verify ServerConnection has listener support
    conn = getattr(session, "_conn", None)
    if conn is None or not hasattr(conn, "add_query_listener"):
        return None

    tracker = QueryTracker()

    try:
        conn.add_query_listener(tracker)
    except BaseException:
        return None

    _tracker = tracker

    ip = _get_ipython()
    if ip is not None:
        try:
            ip.events.register("pre_run_cell", tracker._pre_run_cell)
            ip.events.register("post_run_cell", tracker._post_run_cell)
        except BaseException:
            pass

        try:
            ip.user_ns["_nb_query_tracker"] = tracker
            ip.user_ns["_nb_queries"] = _nb_queries
            ip.user_ns["nb_last_query_id"] = nb_last_query_id
            ip.user_ns["nb_query_id"] = nb_query_id
        except BaseException:
            pass

    return tracker
