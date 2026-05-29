"""
자산배분 듀얼 모멘텀 스위칭 & 시장 위기 감지 모니터링 스크립트
실행: python monitor.py
의존성: pip install yfinance pandas requests
"""

import os
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

# ──────────────────────────────────────────────
#  텔레그램 설정 (여기에 직접 입력하거나 환경변수 사용)
# ──────────────────────────────────────────────
TELEGRAM_TOKEN   = ("8847077981:AAHtmNitAv8FJEojD8ZgtiRgX7SiDZyIVWk")
TELEGRAM_CHAT_ID = ("1509458456")

# ──────────────────────────────────────────────
#  분석 파라미터
# ──────────────────────────────────────────────
MOMENTUM_PERIOD   = 120   # 영업일 기준 6개월
SMA_PERIOD        = 120   # 120일 이동평균
SMA_ALERT_PCT     = 1.5   # SMA 근접 경보 임계값 (%)
VIX_CAUTION       = 25
VIX_DANGER        = 30
VIX_PANIC         = 35

TICKERS = {
    "KOSPI":    "^KS11",
    "SP500":    "^GSPC",
    "Bond_IEF": "IEF",
    "Cash_BIL": "BIL",
    "VIX":      "^VIX",
}


# ══════════════════════════════════════════════
#  데이터 수집
# ══════════════════════════════════════════════

def fetch_close(ticker: str, period: str = "1y") -> pd.Series:
    """yfinance에서 종가 시리즈를 가져온다."""
    df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"데이터 없음: {ticker}")
    close = df["Close"].dropna()
    if isinstance(close, pd.DataFrame):
        close = close.squeeze()
    return close


# ══════════════════════════════════════════════
#  기능 1: 6개월 듀얼 모멘텀
# ══════════════════════════════════════════════

def calc_momentum(series: pd.Series, period: int = MOMENTUM_PERIOD) -> float:
    """최근 영업일 기준 period일 수익률(%)을 반환한다."""
    if len(series) < period + 1:
        raise ValueError(f"데이터 부족: {len(series)}행 (필요 {period + 1}행)")
    ret = (series.iloc[-1] / series.iloc[-(period + 1)] - 1) * 100
    return round(float(ret), 2)


def analyze_dual_momentum() -> dict:
    """
    듀얼 모멘텀 로직:
    1) KOSPI / S&P500 중 양수이면서 더 높은 쪽 → 추천 자산
    2) 둘 다 음수 → IEF 확인: 양수면 채권, 음수면 현금
    """
    series_kospi = fetch_close(TICKERS["KOSPI"], period="2y")
    series_sp500 = fetch_close(TICKERS["SP500"], period="2y")
    series_ief   = fetch_close(TICKERS["Bond_IEF"], period="2y")

    ret_kospi = calc_momentum(series_kospi)
    ret_sp500 = calc_momentum(series_sp500)
    ret_ief   = calc_momentum(series_ief)

    if ret_kospi > 0 or ret_sp500 > 0:
        if ret_kospi >= ret_sp500:
            signal  = "코스피(^KS11) 매수"
            winner  = "KOSPI"
        else:
            signal  = "S&P 500(^GSPC) 매수"
            winner  = "S&P 500"
    else:
        if ret_ief > 0:
            signal  = "채권(IEF) 매수"
            winner  = "IEF"
        else:
            signal  = "현금(BIL) 보유"
            winner  = "Cash"

    return {
        "signal":     signal,
        "winner":     winner,
        "ret_kospi":  ret_kospi,
        "ret_sp500":  ret_sp500,
        "ret_ief":    ret_ief,
    }


# ══════════════════════════════════════════════
#  기능 2: S&P 500 120일 SMA 근접 감지
# ══════════════════════════════════════════════

def analyze_sma_proximity() -> dict:
    """현재 S&P500이 120일 SMA 위에 있고, 격차가 1.5% 이내면 경보."""
    series = fetch_close(TICKERS["SP500"], period="2y")
    if len(series) < SMA_PERIOD:
        raise ValueError("SMA 계산을 위한 데이터 부족")

    current = float(series.iloc[-1])
    sma120  = float(series.rolling(SMA_PERIOD).mean().iloc[-1])
    gap_pct = (current - sma120) / sma120 * 100

    above_sma = current > sma120
    near_alert = above_sma and (0 <= gap_pct <= SMA_ALERT_PCT)

    return {
        "current":    round(current, 2),
        "sma120":     round(sma120, 2),
        "gap_pct":    round(gap_pct, 2),
        "above_sma":  above_sma,
        "near_alert": near_alert,
    }


