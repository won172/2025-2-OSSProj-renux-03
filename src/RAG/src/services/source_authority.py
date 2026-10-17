"""Source authority is narrower than official hosting; no user text is read here."""
import re
from urllib.parse import urlsplit,unquote,parse_qs


def requires_authority_review(url,metadata=None):
    metadata=metadata or {}
    if metadata.get('source_authority') in {'unreviewed_user_content','requires_authority_review'}:
        return True
    parsed=urlsplit(url or '')
    path=unquote(parsed.path).lower()
    boards=parse_qs(parsed.query,keep_blank_values=True)
    if any(re.fullmatch(r'(?:qna|freeboard|studentboard|free)',value,re.I) for key,values in boards.items() if key.lower() in {'bo_table','board','board_id','boardid'} for value in values):return True
    return bool(re.search(r'/(?:qna|freeboard|studentboard)(?:/|$)|/article/(?:free|qna)(?:/|$)',path))
