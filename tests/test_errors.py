"""Tests for the exception hierarchy in beenary.errors."""

from __future__ import annotations

import pytest

from beenary.errors import BeenaryError, FormatError, LensError, PatchError


def test_all_errors_derive_from_beenary_error() -> None:
    for cls in (LensError, FormatError, PatchError):
        assert issubclass(cls, BeenaryError)
        assert issubclass(cls, Exception)


def test_lens_error_without_path_keeps_message() -> None:
    err = LensError("boom")
    assert err.path is None
    assert err.message == "boom"
    assert str(err) == "boom"


def test_lens_error_with_empty_path_has_no_prefix() -> None:
    err = LensError("boom", ())
    assert err.path == ()
    assert str(err) == "boom"


def test_lens_error_formats_field_and_index_path() -> None:
    err = LensError("boom", ("header", 0, "value"))
    assert err.path == ("header", 0, "value")
    assert err.message == "header[0].value: boom"
    assert str(err) == "header[0].value: boom"


def test_lens_error_formats_path_starting_with_index() -> None:
    err = LensError("boom", (0, "value"))
    assert str(err) == "[0].value: boom"


def test_lens_error_single_field_path() -> None:
    err = LensError("boom", ("items",))
    assert str(err) == "items: boom"


@pytest.mark.parametrize("cls", [FormatError, PatchError])
def test_other_errors_accept_plain_message(cls: type[BeenaryError]) -> None:
    err = cls("bad format")
    assert str(err) == "bad format"


def test_lens_error_is_catchable_as_beanary_error() -> None:
    with pytest.raises(BeenaryError):
        raise LensError("nope", ("a",))
