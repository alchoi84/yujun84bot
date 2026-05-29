"""
자산 모멘텀 모니터 — 텔레그램 명령어 지원 버전
종목 관리: 텔레그램에서 /add, /remove 명령어로 실시간 변경
실행: python monitor.py
의존성: pip install yfinance pandas requests pytz
"""

import os
import subprocess
import requests
import pandas as pd
import yfinance as yf
import pytz
from datetime import datetime
from typing import Optional

# ──────────────────────────────────────────────
#  경로 설정
# ──────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.txt")
LAST_ID_FILE   = os.path.join(BASE_DIR, "last_update_id.txt")

# ──────────────────────────────────────────────
#  텔레그램 설정
# ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "8847077981:AAHtmNitAv8FJEojD8ZgtiRgX7SiDZyIVWk")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1509458456")

# ──────────────────────────────────────────────
#  분석 파라미터
# ──────────────────────────────────────────────
RSI_PERIOD   = 14
RSI_OVERSOLD = 30.0
SMA_PERIODS  = [20, 120]

KST = pytz.timezone("Asia/Seoul")

TITLE_MAP = {
    7:  "☀️ [오전 7시 정기 마켓 브리핑]",
    16: "🌙 [오후 4시 국장 마감 브리핑]",
    22: "🌌 [오후 10시 미장 장전 브리핑]",
}
TITLE_DEFAULT = "⏰ [정기 수동 브리핑]"
NO_ALERT_MSG  = "- 특이사항 및 조건 부합 종목 없음 (시장 관망 유지)"

# watchlist.txt 없을 때 자동 생성할 기본 종목
DEFAULT_TICKERS = [
    "SCHD", "PDBC", "BULZ", "FNGU", "HOOD",
    "CPNG", "NTRA", "EFA",  "EEM",  "QQQ", "RBLX",
    "005930.KS", "000660.KS", "005380.KS",
    "BTC-USD", "ETH-USD", "USDT-USD",
]

HELP_TEXT = (
    "📖 사용 가능한 명령어\n\n"
    "/list            — 현재 감시 종목 목록 조회\n"
    "/add [티커]      — 종목 추가   예) /add NVDA\n"
    "/remove [티커]   — 종목 제거   예) /remove NVDA"
)


# ══════════════════════════════════════════════
#  watchlist.txt 관리
# ══════════════════════════════════════════════

def normalize_ticker(raw: str) -> str:
    """티커를 정규화한다. (.KS 종목은 대소문자 보정)"""
    t = raw.strip()
    if t.upper().endswith(".KS"):
        return t[:-3].upper() + ".KS"
    return t.upper()


def load_watchlist() -> list:
    """watchlist.txt에서 종목 리스트를 읽어온다.
    파일이 없으면 기본 종목으로 자동 생성한다."""
    if not os.path.exists(WATCHLIST_FILE):
        save_watchlist(DEFAULT_TICKERS)

    tickers = []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                tickers.append(normalize_ticker(line))
    return tickers


def save_watchlist(tickers: list) -> None:
    """종목 리스트를 watchlist.txt에 저장한다."""
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        f.write("# 자산 모멘텀 감시 종목 목록\n")
        f.write("# 텔레그램 /add [티커], /remove [티커] 명령어로 수정 가능\n")
        f.write("# 한 줄에 티커 하나. #으로 시작하는 줄은 주석.\n\n")
        for ticker in tickers:
            f.write(ticker + "\n")


# ══════════════════════════════════════════════
#  중복 처리 방지: last_update_id 관리
# ══════════════════════════════════════════════

def get_last_update_id() -> int:
    """저장된 마지막 처리 update_id를 읽어온다. (없으면 0)"""
    try:
        if os.path.exists(LAST_ID_FILE):
            return int(open(LAST_ID_FILE).read().strip())
    except (ValueError, IOError):
        pass
    return 0


def save_last_update_id(uid: int) -> None:
    """마지막으로 처리된 update_id를 파일에 저장한다."""
    with open(LAST_ID_FILE, "w") as f:
        f.write(str(uid))


# ══════════════════════════════════════════════
#  Git 연동
# ══════════════════════════════════════════════

