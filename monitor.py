name: 종합 투자 모니터링 시스템

on:
  schedule:
    - cron: '0 22 * * *'    # ① 한국 시간 아침 7시 정각 (주식 1부)
    - cron: '0 7 * * *'     # ② 한국 시간 오후 4시 정각 (주식 2부)
    - cron: '*/15 * * * *'  # ③ 15분마다 상시 기상 (트위터 감시 전용)
    - cron: '*/10 0-7 * * 1-5' # ★ [추가] 한국 시간 평일 오전 9시~오후 4시 사이 10분마다 기상 (코스피 테스트용)
  workflow_dispatch:        # 수동 즉시 실행용 버튼

jobs:
  run-all-tasks:
    runs-on: ubuntu-latest
    permissions:
      contents: write       
    steps:
      - name: 코드 가져오기
        uses: actions/checkout@v4

      - name: 파이썬 세팅
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: 필수 라이브러리 설치
        run: |
          pip install yfinance pandas requests pytz

      - name: ⏰ [주식/RSI/이평선/장중코스피] 모니터링 실행
        run: python monitor.py

      - name: ⏰ [트위터] 15분마다 @0xGwoni 감시 실행
        run: python twitter.py

      - name: 읽은 트윗 ID 자동 업데이트 (기억력 유지)
        run: |
          git config --global user.name "GitHub Actions"
          git config --global user.email "actions@github.com"
          git add last_tweet.txt
          git diff --quiet && git diff --staged --quiet || (git commit -m "Update last tweet ID" && git push)
