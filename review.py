"""방송 다음 날 리뷰 초안.

1) 코드가 계산표를 만든다(예측 대비 차이, 비교군, 요인별 기여, 공헌이익, 반품률).
2) 초안 작성기가 계산표를 보고 3~4문장 초안을 쓴다. 문장마다 근거 칸 id를 단다.
   예선 데모는 API 키 없이 돌리므로 초안 작성기는 '녹화 응답'(아래 draft_from_table)이다.
   본선에서는 같은 입력·같은 출력 형식으로 OpenAI 모델을 부른다(agent_openai.py).
3) checker.py가 초안을 검사하고, 통과한 초안만 MD 승인 대기열에 올라간다.
"""
import math

import numpy as np

from data import FACTORS, GIFT_COST

FLAG_GAP = 0.12          # 예측 대비 12% 이상 벗어난 방송만 MD에게 올린다
MIN_COMPARE = 3          # 같은 조건·다른 조건 비교군이 각각 3건 미만이면 판단 보류
CARD_SHARE = 0.5         # 카드 할인 비용 중 홈쇼핑 부담 비율(가정)
NOINTEREST_FEE = 0.003   # 무이자 1개월당 수수료율(가정)
RETURN_COST = 5000       # 반품 1건 회수비(가정)

FACTOR_LABEL = {"카드할인": "카드 즉시할인", "사은품": "사은품", "무이자": "무이자 할부", "날씨": "비 온 날"}


def features(b):
    """요인별 더미 변수. 기준: 카드할인 0%, 사은품 없음, 무이자 없음, 맑음."""
    return {
        "카드할인": [b.card_disc == 5, b.card_disc == 7, b.card_disc == 10],
        "사은품": [b.gift == "저가", b.gift == "고가"],
        "무이자": [b.nointerest >= 10],
        "날씨": [b.rain],
    }


def flat(b):
    f = features(b)
    return [float(v) for k in FACTORS for v in f[k]]


def factor_slices():
    out, i = {}, 0
    for k, n in (("카드할인", 3), ("사은품", 2), ("무이자", 1), ("날씨", 1)):
        out[k] = slice(i, i + n)
        i += n
    return out


def fit_effects(rows, ridge=1.0):
    """log(실제/예측)을 조건 더미로 회귀. 반환: (절편, 계수, 평균 특성)."""
    X = np.array([flat(b) for b in rows])
    y = np.array([math.log(b.orders / b.forecast_orders) for b in rows])
    mu = X.mean(axis=0)
    Xc = X - mu
    coef = np.linalg.solve(Xc.T @ Xc + ridge * np.eye(X.shape[1]), Xc.T @ (y - y.mean()))
    return y.mean(), coef, mu


def contribution_margin(b):
    revenue = b.orders * b.price
    cost = (
        b.orders * GIFT_COST[b.gift]
        + revenue * b.card_disc / 100 * CARD_SHARE
        + revenue * NOINTEREST_FEE * b.nointerest
        + b.returns_7d * (b.price * b.margin + RETURN_COST)
    )
    return revenue * b.margin - cost


def build_table(b, history):
    """b 이전 같은 상품군 방송(history)으로 계산표를 만든다. 칸 id → (이름, 값, 표시 문자열)."""
    comps = [h for h in history if h.category == b.category and h.day < b.day]
    gap = b.orders / b.forecast_orders - 1
    cells = {
        "A1": ("편성 AI 예측 주문(건)", b.forecast_orders, f"{b.forecast_orders:,.0f}"),
        "A2": ("실제 주문(건)", b.orders, f"{b.orders:,}"),
        "A3": ("예측 대비 차이(%)", gap * 100, f"{gap * 100:+.0f}"),
        "B1": ("비교군 방송 수(같은 상품군, 이전)", len(comps), f"{len(comps)}"),
        "E1": ("이 방송 카드 즉시할인(%)", b.card_disc, f"{b.card_disc}"),
        "E2": ("이 방송 무이자(개월)", b.nointerest, f"{b.nointerest}"),
    }
    table = {"id": b.id, "gap": gap, "cells": cells, "factor": None, "same": 0, "diff": 0, "contrib": {}}
    cm = contribution_margin(b)
    cells["D1"] = ("공헌이익(만원)", cm / 10000, f"{cm / 10000:,.0f}")
    cells["D2"] = ("7일 반품률(%)", b.returns_7d / b.orders * 100, f"{b.returns_7d / b.orders * 100:.1f}")
    if comps:
        cr = sum(c.returns_7d for c in comps) / sum(c.orders for c in comps) * 100
        cells["D3"] = ("비교군 7일 반품률(%)", cr, f"{cr:.1f}")
    if len(comps) < 8:
        return table
    const, coef, mu = fit_effects(comps)
    x = np.array(flat(b))
    sl = factor_slices()
    for k, s in sl.items():
        c = float(coef[s] @ (x[s] - mu[s]))
        table["contrib"][k] = c
        cid = {"카드할인": "C1", "사은품": "C2", "무이자": "C3", "날씨": "C4"}[k]
        cells[cid] = (f"{FACTOR_LABEL[k]} 기여(%p)", (math.exp(c) - 1) * 100, f"{(math.exp(c) - 1) * 100:+.0f}")
    top = max(table["contrib"], key=lambda k: abs(table["contrib"][k]))
    loggap = math.log(1 + gap)
    explains = table["contrib"][top] * loggap > 0 and abs(table["contrib"][top]) >= 0.4 * abs(loggap)
    fb = features(b)[top]
    same = [c for c in comps if features(c)[top] == fb]
    table["same"], table["diff"] = len(same), len(comps) - len(same)
    cells["B2"] = ("같은 조건 비교 방송 수", len(same), f"{len(same)}")
    cells["B3"] = ("다른 조건 비교 방송 수", len(comps) - len(same), f"{len(comps) - len(same)}")
    table["factor"] = top if explains else "우연"
    return table


