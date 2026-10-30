"""예선 데모: 합성 방송 300건을 날짜순으로 흘리며 매일 아침 리뷰를 만든다.

출력(out/):
- metrics.json      적중률·처리 시간·검사기 차단율·시뮬레이터 효과 오차
- drafts.json       모든 초안과 검사 결과
- learning_table.csv 승인된 '조건-결과' 줄
- morning_D{n}.html 하루치 아침 리뷰 화면
- rejected_example.json 숫자를 틀리게 바꾼 초안이 돌려보내진 예
"""
import csv
import copy
import html
import json
import random
import time
from collections import Counter
from pathlib import Path

from checker import NUM, check
from data import FACTORS, make_broadcasts
from review import FLAG_GAP, build_table, draft_from_table
from simulate import effect_error, mean_abs, rank_combos

OUT = Path(__file__).parent / "out"


def corrupt(draft, rng):
    """모델이 숫자를 잘못 옮긴 상황을 흉내 낸다."""
    d = copy.deepcopy(draft)
    s = d["sentences"][rng.randrange(len(d["sentences"]) - 1)]  # 마지막 제안 문장은 숫자가 없다
    nums = list(NUM.finditer(s["text"]))
    m = nums[rng.randrange(len(nums))]
    v = float(m.group().replace(",", "")) + rng.choice([1, 2, 7, 10, 30])
    s["text"] = s["text"][:m.start()] + (f"{v:,.0f}" if "." not in m.group() else f"{v:.1f}") + s["text"][m.end():]
    return d


def naive_cause(b, history):
    """비교 기준선: 같은 상품 직전 방송과 비교해 바뀐 조건 중 첫 번째를 원인으로 본다."""
    prev = [h for h in history if h.product == b.product and h.day < b.day]
    if not prev:
        return "우연"
    p = prev[-1]
    for f, changed in (("카드할인", p.card_disc != b.card_disc), ("사은품", p.gift != b.gift),
                       ("무이자", p.nointerest != b.nointerest), ("날씨", p.rain != b.rain)):
        if changed:
            return f
    return "우연"


def render_morning(day, items):
    rows = []
    for b, d, errs in items:
        sents = "".join(
            f"<li>{html.escape(s['text'])} <span class=c>{' '.join(s['cites'])}</span></li>" for s in d["sentences"])
        rows.append(
            f"<section><h2>{b.id} · {b.product} · {b.slot} · 카드 {b.card_disc}% / 사은품 {b.gift}"
            f"{' / 무이자 ' + str(b.nointerest) + '개월' if b.nointerest else ''}{' / 비' if b.rain else ''}</h2>"
            f"<p class=v>판정: {d['verdict']}</p><ul>{sents}</ul>"
            f"<p class=btn><button>승인</button> <button>고쳐서 승인</button> <button>보류</button></p></section>")
    return ("<!doctype html><meta charset=utf-8><title>아침 리뷰</title><style>"
            "body{font:15px/1.6 -apple-system,'Apple SD Gothic Neo',sans-serif;max-width:760px;margin:24px auto;padding:0 16px;color:#222}"
            "section{border:1px solid #ddd;border-radius:6px;padding:10px 14px;margin:12px 0}h2{font-size:15px;margin:0 0 4px}"
            ".v{margin:0;color:#a33;font-weight:600}.c{color:#888;font-size:12px}.btn button{font-size:13px}</style>"
            f"<h1>D{day} 아침 8시 프로모션 리뷰 — 예측 대비 ±{int(FLAG_GAP * 100)}% 넘은 방송 {len(items)}건</h1>"
            + "".join(rows))


def seed_sweep(seeds=(1, 2, 3, 4, 5, 7)):
    """시드를 바꿔 다시 만든 데이터에서도 적중률이 버티는지 본다."""
    rows = []
    for seed in seeds:
        data = make_broadcasts(seed=seed)
        ok = nv = n = 0
        for b in data:
            h = [x for x in data if x.day < b.day]
            t = build_table(b, h)
            if abs(t["gap"]) < FLAG_GAP:
                continue
            d = draft_from_table(b, t)
            if d["verdict"] == "보류":
                continue
            n += 1
            ok += d["factor"] == b.true_cause
            nv += naive_cause(b, h) == b.true_cause
        rows.append({"seed": seed, "판정": n, "적중률_%": round(ok / n * 100, 1), "기준선_%": round(nv / n * 100, 1)})
    return {"시드별": rows,
            "평균_적중률_%": round(sum(r["적중률_%"] for r in rows) / len(rows), 1),
            "평균_기준선_%": round(sum(r["기준선_%"] for r in rows) / len(rows), 1)}


