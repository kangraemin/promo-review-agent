# promo-review — 방송 다음 날 프로모션 리뷰 초안 + 승인 학습표 + 조건별 시뮬레이터

LOTTE AX Challenge 2026 과제3(롯데홈쇼핑 프로모션 시뮬레이터) 예선용 시제품.
API 키 없이 돈다. 모델 자리는 '녹화 응답'(review.draft_from_table)이 대신하고, 본선에서는 같은 입출력으로 agent_openai.py가 모델을 부른다.

## 돌리기
```
python3 run_demo.py        # out/ 에 결과
python3 -m pytest -q tests # 6개
python agent_openai.py --build-only   # openai-agents 0.24.0 설치 시, 키 없이 구성만 확인
```

## 파일
- data.py — 조건 효과를 심어 둔 합성 방송 300건(정답 원인 포함). 방송 기록에 안 남는 돌발(품절 조기 종료, 약 4~5%)도 섞음
- review.py — 계산표(예측 대비 차이·비교군·요인별 기여·공헌이익·반품률)와 초안
- checker.py — 숫자·근거 칸·인과 단정·비교군 수 검사(agent_openai.py에선 output guardrail)
- simulate.py — 학습표로 조건 효과 재추정, 조합별 예상 매출·공헌이익 순위, 반품률 상한 안 최적안
  (MD가 승인한 리뷰에 '돌발' 메모를 달면 그 방송은 학습·비교군에서 빠진다)
- agent_openai.py — Agents SDK 연결(함수 도구, Structured Outputs, output guardrail, needs_approval, trace)
- run_demo.py — 날짜순으로 흘리며 매일 아침 리뷰를 만들고 지표를 잰다

## 가정(본선에서 실제 값으로 교체)
- 카드 할인 비용 홈쇼핑 부담 50%, 무이자 1개월 수수료 0.3%, 반품 회수비 5,000원
- 편성 AI 예측은 상품·시간대 기준선과 '평소 조건' 평균만 반영한다고 둠