def git_commit_and_push(commit_msg: str, files: list) -> None:
    """지정 파일들을 git add → commit → push한다.
    GitHub Actions 환경에서 git user 설정도 함께 처리한다."""
    try:
        # CI 환경에서 필요한 git identity 설정
        subprocess.run(
            ["git", "config", "user.name", "monitor-bot"],
            cwd=BASE_DIR, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "monitor-bot@github-actions"],
            cwd=BASE_DIR, capture_output=True
        )
        # 변경 파일 스테이징
        for f in files:
            subprocess.run(["git", "add", f], cwd=BASE_DIR, check=True)

        # 커밋 (변경사항 없으면 skip)
        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=BASE_DIR, capture_output=True, text=True
        )
        if "nothing to commit" in result.stdout:
            print("[GIT] 변경사항 없음 — 커밋 생략")
            return

        # 푸시
        push = subprocess.run(
            ["git", "push"], cwd=BASE_DIR, capture_output=True, text=True
        )
        if push.returncode == 0:
            print(f"[GIT] 푸시 완료: {commit_msg}")
        else:
            print(f"[GIT] 푸시 실패: {push.stderr.strip()}")
    except Exception as e:
        print(f"[WARN] git 작업 오류: {e}")


# ══════════════════════════════════════════════
#  텔레그램 API
# ══════════════════════════════════════════════

def send_telegram_message(text: str) -> bool:
    """텔레그램 봇 API를 통해 메시지를 발송한다."""
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        print("[OK] 텔레그램 발송 완료")
        return True
    except requests.RequestException as e:
        print(f"[ERROR] 텔레그램 발송 실패: {e}")
        return False


