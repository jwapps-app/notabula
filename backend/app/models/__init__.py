"""ORM models. Importing this package registers all tables on Base.metadata."""

from app.models.attachment import Attachment
from app.models.folder import Folder
from app.models.link import NoteLink
from app.models.link_preview import LinkPreview
from app.models.note import Note
from app.models.revision import NoteRevision
from app.models.share import FolderShare, NoteShare
from app.models.tag import Tag, note_tags
from app.models.user import Session, TotpRecoveryCode, User

__all__ = [
    "Attachment",
    "Folder",
    "FolderShare",
    "LinkPreview",
    "Note",
    "NoteLink",
    "NoteRevision",
    "NoteShare",
    "Session",
    "Tag",
    "TotpRecoveryCode",
    "User",
    "note_tags",
]
