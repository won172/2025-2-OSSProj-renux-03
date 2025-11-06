import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../../api/client'
import AppHeader from '../../components/layout/AppHeader'
import NewChatSection from '../../components/chat/NewChatSection'
import ActiveChatList from '../../components/chat/ActiveChatList'
import type { Department } from '../../types/organization'
import type { ActiveChat} from '../../types/chat'
import type { AuthNameResponse } from '../../types/auth'
import dongddokiLogo from '../../assets/images/dongddoki-logo.png'

const defaultWelcomeMessage = '환영합니다, 사용자님'

const heroHighlights = [
  { title: '학생회 맞춤 정보', description: '학생회별 공지와 일정을 자동으로 모아 한눈에 확인할 수 있어요.' },
  { title: 'AI 상담', description: '24시간 AI 챗봇과 대화하며 필요한 정보를 빠르게 검색해보세요.' },
  { title: '학교 인증 기반', description: '학교 계정으로 안전하게 로그인하고 개인화된 상담을 받으세요.' },
]

const HomePage = () => {
  const navigate = useNavigate()
  const [isAuthenticated, setIsAuthenticated] = useState(false)//인증 상태(boolean)
  const [userName, setUserName] = useState<string | null>(null)//사용자 이름
  const [departments, setDepartments] = useState<Department[]>([])//학과 목록
  const [departmentsLoading, setDepartmentsLoading] = useState(true)//학과 로딩 상태
  const [activeChats, setActiveChats] = useState<ActiveChat[]>([])//활성 채팅 목록
  const [activeChatsError, setActiveChatsError] = useState<string | null>(null)//활성 채팅 오류 메시지
  const [isModalOpen, setIsModalOpen] = useState(false)//모달 열림 상태
  const [selectedDepartmentId, setSelectedDepartmentId] = useState('')//선택된 학과 ID
  const [chatRoomTitle, setChatRoomTitle] = useState('')//채팅방 제목
  const [isCreatingChat, setIsCreatingChat] = useState(false)//채팅 생성 중 상태
  const [createChatError, setCreateChatError] = useState<string | null>(null)//채팅 생성 오류 메시지
  const [isSidebarOpen, setIsSidebarOpen] = useState(true) //사이드바 열림 상태

  // 서버 연결 안되어 있을 때 테스트용
  /* const isNewChatDisabled = false */
  

 //새 채팅 버튼 비활성화 여부 메모이제이션
  const isNewChatDisabled = useMemo(() => {
    if (departmentsLoading) return true//학과 로딩 중이면 true 반환
    return departments.length === 0//학과가 없으면 true 반환
  }, [departments, departmentsLoading])
  


  /* 서버 연결 안되어 있을 때 테스트용
  useEffect(() => {
    // 서버 미연결 상태 임시 더미 학과
    setDepartments([
      { id: '00000000-0000-0000-0000-000000000001', major: { majorname: '임시 학과 A' } },
      { id: '00000000-0000-0000-0000-000000000002', major: { majorname: '임시 학과 B' } },
    ])
    setDepartmentsLoading(false)
    }, []
    )
  */

  //학과 불러오기

  useEffect(() => {
    const loadDepartments = async () => {
      setDepartmentsLoading(true)//로딩 상태 설정
      try {//학과 데이터 불러오기
        const data = await apiFetch<Department[]>('/req/orgs', { method: 'GET' })//학과 목록 API 호출
        if (Array.isArray(data)) {//데이터가 배열이면
          setDepartments(data)//학과 설정
        } else {//데이터가 배열이 아니면
          setDepartments([])//빈 배열 설정
        }
      } catch (error) {//오류 발생 시
        console.error('Failed to load departments', error)//오류 로그 출력
        setDepartments([])//빈 배열 설정
      } finally {
        setDepartmentsLoading(false)//로딩 상태 해제
      }
    }
    loadDepartments()
  }, []
  )


  /* 학과 불러오는 코드 해석
  - 서버에서 학과 목록 받아오면 departments 상태에 저장
  - 못 받아오면 빈 배열로 저장해둠(굳이 배열로 저장하는 이유는 타입을 통일하려고.)
  - departmentsLoading이 true이면(학과 목록을 전송받지 못해서 로딩 중) isNewChatDisabled가 true가 되어(채팅이 불가한 상태)
    => “로딩 중이라 아직 버튼 누르면 안되겠다.”라고 판단해서 버튼을 비활성화함.
  */


  //로그인 상태 확인 후 사용자 이름 불러오기
  useEffect(() => {
    const checkLoginStatus = async () => {
      try {//로그인 상태 확인
        const data = await apiFetch<AuthNameResponse>('/auth/name', { method: 'GET' })//사용자 이름 불러오기
        if (data?.name) {//사용자 이름이 있으면
          setIsAuthenticated(true)//인증 상태 설정
          setUserName(data.name)//사용자 이름 설정
        }
      } // 로그인이 되어 있지 않으면 상태 초기화
      catch (error) {
        console.log('User is not logged in', error)
        setIsAuthenticated(false)
        setUserName(null)
      }
    }
    checkLoginStatus()
  }, [])

  /* 로그인 상태 확인 후 사용자 이름 불러오는 코드 해석
  - data 객체에 name 속성이 있으면 로그인된 상태이므로 isAuthenticated를 true로 설정하고(인증되었다는 뜻)
    => userName에 이름 저장함
  - try가 오류가 발생하면(로그인이 안 되어 있는 거니까..겠지..회원가입할 때 사용자 이름도 필수로 입력하니깐.) isAuthenticated를 false로 설정하고(인증이 안되어있다는 뜻)
    => userName을 null로 설정
  */


  //활성 채팅 불러오기
  useEffect(() => {
    //로그인 되어 있지 않으면 활성 채팅 초기화
    if (!isAuthenticated) {
      setActiveChats([])
      return
    }
    //로그인 되어 있으면 활성 채팅 불러오기
    const fetchActiveChats = async () => {
      try {
        const data = await apiFetch<ActiveChat[]>('/chat/active', { method: 'GET' }) //활성 채팅 목록 불러오기
        //활성 채팅 설정
        if (Array.isArray(data)) {//데이터가 배열이면 활성 채팅 설정
          setActiveChats(data)//활성 채팅 설정
          setActiveChatsError(null)//오류 메시지 초기화
        }
      } catch (error) {//오류 발생 시
        console.error('Failed to load active chats', error)//오류 로그 출력
        setActiveChats([])//활성 채팅 초기화
        setActiveChatsError('최근 채팅을 불러오지 못했습니다.')//오류 메시지 설정
      }
    }
    fetchActiveChats()
  }, [isAuthenticated])

  /* 활성 채팅 불러오기 코드 해석
  - 이거는 오른쪽에 채팅을 하고있는 화면에 '최근 채팅' 목록을 불러오는 코드임.
  - 우선, isAuthenticated를 확인해서 이 값이 false면 로그인이 안 되어있다는 뜻이니까 활성 채팅 목록(setActiveChats)을 빈 배열로 초기화함.
  - 그 담에 로그인 되어 있으면 data 객체에 활성 채팅 목록을 서버에서 불러오기 위해 호출을 try함.
  - 그 목록이 배열로 되어 있으면 활성 채팅 목록(setActiveChats)에 저장함. 그리고 setActiveChatsError를 null로 설정해서 오류 메시지를 초기화 해줘. 오류가 안났으니까.
    +) 처음 채팅을 해보는 사람이면 채팅목록이 없을텐데 그때는 data에 빈배열을 저장해서 줌.즉 위와같이 처리됨.
  - try가 오류가 발생하면 콘솔창에 오류났다고 말해주고 활성 채팅(setActiveChats)을 빈 배열로 초기화해주고 setActiveChatsError에 오류 메시지를 설정해줌.
  근데 채팅을 처음해보는 사람이면 기존 채팅목록이 없어서 data객체에 값이 없지 않나? 그럼 그때도 오류가 발생해서 오류 로그를 출력하는 거 아니야?? 원래 내 생각대로라면 data가 빈 배열이면 그냥 활성 채팅 목록을 빈 배열로 설정해주고 오류 메시지는 초기화 해주는 게 맞는 거 같은데..한 번 확인해줘
  */

  //새 채팅 만들기 모달 열림 상태에 따른 body 클래스 토글
  useEffect(() => {
    document.body.classList.toggle('modal-open', isModalOpen)
    return () => {
      document.body.classList.remove('modal-open')//컴포넌트 언마운트 시 클래스 제거
    }
  }, [isModalOpen])

  /* 새 채팅 만들기 모달 열림 상태에 따른 body 클래스 토글 코드 해석
  1. 처음 상태는 false라 닫혀 있음.
  2. 사용자가 새 채팅 버튼을 누르면 handleNewChatClick이 실행되고, 그 안에서 toggleModal(true)를 호출함.
  3. toggleModal은 내부에서 setIsModalOpen(open)을 실행하므로(저 open이 true를 전달함.), 전달받은 true가 isModalOpen에 저장됩니다.
  4. isModalOpen이 true가 되면 return JSX 안에서 모달 요소가 display: block으로 바뀌고, 앞서 언급한 useEffect가 body에 modal-open 클래스를 붙여 줍니다.
  모달을 닫을 때는 handleModalClose → toggleModal(false) → setIsModalOpen(false) 순서로 돌아가며 다시 false가 됩니다.
  */

  //환영 메시지 메모이제이션
  const welcomeMessage = useMemo(() => {
    if (!userName) return defaultWelcomeMessage
    return `환영합니다, ${userName}님`
  }, [userName])

  /* 환영 메시지 메모이제이션 코드 해석
  - userName이 null이면 기본 환영 메시지 반환
  - userName이 있으면 사용자 이름을 포함한 환영 메시지 반환
  - userName이 변경될 때만 재계산
  */

  //모달 토글 함수
  const toggleModal = (open: boolean) => {
    setCreateChatError(null)
    setSelectedDepartmentId('')
    setChatRoomTitle('')
    setIsModalOpen(open)
  }

  /* 모달 토글 함수 코드 해석
  - 모달을 열거나 닫을 때 호출되는 함수
  - 매개변수 open이 true면 모달을 열고, false면 닫음 (이건 이 아래의 handleNewChatClick를 통해 새 채팅 버튼을 누르는지 여부에 따라서 결정됨)
  - 모달이 열릴 때마다 "채팅 생성 오류 메시지 초기화", "선택된 학과 ID 초기화", "채팅방 제목 초기화"
  */

  //새 채팅 클릭 핸들러
  const handleNewChatClick = () => {
    toggleModal(true)
  }

  /* 새 채팅 클릭 핸들러 코드 해석
  - 사용자가 새 채팅 버튼을 클릭할 때 호출되는 함수
  - toggleModal(true)를 호출하여 모달을 엽니다.
  */

  //모달 닫기 핸들러
  const handleModalClose = () => {
    toggleModal(false)
  }

  /* 모달 닫기 핸들러 코드 해석
  - 사용자가 모달 닫기 버튼을 클릭할 때 호출되는 함수
  - toggleModal(false)를 호출하여 모달을 닫습니다.
  */

  //채팅 생성 핸들러
  const handleCreateChat = async (event: FormEvent<HTMLFormElement>) => {//form 제출 이벤트 타입
    event.preventDefault()//기본 폼 제출 동작 방지
    setCreateChatError(null)//오류 메시지 초기화

    if (!selectedDepartmentId) {//학과가 선택되어있지 않으면
      setCreateChatError('학과를 먼저 선택해주세요.')//선택해달라고 말하고 
      return
    }

    const trimmedTitle = chatRoomTitle.trim() //채팅방 제목 공백 제거
    if (!trimmedTitle) { //제목이 비어있으면
      setCreateChatError('채팅방 제목을 입력해주세요.')//제목 입력해달라고 말하고
      return
    }

    try {//채팅방 생성 시도
      setIsCreatingChat(true)//채팅 생성 중 상태(IsCreatingChat가 원래 false인데)를 true로 두고 시작
      const chatRoom = await apiFetch<ActiveChat>('/chat/start', {//채팅 시작 API 호출
        method: 'POST',
        json: { orgId: selectedDepartmentId, title: trimmedTitle },//학과 ID와 제목을 서버에 전달함
      })
      toggleModal(false)//모달 닫기
      navigate(`/chat/${chatRoom.id}`)//생성된 채팅방으로 이동
    } catch (error) {//오류 발생 시
      console.error('Failed to create chat room', error)//오류 로그 출력하고
      setCreateChatError('채팅방을 생성하지 못했습니다. 잠시 후 다시 시도해주세요.')//오류 메시지도 보여주고
    } finally {// 항상 실행
      setIsCreatingChat(false)//채팅 생성 중 상태를 false로 바꿈
    }
  }

  /* 채팅 생성 핸들러 코드 해석
  - 사용자가 채팅 생성 폼을 제출할 때 호출되는 비동기 함수
  - 폼 제출 기본 동작을 방지하고 오류 메시지를 초기화함.
  - 선택된 학과 ID가 없으면 오류 메시지를 설정하고 함수를 종료함.
  - 채팅방 제목에서 공백을 제거한 후, 제목이 비어있으면 오류 메시지를 설정하고 함수를 종료함.
  - 채팅방 생성 시도를 위해 isCreatingChat 상태를 true로 설정함.
  - 서버에 채팅 시작 요청을 보내고, 성공하면 모달을 닫고 새로 생성된 채팅방으로 이동함.
  - 오류가 발생하면 콘솔에 오류를 출력하고 사용자에게 오류 메시지를 보여줌.
  - 마지막으로, 채팅 생성 중 상태를 false로 설정함.
  */

  //로그인 핸들러
  const handleLogin = () => {
    navigate('/auth/in')//로그인 페이지로 이동
  }

  //회원가입 핸들러
  const handleSignup = () => {
    navigate('/auth/up')//회원가입 페이지로 이동
  }

  const handleOpenSettings = () => {
    navigate('/settings')
  }

  const handleToggleSidebar = () => {
    setIsSidebarOpen((prev) => !prev)
  }

  //로그아웃 핸들러
  const handleLogout = async () => {
    try {
      await apiFetch('/auth/signout', { method: 'GET' })//로그아웃 API 호출
      window.location.reload()//페이지 새로고침
    } catch (error) {//오류 발생 시
      console.error('Failed to logout', error)//오류 로그 출력
      alert('로그아웃에 실패했습니다. 다시 시도해주세요.')//알림창으로 오류 메시지 표시
    }
  }

  //모달 백드롭 렌더링 함수
  const renderBackdrop = () => {
    if (!isModalOpen) return null//모달이 열려있지 않으면 아무것도 렌더링하지 않음
    return <div className="modal-backdrop fade show" />//모달이 열려있으면 백드롭 렌더링. (모달이 열려있을 때 배경을 어둡게 처리하는 역할)
  }

  const sidebarClassName = `home-layout__sidebar${isSidebarOpen ? '' : ' hidden'}`
  const heroPrimaryLabel = isAuthenticated ? '새 채팅 시작하기' : '로그인하고 시작하기'
  const heroSecondaryLabel = isAuthenticated ? '환경설정 열기' : '회원가입하기'
  const isHeroPrimaryDisabled = isAuthenticated && isNewChatDisabled
  const heroDescription = isAuthenticated
    ? `${userName ?? '학우'}님, 필요한 정보를 골라 빠르게 확인하세요.`
    : '학생회별 맞춤 정보와 실시간 상담을 한 곳에서 경험해보세요.'

  const handleHeroPrimary = () => {
    if (!isAuthenticated) {
      handleLogin()
      return
    }
    handleNewChatClick()
  }

  const handleHeroSecondary = () => {
    if (isAuthenticated) {
      handleOpenSettings()
      return
    }
    handleSignup()
  }

// ====================================================================================================================
  //JSX 반환
  return (
    <div className="app-container bg-gradient-hero">
      <AppHeader
        isAuthenticated={isAuthenticated}
        welcomeMessage={welcomeMessage}
        onLogin={handleLogin}
        onSignup={handleSignup}
        onLogout={handleLogout}
        onOpenSettings={handleOpenSettings}
        onToggleSidebar={handleToggleSidebar}
        isSidebarOpen={isSidebarOpen}
      />

      <main className="home-layout">
        <aside className={sidebarClassName}>
          <NewChatSection
            disabled={isNewChatDisabled}
            loading={departmentsLoading}
            hasDepartments={departments.length > 0}
            onNewChat={handleNewChatClick}
          />

          <ActiveChatList chats={activeChats} errorMessage={activeChatsError} isAuthenticated={isAuthenticated} />
        </aside>

        <section className="chatbot-hero">
          <div className="chatbot-panel" aria-hidden="true" />
          <div className="chatbot-panel__content">
            <div className="chatbot-hero__layout">
              <div className="chatbot-hero__content">
                <p className="chatbot-hero__badge">Dongguk Buddy AI</p>
                <h2 className="chatbot-hero__title">학생회별 맞춤형 대학생활 도우미</h2>
                <p className="chatbot-hero__text">{heroDescription}</p>
                <div className="chatbot-hero__cta">
                  <button
                    type="button"
                    className="buddy-primary-btn"
                    onClick={handleHeroPrimary}
                    disabled={isHeroPrimaryDisabled}
                  >
                    {heroPrimaryLabel}
                  </button>
                  <button type="button" className="buddy-secondary-btn" onClick={handleHeroSecondary}>
                    {heroSecondaryLabel}
                  </button>
                </div>

                <ul className="hero-feature-grid">
                  {heroHighlights.map((feature) => (
                    <li key={feature.title} className="hero-feature glass-panel">
                      <h3>{feature.title}</h3>
                      <p>{feature.description}</p>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="chatbot-hero__visual">
                <div className="chatbot-hero__visual-card glass-panel">
                  <img src={dongddokiLogo} alt="동똑이 마스코트" />
                  <p>AI 챗봇과 함께하는 빠른 상담</p>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>

      <div
        className={`modal fade${isModalOpen ? ' show' : ''}`}
        style={{ display: isModalOpen ? 'block' : 'none' }}
        id="new-chat-modal"
        role="dialog"
        aria-modal={isModalOpen}
        aria-hidden={!isModalOpen}
      >
        {/* 새 채팅 눌렀을 때 나타나는 모달 */}
        <div className="modal-dialog">
          <div className="modal-content">
            <div className="modal-header">
              {/* 모달 헤더에 새 채팅방 만들기랑 엑스표시로 닫는 거 */}
              <h5 className="modal-title">새 채팅방 만들기</h5> 
              <button
                type="button"
                className="btn-close"
                aria-label="닫기"
                onClick={handleModalClose}
                disabled={isCreatingChat}
              />
            </div>

            <form onSubmit={handleCreateChat}>
              <div className="modal-body">
                <label htmlFor="chat-room-department" className="form-label">
                  학과 선택
                </label>
                <select
                  id="chat-room-department"
                  className="form-select"
                  required
                  value={selectedDepartmentId}
                  onChange={(event) => setSelectedDepartmentId(event.target.value)}
                  disabled={departments.length === 0 || isCreatingChat}
                >
                  <option value="">학과를 선택해주세요.</option>
                  {departments.map((department) => (
                    <option key={department.id} value={department.id}>
                      {department.major?.majorname ?? '알 수 없는 학과'}
                    </option>
                  ))}
                </select>

                <label htmlFor="chat-room-title" className="form-label mt-3">
                  채팅방 제목
                </label>
                <input
                  type="text"
                  id="chat-room-title"
                  className="form-control"
                  placeholder="예: 2024-1 통계학과 상담" 
                  required
                  value={chatRoomTitle}
                  onChange={(event) => setChatRoomTitle(event.target.value)}
                  disabled={isCreatingChat}
                />
                <div className="form-text">선택한 학과 기준으로 새로운 채팅방이 생성됩니다.</div>

                {createChatError && <p className="error-text mt-2">{createChatError}</p>}
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={handleModalClose} disabled={isCreatingChat}>
                  취소
                </button>
                <button type="submit" className="btn btn-primary" disabled={isCreatingChat}>
                  {isCreatingChat ? '생성 중...' : '생성'}
                </button>
              </div>
            </form>
          </div>
        </div>
      </div>

      {renderBackdrop()}
    </div>
  )
}

export default HomePage
