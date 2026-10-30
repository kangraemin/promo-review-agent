"""합성 방송 데이터.

조건별 효과를 미리 심어 둔 가상 방송을 만든다. 정답(어떤 조건이 차이를 만들었는지)을
알고 있어서, 리뷰 초안이 그 정답을 맞혔는지 잴 수 있다.

심어 둔 효과(분당 주문 배율):
- 카드 즉시할인 5% x1.04, 7% x1.10, 10% x1.20
- 사은품 저가 x1.03, 고가 x1.12(패션·뷰티) / x1.02(그 밖)
- 무이자 10개월 x1.15(가전만)
- 비 오는 날 x1.10 (프로모션 아닌 원인)
- 고가 사은품은 7일 반품률 +4%p
편성 AI 예측은 상품·시간대 기준선에 '평소 조건' 평균 효과만 반영하고, 그날 조건·날씨는 모른다.
"""
import math
import random
from dataclasses import dataclass, asdict

CARD_EFF = {0: 1.00, 5: 1.04, 7: 1.10, 10: 1.20}
GIFT_COST = {"없음": 0, "저가": 3000, "고가": 15000}
SLOTS = {"아침": 0.8, "오전": 1.0, "오후": 0.9, "저녁": 1.4, "심야": 0.7}
SLOT_ORDER = list(SLOTS)
CATEGORIES = {
    "패션": (89000, 0.38),
    "뷰티": (59000, 0.45),
    "식품": (39000, 0.25),
    "가전": (390000, 0.18),
    "리빙": (129000, 0.30),
}
FACTORS = ["카드할인", "사은품", "무이자", "날씨"]


def gift_eff(category, gift):
    if gift == "고가":
        return 1.12 if category in ("패션", "뷰티") else 1.02
    return 1.03 if gift == "저가" else 1.00


def nointerest_eff(category, months):
    return 1.15 if (category == "가전" and months >= 10) else 1.00


def rain_eff(rain):
    return 1.10 if rain else 1.00


@dataclass
class Broadcast:
    id: str
    day: int
    product: str
    category: str
    price: int
    margin: float
    slot: str
    minutes: int
    card_disc: int
    gift: str
    nointerest: int
    rain: bool
    forecast_orders: float
    orders: int
    returns_7d: int
    true_cause: str  # 예측과 실제의 차이를 가장 크게 만든 요인(정답)

    def to_dict(self):
        return asdict(self)


def _typical(category):
    """편성 AI가 아는 '평소 조건' 평균 효과(상품군별 과거 평균)."""
    card = sum(CARD_EFF.values()) / len(CARD_EFF)
    gift = (gift_eff(category, "없음") + gift_eff(category, "저가") + gift_eff(category, "고가")) / 3
    ni = 1.15 * 0.5 + 1.0 * 0.5 if category == "가전" else 1.0
    return card * gift * ni * (1.10 * 0.25 + 1.0 * 0.75)


def make_broadcasts(n=300, seed=7):
    rng = random.Random(seed)
    products = []
    for cat, (price, margin) in CATEGORIES.items():
        for k in range(5):
            p = int(price * rng.uniform(0.7, 1.4) / 1000) * 1000
            products.append((f"{cat}-{k + 1:02d}", cat, p, margin, rng.uniform(25, 60)))
    out = []
    for i in range(n):
        name, cat, price, margin, base = products[i % len(products)]
        day = i * 90 // n
        slot = rng.choice(SLOT_ORDER)
        card = rng.choice(list(CARD_EFF))
        gift = rng.choice(list(GIFT_COST))
        ni = rng.choice([0, 6, 10]) if price >= 200000 else 0
        rain = rng.random() < 0.25
        minutes = 60
        expected = base * SLOTS[slot] * minutes
        forecast = expected * _typical(cat) * math.exp(rng.gauss(0, 0.05))
        effs = {
            "카드할인": CARD_EFF[card],
            "사은품": gift_eff(cat, gift),
            "무이자": nointerest_eff(cat, ni),
            "날씨": rain_eff(rain),
        }
        noise = math.exp(rng.gauss(0, 0.06))
        actual = expected * math.prod(effs.values()) * noise
        orders = max(1, round(actual))
        ret_rate = 0.08 + (0.04 if gift == "고가" else 0) + rng.uniform(-0.01, 0.01)
        returns = round(orders * ret_rate)
        # 정답: 예측 대비 차이(로그)를 가장 크게 설명하는 요인
        typical_log = {
            "카드할인": math.log(sum(CARD_EFF.values()) / 4),
            "사은품": math.log((gift_eff(cat, "없음") + gift_eff(cat, "저가") + gift_eff(cat, "고가")) / 3),
            "무이자": math.log(1.075) if cat == "가전" else 0.0,
            "날씨": math.log(1.025),
        }
        contrib = {f: math.log(effs[f]) - typical_log[f] for f in FACTORS}
        contrib["우연"] = math.log(noise) - math.log(forecast / (expected * _typical(cat)))
        true_cause = max(contrib, key=lambda f: abs(contrib[f]))
        out.append(Broadcast(
            id=f"B{i + 1:03d}", day=day, product=name, category=cat, price=price, margin=margin,
            slot=slot, minutes=minutes, card_disc=card, gift=gift, nointerest=ni, rain=rain,
            forecast_orders=round(forecast, 1), orders=orders, returns_7d=returns, true_cause=true_cause,
        ))
    return out