def fetch_updates(offset: int) -> list:
    """offset+1 이후의 새 업데이트 목록을 가져온다."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset + 1, "timeout": 0, "limit": 50},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            return data.get("result", [])
    except Exception as e:
        print(f"[WARN] getUpdates 실패: {e}")
    return []


# ══════════════════════════════════════════════
#  텔레그램 명령어 핸들러
# ══════════════════════════════════════════════

def cmd_list() -> None:
    tickers = load_watchlist()
    if not tickers:
        send_telegram_message("📋 감시 종목이 없습니다. /add [티커]로 추가하세요.")
        return
    numbered = "\n".join(f"  {i+1:>2}. {t}" for i, t in enumerate(tickers))
    send_telegram_message(f"📋 현재 감시 종목 ({len(tickers)}개)\n\n{numbered}")


def cmd_add(ticker: str) -> None:
    if not ticker:
        send_telegram_message("❌ 티커를 입력해주세요.\n예) /add NVDA")
        return
    tickers = load_watchlist()
    if ticker in tickers:
        send_telegram_message(f"⚠️ {ticker} 은(는) 이미 감시 목록에 있습니다.")
        return
    tickers.append(ticker)
    save_watchlist(tickers)
    git_commit_and_push(
        f"[bot] add {ticker} to watchlist",
        [WATCHLIST_FILE, LAST_ID_FILE],
    )
    send_telegram_message(f"✅ {ticker} 추가 완료  (감시 종목 총 {len(tickers)}개)")


def cmd_remove(ticker: str) -> None:
    if not ticker:
        send_telegram_message("❌ 티커를 입력해주세요.\n예) /remove NVDA")
        return
    tickers = load_watchlist()
    if ticker not in tickers:
        send_telegram_message(f"⚠️ {ticker} 은(는) 감시 목록에 없습니다.")
        return
    tickers.remove(ticker)
    save_watchlist(tickers)
    git_commit_and_push(
        f"[bot] remove {ticker} from watchlist",
        [WATCHLIST_FILE, LAST_ID_FILE],
    )
    send_telegram_message(f"🗑️ {ticker} 제거 완료  (감시 종목 총 {len(tickers)}개)")


def dispatch(text: str) -> None:
    """명령어 텍스트를 파싱해 해당 핸들러로 라우팅한다."""
    parts = text.strip().split(maxsplit=1)
    cmd   = parts[0].lower()
    arg   = normalize_ticker(parts[1]) if len(parts) > 1 else ""

    if cmd == "/list":
        cmd_list()
    elif cmd == "/add":
        cmd_add(arg)
    elif cmd == "/remove":
        cmd_remove(arg)
    else:
        send_telegram_message(HELP_TEXT)


def process_commands() -> None:
    """
    새 텔레그램 메시지를 수신해 명령어를 처리한다.
    - 허가된 채팅(TELEGRAM_CHAT_ID)에서 온 메시지만 처리
    - 처리된 update_id를 저장해 다음 실행 시 중복 방지
    """
    last_id = get_last_update_id()
    updates = fetch_updates(last_id)
    if not updates:
        return

    max_id = max(u["update_id"] for u in updates)

    # update_id 먼저 저장: /add, /remove 커밋 시 최신 ID도 함께 포함됨
    save_last_update_id(max_id)

    for update in updates:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue
        # 허가된 채팅만 수락
        if str(msg.get("chat", {}).get("id", "")) != str(TELEGRAM_CHAT_ID):
            continue
        text = msg.get("text", "").strip()
        if text.startswith("/"):
            print(f"[CMD] {text}")
            dispatch(text)


# ══════════════════════════════════════════════
#  시장 분석
# ══════════════════════════════════════════════

def fetch_close(ticker: str, period: str = "1y") -> pd.Series:
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


def fmt_price(value: float, ticker: str) -> str:
    """국내주(.KS) → 원화 / 그 외 → 달러 포맷으로 반환한다."""
    if ticker.endswith(".KS"):
        return f"{int(round(value)):,}원"
    return f"${value:,.2f}"


def calc_rsi_wilder(series: pd.Series, period: int = RSI_PERIOD) -> float:
    """Wilder EWM 방식 RSI (USDT 등 변동 없는 자산의 분모 0 방어 포함)."""
    if len(series) < period + 1:
        raise ValueError(f"데이터 부족: {len(series)}행 (최소 {period + 1}행)")
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


def scan_ticker(ticker: str, series: pd.Series) -> list:
    """단일 종목의 RSI 과매도 + 20·120일 SMA 돌파를 스캔해 알림 리스트를 반환한다."""
    alerts    = []
    price_str = fmt_price(float(series.iloc[-1]), ticker)

    # ── RSI 과매도 (30 이하)
    try:
        rsi = calc_rsi_wilder(series)
        if rsi <= RSI_OVERSOLD:
            alerts.append(
                f"🔥 [분석 타점] {ticker}  RSI {rsi:.2f} — 30 이하 과매도 진입"
                f"  (현재가: {price_str})"
            )
    except Exception as e:
        print(f"[WARN] {ticker} RSI: {e}")

    # ── SMA 돌파 (20일, 120일)
    for sma_period in SMA_PERIODS:
        try:
            if len(series) < sma_period + 1:
                continue
            sma = series.rolling(sma_period).mean()
            if pd.isna(sma.iloc[-2]) or pd.isna(sma.iloc[-1]):
                continue
            prev_close = float(series.iloc[-2])
            curr_close = float(series.iloc[-1])
            prev_sma   = float(sma.iloc[-2])
            curr_sma   = float(sma.iloc[-1])
            tag        = "장기이평" if sma_period == 120 else f"{sma_period}일선"

            if prev_close <= prev_sma and curr_close > curr_sma:
                alerts.append(
                    f"📈 [{tag} 돌파] {ticker}  {sma_period}일선 상향 돌파"
                    f"  (현재가: {price_str})"
                )
            elif prev_close >= prev_sma and curr_close < curr_sma:
                alerts.append(
                    f"📉 [{tag} 이탈] {ticker}  {sma_period}일선 하향 이탈"
                    f"  (현재가: {price_str})"
                )
        except Exception as e:
            print(f"[WARN] {ticker} SMA-{sma_period}: {e}")

    return alerts


# ══════════════════════════════════════════════
#  브리핑 빌더
# ══════════════════════════════════════════════

def build_briefing(now_kst: datetime, tickers: list) -> str:
    title   = TITLE_MAP.get(now_kst.hour, TITLE_DEFAULT)
    now_str = now_kst.strftime("%Y-%m-%d %H:%M")
    alerts  = []

    for ticker in tickers:
        try:
            series = fetch_close(ticker, period="1y")
            alerts.extend(scan_ticker(ticker, series))
        except Exception as e:
            print(f"[WARN] {ticker} 조회 실패: {e}")

    body = "\n".join(alerts) if alerts else NO_ALERT_MSG
    return f"{title}\n기준 시각: {now_str} KST\n\n{body}"


# ══════════════════════════════════════════════
#  메인 실행
# ══════════════════════════════════════════════

if __name__ == "__main__":
    now_kst = datetime.now(KST)
    print(f"현재 KST: {now_kst.strftime('%Y-%m-%d %H:%M')}")

    # ① 텔레그램 명령어 처리 (브리핑보다 먼저)
    process_commands()

    # ② watchlist.txt 로드
    tickers = load_watchlist()
    print(f"감시 종목: {len(tickers)}개")

    # ③ 정기 브리핑 생성 & 발송
    message = build_briefing(now_kst, tickers)
    print("\n" + "=" * 50)
    print(message)
    print("=" * 50 + "\n")
    send_telegram_message(message)

    print("완료.")
