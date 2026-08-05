"""Multipart parsing, so the person can drag a file in instead of typing a path. D059.

A browser will not tell a page where a chosen file lives on disk, and it is
right not to: a page that learned your filesystem layout from a file picker
would be a page that learned more than you offered. So the only way to accept a
file from the form is to accept its **contents**.

Parsing multipart by hand is a known source of subtle errors: boundaries,
line endings, quoting, encoding. None of that is written here. The body is
handed to :mod:`email`, which is the standard library's MIME parser and has
been reading exactly this format for decades. Building the wrapper is nine
lines; getting the format right ourselves would be a hundred and would be
wrong in ways only a strange filename would reveal.

The result carries the **original filename**, which matters more than it looks.
D042 made the provenance record the input's name, and a file written to a
temporary directory under a generated name would put that generated name in
the report. The person would then have provenance for a file that never
existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import HTTP

__all__ = ["Upload", "parse_multipart"]


@dataclass(frozen=True, slots=True)
class Upload:
    """One submitted field, which may or may not have been a file.

    Attributes
    ----------
    value : str
        The field's text. Empty for a file field.
    filename : str or None
        The name the browser reported, or ``None`` for a plain text field. It
        is untrusted: it comes from the person's machine and is used only as a
        label and as the basename of a temporary file, never as a path.
    content : bytes or None
        The file's bytes, or ``None`` for a plain text field.
    """

    value: str = ""
    filename: str | None = None
    content: bytes | None = None

    @property
    def is_file(self) -> bool:
        """True when the browser sent a file rather than a text field."""
        return self.content is not None


def parse_multipart(body: bytes, content_type: str) -> dict[str, Upload]:
    """Split a ``multipart/form-data`` body into its fields.

    Parameters
    ----------
    body : bytes
        The raw request body.
    content_type : str
        The ``Content-Type`` header, which carries the boundary.

    Returns
    -------
    dict of str to Upload
        Keyed by field name. A field submitted twice keeps the last, which is
        what a form with unique names can only produce by accident.

    Notes
    -----
    A body that is not multipart, or that carries no parts, returns an empty
    mapping rather than raising. The caller answers that with the form and a
    reason, and a malformed request from something that is not our form is not
    an event worth a traceback.
    """
    if "multipart/form-data" not in content_type.lower():
        return {}

    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body
    )
    if not message.is_multipart():
        return {}

    fields: dict[str, Upload] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            continue
        filename = part.get_filename()
        # ``get_payload(decode=True)`` is typed as returning any of the shapes a
        # MIME part can hold. Only bytes is a body here, and anything else means
        # the part was not what our form sends.
        raw = part.get_payload(decode=True)
        payload = raw if isinstance(raw, bytes) else b""
        if filename:
            fields[name] = Upload(filename=filename, content=payload)
        else:
            fields[name] = Upload(value=payload.decode("utf-8", "replace").strip())
    return fields
