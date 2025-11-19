# core.py
###############################################
# 도로주행 자동 배정 로직 (순수 로직 모듈)
###############################################
import os
import json
import re
import random
from datetime import date

# 데이터 디렉토리 및 랜덤 히스토리 파일
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(DATA_DIR, "random_history.json")

###########################################################
# JSON Load / Save
###########################################################
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

###########################################################
# 수동 가능자 설정
###########################################################
MANUAL_SET = {
    "권한솔", "김남균", "김성연",
    "김주현", "이호석", "조정래",
}

###########################################################
# 텍스트 파싱
###########################################################
def extract_staff(text: str):
    """
    오전/오후 텍스트에서 도로주행 근무자(1종수동 + 2종자동)만 추출
    - 1종수동: '1종수동: 9호 김주현'
    - 2종자동: '• 6호 김지은'
    """
    staff = []

    # 1종수동: "1종수동: 9호 김주현"
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    for name in m:
        staff.append(name.strip())

    # 2종자동: "• 6호 김지은"
    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    for name in m2:
        staff.append(name.strip())

    # 중복 제거(순서 유지)
    return list(dict.fromkeys(staff))


def extract_extra(text: str):
    """
    - 교양: '1교시: 안유미'
    - 코스점검: '코스점검 : • A코스 합격: 이호석 ...'
    """
    # 교양 담당자
    edu = {}
    m = re.findall(r"(\d)교시\s*:\s*([가-힣]+)", text)
    for gyo, name in m:
        edu[int(gyo)] = name.strip()

    # 코스점검 담당자들
    course = []
    m2 = re.findall(r"코스점검\s*:\s*(.*)", text)
    if m2:
        body = m2[0]
        mm = re.findall(r"[A-Z]코스.*?:\s*([가-힣]+)", body)
        course = [x.strip() for x in mm]

    return edu, course

###########################################################
# Staff Class
###########################################################
class Staff:
    def __init__(self, name: str):
        self.name = name
        self.is_manual = (name in MANUAL_SET)        # 수동 가능자 여부
        self.is_course = False                       # 코스 점검자 여부
        self.is_edu = {i: False for i in range(1, 6)}  # 교양 담당 (교시별)

        self.load = 0.0
        self.need_low_next = False   # 2교시 코스 연장용 (엑셀 로직)
        self.assigned = {"prev_zero": False}

###########################################################
# 랜덤결과(우선 배정 대상) 리스트
#  - 포맷: ["김성연", "조정래", ...]
###########################################################
def load_history():
    """
    random_history.json 포맷 마이그레이션 포함:
    - 예전: [{"date":..., "name":..., "period":..., "type":...}, ...]
    - 현재: ["김성연", "조정래", ...]
    """
    data = load_json(HISTORY_FILE, [])

    if not isinstance(data, list):
        return []

    if not data:
        return []

    # 예전 포맷(dict 리스트) → name만 추출
    if isinstance(data[0], dict):
        names = []
        for item in data:
            if isinstance(item, dict) and "name" in item:
                nm = item["name"]
                if isinstance(nm, str) and nm not in names:
                    names.append(nm)
        save_history(names)
        return names

    # 새 포맷: 문자열 리스트
    cleaned = []
    for v in data:
        if isinstance(v, str) and v not in cleaned:
            cleaned.append(v)
    return cleaned

def save_history(d):
    """d: 이름 문자열 리스트"""
    save_json(HISTORY_FILE, d)

def reset_history():
    save_history([])

###########################################################
# 가중치 (코스/교양, 중복 시 최대 1)
###########################################################
def apply_weights(staff_list, period: int, is_morning: bool):
    for s in staff_list:
        weight = 0.0

        # 코스 1교시
        if is_morning and period == 1 and s.is_course:
            weight += 1

        # 코스 연장 2교시
        if is_morning and period == 2 and s.need_low_next:
            weight += 1

        # 교양: k교시 담당자 → (k-1)교시에 가중치
        # 1,3교시는 제외(엑셀 로직)
        for k in [2, 4, 5]:
            if period == k - 1 and s.is_edu[k]:
                weight += 1

        # 코스+교양 중복 시 최대 1
        if weight > 1:
            weight = 1

        s.load += weight

###########################################################
# 자격 체크
###########################################################
def is_eligible(st: Staff, type_code: str) -> bool:
    # 수동 가능자는 전 종별 가능 (1M,2M,1A,2A)
    if st.is_manual:
        return True
    # 자동 전용은 1A,2A만 가능
    return type_code in ("1A", "2A")

