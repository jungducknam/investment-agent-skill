"""
flow_analyzer.py — 흐름 분석 엔진

축적된 스냅샷 데이터를 분석하여 "연속적 흐름"을 도출한다.
단순 현재값이 아닌, 시간에 따른 변화 방향·가속·감속을 판단.

핵심 분석:
1. 추세 방향 및 가속도 (지수/종목)
2. 섹터 로테이션 흐름 (자금 이동 방향)
3. 뉴스 테마 빈도 변화 (이슈 부상/소멸)
4. 시장 레짐 전환 감지
5. 종목별 기술적 상태 변화
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from .config import KST
from .snapshot_collector import get_recent_snapshots, get_recent_news, get_recent_signals

logger = logging.getLogger(__name__)


def analyze_index_flow(hours: int = 24) -> dict:
    """
    지수 흐름 분석
    - 방향: 상승/하락/횡보
    - 가속도: 가속/감속/정체
    - 변동성: 확대/축소
    """
    snapshots = get_recent_snapshots(hours)
    if len(snapshots) < 3:
        return {"status": "insufficient_data", "message": f"스냅샷 {len(snapshots)}개 — 최소 3개 필요"}
    
    results = {}
    index_names = ["KOSPI", "KOSDAQ", "SP500", "NASDAQ", "VIX", "USD_KRW"]
    
    for name in index_names:
        prices = []
        timestamps = []
        for s in snapshots:
            idx_data = json.loads(s.get("indices_json", "{}"))
            price = idx_data.get(name, {}).get("price")
            if price:
                prices.append(price)
                timestamps.append(s["timestamp"])
        
        if len(prices) < 3:
            results[name] = {"status": "no_data"}
            continue
        
        # 전체 변화율
        total_change = (prices[-1] - prices[0]) / prices[0] * 100
        
        # 전반부 vs 후반부 (가속도 판단)
        mid = len(prices) // 2
        first_half_chg = (prices[mid] - prices[0]) / prices[0] * 100 if prices[0] > 0 else 0
        second_half_chg = (prices[-1] - prices[mid]) / prices[mid] * 100 if prices[mid] > 0 else 0
        
        # 방향 판단
        if total_change > 0.5:
            direction = "상승"
        elif total_change < -0.5:
            direction = "하락"
        else:
            direction = "횡보"
        
        # 가속도 판단
        if abs(second_half_chg) > abs(first_half_chg) * 1.5:
            if (second_half_chg > 0 and first_half_chg > 0) or (second_half_chg < 0 and first_half_chg < 0):
                acceleration = "가속"
            else:
                acceleration = "반전"
        elif abs(second_half_chg) < abs(first_half_chg) * 0.5:
            acceleration = "감속"
        else:
            acceleration = "유지"
        
        # 변동성 (표준편차)
        if len(prices) >= 5:
            returns = [(prices[i] - prices[i-1]) / prices[i-1] * 100 for i in range(1, len(prices))]
            volatility = np.std(returns)
            vol_level = "높음" if volatility > 1.0 else ("보통" if volatility > 0.3 else "낮음")
        else:
            volatility = 0
            vol_level = "측정불가"
        
        results[name] = {
            "first_price": prices[0],
            "last_price": prices[-1],
            "total_change_pct": round(total_change, 2),
            "direction": direction,
            "acceleration": acceleration,
            "first_half_chg": round(first_half_chg, 2),
            "second_half_chg": round(second_half_chg, 2),
            "volatility": round(volatility, 3),
            "vol_level": vol_level,
            "data_points": len(prices),
        }
    
    return results


def analyze_sector_rotation(hours: int = 24) -> dict:
    """
    섹터 로테이션 분석
    - 자금 유입 섹터 vs 유출 섹터
    - 섹터 순위 변동
    """
    snapshots = get_recent_snapshots(hours)
    if len(snapshots) < 2:
        return {"status": "insufficient_data"}
    
    first_sectors = json.loads(snapshots[0].get("sector_momentum_json", "{}"))
    last_sectors = json.loads(snapshots[-1].get("sector_momentum_json", "{}"))
    
    if not first_sectors or not last_sectors:
        return {"status": "no_sector_data"}
    
    # 섹터별 모멘텀 변화
    rotation = []
    for sector in last_sectors:
        curr_5d = last_sectors[sector].get("ret_5d", 0)
        prev_5d = first_sectors.get(sector, {}).get("ret_5d", 0)
        change = curr_5d - prev_5d
        rotation.append({
            "sector": sector,
            "current_momentum": curr_5d,
            "prev_momentum": prev_5d,
            "momentum_change": round(change, 2),
            "direction": "강화" if change > 0.5 else ("약화" if change < -0.5 else "유지"),
        })
    
    # 모멘텀 변화 순으로 정렬
    rotation.sort(key=lambda x: x["momentum_change"], reverse=True)
    
    # 유입/유출 분류
    inflow = [s for s in rotation if s["momentum_change"] > 0.5]
    outflow = [s for s in rotation if s["momentum_change"] < -0.5]
    
    return {
        "inflow_sectors": inflow[:5],
        "outflow_sectors": outflow[:5],
        "all_rotation": rotation,
        "rotation_strength": "강함" if len(inflow) + len(outflow) > 4 else "약함",
    }


def analyze_news_flow(hours: int = 24) -> dict:
    """
    뉴스 흐름 분석
    - 테마별 빈도 변화 (이슈 부상/소멸)
    - 시간대별 뉴스 밀도
    """
    news = get_recent_news(hours)
    if not news:
        return {"status": "no_news"}
    
    # 테마별 카운트 및 시간 분포
    theme_data = {}
    for n in news:
        theme = n.get("theme", "general")
        ts = n.get("timestamp", "")[:13]  # 시간 단위
        if theme not in theme_data:
            theme_data[theme] = {"count": 0, "hours": set(), "latest": ""}
        theme_data[theme]["count"] += 1
        theme_data[theme]["hours"].add(ts)
        if not theme_data[theme]["latest"] or ts > theme_data[theme]["latest"]:
            theme_data[theme]["latest"] = ts
    
    # 부상 테마 (최근 시간에 집중된 뉴스)
    now_str = datetime.now(KST).isoformat()[:13]
    emerging = []
    for theme, data in theme_data.items():
        if data["count"] >= 3 and data["latest"] >= (datetime.now(KST) - timedelta(hours=3)).isoformat()[:13]:
            emerging.append({"theme": theme, "count": data["count"], "spread_hours": len(data["hours"])})
    
    emerging.sort(key=lambda x: x["count"], reverse=True)
    
    return {
        "total_news": len(news),
        "themes": {k: v["count"] for k, v in sorted(theme_data.items(), key=lambda x: x[1]["count"], reverse=True)},
        "emerging_themes": emerging[:5],
        "news_density": len(news) / max(hours, 1),
    }


def analyze_regime_transitions(hours: int = 48) -> dict:
    """
    시장 레짐 전환 감지
    - 레짐이 바뀌는 시점 포착
    - 전환 방향 및 속도
    """
    snapshots = get_recent_snapshots(hours)
    if len(snapshots) < 3:
        return {"status": "insufficient_data"}
    
    kr_transitions = []
    us_transitions = []
    
    prev_kr, prev_us = None, None
    for s in snapshots:
        kr = s.get("regime_kr")
        us = s.get("regime_us")
        ts = s["timestamp"][:16]
        
        if prev_kr and kr != prev_kr:
            kr_transitions.append({"time": ts, "from": prev_kr, "to": kr})
        if prev_us and us != prev_us:
            us_transitions.append({"time": ts, "from": prev_us, "to": us})
        
        prev_kr, prev_us = kr, us
    
    # 현재 레짐 지속 시간
    current_kr = snapshots[-1].get("regime_kr", "unknown")
    current_us = snapshots[-1].get("regime_us", "unknown")
    
    kr_duration = 0
    for s in reversed(snapshots):
        if s.get("regime_kr") == current_kr:
            kr_duration += 1
        else:
            break
    
    us_duration = 0
    for s in reversed(snapshots):
        if s.get("regime_us") == current_us:
            us_duration += 1
        else:
            break
    
    return {
        "kr_current": current_kr,
        "us_current": current_us,
        "kr_duration_snapshots": kr_duration,
        "us_duration_snapshots": us_duration,
        "kr_transitions": kr_transitions,
        "us_transitions": us_transitions,
        "kr_stable": len(kr_transitions) == 0,
        "us_stable": len(us_transitions) == 0,
    }


def generate_flow_summary(hours: int = 24) -> str:
    """
    전체 흐름 분석 요약 (리포트 AI에게 전달용)
    """
    index_flow = analyze_index_flow(hours)
    sector_rot = analyze_sector_rotation(hours)
    news_flow = analyze_news_flow(hours)
    regime = analyze_regime_transitions(hours * 2)  # 레짐은 더 넓은 범위
    signals = get_recent_signals(hours)
    
    lines = []
    lines.append(f"━━━ 시장 흐름 종합 분석 (최근 {hours}시간) ━━━\n")
    
    # 1. 레짐 상태
    lines.append("【시장 레짐】")
    if isinstance(regime, dict) and regime.get("status") != "insufficient_data":
        kr_stable = "안정" if regime.get("kr_stable") else f"전환 {len(regime.get('kr_transitions', []))}회"
        us_stable = "안정" if regime.get("us_stable") else f"전환 {len(regime.get('us_transitions', []))}회"
        lines.append(f"  한국: {regime.get('kr_current', '?')} ({kr_stable}, {regime.get('kr_duration_snapshots', 0)}스냅샷 지속)")
        lines.append(f"  미국: {regime.get('us_current', '?')} ({us_stable}, {regime.get('us_duration_snapshots', 0)}스냅샷 지속)")
        
        # 레짐 전환 이력
        for t in regime.get("kr_transitions", [])[-3:]:
            lines.append(f"  ⚡ [{t['time']}] 한국 {t['from']} → {t['to']}")
        for t in regime.get("us_transitions", [])[-3:]:
            lines.append(f"  ⚡ [{t['time']}] 미국 {t['from']} → {t['to']}")
    else:
        lines.append("  데이터 축적 중...")
    
    # 2. 지수 흐름
    lines.append("\n【지수 흐름】")
    if isinstance(index_flow, dict) and index_flow.get("status") != "insufficient_data":
        for name in ["KOSPI", "KOSDAQ", "SP500", "NASDAQ", "VIX"]:
            data = index_flow.get(name, {})
            if data and data.get("status") != "no_data":
                emoji = "📈" if data["direction"] == "상승" else ("📉" if data["direction"] == "하락" else "➡️")
                acc = f"[{data['acceleration']}]" if data["acceleration"] != "유지" else ""
                lines.append(f"  {emoji} {name}: {data['total_change_pct']:+.2f}% {data['direction']} {acc} (변동성: {data['vol_level']})")
    
    # 3. 섹터 로테이션
    lines.append("\n【섹터 로테이션】")
    if isinstance(sector_rot, dict) and sector_rot.get("status") != "no_sector_data":
        inflow = sector_rot.get("inflow_sectors", [])
        outflow = sector_rot.get("outflow_sectors", [])
        if inflow:
            lines.append(f"  🟢 자금 유입: {', '.join(s['sector'] for s in inflow[:3])}")
        if outflow:
            lines.append(f"  🔴 자금 유출: {', '.join(s['sector'] for s in outflow[:3])}")
        lines.append(f"  로테이션 강도: {sector_rot.get('rotation_strength', '?')}")
    
    # 4. 뉴스 흐름
    lines.append("\n【뉴스 흐름】")
    if isinstance(news_flow, dict) and news_flow.get("status") != "no_news":
        lines.append(f"  총 {news_flow.get('total_news', 0)}건 수집 (밀도: {news_flow.get('news_density', 0):.1f}건/시간)")
        emerging = news_flow.get("emerging_themes", [])
        if emerging:
            emerging_text = ", ".join(f"{t['theme']}({t['count']}건)" for t in emerging[:3])
            lines.append(f"  🔥 부상 테마: {emerging_text}")
        themes = news_flow.get("themes", {})
        if themes:
            top3 = list(themes.items())[:3]
            lines.append(f"  주요 테마: {', '.join(f'{k}({v})' for k, v in top3)}")
    
    # 5. 주요 시그널
    if signals:
        lines.append(f"\n【주요 시그널 ({len(signals)}건)】")
        for sig in signals[:5]:
            ts = sig["timestamp"][11:16]
            lines.append(f"  [{ts}] {sig['description']}")
    
    # 6. 종합 판단 힌트
    lines.append("\n【흐름 종합 판단 힌트】")
    
    # 한미 괴리 감지
    if isinstance(index_flow, dict):
        kr_chg = index_flow.get("KOSPI", {}).get("total_change_pct", 0)
        us_chg = index_flow.get("SP500", {}).get("total_change_pct", 0)
        if isinstance(kr_chg, (int, float)) and isinstance(us_chg, (int, float)):
            divergence = kr_chg - us_chg
            if abs(divergence) > 2:
                stronger = "미국" if us_chg > kr_chg else "한국"
                lines.append(f"  ⚠️ 한미 괴리 {abs(divergence):.1f}%p — {stronger} 시장이 상대적 강세")
    
    return "\n".join(lines)
