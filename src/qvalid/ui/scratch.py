"""Where an uploaded log waits between two requests. See D063.

The one step form did not need this: it wrote the upload, ran, and deleted the
directory before answering. Guided setup is two requests, because the symbology
draft cannot exist until the person has settled the column mapping, so the file
has to outlive the response that showed them the first draft.

Deliberately not a session, a cookie or a database. A token names a directory
this process created, the directory holds the person's own file on the person's
own machine, and everything is removed when the process ends. The interface
binds to the loopback interface only, so the token is not a secret keeping
anyone out; it is a name that cannot be guessed into naming somebody else's
directory, which matters because two browser tabs are two uploads.

Nothing here decides anything about a trade, which keeps ``05``'s permanent
constraint intact: this is storage, not calculation.
"""

from __future__ import annotations

import atexit
import secrets
import shutil
import tempfile
from pathlib import Path

__all__ = ["Scratch"]


class Scratch:
    """A directory per upload, named by an unguessable token.

    Notes
    -----
    Bounded by :attr:`limit`. A browser left open all day, reloading, would
    otherwise fill the disk with copies of the same log, and the interface has
    no idea when a person is finished. The oldest directory goes when the limit
    is reached, which can strand a tab that has been idle for a long time; that
    is a worse failure than filling a disk, and it announces itself as a
    missing token rather than silently.
    """

    def __init__(self, limit: int = 16) -> None:
        self.limit = limit
        self._root = Path(tempfile.mkdtemp(prefix="quantify-ui-"))
        self._order: list[str] = []
        atexit.register(self.close)

    def store(self, filename: str, content: bytes) -> str:
        """Write an upload under a fresh token and return it.

        Only the last component of ``filename`` is used, and only as a leaf
        inside a directory this process just made: a filename is untrusted text
        from another machine and never a path. The original name is kept
        because D042 puts it in the provenance, and a generated name would give
        the person a report naming a file that never existed.
        """
        token = secrets.token_urlsafe(16)
        folder = self._root / token
        folder.mkdir()
        (folder / Path(filename).name).write_bytes(content)
        self._order.append(token)
        while len(self._order) > self.limit:
            shutil.rmtree(self._root / self._order.pop(0), ignore_errors=True)
        return token

    def folder_of(self, token: str) -> Path | None:
        """Return the directory for a token, or ``None`` if unknown or expired.

        The token is checked against the list this object keeps rather than by
        looking on disk, so a token containing separators or dots cannot reach
        a directory by describing a route to it.
        """
        if token not in self._order:
            return None
        folder = self._root / token
        return folder if folder.is_dir() else None

    def log_of(self, token: str) -> Path | None:
        """Return the stored trade log for a token, or ``None``.

        The log is the only file the store puts there itself; the three YAML
        files that :func:`~qvalid.ui.pages.finish_page` writes alongside it are
        named, so the log is what remains once those are excluded.
        """
        folder = self.folder_of(token)
        if folder is None:
            return None
        logs = [item for item in folder.iterdir() if item.suffix.lower() != ".yaml"]
        return logs[0] if len(logs) == 1 else None

    def close(self) -> None:
        """Remove everything. Registered at exit and safe to call twice."""
        shutil.rmtree(self._root, ignore_errors=True)
        self._order.clear()
