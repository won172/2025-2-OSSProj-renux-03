"""동국대 직원 연락처를 수집하는 일회성 Selenium 크롤러입니다."""
from __future__ import annotations

import time
import warnings
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm

warnings.filterwarnings("ignore")

TARGET_URL = "https://www.dongguk.edu/staff/list"
OUTPUT_PATH = Path("./data/dongguk_staff_contacts.csv")


def build_driver() -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    return webdriver.Chrome(options=chrome_options)


def expand_all_nodes(driver) -> None:
    """모든 트리를 재귀적으로(반복적으로) 펼칩니다."""
    print("🌳 트리 메뉴 펼치기 시작...")
    max_depth = 20 # 무한 루프 방지
    for _ in range(max_depth):
        # 닫혀있는 노드의 '펼치기 아이콘' 찾기 (li.jstree-closed 직계 자식 i.jstree-ocl)
        closed_icons = driver.find_elements(By.CSS_SELECTOR, "li.jstree-closed > i.jstree-ocl")
        
        if not closed_icons:
            print("✨ 모든 트리가 펼쳐졌습니다.")
            break
        
        print(f"📂 {len(closed_icons)}개의 닫힌 폴더를 펼칩니다...")
        for icon in closed_icons:
            try:
                # 화면에 안 보일 수 있으므로 JS로 클릭
                driver.execute_script("arguments[0].click();", icon)
                time.sleep(0.05) 
            except Exception:
                pass
        
        time.sleep(1.0) # DOM 업데이트 대기


def crawl_staff_contacts() -> pd.DataFrame:
    driver = build_driver()
    driver.get(TARGET_URL)
    wait = WebDriverWait(driver, 20)

    try:
        time.sleep(3.0)
        # 트리 영역 대기
        wait.until(EC.presence_of_element_located((By.ID, "tree")))
    except Exception as exc:
        driver.quit()
        raise RuntimeError("부서 트리(#tree)를 찾을 수 없습니다.") from exc

    # 1. 트리 모두 펼치기
    expand_all_nodes(driver)

    data: list[list[str]] = []
    
    # 2. 모든 부서 링크 수집
    # 트리가 다 펼쳐졌으므로 모든 앵커 태그를 가져옴
    dept_links = driver.find_elements(By.CSS_SELECTOR, "#tree a.jstree-anchor")
    print(f"총 {len(dept_links)}개의 부서 링크를 찾았습니다.")

    # 3. 순회하며 데이터 수집
    for index in tqdm(range(len(dept_links)), desc="부서별 데이터 수집"):
        try:
            # DOM이 변경되었을 수 있으므로 다시 찾아서 인덱스로 접근 (안전한 방법)
            current_links = driver.find_elements(By.CSS_SELECTOR, "#tree a.jstree-anchor")
            if index >= len(current_links):
                break
            
            link = current_links[index]
            dept_name = link.text.strip()
            
            # 클릭하여 테이블 로드
            driver.execute_script("arguments[0].click();", link)
            time.sleep(0.8) # 데이터 로딩 대기

            # 테이블 데이터 수집
            rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
            
            if not rows:
                continue

            for row in rows:
                # "데이터가 없습니다" 같은 안내 메시지 제외
                if "데이터가 없습니다" in row.text:
                    continue

                cols = [cell.text.strip() for cell in row.find_elements(By.TAG_NAME, "td")]
                
                # 유효한 데이터 행인지 확인 (최소 2개 이상 컬럼 등)
                if len(cols) >= 2:
                    # 부서명(트리에서 클릭한 이름)을 첫 번째 컬럼으로 추가하여 저장
                    # cols가 [이름, 담당업무, 전화번호, ...] 형태라고 가정 시
                    # 만약 테이블 안에 부서명이 없다면 dept_name을 활용
                    
                    # 여기서는 단순하게 [트리부서명] + [테이블컬럼들...] 로 저장
                    data.append([dept_name] + cols)

        except Exception as e:
            # 특정 부서 수집 실패해도 계속 진행
            # print(f"⚠️ '{dept_name}' 수집 중 오류: {e}")
            pass

    driver.quit()
    print("✅ 크롤링 완료 및 브라우저 종료.")

    # 데이터프레임 생성
    # 컬럼명은 실제 데이터 구조에 따라 조정 필요. 동적으로 생성.
    if data:
        max_cols = max(len(row) for row in data)
        columns = ["조직(트리)"] + [f"Data_{i}" for i in range(max_cols - 1)]
        # 일반적인 예상 컬럼: 조직, 직위, 성명, 담당업무, 전화번호, 이메일 등
        # 필요시 columns = ["조직", "직위", "성명", "담당업무", "전화번호", "이메일"] 등으로 고정 가능
        
        df = pd.DataFrame(data, columns=columns)
        df.drop_duplicates(inplace=True)
    else:
        df = pd.DataFrame()

    return df


def main() -> None:
    df = crawl_staff_contacts()
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"총 {len(df)}건의 데이터 저장 완료! ({OUTPUT_PATH.resolve()})")


if __name__ == "__main__":
    main()