def main():
    OUT.mkdir(exist_ok=True)
    rng = random.Random(11)
    data = make_broadcasts()
    drafts, approved, timings = [], [], []
    hit = Counter()
    naive_hit = Counter()
    by_day = {}
    caught = total_bad = 0
    rejected_example = None
    for b in data:
        history = [h for h in data if h.day < b.day]
        t0 = time.perf_counter()
        table = build_table(b, history)
        draft = draft_from_table(b, table)
        errs = check(draft, table)
        timings.append(time.perf_counter() - t0)
        flagged = abs(table["gap"]) >= FLAG_GAP
        drafts.append({"id": b.id, "flagged": flagged, "true_cause": b.true_cause, "draft": draft, "errors": errs})
        bad = corrupt(draft, rng)
        bad_errs = check(bad, table)
        total_bad += 1
        caught += bool(bad_errs)
        if rejected_example is None and flagged and draft["verdict"] == "조건":
            rejected_example = {"original": draft, "corrupted": bad, "checker_errors": bad_errs}
        if flagged:
            by_day.setdefault(b.day, []).append((b, draft, errs))
            if draft["verdict"] != "보류":
                pred = draft["factor"]
                hit["판정"] += 1
                hit["맞음"] += pred == b.true_cause
                nv = naive_cause(b, history)
                naive_hit["맞음"] += nv == b.true_cause
            else:
                hit["보류"] += 1
        # 시연용: MD가 검사 통과 초안을 그대로 승인한다고 가정
        if not errs and draft["verdict"] != "보류":
            approved.append(b)

    with open(OUT / "learning_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["방송", "상품", "상품군", "시간대", "카드할인", "사은품", "무이자", "비", "예측주문", "실제주문", "7일반품"])
        for b in approved:
            w.writerow([b.id, b.product, b.category, b.slot, b.card_disc, b.gift, b.nointerest, int(b.rain),
                        b.forecast_orders, b.orders, b.returns_7d])

    sim = {}
    for cat in ("패션", "뷰티", "식품", "가전", "리빙"):
        rows = [b for b in approved if b.category == cat]
        err, est, tru = effect_error(rows, cat)
        sim[cat] = {"학습표_줄수": len(rows), "효과_평균오차_%p": round(mean_abs(err), 1),
                    "추정": {k: round(v, 3) for k, v in est.items()}, "심어둔값": tru}
    ex = next(b for b in data if b.category == "뷰티")
    beauty = [b for b in approved if b.category == "뷰티"]
    rr = sum(b.returns_7d for b in beauty) / sum(b.orders for b in beauty)
    combos = rank_combos(beauty, base_orders=1800, price=ex.price, margin=ex.margin, category="뷰티", return_rate=rr)
    by_rev = sorted(combos, key=lambda c: -c["예상매출_만원"])[:3]
    by_cm = sorted(combos, key=lambda c: -c["예상공헌이익_만원"])[:3]

    flagged_n = sum(1 for d in drafts if d["flagged"])
    best_day = max(by_day, key=lambda d: len(by_day[d]))
    (OUT / f"morning_D{best_day}.html").write_text(render_morning(best_day, by_day[best_day]), encoding="utf-8")
    metrics = {
        "방송_수": len(data),
        "MD에게_올린_방송(예측대비_±12%_이상)": flagged_n,
        "MD가_안_봐도_되는_방송_비율_%": round((1 - flagged_n / len(data)) * 100, 1),
        "올린_방송_중_판정": hit["판정"], "올린_방송_중_판단보류": hit["보류"],
        "원인_적중률_%": round(hit["맞음"] / hit["판정"] * 100, 1),
        "기준선(직전_방송과_비교)_적중률_%": round(naive_hit["맞음"] / hit["판정"] * 100, 1),
        "초안_1건_계산+작성+검사_평균_초": round(sum(timings) / len(timings), 4),
        "원본_초안_검사_통과_%": round(sum(1 for d in drafts if not d["errors"]) / len(drafts) * 100, 1),
        "숫자_틀린_초안_차단": f"{caught}/{total_bad}",
        "학습표_줄수": len(approved),
        "시드_6개_반복": seed_sweep(),
        "시뮬레이터_효과추정": sim,
        "뷰티_예상매출_상위3": by_rev,
        "뷰티_예상공헌이익_상위3": by_cm,
        "정답_분포": dict(Counter(b.true_cause for b in data)),
        "판정_분포(올린_방송)": dict(Counter(d["draft"]["verdict"] for d in drafts if d["flagged"])),
    }
    (OUT / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "drafts.json").write_text(json.dumps(drafts, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "rejected_example.json").write_text(json.dumps(rejected_example, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in metrics.items() if k != "시뮬레이터_효과추정"}, ensure_ascii=False, indent=1))
    print({c: (v["학습표_줄수"], v["효과_평균오차_%p"]) for c, v in sim.items()})


if __name__ == "__main__":
    main()