# ══════════════════════════════════════════════
#  기능 3: VIX 공포지수 단계 분류
# ══════════════════════════════════════════════

def analyze_vix() -> dict:
    """VIX 현재값을 가져와 위험 단계를 분류한다."""
    series = fetch_close(TICKERS["VIX"], period="5d")
    vix_val = round(float(series.iloc[-1]), 2)

    if vix_val >= VIX_PANIC:
        level = "초비상 공황"
    elif vix_val >= VIX_DANGER:
        level = "위험"
    elif vix_val >= VIX_CAUTION:
        level = "주의"
    else:
        level = "안정"

    return {
        "vix_val": vix_val,
        "level":   level,
    }


# ══════════════════════════════════════════════
#  메시지 조합
# ══════════════════════════════════════════════

def build_message(momentum: dict, sma: dict, vix: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if sma["above_sma"]:
        if sma["near_alert"]:
            sma_status = f"하향 돌파 근접 경보 (격차 +{sma['gap_pct']}%)"
        else:
            sma_status = f"120일선 위 안전 (격차 +{sma['gap_pct']}%)"
    else:
        sma_status = f"120일선 하향 돌파 중 (격차 {sma['gap_pct']}%)"

    msg = f"""자산배분 모멘텀 리포트
기준 시각: {now}

[1] 6개월 듀얼 모멘텀 신호
추천 신호: {momentum['signal']}
코스피 6개월 수익률: {momentum['ret_kospi']:+.2f}%
S&P 500 6개월 수익률: {momentum['ret_sp500']:+.2f}%
채권(IEF) 6개월 수익률: {momentum['ret_ief']:+.2f}%

[2] S&P 500 120일 이평선
현재 지수: {sma['current']:,.2f}
120일 SMA: {sma['sma120']:,.2f}
상태: {sma_status}

[3] VIX 공포지수
현재 VIX: {vix['vix_val']}
위험 단계: {vix['level']}
(기준: 25 이상 주의 / 30 이상 위험 / 35 이상 초비상)
"""
    return msg.strip()


# ══════════════════════════════════════════════
#  텔레그램 발송
# ══════════════════════════════════════════════

def send_telegram_message(text: str) -> bool:
    """
    텔레그램 봇 API를 통해 메시지를 발송한다.
    성공 시 True, 실패 시 False를 반환하고 오류를 출력한다.
    """
    if TELEGRAM_TOKEN.startswith("여기에") or TELEGRAM_CHAT_ID.startswith("여기에"):
        print("[경고] 텔레그램 토큰/채팅ID가 설정되지 않았습니다.")
        print("       환경변수 TELEGRAM_TOKEN, TELEGRAM_CHAT_ID를 설정하거나")
        print("       코드 상단 변수에 직접 입력하세요.\n")
        print("──── 발송 예정 메시지 미리보기 ────")
        print(text)
        return False

    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text":    text,
    }
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
    print("=" * 50)
    print("  자산배분 듀얼 모멘텀 모니터링 시작")
    print("=" * 50)

    try:
        print("[1/3] 6개월 듀얼 모멘텀 계산 중...")
        momentum = analyze_dual_momentum()
        print(f"      → 신호: {momentum['signal']}")

        print("[2/3] S&P 500 120일 SMA 분석 중...")
        sma = analyze_sma_proximity()
        proximity = "⚠️ 근접 경보" if sma["near_alert"] else "정상"
        print(f"      → SMA 격차: {sma['gap_pct']:+.2f}% ({proximity})")

        print("[3/3] VIX 공포지수 조회 중...")
        vix = analyze_vix()
        print(f"      → VIX: {vix['vix_val']} ({vix['level']})")

        print("\n메시지 조합 및 텔레그램 발송 중...")
        message = build_message(momentum, sma, vix)
        send_telegram_message(message)

    except ValueError as exc:
        print(f"[ERROR] 데이터 오류: {exc}")
    except Exception as exc:
        print(f"[ERROR] 예기치 않은 오류: {exc}")
        raise

    print("\n완료.")
