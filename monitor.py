"""
시장 브리핑 모니터링 스크립트
- KST 12시 이전: 미국장 & 크립토 브리핑
- KST 12시 이후: 국장 브리핑
실행: python monitor.py
의존성: pip install yfinance pandas requests pytz
"""

import os
import requests
import pandas as pd
import yfinance as yf
import pytz
from datetime import datetime
from typing import Optional

# ──────────────────────────────────────────────
#  텔레그램 설정
# ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "8847077981:AAHtmNitAv8FJEojD8ZgtiRgX7SiDZyIVWk")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1509458456")

# ──────────────────────────────────────────────
#  경보 임계값
# ──────────────────────────────────────────────
NASDAQ_CRASH_PCT  = -3.0    # 나스닥 일간 수익률 경보 기준 (%)
BTC_CRASH_PCT     = -5.0    # 비트코인 일간 수익률 경보 기준 (%)
GOLD_ALERT_PRICE  = 4400.0  # 금 가격 하회 경보 기준 ($)

KST = pytz.timezone("Asia/Seoul")


# ══════════════════════════════════════════════
#  공통 유틸
# ══════════════════════════════════════════════

def fetch_close(ticker: str, period: str = "5d") -> pd.Series:
    """yfinance에서 종가 시리즈를 가져온다."""
    df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"데이터 없음: {ticker}")
    close = df["Close"].dropna()
    if isinstance(close, pd.DataFrame):
        close = close.squeeze()
    return close


def daily_return(series: pd.Series) -> float:
    """직전 거래일 대비 수익률(%)을 반환한다."""
    if len(series) < 2:
        raise ValueError("일간 수익률 계산을 위한 데이터 부족")
    return round((float(series.iloc[-1]) / float(series.iloc[-2]) - 1) * 100, 2)


# ══════════════════════════════════════════════
#  오전 브리핑: 미국장 & 크립토
# ══════════════════════════════════════════════

def build_morning_briefing() -> str:
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    nasdaq_s = fetch_close("^IXIC")
    btc_s    = fetch_close("BTC-USD")
    gold_s   = fetch_close("GC=F")

    nasdaq_price = round(float(nasdaq_s.iloc[-1]), 2)
    nasdaq_ret   = daily_return(nasdaq_s)
    btc_price    = round(float(btc_s.iloc[-1]), 2)
    btc_ret      = daily_return(btc_s)
    gold_price   = round(float(gold_s.iloc[-1]), 2)
    gold_ret     = daily_return(gold_s)

    alerts = []
    if nasdaq_ret <= NASDAQ_CRASH_PCT:
        alerts.append(f"🚨 [경고] 나스닥 -3% 이상 폭락! ({nasdaq_ret:+.2f}%)")
    if btc_ret <= BTC_CRASH_PCT:
        alerts.append(f"🚨 [경고] 비트코인 -5% 이상 급락! ({btc_ret:+.2f}%)")
    if gold_price < GOLD_ALERT_PRICE:
        alerts.append(f"🚨 [경고] 금 가격 $4,400 하회! (현재 ${gold_price:,.2f})")

    alert_block = ("\n\n" + "\n".join(alerts)) if alerts else ""

    return f"""[미국장 & 크립토 브리핑]
기준 시각: {now_str} KST

나스닥 (^IXIC)
현재: {nasdaq_price:,.2f}  /  일간: {nasdaq_ret:+.2f}%

비트코인 (BTC-USD)
현재: ${btc_price:,.0f}  /  일간: {btc_ret:+.2f}%

국제 금 (GC=F)
현재: ${gold_price:,.2f}  /  일간: {gold_ret:+.2f}%{alert_block}""".strip()


# ══════════════════════════════════════════════
#  오후 브리핑: 국장
# ══════════════════════════════════════════════

def check_kospi_level_cross(series: pd.Series) -> Optional[str]:
    """KOSPI가 천 단위 경계를 돌파/이탈했으면 알림 문구를, 아니면 None을 반환한다."""
    if len(series) < 2:
        return None

    prev    = float(series.iloc[-2])
    current = float(series.iloc[-1])
    prev_level    = int(prev    // 1000)
    current_level = int(current // 1000)

    if current_level == prev_level:
        return None

    # 상향이면 current_level * 1000, 하향이면 prev_level * 1000이 경계
    if current_level > prev_level:
        boundary  = current_level * 1000
        direction = "상향 돌파"
    else:
        boundary  = prev_level * 1000
        direction = "하향 이탈"

    return (
        f"🔔 [알림] 코스피 {boundary:,} 포인트 {direction}!\n"
        f"전일 {prev:,.2f}  →  현재 {current:,.2f}"
    )


def build_afternoon_briefing() -> str:
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    kospi_s     = fetch_close("^KS11")
    kospi_price = round(float(kospi_s.iloc[-1]), 2)
    kospi_ret   = daily_return(kospi_s)

    level_alert = check_kospi_level_cross(kospi_s)
    alert_block = (f"\n\n{level_alert}") if level_alert else ""

    return f"""[국장 브리핑]
기준 시각: {now_str} KST

코스피 (^KS11)
현재: {kospi_price:,.2f}  /  일간: {kospi_ret:+.2f}%{alert_block}""".strip()


# ══════════════════════════════════════════════
#  텔레그램 발송
# ══════════════════════════════════════════════

def send_telegram_message(text: str) -> bool:
    """텔레그램 봇 API를 통해 메시지를 발송한다."""
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        print(f"[OK] 텔레그램 발송 완료 (status={resp.status_code})")
        return True
    except requests.RequestException as exc:
        print(f"[ERROR] 텔레그램 발송 실패: {exc}")
        return False


# ══════════════════════════════════════════════
#  메인 실행
# ══════════════════════════════════════════════

if __name__ == "__main__":
    now_kst = datetime.now(KST)
    print(f"현재 KST: {now_kst.strftime('%Y-%m-%d %H:%M')}")

    try:
        if now_kst.hour < 12:
            print("오전 → 미국장 & 크립토 브리핑 실행 중...")
            message = build_morning_briefing()
        else:
            print("오후 → 국장 브리핑 실행 중...")
            message = build_afternoon_briefing()

        print("\n" + "=" * 44)
        print(message)
        print("=" * 44 + "\n")

        send_telegram_message(message)

    except ValueError as exc:
        print(f"[ERROR] 데이터 오류: {exc}")
    except Exception as exc:
        print(f"[ERROR] 예기치 않은 오류: {exc}")
        raise

    print("완료.")
