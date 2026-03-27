"""Backward-compatibility stub -- delegates to sfnb_setup.

Existing notebooks that ``from eai_helper import ensure_eai`` will
continue to work.  New notebooks should use sfnb_setup.setup_notebook()
or sfnb_setup.ensure_eai() directly.
"""
from sfnb_setup import ensure_eai  # noqa: F401

__all__ = ["ensure_eai"]