###########################################################
# 한 교시 배정 (랜덤/우선배정 포함)
###########################################################
def assign_one_period(staff_list, period: int, demand: dict, is_morning: bool):
    """
    - 이전 교시에서 '적게 배정된 사람 리스트'(history)를 우선 배정
    - 이번 교시 끝나면, 진짜 적게 배정된 사람(low_group)을 history에 기록
      (코스/교양 가중치 받은 사람은 low_group에서 제외)
    - history에 현재 근무자 전원이 한 번씩 들어가면 자동 초기화
    """
    # 이전 교시에서 적게 배정된 사람 이름 리스트(우선 배정 대상)
    hist = load_history()
    hist_set = set(hist)

    # 전교시 미배정 보정(엑셀 로직 유지)
    for s in staff_list:
        if s.assigned["prev_zero"]:
            s.load += 1
        s.assigned["prev_zero"] = False

    # 코스/교양 가중치 적용
    apply_weights(staff_list, period, is_morning)

    # baseCap: 1·5교시 2명, 나머지 3명
    base_cap = 2 if period in (1, 5) else 3
    n = len(staff_list)

    assigned = {s.name: {"1M": 0, "1A": 0, "2A": 0, "2M": 0} for s in staff_list}
    total = [0] * n

    order = [
        ("1M", demand.get("1M", 0)),
        ("1A", demand.get("1A", 0)),
        ("2A", demand.get("2A", 0)),
        ("2M", demand.get("2M", 0)),
    ]

    # 종별별 배정
    for type_code, need in order:
        for _ in range(need):
            candidates = []
            min_load = None

            # 1차: 최소 load 찾기
            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if (min_load is None) or (s.load < min_load):
                        min_load = s.load

            if min_load is None:
                continue

            # 2차: 동점자 후보
            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if abs(s.load - min_load) < 1e-9:
                        candidates.append(i)

            if not candidates:
                continue

            # 우선 배정: candidates 중 hist에 있는 사람 먼저 사용
            priority_cands = [i for i in candidates if staff_list[i].name in hist_set]
            pool = priority_cands if priority_cands else candidates

            # 동점자 랜덤
            if len(pool) == 1:
                pick = pool[0]
            else:
                pick = random.choice(pool)

            assigned[staff_list[pick].name][type_code] += 1
            total[pick] += 1

    # 혼합배정 효과 + 공평성 보정 (엑셀 로직 그대로)
    def mix(i):
        info = assigned[staff_list[i].name]
        c = sum(1 for v in info.values() if v > 0)
        return 1 if c >= 2 else 0

    def fairness(i):
        return total[i] + mix(i)

    for _ in range(40):
        scores = [fairness(i) for i in range(n)]
        if max(scores) - min(scores) <= 1:
            break
        idx_max = scores.index(max(scores))
        idx_min = scores.index(min(scores))

        moved = False
        for t in ("1M", "1A", "2A", "2M"):
            if (
                assigned[staff_list[idx_max].name][t] > 0
                and is_eligible(staff_list[idx_min], t)
                and total[idx_min] < base_cap
            ):
                assigned[staff_list[idx_max].name][t] -= 1
                assigned[staff_list[idx_min].name][t] += 1
                total[idx_max] -= 1
                total[idx_min] += 1
                moved = True
                break

        if not moved:
            break

    # Load/prev_zero/코스연장 갱신
    for i, s in enumerate(staff_list):
        s.load += total[i]
        s.assigned["prev_zero"] = (total[i] == 0)

    if is_morning and period == 1 and n > 0:
        min_assign = min(total)
        for i, s in enumerate(staff_list):
            s.need_low_next = (s.is_course and total[i] > min_assign)
    else:
        for s in staff_list:
            s.need_low_next = False

    # 🔻 이번 교시에서 "진짜 적게 배정된 사람" 계산 (코스/교양 가중치 받은 사람 제외)
    low_group = []
    if n > 0:
        min_val = min(total)
        for i, s in enumerate(staff_list):
            if total[i] != min_val:
                continue

            # 코스 가중치자 (1교시)
            if is_morning and period == 1 and s.is_course:
                continue

            # 코스 연장 가중치자 (2교시)
            if is_morning and period == 2 and s.need_low_next:
                continue

            # 교양 가중치자 (2→1, 4→3, 5→4)
            is_edu_weighted = False
            for k in [2, 4, 5]:
                if period == k - 1 and s.is_edu[k]:
                    is_edu_weighted = True
                    break
            if is_edu_weighted:
                continue

            # 여기까지 통과한 사람만 진짜 "적게 받은" 사람
            low_group.append(s.name)

    # history 업데이트 (중복 없이 추가)
    for name in low_group:
        if name not in hist:
            hist.append(name)

    # 모든 근무자가 한 번씩 기록되면 자동 초기화
    current_staff_names = [s.name for s in staff_list]
    if set(current_staff_names).issubset(set(hist)) and len(hist) >= len(current_staff_names):
        hist = []

    save_history(hist)

    return assigned, low_group

###########################################################
# 1명/0명 배정자 짝짓기 (UI 표시용)
###########################################################
def make_pairs(staff_list, assigned_dict):
    """
    staff_list : [Staff, ...]
    assigned_dict: assign_one_period 리턴값 (이름→{1M,1A,2A,2M})
    출력: ["김병욱-김성연", "김주현-이호석(참관)", ...]
    """
    # 감독관별 총 배정 수
    total_assign = {
        s.name: sum(assigned_dict[s.name].values())
        for s in staff_list
    }

    list_one = [name for name, val in total_assign.items() if val == 1]
    list_zero = [name for name, val in total_assign.items() if val == 0]

    pairs = []

    # 1) 배정 1끼리 짝짓기
    while len(list_one) >= 2:
        a = list_one.pop(0)
        b = list_one.pop(0)
        pairs.append(f"{a}-{b}")

    # 2) 배정 1이 하나 남아 있으면 0과 짝짓기
    if len(list_one) == 1 and len(list_zero) >= 1:
        a = list_one.pop(0)
        b = list_zero.pop(0)
        pairs.append(f"{a}-{b}(참관)")

    # 3) 남은 0들은 단독 참관인데, 요구사항엔 별도 출력 언급 없어서 생략
    return pairs

# UI 쪽에서 쓸 때 예쁜 한글 라벨 맵
LABEL_MAP = {
    "1M": "1종수동",
    "1A": "1종자동",
    "2A": "2종자동",
    "2M": "2종수동",
}
