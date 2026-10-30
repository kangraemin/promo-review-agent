"""API 키 없이 가짜 모델로 SDK 흐름(승인·거절·검사기 멈춤)을 확인한다. openai-agents가 없으면 건너뛴다."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("agents")
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"

from agents.items import ModelResponse  # noqa: E402
from agents.models.interface import Model  # noqa: E402
from agents.usage import Usage  # noqa: E402
from openai.types.responses import (ResponseFunctionToolCall, ResponseOutputMessage,  # noqa: E402
                                    ResponseOutputText)

import agent_openai as A  # noqa: E402
from review import draft_from_table  # noqa: E402

BID = "B065"


class Fake(Model):
    """1) 계산표 도구 2) 저장 도구(초안보다 먼저 — 가장 나쁜 순서) 3) 초안."""

    def __init__(self, draft):
        self.draft, self.n = draft, 0

    async def get_response(self, *a, **k):
        self.n += 1
        if self.n == 1:
            out = [ResponseFunctionToolCall(type="function_call", id="c1", call_id="c1", name="get_review_table",
                                            arguments=json.dumps({"broadcast_id": BID}))]
        elif self.n == 2:
            out = [ResponseFunctionToolCall(type="function_call", id="c2", call_id="c2", name="save_to_learning_table",
                                            arguments=json.dumps({"broadcast_id": BID, "verdict": self.draft["verdict"]}))]
        else:
            out = [ResponseOutputMessage(id="m", type="message", role="assistant", status="completed", content=[
                ResponseOutputText(type="output_text", text=json.dumps(self.draft, ensure_ascii=False), annotations=[])])]
        return ModelResponse(output=out, usage=Usage(), response_id=None)

    def stream_response(self, *a, **k):
        raise NotImplementedError


def _run(tmp_path, monkeypatch, bad=False, approve=True):
    monkeypatch.setattr(A, "LEARNING", tmp_path / "lt.csv")
    d = draft_from_table(A.DATA[BID], A._table(BID))
    if bad:
        d["sentences"][1]["text"] = d["sentences"][1]["text"].replace("+9%p", "39%p")
    monkeypatch.setattr(A.review_agent, "model", Fake(d))
    asked = []
    out = A.run(BID, lambda name, args: asked.append(name) or approve)
    rows = (tmp_path / "lt.csv").read_text().splitlines() if (tmp_path / "lt.csv").exists() else []
    return out, asked, rows


def test_approved_draft_is_saved(tmp_path, monkeypatch):
    (draft, errs), asked, rows = _run(tmp_path, monkeypatch)
    assert draft and not errs and asked == ["save_to_learning_table"] and len(rows) == 1


def test_rejected_save_is_not_written(tmp_path, monkeypatch):
    (draft, errs), asked, rows = _run(tmp_path, monkeypatch, approve=False)
    assert draft and asked and rows == []


def test_checker_stops_draft_and_drops_approved_save(tmp_path, monkeypatch):
    (draft, errs), asked, rows = _run(tmp_path, monkeypatch, bad=True)
    assert draft is None and errs and rows == []
