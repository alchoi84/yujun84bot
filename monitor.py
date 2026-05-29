"""
시장 특이사항 브리핑 스크립트
- 장중(KST 평일 09:00~15:40): 국내 실시간 시황 테스트 발송
- KST 12시 이전 또는 22시 정각: 미국장 & 크립토 특이사항 알림
- 그 외 시간: 오후 국장 특이사항 알림
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
#  임계값 & 설정
# ──────────────────────────────────────────────
NASDAQ_CRASH_PCT = -3.0    # 나스닥 일간 하락 경보 (%)
BTC_CRASH_PCT    = -5.0    # 비트코인 일간 하락 경보 (%)
GOLD_ALERT_PRICE = 4400.0  # 금 가격 하회 경보 ($)
VIX_DANGER       = 30.0    # VIX 위험 기준
RSI_OVERSOLD     = 30.0    # RSI 과매도 기준
RSI_PERIOD       = 14
SMA_PERIODS      = [20, 120]

# 오전/장전 브리핑 감시 종목 (RSI + 이평선 공통)
WATCH_TICKERS = [
    "SCHD", "PDBC", "BULZ", "FNGU", "HOOD",
    "CPNG", "NTRA", "EFA",  "EEM",  "QQQ", "RBLX",
]

# 장중 실시간 시황 대상 자산
# (name, ticker, currency_type)
#   currency_type: "index" → 소수점 2자리 / "krw" → 정수 + 원
INTRADAY_ASSETS = [
    ("코스피",     "^KS11",     "index"),
    ("코스닥",     "^KQ11",     "index"),
    ("삼성전자",   "005930.KS", "krw"),
    ("SK하이닉스", "000660.KS", "krw"),
    ("현대차",     "005380.KS", "krw"),
]

# 코스피 장중 시간 (KST, 튜플 비교용)
MARKET_OPEN  = (9,  0)
MARKET_CLOSE = (15, 40)

KST = pytz.timezone("Asia/Seoul")


# ══════════════════════════════════════════════
#  공통 유틸
# ══════════════════════════════════════════════

def fetch_close(ticker: str, period: str = "5d") -> pd.Series:
    """yfinance에서 종가 시리즈를 가져온다."""
    df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"빈 응답: {ticker}")
    close = df["Close"].dropna()
    if isinstance(close, pd.DataFrame):
        close = close.squeeze()
    if close.empty:
        raise ValueError(f"유효한 종가 없음: {ticker}")
    return close


def daily_return(series: pd.Series) -> float:
    """직전 거래일 대비 수익률(%)을 반환한다."""
    if len(series) < 2:
        raise ValueError("데이터 부족 (최소 2거래일 필요)")
    return round((float(series.iloc[-1]) / float(series.iloc[-2]) - 1) * 100, 2)


def calc_rsi_wilder(series: pd.Series, period: int = RSI_PERIOD) -> float:
    """Wilder의 EWM 방식으로 14일 RSI를 계산한다 (외부 라이브러리 불필요)."""
    if len(series) < period + 1:
        raise ValueError(f"RSI 계산 불가: {len(series)}행 (최소 {period + 1}행 필요)")
    delta     = series.diff()
    gain      = delta.clip(lower=0)
    loss      = -delta.clip(upper=0)
    avg_gain  = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss  = loss.ewm(alpha=1 / period, adjust=False).mean()
    last_loss = float(avg_loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = float(avg_gain.iloc[-1]) / last_loss
    return round(100 - (100 / (1 + rs)), 2)


def scan_sma_crossovers(series: pd.Series, ticker: str) -> list:
    """20일·120일 SMA 돌파를 스캔하여 알림 문자열 리스트를 반환한다."""
    curr_price = round(float(series.iloc[-1]), 2)
    alerts     = []
    for period in SMA_PERIODS:
        if len(series) < period + 1:
            continue
        sma = series.rolling(period).mean()
        if pd.isna(sma.iloc[-2]) or pd.isna(sma.iloc[-1]):
            continue
        prev_close = float(series.iloc[-2])
        curr_close = float(series.iloc[-1])
        prev_sma   = float(sma.iloc[-2])
        curr_sma   = float(sma.iloc[-1])
        if prev_close <= prev_sma and curr_close > curr_sma:
            alerts.append(
                f"📈 [이평선 상향돌파] {ticker} 이(가) {period}일선을 뚫고 올라갔습니다!"
                f" (가격: ${curr_price:.2f})"
            )
        elif prev_close >= prev_sma and curr_close < curr_sma:
            alerts.append(
                f"📉 [이평선 하향이탈] {ticker} 이(가) {period}일선 아래로 무너졌습니다."
                f" (가격: ${curr_price:.2f})"
            )
    return alerts


# ══════════════════════════════════════════════
#  장중 실시간 시황 알림
# ══════════════════════════════════════════════

def is_kospi_market_hours(now_kst: datetime) -> bool:
    """코스피 장중(평일 KST 09:00~15:40)인지 확인한다."""
    if now_kst.weekday() >= 5:   # 토(5), 일(6) 제외
        return False
    t = (now_kst.hour, now_kst.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


def fmt_price(value: float, currency_type: str) -> str:
    """currency_type에 따라 가격 문자열을 포맷한다."""
    if currency_type == "krw":
        return f"{int(round(value)):,}원"
    return f"{value:,.2f}"   # index: 소수점 2자리, 단위 없음


def build_intraday_message() -> Optional[str]:
    """장중 국내 실시간 시황을 여러 자산 포함 메시지로 반환한다."""
    lines       = ["⏱️ [장중 실시간 시황 테스트]"]
    any_success = False

    for name, ticker, currency_type in INTRADAY_ASSETS:
        try:
            series  = fetch_close(ticker, period="2d")
            current = float(series.iloc[-1])
            ret     = daily_return(series)
            sign    = "+" if ret >= 0 else ""
            price_str = fmt_price(current, currency_type)
            lines.append(f"- {name}: {price_str} ({sign}{ret:.2f}%)")
            any_success = True
        except Exception as e:
            print(f"[WARN] {name}({ticker}) 장중 조회 실패: {e}")
            lines.append(f"- {name}: 조회 실패")

    return "\n".join(lines) if any_success else None


# ══════════════════════════════════════════════
#  오전 / 장전 브리핑: 미국장 & 크립토 특이사항
# ══════════════════════════════════════════════

def build_morning_briefing(title: Optional[str] = None) -> str:
    """
    title 미지정 → ☀️ [오전 미국장 & 크립토 특이사항 알림]
    title 지정   → 전달된 제목 문자열 사용 (예: 🌌 [오후 10시 장전 브리핑])
    """
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    header  = title if title else "☀️ [오전 미국장 & 크립토 특이사항 알림]"
    alerts  = []

    # ── 나스닥: -3% 이하일 때만
    try:
        ret = daily_return(fetch_close("^IXIC"))
        if ret <= NASDAQ_CRASH_PCT:
            alerts.append(f"🚨 [폭락] 나스닥 {ret:+.2f}% — -3% 이상 하락")
    except Exception as e:
        print(f"[WARN] 나스닥: {e}")

    # ── 비트코인: -5% 이하일 때만
    try:
        ret = daily_return(fetch_close("BTC-USD"))
        if ret <= BTC_CRASH_PCT:
            alerts.append(f"🚨 [급락] 비트코인 {ret:+.2f}% — -5% 이상 하락")
    except Exception as e:
        print(f"[WARN] 비트코인: {e}")

    # ── 금: $4,400 하회일 때만
    try:
        price = round(float(fetch_close("GC=F").iloc[-1]), 2)
        if price < GOLD_ALERT_PRICE:
            alerts.append(f"🚨 [하회] 국제 금 ${price:,.2f} — $4,400 하회")
    except Exception as e:
        print(f"[WARN] 금 가격: {e}")

    # ── VIX: 30 이상일 때만
    try:
        vix = round(float(fetch_close("^VIX").iloc[-1]), 2)
        if vix >= VIX_DANGER:
            alerts.append(f"🚨 [위험] VIX 공포지수 {vix} — 30 돌파!")
    except Exception as e:
        print(f"[WARN] VIX: {e}")

    # ── 종목별 RSI 과매도(30 이하) + SMA 돌파
    for ticker in WATCH_TICKERS:
        # RSI (60일 데이터)
        try:
            series_60d = fetch_close(ticker, period="60d")
            rsi        = calc_rsi_wilder(series_60d)
            if rsi <= RSI_OVERSOLD:
                alerts.append(f"🔥 [과매도] {ticker} RSI 30 이하 진입 (RSI: {rsi:.2f})")
        except Exception as e:
            print(f"[WARN] {ticker} RSI: {e}")

        # SMA 돌파 (1년 데이터 — 120일선 계산에 충분)
        try:
            series_1y  = fetch_close(ticker, period="1y")
            sma_alerts = scan_sma_crossovers(series_1y, ticker)
            alerts.extend(sma_alerts)
        except Exception as e:
            print(f"[WARN] {ticker} SMA: {e}")

    body = "\n".join(alerts) if alerts else "- 특이사항 없음"
    return f"{header}\n기준 시각: {now_str} KST\n\n{body}"


# ══════════════════════════════════════════════
#  오후 브리핑: 국장 특이사항
# ══════════════════════════════════════════════

def check_kospi_level_cross(series: pd.Series) -> Optional[str]:
    """KOSPI가 천 단위 경계를 돌파/이탈했으면 문구 반환, 아니면 None."""
    if len(series) < 2:
        return None
    prev    = float(series.iloc[-2])
    current = float(series.iloc[-1])
    prev_level    = int(prev    // 1000)
    current_level = int(current // 1000)
    if current_level == prev_level:
        return None
    if current_level > prev_level:
        boundary, direction = current_level * 1000, "상향 돌파"
    else:
        boundary, direction = prev_level * 1000, "하향 이탈"
    return (
        f"🔔 [알림] 코스피 {boundary:,} 포인트 {direction}!\n"
        f"   전일 {prev:,.2f}  →  현재 {current:,.2f}"
    )


def build_afternoon_briefing() -> str:
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    alerts  = []

    try:
        kospi_s = fetch_close("^KS11")
        alert   = check_kospi_level_cross(kospi_s)
        if alert:
            alerts.append(alert)
    except Exception as e:
        print(f"[WARN] 코스피: {e}")

    body = "\n".join(alerts) if alerts else "- 특이사항 없음"
    return f"2026 🌙 [오후 국장 특이사항 알림]\n기준 시각: {now_str} KST\n\n{body}"


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

    # ① 장중 실시간 시황 — 특이사항 브리핑과 완전히 독립 실행
    if is_kospi_market_hours(now_kst):
        print("장중 시간 감지 → 국내 실시간 시황 발송 중...")
        intraday_msg = build_intraday_message()
        if intraday_msg:
            print(intraday_msg)
            send_telegram_message(intraday_msg)

    # ② 시간별 브리핑 분기
    #    - 12시 이전      → 오전 미국장 브리핑 (☀️)
    #    - 22시 정각      → 장전 미국장 브리핑 (🌌, 제목만 다름)
    #    - 그 외 시간     → 오후 국장 브리핑 (🌙)
    hour = now_kst.hour

    if hour < 12:
        print("오전 브리핑 실행 중...")
        message = build_morning_briefing()
    elif hour == 22:
        print("장전(22시) 브리핑 실행 중...")
        message = build_morning_briefing(title="🌌 [오후 10시 장전 브리핑]")
    else:
        print("오후 브리핑 실행 중...")
        message = build_afternoon_briefing()

    print("\n" + "=" * 44)
    print(message)
    print("=" * 44 + "\n")

    send_telegram_message(message)
    print("완료.")
