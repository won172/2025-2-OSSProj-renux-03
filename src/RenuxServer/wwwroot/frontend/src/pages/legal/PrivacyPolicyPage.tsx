import { Link, useNavigate } from 'react-router-dom'

// 방침 개정 시 이 두 값을 함께 갱신한다. 개정 시행 7일 전 공지 의무(제12조)가 있으므로
// EFFECTIVE_DATE 는 공지 이후 실제 적용 시작일을 적는다.
const EFFECTIVE_DATE = '2026년 8월 27일'
const COUNCIL_INSTAGRAM_URL =
  'https://www.instagram.com/dongttok.dgu?igsh=MWs3MWJ4OWU3NjdlMw%3D%3D&utm_source=qr'

const PrivacyPolicyPage = () => {
  const navigate = useNavigate()

  return (
    <div className="policy-page">
      <header className="policy-page__header">
        <div>
          <p className="policy-page__eyebrow">동똑이</p>
          <h1>개인정보처리방침</h1>
        </div>
        <div className="policy-page__actions">
          <button type="button" className="policy-page__back" onClick={() => navigate('/')}>
            홈으로
          </button>
          <button type="button" className="policy-page__back" onClick={() => navigate(-1)}>
            돌아가기
          </button>
        </div>
      </header>

      <article className="policy-doc">
        <p className="policy-doc__lead">
          동똑이 팀(이하 &lsquo;동똑이&rsquo;)은 「개인정보 보호법」 제30조에 따라 정보주체의 개인정보를 보호하고
          이와 관련한 고충을 신속하고 원활하게 처리할 수 있도록 다음과 같이 개인정보처리방침을 수립·공개합니다.
        </p>
        <p className="policy-doc__lead">
          동똑이는 동국대학교 학생에게 교내 공지·학칙·학사일정·교과목·교직원 연락처·학식 정보를 안내하는
          챗봇 서비스이며, 동국대학교 재학생으로 구성된 학생 프로젝트 팀이 비영리로 운영합니다.
        </p>

        <section className="policy-section">
          <h2>1. 개인정보의 처리 목적</h2>
          <p>
            동똑이는 다음의 목적을 위하여 개인정보를 처리하고 있으며, 다음의 목적 이외의 용도로는 이용하지
            않습니다. 이용 목적이 변경되는 경우에는 「개인정보 보호법」 제18조에 따라 별도의 동의를 받는 등
            필요한 조치를 이행할 예정입니다.
          </p>
          <ul>
            <li>회원 가입 의사 확인, 아이디·비밀번호를 통한 이용자 식별·인증, 회원자격 유지·관리</li>
            <li>이용자의 학과 정보를 반영한 맞춤형 교내 정보 답변 제공</li>
            <li>대화 기록 저장, 이어보기, 답변 다시 생성 기능 제공</li>
            <li>관심 주제 기반 마감 알림(D-day 비서) 생성 및 발송</li>
            <li>학생회 계정 가입 요청의 본인 확인 및 승인·거절 처리</li>
            <li>서비스 오류 확인, 답변 품질 개선, 부정 이용 및 과도한 요청 방지</li>
          </ul>
          <p className="policy-note">
            동똑이는 개인정보를 마케팅·광고 목적으로 이용하지 않으며, 광고나 행태정보 분석을 위한 외부 추적
            도구를 설치하지 않습니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>2. 처리하는 개인정보의 항목</h2>

          <h3>가. 회원 가입 시 (일반학생 · 학생회 공통)</h3>
          <ul>
            <li>
              <strong>필수항목</strong> : 아이디, 비밀번호, 이름, 전공(학과)
            </li>
            <li>
              <strong>선택항목</strong> : 없음
            </li>
            <li>
              <strong>자동 생성·기록되는 항목</strong> : 가입 일시, 정보 수정 일시, 계정 권한 등급
            </li>
          </ul>
          <p className="policy-note">
            동똑이는 이메일 주소, 전화번호, 생년월일, 성별, 학번, 주민등록번호를 <strong>수집하지 않습니다.</strong>{' '}
            비밀번호는 복호화가 불가능한 해시 형태로만 저장되어 운영진을 포함한 누구도 원문을 확인할 수 없습니다.
          </p>

          <h3>나. 학생회 가입 요청 시</h3>
          <ul>
            <li>
              <strong>필수항목</strong> : 위 &lsquo;가&rsquo;항의 회원 가입 항목과 동일
            </li>
            <li>
              <strong>추가 기록 항목</strong> : 요청 상태, 심사 일시, 심사자, 심사 메모
            </li>
          </ul>
          <p className="policy-note">
            요청 확인을 위해 이용자가 동똑이 인스타그램 계정으로 DM을 보내는 경우, 해당 대화 내용은
            인스타그램 운영사(Meta Platforms, Inc.)의 개인정보처리방침에 따라 처리됩니다.
          </p>

          <h3>다. 서비스 이용 과정에서 생성·저장되는 정보</h3>
          <ul>
            <li>
              <strong>로그인 이용자의 대화 기록</strong> : 대화 제목, 질문 내용, 답변 내용, 답변 출처 목록,
              추천 질문, 대화 생성·수정 일시
            </li>
            <li>
              <strong>알림 기능 이용 정보</strong> : 관심 주제 설정값, 리마인드 시점, 생성된 알림 내역과 읽음 여부
            </li>
            <li>
              <strong>답변 품질 개선용 로그</strong> : 질문·답변 원문, 질문 분류 결과, 검색된 문서의 제목·주소·점수,
              세션 식별자, 요청 식별자, 답변 평가(별점·사유·의견)와 학과
            </li>
            <li>
              <strong>익명 이용 통계</strong> : 개인을 식별할 수 없도록 변환된 임의의 키와 이용 지표(평가 점수,
              답변 성공 여부, 출처 개수 등). 질문·답변 본문과 이름 등 직접 식별자는 포함되지 않습니다.
            </li>
          </ul>

          <h3>라. 비회원(로그인하지 않은 이용자)</h3>
          <ul>
            <li>비회원의 대화 목록은 이용자 브라우저의 로컬 저장소에만 저장되며, 계정과 연결되어 보관되지 않습니다.</li>
            <li>남용 방지와 이용자 구분을 위해 암호화된 임시 식별자가 담긴 쿠키가 발급됩니다.</li>
            <li>위 &lsquo;다&rsquo;항의 답변 품질 개선용 로그는 비회원 질문에 대해서도 계정 정보 없이 기록될 수 있습니다.</li>
          </ul>

          <h3>마. 접속 IP 주소</h3>
          <p>
            무차별 대입 공격과 과도한 요청을 차단하기 위한 목적으로만 서버 메모리에서 일시적으로 이용되며,
            데이터베이스에 저장하지 않습니다.
          </p>

          <h3>바. 만 14세 미만 아동의 개인정보</h3>
          <p>
            동똑이는 동국대학교 재학생을 대상으로 하는 서비스로 만 14세 미만 아동의 가입을 예정하고 있지 않으며,
            만 14세 미만 아동의 개인정보를 수집하지 않습니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>3. 개인정보의 처리 및 보유 기간</h2>
          <p>
            ① 동똑이는 정보주체로부터 개인정보를 수집할 때 동의받은 보유·이용기간 또는 법령에 따른
            보유·이용기간 내에서 개인정보를 처리·보유합니다.
          </p>
          <p>② 구체적인 개인정보 처리 및 보유 기간은 다음과 같습니다.</p>
          <div className="policy-table-wrap">
            <table className="policy-table">
              <thead>
                <tr>
                  <th scope="col">구분</th>
                  <th scope="col">보유 기간</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">회원 정보(아이디, 이름, 전공, 비밀번호 해시)</th>
                  <td>회원 탈퇴 시 지체 없이 파기</td>
                </tr>
                <tr>
                  <th scope="row">대화 기록(질문·답변·출처)</th>
                  <td>회원 탈퇴 시 지체 없이 파기 (이용자가 개별 대화를 삭제한 경우 삭제 시점에 파기)</td>
                </tr>
                <tr>
                  <th scope="row">알림 설정 및 알림 내역</th>
                  <td>회원 탈퇴 시 지체 없이 파기</td>
                </tr>
                <tr>
                  <th scope="row">학생회 가입 요청 내역</th>
                  <td>심사 완료 후 1년 (중복·부정 요청 확인 목적)</td>
                </tr>
                <tr>
                  <th scope="row">답변 품질 개선용 로그</th>
                  <td>수집일로부터 1년</td>
                </tr>
                <tr>
                  <th scope="row">익명 이용 통계</th>
                  <td>개인을 식별할 수 없는 형태이므로 기간 제한 없이 보관</td>
                </tr>
                <tr>
                  <th scope="row">로그인 인증 쿠키</th>
                  <td>발급 후 60분 경과 시 자동 만료</td>
                </tr>
                <tr>
                  <th scope="row">비회원 식별 쿠키</th>
                  <td>브라우저 종료 시 삭제</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="policy-section policy-section--highlight">
          <h2>4. 개인정보의 국외 이전</h2>
          <p>
            동똑이는 답변 생성을 위해 다음과 같이 개인정보를 국외로 이전합니다. 이는 챗봇 답변 기능 제공에
            필수적인 처리입니다.
          </p>
          <div className="policy-table-wrap">
            <table className="policy-table">
              <tbody>
                <tr>
                  <th scope="row">이전받는 자</th>
                  <td>OpenAI, L.L.C.</td>
                </tr>
                <tr>
                  <th scope="row">이전되는 국가</th>
                  <td>미국</td>
                </tr>
                <tr>
                  <th scope="row">이전 일시 및 방법</th>
                  <td>이용자가 질문을 전송할 때마다, 정보통신망을 통해 암호화하여 전송</td>
                </tr>
                <tr>
                  <th scope="row">이전되는 항목</th>
                  <td>
                    이용자가 입력한 질문 내용, 답변 생성을 위해 검색된 교내 문서 발췌본, 이용자의 학과 정보
                  </td>
                </tr>
                <tr>
                  <th scope="row">이전받는 자의 이용 목적</th>
                  <td>질문 의도 분류, 질의 분석, 답변 생성</td>
                </tr>
                <tr>
                  <th scope="row">보유·이용 기간</th>
                  <td>
                    이전받는 자의 개인정보 처리방침에 따름 (
                    <a href="https://openai.com/policies/privacy-policy" target="_blank" rel="noopener noreferrer">
                      openai.com/policies/privacy-policy
                    </a>
                    )
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p>
            동똑이는 운영 상황에 따라 답변 생성 모델을 자체 서버에서 구동하는 모델로 전환할 수 있으며, 이 경우에도
            질문 분류와 질의 분석 과정에서는 위 사업자의 서비스를 이용합니다.
          </p>
          <p className="policy-warning">
            대화창에 주민등록번호, 계좌번호, 비밀번호 등 민감한 개인정보를 입력하지 않도록 주의해 주시기 바랍니다.
            입력한 질문 내용은 답변 생성을 위해 위와 같이 국외로 전송됩니다.
          </p>
          <p>
            정보주체는 위 국외 이전을 거부할 수 있습니다. 다만 이는 서비스 제공에 필수적인 처리이므로, 거부하시는
            경우 챗봇 답변 기능을 이용하실 수 없습니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>5. 개인정보의 제3자 제공</h2>
          <p>
            동똑이는 정보주체의 개인정보를 제1조에 명시한 범위 내에서만 처리하며, 정보주체의 동의, 법률의 특별한
            규정 등 「개인정보 보호법」 제17조 및 제18조에 해당하는 경우에만 개인정보를 제3자에게 제공합니다.
            현재 동똑이가 상시적으로 개인정보를 제공하는 제3자는 없습니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>6. 정보주체와 법정대리인의 권리·의무 및 그 행사방법</h2>
          <p>① 정보주체는 동똑이에 대해 언제든지 다음 각 호의 개인정보 보호 관련 권리를 행사할 수 있습니다.</p>
          <ol>
            <li>개인정보 열람 요구</li>
            <li>오류 등이 있을 경우 정정 요구</li>
            <li>삭제 요구</li>
            <li>처리정지 요구</li>
            <li>개인정보 처리에 대한 동의 철회</li>
          </ol>
          <p>
            ② 제1항에 따른 권리 행사는 동똑이 인스타그램 계정{' '}
            <a href={COUNCIL_INSTAGRAM_URL} target="_blank" rel="noopener noreferrer">
              @dongttok.dgu
            </a>{' '}
            DM을 통해 하실 수 있으며, 동똑이는 이에 대해 지체 없이 조치하겠습니다.
          </p>
          <p>
            ③ 로그인 후 설정 화면의 &ldquo;회원 탈퇴&rdquo;에서 직접 계정 삭제를 완료할 수 있습니다. 현재 비밀번호와
            확인 문구를 입력하면 계정 정보, 대화 기록, 알림 설정과 연결된 개인정보가 삭제되며 복구할 수 없습니다.
          </p>
          <p>
            ④ 정보주체 본인이 아닌 대리인이 권리를 행사하는 경우, 동똑이는 위임 사실을 확인할 수 있는 자료를
            요청할 수 있습니다.
          </p>
          <p>
            ⑤ 개인정보 열람 및 처리정지 요구는 「개인정보 보호법」 제35조 제4항, 제37조 제2항에 의하여 정보주체의
            권리가 제한될 수 있습니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>7. 개인정보의 파기</h2>
          <p>
            동똑이는 개인정보 보유기간의 경과, 처리목적 달성 등 개인정보가 불필요하게 되었을 때에는 지체 없이 해당
            개인정보를 파기합니다. 파기의 절차, 기한 및 방법은 다음과 같습니다.
          </p>
          <ul>
            <li>
              <strong>파기절차</strong> : 파기 사유가 발생한 개인정보를 선정하고, 개인정보 보호책임자의 확인을 거쳐
              파기합니다.
            </li>
            <li>
              <strong>파기기한</strong> : 보유기간이 경과한 경우에는 종료일로부터 5일 이내에, 처리 목적 달성,
              해당 서비스의 폐지, 사업의 종료 등 그 개인정보가 불필요하게 되었을 때에는 개인정보의 처리가
              불필요한 것으로 인정되는 날로부터 5일 이내에 파기합니다.
            </li>
            <li>
              <strong>파기방법</strong> : 전자적 파일 형태의 정보는 복구·재생할 수 없는 기술적 방법으로 삭제하며,
              종이에 출력된 개인정보는 분쇄기로 분쇄하거나 소각합니다.
            </li>
          </ul>
        </section>

        <section className="policy-section">
          <h2>8. 개인정보 자동 수집 장치의 설치·운영 및 거부에 관한 사항</h2>
          <p>
            ① 동똑이는 로그인 상태 유지와 비회원 이용자 구분을 위해 쿠키(cookie)를 사용합니다. 쿠키는 서비스를
            운영하는 서버가 이용자의 브라우저에 보내는 소량의 정보로, 이용자의 기기에 저장됩니다.
          </p>
          <p>② 동똑이가 사용하는 쿠키는 다음과 같으며, 모두 서비스 제공에 필수적인 쿠키입니다.</p>
          <div className="policy-table-wrap">
            <table className="policy-table">
              <thead>
                <tr>
                  <th scope="col">쿠키명</th>
                  <th scope="col">목적</th>
                  <th scope="col">보관 기간</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">renux-server-token</th>
                  <td>로그인 인증 상태 유지</td>
                  <td>발급 후 60분</td>
                </tr>
                <tr>
                  <th scope="row">renux-server-guest</th>
                  <td>비회원 이용자 구분 및 남용 방지</td>
                  <td>브라우저 종료 시</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p>
            ③ 두 쿠키 모두 HttpOnly·Secure 속성이 적용되어 웹페이지의 스크립트로 읽을 수 없으며, 암호화된 통신
            구간으로만 전송됩니다.
          </p>
          <p>
            ④ 동똑이는 광고·마케팅·행태정보 분석 목적의 쿠키를 사용하지 않으며, 외부 광고 사업자나 분석 사업자의
            추적 도구를 설치하지 않습니다.
          </p>
          <p>
            ⑤ <strong>쿠키 설치·운영 및 거부</strong> : 이용자는 웹브라우저 설정의 개인정보 및 보안 메뉴에서
            쿠키 저장을 거부할 수 있습니다. 다만 위 필수 쿠키의 저장을 거부하는 경우 로그인이 필요한 기능을
            이용하실 수 없습니다.
          </p>
          <p>
            ⑥ 이 밖에 이용자 브라우저의 로컬 저장소(localStorage)에 화면 표시를 위한 계정 권한 등급과 비회원
            대화 목록이 저장됩니다. 이 정보는 서버로 전송되지 않으며, 브라우저의 사이트 데이터 삭제 기능으로
            언제든지 지울 수 있습니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>9. 개인정보의 안전성 확보 조치</h2>
          <p>
            동똑이는 「개인정보 보호법」 제29조에 따라 다음과 같이 안전성 확보에 필요한 기술적·관리적 조치를 하고
            있습니다.
          </p>
          <ol>
            <li>
              <strong>개인정보 취급자의 최소화 및 교육</strong> : 개인정보를 취급하는 인원을 운영진 중 최소한으로
              지정하고 관리 대책을 시행하고 있습니다.
            </li>
            <li>
              <strong>비밀번호의 일방향 암호화</strong> : 이용자의 비밀번호는 복호화가 불가능한 해시 함수로 변환하여
              저장하므로, 운영진도 원문을 확인할 수 없습니다.
            </li>
            <li>
              <strong>전송 구간 암호화</strong> : 이용자와 서비스 사이의 모든 통신은 HTTPS로 암호화됩니다.
            </li>
            <li>
              <strong>개인정보에 대한 접근 제한</strong> : 데이터베이스 접근 권한을 최소한의 인원에게만 부여하고,
              관리자 기능은 별도의 권한 검사를 통과해야만 접근할 수 있도록 통제하고 있습니다. 관리자 계정은 공개
              회원가입으로 생성할 수 없으며 운영진이 별도로 발급합니다.
            </li>
            <li>
              <strong>인증 정보 보호</strong> : 인증 토큰은 스크립트로 접근할 수 없는 HttpOnly 쿠키로 관리하며,
              60분 후 자동으로 만료됩니다.
            </li>
            <li>
              <strong>부정 이용 방지</strong> : 로그인·회원가입 및 대화 요청에 대해 요청 횟수 제한을 두어 무차별
              대입 공격과 과도한 이용을 차단하고 있습니다.
            </li>
            <li>
              <strong>통계 데이터의 개인정보 최소화</strong> : 서비스 개선용 이용 통계에는 질문·답변 본문과 이름 등
              직접 식별자가 포함될 수 없도록 저장 항목을 설계 단계에서 제한하고 있습니다.
            </li>
          </ol>
        </section>

        <section className="policy-section">
          <h2>10. 개인정보 보호책임자</h2>
          <p>
            ① 동똑이는 개인정보 처리에 관한 업무를 총괄해서 책임지고, 개인정보 처리와 관련한 정보주체의 불만처리 및
            피해구제 등을 위하여 아래와 같이 개인정보 보호책임자를 지정하고 있습니다.
          </p>
          <div className="policy-contact">
            <p className="policy-contact__title">▶ 개인정보 보호책임자</p>
            <ul>
              <li>소속 : 동똑이 팀 (동국대학교 학생 프로젝트)</li>
              <li>담당 : 서비스 운영 총괄</li>
              <li>
                문의 :{' '}
                <a href={COUNCIL_INSTAGRAM_URL} target="_blank" rel="noopener noreferrer">
                  인스타그램 @dongttok.dgu DM
                </a>
              </li>
            </ul>
          </div>
          <p>
            ② 정보주체는 동똑이의 서비스를 이용하면서 발생한 모든 개인정보 보호 관련 문의, 불만처리, 피해구제 등에
            관한 사항을 위 연락처로 문의하실 수 있습니다. 동똑이는 정보주체의 문의에 대해 지체 없이 답변 및 처리해
            드릴 것입니다.
          </p>
        </section>

        <section className="policy-section">
          <h2>11. 정보주체의 권익침해에 대한 구제방법</h2>
          <p>
            정보주체는 개인정보 침해로 인한 구제를 받기 위하여 아래 기관에 분쟁 해결이나 상담 등을 신청할 수
            있습니다. 아래 기관은 동똑이와는 별개의 기관으로서, 동똑이의 자체적인 개인정보 불만처리·피해구제
            결과에 만족하지 못하시거나 보다 자세한 도움이 필요하시면 문의하여 주시기 바랍니다.
          </p>
          <ul className="policy-agency-list">
            <li>
              <strong>개인정보 침해신고센터</strong> (한국인터넷진흥원 운영)
              <br />
              소관업무 : 개인정보 침해사실 신고, 상담 신청 / 홈페이지 : privacy.kisa.or.kr / 전화 : (국번없이) 118
            </li>
            <li>
              <strong>개인정보 분쟁조정위원회</strong>
              <br />
              소관업무 : 개인정보 분쟁조정신청, 집단분쟁조정(민사적 해결) / 홈페이지 : www.kopico.go.kr / 전화 :
              (국번없이) 1833-6972
            </li>
            <li>
              <strong>대검찰청 사이버수사과</strong>
              <br />
              홈페이지 : www.spo.go.kr / 전화 : (국번없이) 1301
            </li>
            <li>
              <strong>경찰청 국가수사본부 사이버범죄 신고시스템</strong>
              <br />
              홈페이지 : ecrm.police.go.kr / 전화 : (국번없이) 182
            </li>
          </ul>
        </section>

        <section className="policy-section">
          <h2>12. 개인정보 처리방침의 변경</h2>
          <p>① 이 개인정보처리방침은 {EFFECTIVE_DATE}부터 적용됩니다.</p>
          <p>
            ② 법령 및 방침에 따른 변경내용의 추가, 삭제 및 정정이 있는 경우에는 변경사항의 시행 7일 전부터 서비스
            내 공지를 통하여 고지할 것입니다.
          </p>
        </section>

        <footer className="policy-doc__footer">
          <p>시행일자 : {EFFECTIVE_DATE}</p>
          <p>
            <Link to="/">동똑이 홈으로 돌아가기</Link>
          </p>
        </footer>
      </article>
    </div>
  )
}

export default PrivacyPolicyPage