def verdict_of(table):
    if table["factor"] is None:
        return "보류"
    if table["factor"] == "우연":
        return "우연"
    if min(table["same"], table["diff"]) < MIN_COMPARE:
        return "보류"
    return "날씨" if table["factor"] == "날씨" else "조건"


def draft_from_table(b, table):
    """녹화 응답: 모델이 Structured Outputs로 돌려줄 초안과 같은 형식."""
    c = table["cells"]
    v = verdict_of(table)
    s = [{"text": f"편성 AI 예측 {c['A1'][2]}건, 실제 {c['A2'][2]}건으로 예측 대비 {c['A3'][2]}%다.",
          "cites": ["A1", "A2", "A3"]}]
    f = table["factor"]
    if v == "조건":
        cid = {"카드할인": "C1", "사은품": "C2", "무이자": "C3"}[f]
        cond = {"카드할인": f"카드 즉시할인 {b.card_disc}%", "사은품": f"사은품 {b.gift}",
                "무이자": f"무이자 {b.nointerest}개월"}[f]
        extra = {"카드할인": ["E1"], "무이자": ["E2"]}.get(f, [])  # 조건 이름 속 숫자가 먼저 나온다
        s.append({"text": f"비교군 {c['B1'][2]}건 대비 {cond} 조건이 차이 중 {c[cid][2]}%p를 설명한다.",
                  "cites": ["B1"] + extra + [cid]})
    elif v == "날씨":
        s.append({"text": f"비교군 {c['B1'][2]}건 대비 비 온 날 효과가 {c['C4'][2]}%p로 가장 크다. 프로모션 조건 탓으로 보지 않는다.",
                  "cites": ["B1", "C4"]})
    elif v == "우연":
        s.append({"text": f"비교군 {c['B1'][2]}건 대비 어느 조건으로도 차이의 대부분이 설명되지 않는다.",
                  "cites": ["B1"]})
    else:
        s.append({"text": f"비교할 방송이 모자라 판단을 보류한다(비교군 {c['B1'][2]}건).", "cites": ["B1"]})
    if "D3" in c:
        s.append({"text": f"공헌이익 {c['D1'][2]}만원, 7일 반품률 {c['D2'][2]}%(비교군 {c['D3'][2]}%).",
                  "cites": ["D1", "D2", "D3"]})
    if v == "조건" and f == "사은품" and b.gift == "고가" and c["D2"][1] > c["D3"][1] + 2:
        s.append({"text": "다음 방송 제안: 주문은 늘었지만 반품률이 높아 저가 사은품으로 바꿔 한 번 시험한다.", "cites": []})
    elif v == "조건" and table["contrib"][f] > 0:
        s.append({"text": "다음 방송 제안: 같은 조건을 유지하고 결과를 한 번 더 확인한다.", "cites": []})
    elif v == "조건":
        s.append({"text": "다음 방송 제안: 이 조건을 빼고 한 번 시험한다.", "cites": []})
    elif v in ("우연", "보류"):
        s.append({"text": "다음 방송 제안: 같은 조건으로 한 번 더 보고 판단한다.", "cites": []})
    return {"broadcast_id": b.id, "verdict": v, "factor": f, "sentences": s}
