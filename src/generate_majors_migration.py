import uuid

majors = [
    # 관리자
    "관리자",
    # 총학생회
    "총학생회",
    # 불교대학
    "불교학부", "문화유산학과",
    # 문과대학
    "국어국문문예창작학부", "영어영문학부", "일본학과", "중어중문학과", "철학과", "사학과",
    # 이과대학
    "수학과", "화학과", "통계학과", "물리반도체과학부",
    # 법과대학
    "법학과",
    # 사회과학대학
    "경제학과", "광고홍보학과", "국제통상학전공", "미디어커뮤니케이션학전공", "북한학전공", 
    "사회복지학과", "사회학전공", "식품산업관리학과", "정치외교학전공", "행정학전공",
    # 경찰사법대학
    "경찰행정학부",
    # 경영대학
    "경영정보학과", "경영학과", "회계학과",
    # 공과대학
    "건설환경공학과", "건축학전공", "기계로봇에너지공학과", "산업시스템공학과", 
    "에너지신소재공학과", "전자전기공학부", "정보통신공학전공", "화공생물공학과",
    # 첨단융합대학
    "시스템반도체학부", "의료인공지능공학과", "지능형네트워크융합학과", "컴퓨터·AI학부",
    # 사범대학
    "가정교육과", "교육학과", "국어교육과", "수학교육과", "역사교육과", "지리교육과", "체육교육과",
    # 예술대학
    "미술학부", "연극학부", "영화영상학과", "한국음악과", "스포츠문화학과",
    # 미래융합대학
    "글로벌무역학과", "사회복지상담학과", "융합보안학과",
    # 바이오시스템대학
    "바이오환경과학과", "생명과학과", "식품생명공학과", "의생명공학과",
    # 약학대학
    "약학과"
]

# Sort for consistency
majors.sort()

# Remove duplicates if any
majors = list(set(majors))
majors.sort()

print('            migrationBuilder.InsertData(')
print('                table: "majors",')
print('                columns: new[] { "id", "major_name" },')
print('                values: new object[,]')
print('                {')

for i, major in enumerate(majors):
    uid = uuid.uuid4()
    # Handle computer engineering special case or simple mapping
    if major == "컴퓨터·AI학부":
        # Keep consistent if possible, but new UUID is fine for initial create
        pass
    
    ending = "," if i < len(majors) - 1 else ""
    print(f'                    {{ new Guid("{uid}"), "{major}" }}{ending}')

print('                });')
