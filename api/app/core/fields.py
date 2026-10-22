# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Reusable request field types: trimmed text that cannot be blank, cannot hold a NUL character
and has a length limit (BUG-P2-01, -07, -28, -35). `Opt*` types turn blank text into None."""

from typing import Annotated

from pydantic import AfterValidator, StringConstraints


def _no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Remove the unusual character (NUL) from this text")
    return value


def _blank_to_none(value: str) -> str | None:
    return _no_nul(value) or None


Name200 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_no_nul),
]
Text500 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(_no_nul),
]
Opt200 = (
    Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=200),
        AfterValidator(_blank_to_none),
    ]
    | None
)
Opt320 = (
    Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=320),
        AfterValidator(_blank_to_none),
    ]
    | None
)
Opt2000 = (
    Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=2000),
        AfterValidator(_blank_to_none),
    ]
    | None
)
# keep the text as typed (blank stays blank) so the service can say which field is wrong
Raw320 = Annotated[str, StringConstraints(max_length=320), AfterValidator(_no_nul)] | None
Raw2000 = Annotated[str, StringConstraints(max_length=2000), AfterValidator(_no_nul)] | None
