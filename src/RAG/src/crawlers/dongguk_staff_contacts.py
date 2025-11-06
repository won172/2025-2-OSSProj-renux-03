"""One-shot Selenium crawler for Dongguk staff contact list."""
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
    return webdriver.Chrome(options=chrome_options)


def crawl_staff_contacts() -> pd.DataFrame:
    driver = build_driver()
    driver.get(TARGET_URL)
    wait = WebDriverWait(driver, 10)

    try:
        time.sleep(5.0)
        dept_elements = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.leftArea a")))
        print(f"총 {len(dept_elements)}개 부서를 감지했습니다.")
    except Exception as exc:  # noqa: BLE001
        driver.quit()
        raise RuntimeError("부서 목록을 불러올 수 없습니다.") from exc

    data: list[list[str]] = []

    try:
        for index in tqdm(range(len(dept_elements)), desc="부서 크롤링"):
            dept_elements = driver.find_elements(By.CSS_SELECTOR, "div.leftArea a")
            if index >= len(dept_elements):
                break
            dept = dept_elements[index]
            dept_name = dept.text.strip()
            if not dept_name:
                continue
            driver.execute_script("arguments[0].click();", dept)
            time.sleep(1.5)
            rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
            for row in rows:
                cols = [cell.text.strip() for cell in row.find_elements(By.TAG_NAME, "td")]
                if len(cols) == 3:
                    data.append([dept_name] + cols)
    finally:
        driver.quit()
        print("✅ 크롤링 완료 및 브라우저 종료.")

    df = pd.DataFrame(data, columns=["조직", "부서(학과)명", "담당업무", "전화번호"])
    return df


def main() -> None:
    df = crawl_staff_contacts()
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"총 {len(df)}건의 데이터 저장 완료! ({OUTPUT_PATH.resolve()})")


if __name__ == "__main__":
    main()
