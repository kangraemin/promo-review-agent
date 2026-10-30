"""예선 데모: 합성 방송 300건을 날짜순으로 흘리며 매일 아침 리뷰를 만든다.

출력(out/):
- metrics.json      적중률·처리 시간·검사기 차단율·시뮬레이터 효과 오차
- drafts.json       모든 초안과 검사 결과
- learning_table.csv 방송 기록 + 승인된 리뷰 표시(판정·MD 메모·학습 포함 여부)
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
from simulate import best_combo, effect_error, mean_abs, rank_combos

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


def correct(draft, b):
    """'우연'은 '어느 프로모션 조건으로도 설명 안 됨'이라는 뜻이라 돌발이 정답일 때도 맞음으로 센다."""
    if draft["verdict"] == "우연":
        return b.true_cause in ("우연", "돌발")
    return draft["factor"] == b.true_cause


def md_approve(b, draft, flagged):
    """시연용 MD 승인. 올라온 방송만 보고, 품절 같은 돌발은 MD가 메모로 달아 학습에서 뺀다."""
    if not flagged:
        return {"검토": False, "판정": "", "메모": "", "학습": True}
    note = f"돌발: {b.event} — 학습 제외" if b.event else ""
    return {"검토": True, "판정": draft["verdict"], "메모": note, "학습": not b.event}


def sim_error(data, keep):
    errs = []
    for cat in ("패션", "뷰티", "식품", "가전", "리빙"):
        e, _, _ = effect_error([b for b in data if b.category == cat and keep(b)], cat)
        errs.append(mean_abs(e))
    return round(sum(errs) / len(errs), 2)


def sim_sweep(seeds=(1, 2, 3, 4, 5, 7), event_rate=0.5):
    """리뷰 표시 없이 방송 기록만 쓴 시뮬레이터 vs 승인된 리뷰 표시를 반영한 시뮬레이터."""
    raw = rev = 0
    for seed in seeds:
        data = make_broadcasts(seed=seed, event_rate=event_rate)
        flagged = {b.id: abs(build_table(b, [x for x in data if x.day < b.day])["gap"]) >= FLAG_GAP for b in data}
        raw += sim_error(data, lambda b: True)
        rev += sim_error(data, lambda b: not (flagged[b.id] and b.event))
    return {"방송기록만_효과오차_%p": round(raw / len(seeds), 2), "리뷰반영_효과오차_%p": round(rev / len(seeds), 2)}


def seed_sweep(seeds=(1, 2, 3, 4, 5, 7)):
    """시드를 바꿔 다시 만든 데이터에서도 적중률이 버티는지 본다."""
    rows = []
    tot = Counter()
    for seed in seeds:
        data = make_broadcasts(seed=seed)
        ok = nv = n = al = 0
        out = set()  # MD가 돌발로 표시한 방송
        for b in data:
            h = [x for x in data if x.day < b.day and x.id not in out]
            t = build_table(b, h)
            if abs(t["gap"]) < FLAG_GAP:
                continue
            if b.event:
                out.add(b.id)
            d = draft_from_table(b, t)
            v = naive_cause(b, h)
            if v in ("카드할인", "사은품", "무이자"):
                tot["기준선_조건"] += 1
                tot["기준선_조건_맞음"] += v == b.true_cause
            if b.true_cause in ("카드할인", "사은품", "무이자"):
                tot["조건이_정답"] += 1
                tot["조건이_정답_짚음"] += d["verdict"] == "조건" and d["factor"] == b.true_cause
            if d["verdict"] == "보류":
                continue
            if d["verdict"] == "조건":
                tot["조건"] += 1
                tot["조건_맞음"] += correct(d, b)
            n += 1
            ok += correct(d, b)
            nv += naive_cause(b, h) == b.true_cause
            al += b.true_cause in ("우연", "돌발")
        rows.append({"seed": seed, "판정": n, "적중률_%": round(ok / n * 100, 1), "기준선_%": round(nv / n * 100, 1),
                     "늘_우연_%": round(al / n * 100, 1)})
    return {"시드별": rows,
            "평균_적중률_%": round(sum(r["적중률_%"] for r in rows) / len(rows), 1),
            "평균_기준선_%": round(sum(r["기준선_%"] for r in rows) / len(rows), 1),
            "평균_늘_우연_%": round(sum(r["늘_우연_%"] for r in rows) / len(rows), 1),
            "조건_판정_정밀도": f'{tot["조건_맞음"]}/{tot["조건"]} ({tot["조건_맞음"] / tot["조건"] * 100:.1f}%)',
            "기준선_조건_정밀도": f'{tot["기준선_조건_맞음"]}/{tot["기준선_조건"]} ({tot["기준선_조건_맞음"] / tot["기준선_조건"] * 100:.1f}%)',
            "조건이_정답인_방송_중_짚은_비율": f'{tot["조건이_정답_짚음"]}/{tot["조건이_정답"]} ({tot["조건이_정답_짚음"] / tot["조건이_정답"] * 100:.1f}%)'}


def main():
    OUT.mkdir(exist_ok=True)
    rng = random.Random(11)
    data = make_broadcasts()
    drafts, timings, marks = [], [], {}
    hit = Counter()
    naive_hit = Counter()
    by_day = {}
    caught = total_bad = 0
    rejected_example = None
    for b in data:
        # 이미 승인된 리뷰에서 MD가 돌발로 표시한 방송은 비교군에서 뺀다
        history = [h for h in data if h.day < b.day and marks[h.id]["학습"]]
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
                hit["판정"] += 1
                hit["맞음"] += correct(draft, b)
                hit["늘우연_맞음"] += b.true_cause in ("우연", "돌발")
                if draft["verdict"] == "조건":
                    hit["조건"] += 1
                    hit["조건_맞음"] += correct(draft, b)
                nv = naive_cause(b, history)
                naive_hit["맞음"] += nv == b.true_cause
            else:
                hit["보류"] += 1
        marks[b.id] = md_approve(b, draft, flagged)

    with open(OUT / "learning_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["방송", "상품", "상품군", "시간대", "카드할인", "사은품", "무이자", "비", "예측주문", "실제주문", "7일반품",
                    "리뷰검토", "리뷰판정", "MD메모", "학습포함"])
        for b in data:
            m = marks[b.id]
            w.writerow([b.id, b.product, b.category, b.slot, b.card_disc, b.gift, b.nointerest, int(b.rain),
                        b.forecast_orders, b.orders, b.returns_7d, int(m["검토"]), m["판정"], m["메모"], int(m["학습"])])
    approved = [b for b in data if marks[b.id]["학습"]]

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
    best = best_combo(combos, max_return_pct=10.0)

    flagged_n = sum(1 for d in drafts if d["flagged"])
    best_day = max(by_day, key=lambda d: len(by_day[d]))
    (OUT / f"morning_D{best_day}.html").write_text(render_morning(best_day, by_day[best_day]), encoding="utf-8")
    metrics = {
        "방송_수": len(data),
        "MD에게_올린_방송(예측대비_±12%_이상)": flagged_n,
        "MD가_안_봐도_되는_방송_비율_%": round((1 - flagged_n / len(data)) * 100, 1),
        "올린_방송_중_판정": hit["판정"], "올린_방송_중_판단보류": hit["보류"],
        "원인_적중률_%": round(hit["맞음"] / hit["판정"] * 100, 1),
        "조건_판정_정밀도": f'{hit["조건_맞음"]}/{hit["조건"]}',
        "늘_우연이라고_답할때_%": round(hit["늘우연_맞음"] / hit["판정"] * 100, 1),
        "기준선(직전_방송과_비교)_적중률_%": round(naive_hit["맞음"] / hit["판정"] * 100, 1),
        "초안_1건_계산+작성+검사_평균_초": round(sum(timings) / len(timings), 4),
        "원본_초안_검사_통과_%": round(sum(1 for d in drafts if not d["errors"]) / len(drafts) * 100, 1),
        "숫자_틀린_초안_차단": f"{caught}/{total_bad}",
        "학습표_줄수": len(data),
        "학습_포함_줄수": len(approved),
        "MD가_돌발로_뺀_줄수": sum(1 for m in marks.values() if m["메모"]),
        "돌발_방송_수(MD가_못_본_것_포함)": sum(1 for b in data if b.event),
        "시뮬레이터_시드6_돌발_기본(약4~5%)": sim_sweep(),
        "시뮬레이터_시드6_돌발_2배": sim_sweep(event_rate=1.0),
        "시뮬레이터_시드6_돌발없음": sim_sweep(event_rate=0.0),
        "방송기록만_효과오차_%p": sim_error(data, lambda b: True),
        "리뷰반영_효과오차_%p": sim_error(data, lambda b: marks[b.id]["학습"]),
        "시드_6개_반복": seed_sweep(),
        "시뮬레이터_효과추정": sim,
        "뷰티_예상매출_상위3": by_rev,
        "뷰티_예상공헌이익_상위3": by_cm,
        "뷰티_반품률10%이하_최적안": best,
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
