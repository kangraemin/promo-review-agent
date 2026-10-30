"""승인된 리뷰 학습표로 돌리는 조건별 시뮬레이터.

학습표의 '조건-결과' 줄로 상품군별 조건 효과를 다시 추정하고,
상품·시간대 하나에 대해 카드 할인 x 사은품 x 무이자 조합을 모두 돌려
예상 주문과 예상 공헌이익 순위를 낸다.
"""
import itertools
import math

import numpy as np

from data import CARD_EFF, GIFT_COST, gift_eff, nointerest_eff
from review import CARD_SHARE, NOINTEREST_FEE, RETURN_COST, fit_effects, factor_slices


def estimated_effects(rows):
    """상품군 rows에서 추정한 조건별 주문 배율."""
    _, coef, _ = fit_effects(rows, ridge=0.5)
    sl = factor_slices()
    c = coef
    return {
        "카드5": math.exp(c[sl["카드할인"]][0]), "카드7": math.exp(c[sl["카드할인"]][1]),
        "카드10": math.exp(c[sl["카드할인"]][2]),
        "사은품저가": math.exp(c[sl["사은품"]][0]), "사은품고가": math.exp(c[sl["사은품"]][1]),
        "무이자10": math.exp(c[sl["무이자"]][0]),
    }


def true_effects(category):
    return {
        "카드5": CARD_EFF[5], "카드7": CARD_EFF[7], "카드10": CARD_EFF[10],
        "사은품저가": gift_eff(category, "저가"), "사은품고가": gift_eff(category, "고가"),
        "무이자10": nointerest_eff(category, 10),
    }


def rank_combos(rows, base_orders, price, margin, category, return_rate):
    eff = estimated_effects(rows)
    # 고가 사은품의 반품률 차이도 학습표에서 그대로 잰다
    rr = {}
    for g in GIFT_COST:
        sub = [r for r in rows if r.gift == g]
        rr[g] = sum(r.returns_7d for r in sub) / max(1, sum(r.orders for r in sub)) if sub else return_rate
    months = [0, 10] if price >= 200000 else [0]
    out = []
    for card, gift, ni in itertools.product(CARD_EFF, GIFT_COST, months):
        m = 1.0
        m *= eff.get(f"카드{card}", 1.0)
        m *= eff.get(f"사은품{gift}", 1.0)
        m *= eff["무이자10"] if ni >= 10 else 1.0
        orders = base_orders * m
        revenue = orders * price
        returns = orders * rr[gift]
        cm = (revenue * margin - orders * GIFT_COST[gift] - revenue * card / 100 * CARD_SHARE
              - revenue * NOINTEREST_FEE * ni - returns * (price * margin + RETURN_COST))
        out.append({"카드할인": card, "사은품": gift, "무이자": ni, "예상주문": round(orders),
                    "예상매출_만원": round(revenue / 10000), "예상공헌이익_만원": round(cm / 10000)})
    return out


def effect_error(rows, category):
    est, tru = estimated_effects(rows), true_effects(category)
    return {k: round((est[k] - tru[k]) * 100, 1) for k in tru}, est, tru


def mean_abs(d):
    return float(np.mean([abs(v) for v in d.values()]))
