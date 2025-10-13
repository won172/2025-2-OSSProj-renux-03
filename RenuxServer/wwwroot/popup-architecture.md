# 학과별 채팅방 생성 팝업 구현 구상

## 서비스 흐름 요약
1. 좌측 사이드바에 "새로운 채팅 시작" 섹션 아래 학과 목록을 수직으로 출력합니다. (예: 통계학과, 경제학과, 경영학과 …)
2. 사용자가 특정 학과를 클릭하면 학과 선택을 고정한 채 팝업(모달) 레이어가 뜨고, 채팅방 제목을 입력받습니다.
3. "생성" 버튼을 누르면 백엔드 API로 학과 ID/이름과 채팅방 제목을 전달하여 DB에 새로운 채팅방 레코드를 생성합니다.
4. 성공 응답을 받으면 해당 채팅방 화면으로 라우팅하거나 채팅 내역을 초기화한 뒤 대화 영역으로 focus를 이동합니다.

## UI 구성 계획
### 사이드바 목록 (index.html)
- 기존 "새로운 채팅 시작" 아래에 `<ul id="department-list">`를 배치하고, 서버에서 받은 학과 목록을 `<li>`로 렌더링합니다.
- 각 항목에는 `data-department-id` 속성을 부여하여 후속 요청 시 사용할 수 있도록 합니다.

```html
<section class="chat-start">
  <h2>새로운 채팅 시작</h2>
  <ul id="department-list" class="department-list"></ul>
</section>
```

### 팝업 레이어
- Bootstrap 모달 컴포넌트를 활용하여 재사용성과 접근성을 확보합니다.
- 기본 구조는 `#new-chat-modal`로 정의하고, 선택된 학과명을 표시하는 영역과 제목 입력 필드, 확인/취소 버튼을 포함합니다.

```html
<div class="modal fade" id="new-chat-modal" tabindex="-1" aria-hidden="true">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title">새 채팅방 만들기</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="닫기"></button>
      </div>
      <div class="modal-body">
        <p id="selected-department-label"></p>
        <label for="chat-room-title" class="form-label">채팅방 제목</label>
        <input type="text" id="chat-room-title" class="form-control" placeholder="예: 2024-1 통계학과 상담" required />
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">취소</button>
        <button type="button" id="create-chat-room" class="btn btn-primary">생성</button>
      </div>
    </div>
  </div>
</div>
```

### 스타일 보강 (index.css / site.css)
- 사이드바 학과 목록을 명확하게 보이도록 패딩, hover 효과를 정의합니다.

```css
.department-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.department-list li {
  padding: 8px 12px;
  cursor: pointer;
  border-radius: 6px;
}

.department-list li:hover,
.department-list li:focus {
  background-color: var(--sidebar-hover, #f3f4f6);
}
```

## 스크립트 흐름 (index.js)
1. 페이지 로딩 시 학과 목록을 불러오는 API (`GET /api/departments`)를 호출하여 사이드바를 채웁니다.
2. 학과 항목 클릭 시 선택된 학과 정보를 전역 상태(or 모듈 스코프 변수)에 저장하고 모달을 호출합니다.
3. 모달의 "생성" 버튼을 누르면 입력값 검증 후 새로운 채팅방 생성 API (`POST /api/departments/{departmentId}/chatrooms`)에 요청합니다.
4. 성공 시 채팅방 ID를 받아 라우팅하거나, 채팅 메시지를 초기화한 뒤 채팅 영역으로 전환합니다.

```javascript
document.addEventListener('DOMContentLoaded', async () => {
  const listEl = document.getElementById('department-list');
  const modalEl = document.getElementById('new-chat-modal');
  const modal = new bootstrap.Modal(modalEl);
  const labelEl = document.getElementById('selected-department-label');
  const titleInput = document.getElementById('chat-room-title');
  let selectedDepartment = null;

  const departments = await fetch('/api/departments').then(res => res.json());
  listEl.innerHTML = departments
    .map(dep => `<li data-id="${dep.id}">${dep.name}</li>`) 
    .join('');

  listEl.addEventListener('click', event => {
    const target = event.target.closest('li');
    if (!target) return;
    selectedDepartment = {
      id: target.dataset.id,
      name: target.textContent.trim(),
    };
    labelEl.textContent = `${selectedDepartment.name} 학과 채팅방 생성`; // 선택된 학과 노출
    titleInput.value = '';
    modal.show();
  });

  document.getElementById('create-chat-room').addEventListener('click', async () => {
    const title = titleInput.value.trim();
    if (!selectedDepartment || !title) {
      alert('채팅방 제목을 입력해주세요.');
      return;
    }

    const response = await fetch(`/api/departments/${selectedDepartment.id}/chatrooms`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });

    if (!response.ok) {
      alert('채팅방 생성에 실패했습니다. 다시 시도해주세요.');
      return;
    }

    const chatRoom = await response.json();
    modal.hide();
    // TODO: 새 채팅방 페이지로 이동하거나, 채팅 영역 초기화 후 연결
    openChatRoom(chatRoom.id);
  });
});
```

> `openChatRoom` 함수는 기존 라우팅/소켓 연결 로직에 맞춰 새 채팅방으로 전환하는 역할을 수행합니다.

## 백엔드 연동 고려사항
- 학과 목록 API: 프론트엔드에서 사용 가능한 최소 정보를 제공 (학과 ID, 이름).
- 채팅방 생성 API: 요청 body로 `title`, 필요 시 `userId` 또는 작성자 정보 포함. 서버에서 생성된 채팅방 ID, 초기 상태를 응답으로 반환.
- 인증: 로그인된 사용자만 접근 가능하도록 세션/JWT 체크 후 API 접근을 허용합니다.

## 테스트 시나리오
- 학과 목록이 비어있는 경우 graceful fallback ("등록된 학과가 없습니다" 메시지).
- 제목 미입력 시 안내 문구 노출.
- API 실패 시 사용자 알림 및 재시도 가능하도록 처리.
- 생성 후 자동으로 새 채팅방으로 전환되는지 확인.

위 흐름을 기반으로 프론트엔드와 백엔드 작업을 병행하면 원하는 학과별 채팅방 생성 기능을 구현할 수 있습니다.
