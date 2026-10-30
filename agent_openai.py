"""본선용 OpenAI Agents SDK 연결(openai-agents 0.24.0 기준).

계산은 전부 함수 도구(코드)가 하고, 모델은 계산표를 읽고 초안 문장만 쓴다.
초안은 Structured Outputs(Draft)로 받고, checker.check를 통과해야 MD 화면에 올라간다.
학습표 저장 도구는 needs_approval=True라 MD가 승인해야 실행된다.

예선 데모는 API 키 없이 run_demo.py(녹화 응답)로 돌린다. 이 파일은 키가 있을 때만 모델을 부른다.
    python agent_openai.py --build-only   # 키 없이 에이전트 구성만 확인
"""
import csv
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from agents import Agent, Runner, function_tool

from checker import check
from data import make_broadcasts
from review import build_table

DATA = {b.id: b for b in make_broadcasts()}
LEARNING = Path(__file__).parent / "out" / "learning_table_agent.csv"


class Sentence(BaseModel):
    text: str
    cites: list[str]


class Draft(BaseModel):
    broadcast_id: str
    verdict: str  # 조건 | 날씨 | 우연 | 보류
    factor: str | None
    sentences: list[Sentence]


@function_tool
def get_review_table(broadcast_id: str) -> str:
    """방송 하나의 계산표(칸 id, 이름, 표시값)와 코드가 고른 주요 요인을 돌려준다."""
    b = DATA[broadcast_id]
    t = build_table(b, [h for h in DATA.values() if h.day < b.day])
    return json.dumps({"cells": {k: [v[0], v[2]] for k, v in t["cells"].items()},
                       "factor": t["factor"], "same": t["same"], "diff": t["diff"]}, ensure_ascii=False)


@function_tool(needs_approval=True)
def save_to_learning_table(broadcast_id: str, verdict: str) -> str:
    """MD가 승인한 리뷰의 조건-결과 한 줄을 시뮬레이터 학습표에 저장한다."""
    b = DATA[broadcast_id]
    with open(LEARNING, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([b.id, b.product, b.card_disc, b.gift, b.nointerest, int(b.rain),
                                b.forecast_orders, b.orders, b.returns_7d, verdict])
    return "saved"


INSTRUCTIONS = """너는 홈쇼핑 MD의 방송 다음 날 프로모션 리뷰 초안을 쓴다.
get_review_table로 계산표를 받아, 3~5문장으로 쓴다.
- 숫자는 계산표 표시값을 그대로 옮기고, 그 문장의 cites에 칸 id를 넣는다.
- 관찰 데이터다. '때문', '덕분', '입증'을 쓰지 말고 '비교군 대비'로 쓴다.
- factor가 없거나 same·diff 중 하나가 3 미만이면 verdict는 '보류'.
- 마지막 문장은 다음 방송 제안(숫자 없음, cites 비움).
- save_to_learning_table은 MD 승인 뒤에만 실행된다(승인 대기)."""

review_agent = Agent(
    name="promo-review",
    instructions=INSTRUCTIONS,
    tools=[get_review_table, save_to_learning_table],
    output_type=Draft,
)


def run(broadcast_id):
    res = Runner.run_sync(review_agent, f"방송 {broadcast_id} 리뷰 초안")
    draft = res.final_output.model_dump()
    b = DATA[broadcast_id]
    table = build_table(b, [h for h in DATA.values() if h.day < b.day])
    return draft, check(draft, table)


if __name__ == "__main__":
    if "--build-only" in sys.argv:
        print(review_agent.name, [t.name for t in review_agent.tools], save_to_learning_table.needs_approval)
    else:
        print(run(sys.argv[1] if len(sys.argv) > 1 else "B152"))
