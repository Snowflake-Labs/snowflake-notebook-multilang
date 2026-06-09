"""Tests for %%R magic -i/-o flag normalization."""

from sfnb_multilang.helpers.r_helpers import _normalize_r_magic_io_flags


def test_o_list_spaces_after_comma():
    line = "-w 700 -h 450 -o p, mtcars"
    assert _normalize_r_magic_io_flags(line) == "-w 700 -h 450 -o p,mtcars"


def test_i_list_spaces_after_comma():
    line = "-i py_a, py_b"
    assert _normalize_r_magic_io_flags(line) == "-i py_a,py_b"


def test_o_list_no_interior_spaces_unchanged():
    line = "-o p,mtcars"
    assert _normalize_r_magic_io_flags(line) == "-o p,mtcars"


def test_single_o_unchanged():
    line = "-w 700 -h 450 -o p"
    assert _normalize_r_magic_io_flags(line) == "-w 700 -h 450 -o p"


def test_rename_syntax_preserved():
    line = "-i py_df=r_df, other=r_other"
    assert _normalize_r_magic_io_flags(line) == "-i py_df=r_df,other=r_other"
