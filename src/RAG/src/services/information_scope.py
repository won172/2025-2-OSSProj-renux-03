"""Typed query targets. Applicability targets never grant requester authority.

Academic terms are pairs, not independent year/semester sets. Calendar dates,
entry cohorts and a conditional statement's antecedent/consequent stay distinct.
"""
from dataclasses import dataclass
from datetime import date
import calendar
import re


@dataclass(frozen=True)
class InformationIntent:
    academic_terms: tuple[tuple[int | None, str | None], ...] = ()
    calendar_years: tuple[int, ...] = ()
    audiences: tuple[str, ...] = ()
    admission_terms: tuple[tuple[int | None, str | None], ...] = ()
    conditional_terms: tuple[tuple[int | None, str | None], ...] = ()
    comparison: bool = False
    calendar_ranges: tuple[tuple[str, str], ...] = ()


def temporal_intent(query: str) -> InformationIntent:
    # In a resolved follow-up, only the effective current query is supplied.
    query = str(query)
    years = list(re.finditer(r'(?<!\d)(20\d{2})\s*(학년도|년도|년)?', query))
    terms = []
    calendars = []
    occupied = set()
    for i, match in enumerate(years):
        year = int(match[1])
        tail = query[match.end():years[i+1].start() if i+1 < len(years) else len(query)]
        compact = re.match(r'\s*[-./]\s*([12])(?!\d|\s*[-./]\s*\d)', tail)
        semesters = list(re.finditer(r'([12])\s*학기', tail[:45]))
        # A full ISO/Korean date is a calendar field, never a compact term.
        dated = re.match(r'\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2}', tail)
        if compact and not dated:
            terms.append((year, compact[1])); occupied.add(year)
        elif semesters and not dated and not re.match(r'\s*\d{1,2}\s*월', tail):
            for semester in semesters:
                terms.append((year, semester[1]))
            occupied.add(year)
        elif match[2] == '학년도':
            terms.append((year, None)); occupied.add(year)
        elif match[2] in ('년', '년도') or dated:
            calendars.append(year)
    if not terms and not years:
        terms = [(None, m[1]) for m in re.finditer(r'([12])\s*학기', query)]
    # A resolved correction can put "2학기 기준" before the retained year.
    if len(terms)==1 and terms[0][1] is None:
        standalone={m[1] for m in re.finditer(r'([12])\s*학기',query)}
        if len(standalone)==1:terms=[(terms[0][0],next(iter(standalone)))]
    # Pure year coordination shares the following term regardless of the
    # year suffix. An explicit pair or intervening "전체"/month/cohort wording
    # is its own target and must not acquire that semester.
    for i in reversed(range(len(years)-1)):
        match=years[i]
        year=int(match[1])
        if year not in calendars and (year,None) not in terms:continue
        between=query[match.end():years[i+1].start()]
        following=[s for y,s in terms if y==int(years[i+1][1]) and s]
        if re.fullmatch(r'\s*(?:과|와|및|,|/)\s*',between) and len(set(following))==1:
            terms=[term for term in terms if term!=(year,None)]
            terms.append((year,following[0]))
            if year in calendars:calendars.remove(year)
    foreign = bool(re.search(r'외국인.*신입|신입.*외국인', query))
    reverse = foreign and bool(re.search(r'어느\s*입학|입학\s*학기|언제\s*입학', query))
    admission = tuple(dict.fromkeys(terms)) if foreign and not reverse else ()
    conditional = tuple(dict.fromkeys(terms)) if reverse else ()
    if foreign:
        terms = []
    from src.services.source_audience import labelled_degree
    degree = labelled_degree(query)
    both = degree == 'common' and bool(re.search(r'학부', query) and re.search(r'대학원', query))
    audiences = ('undergraduate', 'graduate') if both else ((degree,) if degree in {'undergraduate', 'graduate'} else ())
    ranges=[]
    days=[]
    for m in re.finditer(r'(?<!\d)(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?:일)?(?!\d)',query):
        try:days.append(date(*map(int,m.groups())).isoformat())
        except ValueError:pass
    if len(days)>1 and re.search(r'~|부터|까지|–',query):ranges=[(min(days),max(days))]
    else:ranges=[(day,day) for day in days]
    if not ranges:
        for m in re.finditer(r'(20\d{2})\s*년\s*(\d{1,2})\s*월',query):
            y,month=map(int,m.groups())
            if 1<=month<=12:ranges.append((date(y,month,1).isoformat(),date(y,month,calendar.monthrange(y,month)[1]).isoformat()))
    return InformationIntent(tuple(sorted(set(terms),key=lambda t:(t[0] or 0,t[1] or ''))), tuple(sorted(set(calendars)-occupied)), audiences,
                             admission, conditional, bool(re.search(r'비교|각각|차이|둘\s*다|모두|각\s*(?:학과|학기|과정)', query)),tuple(ranges[:8]))


def calendar_matches(q, text, ranges):
    if not ranges:return True
    start=str(q.get('event_start') or '')[:10];end=str(q.get('event_end') or start)[:10]
    if not start:
        days=[]
        for m in re.finditer(r'(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?:일)?',text):
            try:days.append(date(*map(int,m.groups())).isoformat())
            except ValueError:pass
        if not days:return False
        start,end=min(days),max(days)
    return any(start<=right and end>=left for left,right in ranges)


def term_matches(q, term):
    year, semester = term
    return (year is None or q.get('academic_year') in (None, '', year, str(year))) and (semester is None or q.get('semester') in (None, '', semester, str(semester)))


def declared_term_matches(q, term):
    year, semester = term
    return (year is None or str(q.get('academic_year', '')) == str(year)) and (semester is None or str(q.get('semester', '')) == str(semester))


def conditional_matches(q, intent):
    notes = q.get('conditional_term_notes', [])
    requested = intent.conditional_terms or intent.admission_terms
    if not requested:
        return True
    for note in notes:
        printed = re.findall(r'(20\d{2})\s*(?:학년도|년)\s*([12])\s*학기', note['source_text'])
        if len(printed) < 2:
            continue
        actual = printed[-1] if intent.conditional_terms else printed[0]
        if any((y is None or str(y) == actual[0]) and (s is None or str(s) == actual[1]) for y, s in requested):
            return True
    return False
