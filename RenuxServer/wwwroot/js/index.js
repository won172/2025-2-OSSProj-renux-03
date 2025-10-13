document.addEventListener('DOMContentLoaded', () => {
    // DOM 요소 가져오기
    const guestView = document.getElementById('guest-view');
    const userView = document.getElementById('user-view');
    const welcomeMessage = document.getElementById('welcome-message');
    const newChatBtn = document.getElementById('new-chat-btn');
    const departmentSelect = document.getElementById('chat-room-department');
    const activeChatSection = document.getElementById('active-chat-section');
    const activeChatList = document.getElementById('active-chat-list');
    const loginBtn = document.getElementById('login-btn');
    const signupBtn = document.getElementById('signup-btn');
    const logoutBtn = document.getElementById('logout-btn');
    const modalElement = document.getElementById('new-chat-modal');
    const chatRoomTitleInput = document.getElementById('chat-room-title');
    const createChatButton = document.getElementById('create-chat-room');
    const modal = modalElement ? new bootstrap.Modal(modalElement) : null;
    let departments = [];

    // 실시간 학과 선택 옵션을 채우는 도우미
    const populateDepartmentSelect = (
        items = [],
        { placeholder = '학과를 선택해주세요.', disabled = false } = {}
    ) => {
        if (!departmentSelect) return;

        departmentSelect.innerHTML = '';
        const defaultOption = document.createElement('option');
        defaultOption.value = '';
        defaultOption.textContent = placeholder;
        departmentSelect.appendChild(defaultOption);

        items.forEach(org => {
            const option = document.createElement('option');
            option.value = org.id;
            option.textContent = org.major?.majorname ?? '알 수 없는 학과';
            departmentSelect.appendChild(option);
        });

        departmentSelect.disabled = disabled;
        if (createChatButton) createChatButton.disabled = disabled;
    };

    // 학과 목록을 불러와 새 채팅 플로우를 준비
    const fetchDepartmentList = async () => {
        try {
            const response = await fetch('/req/orgs');
            if (!response.ok) throw new Error('학과 정보를 불러오지 못했습니다.');
            const orgs = await response.json();

            departments = Array.isArray(orgs) ? orgs : [];

            if (departments.length === 0) {
                populateDepartmentSelect([], {
                    placeholder: '등록된 학과가 없습니다.',
                    disabled: true,
                });
                newChatBtn.disabled = false;
                return;
            }

            populateDepartmentSelect(departments, {
                placeholder: '학과를 선택해주세요.',
                disabled: false,
            });
            if (departmentSelect) departmentSelect.setCustomValidity('');
            newChatBtn.disabled = false;
        } catch (error) {
            console.error('Error fetching new chat options:', error);

            departments = [];
            populateDepartmentSelect([], {
                placeholder: '학과 정보를 불러올 수 없습니다.',
                disabled: true,
            });
            newChatBtn.disabled = false;
        }
    };

    // '최근 채팅' 목록 가져오기 (/chat/active) - 로그인된 사용자 전용
    const fetchActiveChats = () => {
        fetch('/chat/active')
            .then(response => {
                if (!response.ok) throw new Error('Not logged in or failed to fetch');
                return response.json();
            })
            .then(data => {
                if (data.length > 0) {
                    activeChatList.innerHTML = '';
                    data.forEach(chat => {
                        // API 응답이 title을 제공하지 않는 경우를 대비해 안전하게 학과 이름을 사용
                        const fallbackTitle = chat.organization?.major?.majorname ?? '이름 없는 채팅';
                        const title = chat.title || fallbackTitle;
                        const listItem = `<li><a href="/chat/${chat.id}">${title}</a></li>`;
                        activeChatList.innerHTML += listItem;
                    });
                    activeChatSection.classList.remove('hidden'); // 목록이 있으면 섹션을 보여줌
                }
            })
            .catch(error => {
                console.error('Could not fetch active chats:', error);
                activeChatSection.classList.add('hidden'); // 실패 시 섹션을 숨김
            });
    };

    // 로그인 상태 확인 및 UI 설정
    const checkLoginStatus = () => {
        fetch('/auth/name')
            .then(response => {
                if (!response.ok) throw new Error('Not logged in');
                return response.json();
            })
            .then(data => { // 로그인된 상태
                welcomeMessage.textContent = `환영합니다, ${data.name}님`;
                guestView.classList.add('hidden');
                userView.classList.remove('hidden');
                fetchActiveChats(); // 로그인 확인 후 활성화된 채팅 목록 로드
            })
            .catch(error => { // 로그인되지 않은 상태
                console.log('User is not logged in.');
                guestView.classList.remove('hidden');
                userView.classList.add('hidden');
                activeChatSection.classList.add('hidden'); // 비로그인 시 활성화 채팅 섹션 숨김
            });
    };

    // 버튼 이벤트 리스너 설정
    loginBtn.addEventListener('click', () => { window.location.href = '/auth/in'; });
    signupBtn.addEventListener('click', () => { window.location.href = '/auth/up'; });
    logoutBtn.addEventListener('click', () => {
        fetch('/auth/signout', { method: 'GET' })
            .then(response => {
                if (response.ok) window.location.reload();
                else throw new Error('로그아웃 실패');
            })
            .catch(error => alert(error.message));
    });

    // 새 채팅 버튼: 모달을 띄워 학과/제목 입력을 받는다
    newChatBtn?.addEventListener('click', () => {
        if (!modal) return;
        if (departmentSelect) departmentSelect.value = '';
        if (chatRoomTitleInput) chatRoomTitleInput.value = '';
        modal.show();
        if (!departments.length) {
            alert('학과 목록을 아직 불러오지 못했습니다. 서버 연결 후 다시 시도해주세요.');
        }
    });

    modalElement?.addEventListener('shown.bs.modal', () => {
        departmentSelect?.focus();
    });

    // 채팅방 생성 버튼 핸들러: 백엔드 POST /chat/new 연동
    createChatButton?.addEventListener('click', async () => {
        const orgId = departmentSelect?.value ?? '';
        if (!orgId) {
            alert('학과를 먼저 선택해주세요.');
            departmentSelect?.focus();
            return;
        }

        const parsedOrgId = Number(orgId);
        if (Number.isNaN(parsedOrgId)) {
            alert('올바른 학과를 선택해주세요.');
            departmentSelect?.focus();
            return;
        }

        const title = chatRoomTitleInput.value.trim();
        if (!title) {
            alert('채팅방 제목을 입력해주세요.');
            chatRoomTitleInput.focus();
            return;
        }

        try {
            const response = await fetch('/chat/new', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ orgId: parsedOrgId, title }),
            });

            if (!response.ok) {
                throw new Error('채팅방 생성에 실패했습니다.');
            }

            const chatRoom = await response.json();
            modal.hide();
            window.location.href = `/chat/${chatRoom.id}`;
        } catch (error) {
            console.error('Failed to create chat room:', error);
            alert('채팅방을 생성하지 못했습니다. 잠시 후 다시 시도해주세요.');
        }
    });

    // 페이지 로드 시 실행
    checkLoginStatus();
    fetchDepartmentList(); // 학과 목록은 항상 로드
});
