"""Source authority is narrower than official hosting; no user text is read here."""
import re
from urllib.parse import urlsplit,unquote


def requires_authority_review(url,metadata=None):
    metadata=metadata or {}
    if metadata.get('source_authority') in {'unreviewed_user_content','requires_authority_review'}:
        return True
    path=unquote(urlsplit(url or '').path).lower()
    return bool(re.search(r'/(?:qna|freeboard|studentboard)(?:/|$)|/article/(?:free|qna)(?:/|$)',path))
