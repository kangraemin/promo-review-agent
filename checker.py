"""초안 검사기. 하나라도 걸리면 초안을 돌려보낸다(MD에게 올리지 않는다).

- 문장 속 숫자는 그 문장이 가리킨 칸의 표시값과 순서대로 하나씩 같아야 한다.
- 가리킨 칸 id는 계산표에 있어야 한다.
- 관찰 데이터라 인과 단정('때문', '덕분', '효과가 입증')을 쓰지 않는다.
- '조건' 판정은 같은 조건·다른 조건 비교군이 각각 3건 이상일 때만.
"""
import re

from review import MIN_COMPARE

NUM = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?")
BANNED = ("때문", "덕분", "입증", "확실히")


def check(draft, table):
    errors = []
    cells = table["cells"]
    for i, s in enumerate(draft["sentences"]):
        for cid in s["cites"]:
            if cid not in cells:
                errors.append(f"문장{i + 1}: 없는 칸 {cid}")
        expected = [cells[c][2].replace(",", "") for c in s["cites"] if c in cells]
        found = [m.replace(",", "") for m in NUM.findall(s["text"].replace("7일", ""))]  # 지표 이름 속 숫자 제외
        if found != expected:
            errors.append(f"문장{i + 1}: 숫자 {found}가 가리킨 칸 {s['cites']}의 값 {expected}와 다름")
        for w in BANNED:
            if w in s["text"]:
                errors.append(f"문장{i + 1}: 인과 단정 표현 '{w}'")
    if draft["verdict"] == "조건" and min(table["same"], table["diff"]) < MIN_COMPARE:
        errors.append("비교군 3건 미만인데 '조건' 판정")
    return errors
