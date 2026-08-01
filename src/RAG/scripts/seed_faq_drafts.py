#!/usr/bin/env python3
"""반복 질문 FAQ 초안을 검수 대기 항목으로 등록한다.

로그 1,028건에서 반복 확인된 절차·기간 질문 14건을 관리자 검수함에 올린다.
등록된 항목은 **승인 전까지 챗봇 답변에 쓰이지 않는다.** 초안의 사실은
2026-08-02 기준 동국대학교 공식 규정·학사공지·시설 페이지로 대조했지만,
담당자가 출처와 적용 범위를 마지막으로 검수한 뒤 승인해야 한다.

핵심 원칙: **답변의 사실 부분을 이 스크립트가 지어내지 않는다.** 변동 정보에는
기준일·학기·공식 링크를 함께 적고, 공식 자료에 없는 값은 '확인되지 않음'으로
명시한다. 확인되지 않은 정보를 넣고 승인하면 챗봇이 자신 있게 틀린 답을 하게 되고,
그게 이 프로젝트가 애초에 없애려던 문제다.

사용법:
    python scripts/seed_faq_drafts.py --dry-run          # 등록될 내용만 출력
    python scripts/seed_faq_drafts.py                    # 로컬 DB에 직접 등록
    python scripts/seed_faq_drafts.py --refresh-pending  # 기존 pending 14건을 최신 초안으로 갱신
    python scripts/seed_faq_drafts.py --list-placeholders # 미해결 표시만 보기
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PLACEHOLDER = re.compile(r"\[확인[^\]]*\]")

RULES_URL = (
    "https://rule.dongguk.edu/lmxsrv/law/"
    "lawListManager.srv?LAWGROUP=1&PAGE_MODE=&SEQ=6"
)
ACADEMIC_GUIDE_URL = "https://www.dongguk.edu/page/209"


@dataclass(frozen=True)
class FaqDraft:
    """등록할 FAQ 한 건. question은 로그의 사용자 표현을 그대로 쓴다."""
    question: str
    answer: str
    category: str
    # 이 문구가 로그에서 몇 번 반복됐는지 — 검수 우선순위 판단용.
    observed: int


# 질문은 사용자가 실제로 입력한 문장을 그대로 쓴다.
# 표현이 사용자 말투와 일치할 때 검색이 가장 잘 붙는다.
FAQ_DRAFTS: list[FaqDraft] = [
    FaqDraft(
        "수강신청 취소는 어떻게 해?",
        "수강신청·정정 기간에는 수강신청 사이트(https://sugang.dongguk.edu)에서 직접 삭제할 수 있습니다.\n"
        "2026학년도 2학기 별도 수강취소 기간은 2026.09.16.(수) 10:00~09.18.(금) 23:59이며, nDRIMS에서 취소합니다. 취소 기간에는 다른 과목을 추가할 수 없습니다.\n"
        "2026.06.04. 현행 학칙시행세칙 기준으로 취소 후 최소 1학점 이상이 남아야 하며, 취소 과목은 해당 학기 성적표에 W로 표시됩니다(성적증명서에는 미표시, 평점·취득학점에서 제외).\n"
        "공식 근거: https://www.dongguk.edu/article/HAKSANOTICE/detail/26765498 · " + RULES_URL,
        "공통", 11,
    ),
    FaqDraft(
        "2학기 수강신청 장바구니 기간은 언제야?",
        "2026학년도 2학기 희망강의(장바구니) 신청은 2026.07.20.(월) 10:00~07.22.(수) 23:59에 진행됐습니다.\n"
        "경로는 nDRIMS → 대표-학사행정 → 수강신청 → 희망강의신청입니다. 희망강의 결과와 관계없이 본 수강신청을 다시 해야 합니다.\n"
        "학부 본 수강신청은 2026.08.03.(월) 10:00~08.07.(금) 17:00이며 학년·다전공·전체 신청일이 나뉩니다.\n"
        "공식 근거: https://www.dongguk.edu/article/HAKSANOTICE/detail/26765372 · https://www.dongguk.edu/article/HAKSANOTICE/detail/26765498",
        "공통", 8,
    ),
    FaqDraft(
        "수강신청 오류는 어느 부서에 전화해야 해?",
        "수강신청 웹·앱과 일반 절차는 교무처 학사지원팀(02-2260-3699, 02-2260-3700)으로 문의하세요. 가진급학년(수강신청학년) 표시 오류는 02-2260-3621입니다.\n"
        "과목 정원·선수과목·개설 여부는 해당 과목 개설 학과 사무실에 문의해야 합니다. 로그인 자체가 안 되면 먼저 nDRIMS ID·비밀번호와 앱 최신 버전을 확인하세요.\n"
        "공식 근거: https://www.dongguk.edu/article/HAKSANOTICE/detail/26765498 · https://www.dongguk.edu/article/HAKSANOTICE/detail/26763361",
        "공통", 6,
    ),
    FaqDraft(
        "재수강을 하고 싶은데 어떻게 해야해",
        "서울캠퍼스는 C+ 이하인 과목만 재수강할 수 있습니다. 수강신청 기간에 과목명과 교과내용이 동일한 과목을 신청하며, 폐지된 과목은 학교가 지정한 대체과목만 인정됩니다.\n"
        "재수강 최고 성적은 A0이고, 이전 성적은 R로 표시되어 취득학점과 평점 계산에서 빠집니다. 현행 규정에는 재수강 총횟수 제한이 따로 적혀 있지 않지만 교과목 운영상 특정 과목의 재수강을 제한할 수 있습니다.\n"
        "WISE캠퍼스는 대상 성적 기준이 B0 이하로 다르므로 서울캠퍼스 규정을 그대로 적용하면 안 됩니다.\n"
        "공식 근거: 2026.06.04. 현행 학칙시행세칙 제20조 · " + RULES_URL,
        "공통", 9,
    ),
    FaqDraft(
        "휴학 신청은 어떻게 해?",
        "nDRIMS → 학생신청[신청함] → [학적]휴학신청에서 신청합니다. 일반휴학은 1회 2학기가 원칙이고 통산 8학기까지 가능합니다.\n"
        "군휴학은 입영통지서, 질병휴학은 병원장이 발행한 4주 이상 진단서가 필요합니다. 임신·출산은 진단서 또는 출생신고서, 육아휴학은 가족관계증명서를 첨부합니다.\n"
        "2026-2 정기 신청은 07.13~17, 09.02~04, 09.17~21입니다. 1·2차에는 등록금 반환·징수가 없고, 3차에는 납부생 5/6 반환 또는 미납생 1/6 징수가 적용됩니다. 이후 금액은 휴학 처리일의 등록금 반환기준에 따라 달라집니다.\n"
        "공식 근거: https://www.dongguk.edu/article/HAKSANOTICE/detail/26765379 · " + RULES_URL,
        "공통", 4,
    ),
    FaqDraft(
        "복학하려면 어떤 절차가 필요해?",
        "복학 신청 기간에 nDRIMS → 학생신청[신청함] → [학적]복학신청에서 직접 신청합니다. 군복학은 전역증 또는 전역예정증명서를 스캔해 첨부합니다.\n"
        "2026-2 복학 신청은 1차 06.22~26, 2차 07.13~17에 진행됐습니다. 다음 공표 일정인 2027-1 복학 신청은 1차 2027.01.04~07, 2차 01.14~20입니다.\n"
        "복학 승인이 완료돼야 재학생 신분으로 수강신청할 수 있습니다. 2026-2 학부 수강신청은 08.03~07, 등록은 08.24~28이며 등록하지 않으면 복학과 수강신청이 취소됩니다.\n"
        "공식 근거: https://www.dongguk.edu/article/HAKSANOTICE/detail/26765069 · https://www.dongguk.edu/article/HAKSANOTICE/detail/26765361 · " + ACADEMIC_GUIDE_URL,
        "공통", 4,
    ),
    FaqDraft(
        "등록금 고지서 출력 어디서해?",
        "nDRIMS 로그인 → 대표-학사행정 → 등록 → 등록금고지서출력에서 출력합니다. 등록금을 낸 뒤에는 고지서 대신 등록금납부확인서를 출력합니다.\n"
        "분할납부는 nDRIMS → 대표-학사행정 → [학생신청]신청함 → [등록]분납신청에서 매 학기 공지된 신청기간에 신청합니다. 신·편입생, 재입학생, 휴학 예정자, 학자금대출자 등은 제외될 수 있습니다.\n"
        "등록금 문의는 재무팀 02-2260-3086입니다.\n"
        "공식 근거: https://www.dongguk.edu/page/144 · https://www.dongguk.edu/page/146",
        "공통", 1,
    ),
    FaqDraft(
        "4학년 2학기인데 학점 적게 들어도 등록금 똑같이 내?",
        "정규 수업연한 안의 4학년 2학기라면 신청 학점이 적어도 일반 등록금이 부과됩니다. 학점별 차등 등록금은 정규 수업연한을 넘긴 초과학기 등록생에게 적용됩니다.\n"
        "학부 초과학기 기준은 1~3학점 1/6, 4~6학점 1/3, 7~9학점 1/2, 10학점 이상 전액입니다. 0학점 과목도 1학점으로 계산합니다.\n"
        "본인 학기가 초과학기에 해당하는지와 고지 금액은 재무팀(02-2260-3086)에 확인하세요.\n"
        "공식 근거: https://www.dongguk.edu/page/144",
        "공통", 1,
    ),
    FaqDraft(
        "성적 정정은 언제까지 가능한가요?",
        "2026학년도 2학기 성적 공시·정정 일정은 2026.12.23.(수)~12.28.(월)입니다. 과목 담당교수가 최종시험 종료 후 7일 안에 3일간 성적을 공시하며, 공시기간의 이의는 담당교수 확인을 거쳐 수정할 수 있습니다.\n"
        "통지 후 별도 정정은 학생 책임이 아닌 평가·기재·사무·전산 착오에 한하며, 정정기간에 성적정정원, 담당교수 등의 정정사유서, 증빙문서를 소속대학 학사운영실에 제출해야 합니다.\n"
        "공식 근거: 현행 학칙시행세칙 제51~52조 · " + RULES_URL + " · " + ACADEMIC_GUIDE_URL,
        "공통", 4,
    ),
    FaqDraft(
        "토익 졸업요건이 뭐야",
        "서울캠퍼스 외국어 PASS제(토익 등 외국어시험 졸업인증)는 폐지됐고 2026년 가을 졸업부터 적용됩니다. 따라서 일반 학생에게 적용할 단일 토익 졸업 점수나 대체시험 점수·유효기간 기준은 현재 없습니다.\n"
        "다만 2011학년도 2학기 이후 외국인전형 입학생의 TOPIK/TOPIK IBT 4급 이상 의무는 유지됩니다(공인영어성적으로 입학한 경우 제외). 영어강의 이수요건과 학과별 별도 졸업요건은 외국어 PASS 폐지와 별개이므로 소속 학과 기준을 확인해야 합니다.\n"
        "2026년 가을 졸업대상자의 제외신청은 종료됐으며, 2027년 봄 대상자는 해당 학기 공지를 확인해야 합니다.\n"
        "공식 근거: https://econ.dongguk.edu/article/notice/detail/211782 · " + RULES_URL,
        "공통", 1,
    ),
    FaqDraft(
        "중앙도서관 평일 운영시간과 대출실 마감시간을 알려줘",
        "운영시간은 학기·시험기간·방학을 고정값으로 추정하지 않고 날짜별 공식 운영표를 확인해야 합니다.\n"
        "2026.08.02 조회 기준 다음 평일인 08.03(하계방학)은 중앙도서관 전체 09:00~18:00, 자료실 09:00~17:00, 대출반납실 09:00~17:00, IC Zone·IF Zone 09:00~21:00입니다. 대출 업무는 17:00에 마감합니다.\n"
        "시험기간 연장 여부와 휴관일은 날짜별로 달라질 수 있으므로 도서관 홈페이지의 '이용시간'을 다시 확인하세요.\n"
        "공식 근거: https://lib.dongguk.edu/ · https://lib.dongguk.edu/pyxis-api/1/branches/operation-time",
        "공통", 2,
    ),
    FaqDraft(
        "기숙사 입사 신청 기간과 제출 서류 알려줘",
        "남산학사 2026-2 재학생 정규모집 신청·서류제출은 2026.07.06.(월) 10:00~07.08.(수) 16:00에 nDRIMS에서 진행됐습니다. 합격자 납부는 07.10.(금) 16:00~07.13.(월) 15:00, 지정 입사는 08.20~22입니다.\n"
        "신청 단계의 공통 서류는 친권자 주민등록등본이며 주소관계에 따라 가족관계증명서 등이 추가됩니다. 우선선발자는 해당 자격 증명서를 이메일로 제출합니다. 입사 시에는 3개월 이내 폐결핵 검사결과서와 입사서약서가 필요합니다.\n"
        "2인실 기준 비용은 4개월 1,661,280원, 6개월 2,461,600원(입사보증금 100,000원 포함)이었습니다. 선발은 주소지, 직전학기 평점, 상·벌점 및 우선선발 자격을 반영합니다.\n"
        "모집마다 기간·비용이 달라지므로 최신 공지를 확인하세요. 문의: 02-2260-4932~3, dorm@dongguk.edu.\n"
        "공식 근거: https://dorm.dongguk.edu/article/notice/detail/212213 · https://dorm.dongguk.edu/article/notice/list",
        "공통", 6,
    ),
    FaqDraft(
        "2027학년도 1학기 교환학생 지원 기간이 언제야?",
        "2027-1학기 영어권 파견 교환학생 지원서 접수 예정기간은 2026.08.05.(수)~08.13.(목) 14:00이며 nDRIMS로 접수합니다. 선발대학·지원방법 공지는 08.05.(수) 17:00, 온라인 설명회는 08.06.(목) 14:00, 비대면 AI 면접은 08.19~20, 합격자 발표는 08.27.(목) 17:00 이후 예정입니다.\n"
        "지원 자격과 대학별 어학요건은 08.05 공개되는 선발대학·지원방법에서 확정해야 하며, 현재 공식 공지만으로 단일 평점·어학 기준을 일반화하면 안 됩니다. 일정도 변경될 수 있습니다.\n"
        "모집은 파견 학기·언어권별로 별도 공지되므로 국제처 국제교류공지의 교환학생 분류와 공지 안의 일정 페이지를 확인하세요.\n"
        "공식 근거: https://www.dongguk.edu/article/INTEXNOTICE/detail/26765300",
        "공통", 6,
    ),
    FaqDraft(
        "계절학기 시간표는 어디서 확인하지?",
        "계절학기 개설 과목·시간표는 동국대 학사공지의 계절학기 운영계획과 nDRIMS → 수업/강의평가 → 종합강의시간표조회에서 확인합니다. 수강신청은 https://sugang.dongguk.edu 에서 합니다.\n"
        "다음 공표 일정인 2026 겨울 계절학기 수강신청은 2026.11.11.(수)~11.13.(금)입니다. 세부 시간과 최종 개설과목은 추후 학사공지를 확인해야 합니다.\n"
        "2026 여름 기준 수강료는 이론 1학점 90,000원, 실험·실습 1학점 105,000원이었으며 다음 계절학기에는 바뀔 수 있습니다. 현행 규정상 재학생은 계절학기당 최대 6학점, 휴학생은 최대 3학점입니다. 다만 필수 4학점 과목 예외는 재학생 최대 8학점, 휴학생 4학점입니다.\n"
        "공식 근거: https://www.dongguk.edu/article/HAKSANOTICE/detail/26764420 · " + RULES_URL,
        "공통", 2,
    ),
]


def count_placeholders(draft: FaqDraft) -> int:
    return len(PLACEHOLDER.findall(draft.answer))


def draft_payload(draft: FaqDraft, requester: str) -> dict[str, str]:
    return {
        "question": draft.question,
        "answer": draft.answer,
        "category": draft.category,
        "requester": requester,
        "verified_as_of": "2026-08-02",
    }


def print_summary() -> None:
    total = sum(count_placeholders(d) for d in FAQ_DRAFTS)
    print(f"FAQ 초안 {len(FAQ_DRAFTS)}건 · 담당자가 채울 항목 {total}곳\n")
    print(f"{'반복':>4}  {'채울곳':>5}  질문")
    print("-" * 72)
    for draft in sorted(FAQ_DRAFTS, key=lambda d: -d.observed):
        print(f"{draft.observed:>4}회  {count_placeholders(draft):>5}곳  {draft.question}")
    print(
        "\n등록 후 관리자 콘솔 › 검수함에서 기준일·출처·적용 범위를 확인한 뒤 승인하세요.\n"
        "승인 전에는 챗봇 답변에 사용되지 않습니다."
    )


def print_placeholders() -> None:
    for draft in FAQ_DRAFTS:
        holes = PLACEHOLDER.findall(draft.answer)
        if not holes:
            continue
        print(f"\n■ {draft.question}")
        for hole in holes:
            print(f"   - {hole}")


def seed(dry_run: bool, requester: str, *, refresh_pending: bool = False) -> int:
    """검수 대기 항목으로 등록한다.

    ``refresh_pending``은 같은 질문의 *pending* 항목만 최신 공식 근거 초안으로
    교체한다. 승인·반려된 항목은 감사 기록과 노출 상태를 보존하기 위해 건드리지 않는다.
    """
    if dry_run:
        # DB를 건드리지 않는다 — 등록될 내용만 확인하는 용도이므로
        # DB 의존성이 없는 환경에서도 실행되어야 한다.
        for draft in FAQ_DRAFTS:
            payload = draft_payload(draft, requester)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    from src.database import PendingItem, SessionLocal, init_db

    init_db()
    session = SessionLocal()
    created = 0
    updated = 0
    skipped = 0
    try:
        existing_by_question: dict[str, list[PendingItem]] = {}
        for item in session.query(PendingItem).filter(PendingItem.source_type == "custom_knowledge").all():
            try:
                question = json.loads(item.data).get("question", "")
            except (json.JSONDecodeError, TypeError):
                continue
            existing_by_question.setdefault(question, []).append(item)

        for draft in FAQ_DRAFTS:
            payload = json.dumps(draft_payload(draft, requester), ensure_ascii=False)
            existing = existing_by_question.get(draft.question, [])
            if existing:
                pending = [item for item in existing if item.status == "pending"]
                if refresh_pending and pending:
                    for item in pending:
                        item.data = payload
                        item.review_note = None
                        item.reviewed_by = None
                        item.reviewed_at = None
                        updated += 1
                    continue
                skipped += 1
                continue
            session.add(PendingItem(
                source_type="custom_knowledge",
                data=payload,
                status="pending",
            ))
            created += 1
        session.commit()
    finally:
        session.close()

    print(f"검수 대기로 등록: {created}건, 갱신: {updated}건 (건너뜀: {skipped}건)")
    print("관리자 콘솔 › 검수함에서 기준일·출처·적용 범위를 확인한 뒤 승인하세요.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="등록하지 않고 payload만 출력")
    parser.add_argument("--list-placeholders", action="store_true", help="담당자가 채울 항목만 출력")
    parser.add_argument("--summary", action="store_true", help="초안 목록 요약 출력")
    parser.add_argument(
        "--refresh-pending",
        action="store_true",
        help="같은 질문의 pending 항목만 최신 공식 근거 초안으로 갱신",
    )
    parser.add_argument("--requester", default="FAQ 시딩(로그 분석)", help="검수함에 표시할 제출자")
    args = parser.parse_args()

    if args.summary:
        print_summary()
        return 0
    if args.list_placeholders:
        print_placeholders()
        return 0
    return seed(args.dry_run, args.requester, refresh_pending=args.refresh_pending)


if __name__ == "__main__":
    raise SystemExit(main())
