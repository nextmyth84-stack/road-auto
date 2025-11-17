import streamlit as st
import re
import json
import random
import os
from datetime import datetime, timedelta

# ============================================================
# 기본 설정
# ============================================================

MANUAL_STAFF = {
    "권한솔", "김남균", "김성연", "김주현", "이호석", "조정래"
}

HISTORY_FILE = "random_history.json"

st.set_page_config(layout="wide")
st.title("🚗 도로주행 자동배정 시스템 (단일교시 계산 + 가중치=1 버전)")

# ============================================================
# 랜덤 히스토리 관리
# ============================================================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def prune_history(history):
    today = datetime.now().date()
    new_hist = {}
    for d_str, lst in history.items():
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except:
            continue
        if (today - d).days <= 3:
            new_hist[d_str] = lst
    return new_hist

def record_random_pick(name, period, type_code):
    history = load_history()
    today_str = datetime.now().strftime("%Y-%m-%d")
    if today_str not in history:
        history[today_str] = []
    history[today_str].append({
        "name": name,
        "period": period,
        "type": type_code,
    })
    history = prune_history(history)
    save_history(history)

def is_recent_random(name):
    history = load_history()
    today = datetime.now().date()
    for d_str, lst in history.items():
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except:
            continue
        if (today - d).days > 3:
            continue
        for item in lst:
            if item.get("name") == name:
                return True
    return False

# ============================================================
# Staff 구조체
# ============================================================

class Staff:
    def __init__(self, name, is_manual=False):
        self.name = name
        self.is_manual = is_manual

        self.is_course = False
        self.is_edu = {1:False,2:False,3:False,4:False,5:False}

        self.load = 0.0
        self.skipped_prev = False
        self.need_low_next = False

    def eligible(self, type_code):
        if type_code in ["1M","2M"]:
            return self.is_manual
        return True

    def reset(self):
        self.load = 0.0
        self.skipped_prev = False
        self.need_low_next = False


# ============================================================
# 텍스트 파서
# ============================================================

NAME_RE = re.compile(r"[가-힣]{2,4}")

NAME_BLACKLIST = {"교시","코스","종수동","종자동","합격","불합격","마감","오전","오후"}

def extract_name(line):
    found = NAME_RE.findall(line)
    for nm in found:
        if nm in NAME_BLACKLIST:
            continue
        return nm
    return None

def parse_text(raw):
    lines = [l.strip() for l in raw.split("\n") if l.strip()]

    one_manual = []
    two_auto = []
    edu = {}
    course_check = []

    in_two_auto = False
    in_course = False
    in_dead = False

    for line in lines:
        if "마감 차량" in line:
            in_dead = True
            continue
        if in_dead:
            if line.startswith("•") or "호 마감" in line:
                continue
            in_dead = False

        if line.startswith("열쇠:"):
            continue

        # 교양
        if "교시:" in line:
            try:
                period = int(line.split("교시")[0])
            except:
                period = None
            nm = extract_name(line)
            if period and nm:
                edu[period] = nm
            continue

        # 1종수동
        if line.startswith("1종수동:"):
            nm = extract_name(line)
            if nm:
                one_manual.append(nm)
            continue

        # 2종자동 시작
        if line.startswith("2종자동"):
            in_two_auto = True
            continue

        # 2종자동 항목
        if in_two_auto and line.startswith("•"):
            nm = extract_name(line)
            if nm:
                two_auto.append(nm)
            continue

        if in_two_auto and not line.startswith("•"):
            in_two_auto = False

        # 코스점검
        if "코스점검" in line:
            in_course = True
            continue

        if in_course and line.startswith("•"):
            nm = extract_name(line)
            if nm:
                course_check.append(nm)
            continue

        if in_course and not line.startswith("•"):
            in_course = False

    return {
        "one_manual": list(dict.fromkeys(one_manual)),
        "two_auto": list(dict.fromkeys(two_auto)),
        "edu": edu,
        "course_check": list(dict.fromkeys(course_check)),
    }


def build_staff_list(raw):
    p = parse_text(raw)
    names = set()
    names.update(p["one_manual"])
    names.update(p["two_auto"])
    names.update(p["edu"].values())
    names.update(p["course_check"])

    staff = []
    for nm in sorted(names):
        stf = Staff(nm, is_manual=(nm in MANUAL_STAFF))
        for k,v in p["edu"].items():
            if v == nm:
                stf.is_edu[k] = True
        if nm in p["course_check"]:
            stf.is_course = True
        staff.append(stf)

    return staff, p["edu"], p["course_check"]


# ============================================================
# 가중치(전부=1) 적용 함수
# ============================================================

def apply_weights(staff_list, period, is_morning):
    # 교양 가중치 (전부 1)
    for st in staff_list:
        for k in range(2,6):  # k교시 → k-1교시에 영향
            if k == 3:
                continue
            if period == k-1 and st.is_edu.get(k,False):
                st.load += 1

    # 코스 가중치 (전부 1)
    if is_morning and period == 1:
        for st in staff_list:
            if st.is_course:
                st.load += 1

    if is_morning and period == 2:
        for st in staff_list:
            if st.need_low_next:
                st.load += 1


# ============================================================
# 동점자 랜덤 + 최근3일 제외
# ============================================================

def pick_random(staff_list, cand_idx, period, type_code):
    filtered = [i for i in cand_idx if not is_recent_random(staff_list[i].name)]
    if filtered:
        idx = random.choice(filtered)
        record_random_pick(staff_list[idx].name, period, type_code)
        return idx

    idx = random.choice(cand_idx)
    record_random_pick(staff_list[idx].name, period, type_code)
    return idx


# ============================================================
# 단일 교시 배정
# ============================================================

def assign_period(staff_list, period, demand, is_morning):
    """
    demand = {"1M":n,"1A":n,"2A":n,"2M":n}
    """

    # 전교시 미배정 처리
    for st in staff_list:
        if st.skipped_prev:
            st.load += 1
        st.skipped_prev = False

    # 가중치 적용
    apply_weights(staff_list, period, is_morning)

    # baseCap(엑셀 동일)
    base_cap = 2 if period in [1,5] else 3

    n = len(staff_list)
    arr = {st.name: {"1M":0,"1A":0,"2A":0,"2M":0} for st in staff_list}
    assigned_total = [0]*n

    order = [("1M", demand["1M"]),
             ("1A", demand["1A"]),
             ("2A", demand["2A"]),
             ("2M", demand["2M"])]

    for type_code, need in order:
        for _ in range(need):
            eligible = [
                (i,st) for i,st in enumerate(staff_list)
                if st.eligible(type_code) and assigned_total[i] < base_cap
            ]
            if not eligible:
                continue

            eligible.sort(key=lambda x: staff_list[x[0]].load)
            min_load = eligible[0][1].load

            tied = [i for (i,st) in eligible if st.load == min_load]

            if len(tied) == 1:
                pick = tied[0]
            else:
                pick = pick_random(staff_list, tied, period, type_code)

            arr[staff_list[pick].name][type_code] += 1
            assigned_total[pick] += 1

    # 혼합효과 + 공평성
    def mix_effect(i):
        t = arr[staff_list[i].name]
        count = sum(1 for x in t.values() if x > 0)
        return 1 if count >= 2 else 0

    def fairness_score(i):
        return assigned_total[i] + mix_effect(i)

    for _ in range(50):
        scores = [fairness_score(i) for i in range(n)]
        max_v = max(scores)
        min_v = min(scores)
        if max_v - min_v <= 1:
            break

        idx_max = scores.index(max_v)
        idx_min = scores.index(min_v)

        for tc in ["1M","1A","2A","2M"]:
            if arr[staff_list[idx_max].name][tc] > 0 and staff_list[idx_min].eligible(tc) and assigned_total[idx_min] < base_cap:
                arr[staff_list[idx_max].name][tc] -= 1
                arr[staff_list[idx_min].name][tc] += 1
                assigned_total[idx_max] -= 1
                assigned_total[idx_min] += 1
                break

    # Load 업데이트
    for i,st in enumerate(staff_list):
        total = assigned_total[i]
        st.load += total
        st.skipped_prev = (total == 0)

    # 코스 혜택(1→2교시)
    if is_morning and period == 1:
        min_assign = min(assigned_total)
        for i,st in enumerate(staff_list):
            st.need_low_next = (st.is_course and assigned_total[i] > min_assign)
    else:
        for st in staff_list:
            st.need_low_next = False

    return arr


# ============================================================
# Streamlit UI
# ============================================================

tabs = st.tabs(["오전 자동배정", "오후 자동배정", "랜덤결과 히스토리"])

# ----------------------- 오전 탭 -----------------------------
with tabs[0]:
    st.subheader("📥 오전 텍스트")
    text_m = st.text_area("오전 텍스트 입력", height=200)

    period_m = st.selectbox("배정할 교시 선택", [1,2])

    st.subheader("수요 입력")
    c1,c2,c3,c4 = st.columns(4)
    demand = {
        "1M": c1.number_input("1종수동", min_value=0),
        "1A": c2.number_input("1종자동", min_value=0),
        "2A": c3.number_input("2종자동", min_value=0),
        "2M": c4.number_input("2종수동", min_value=0),
    }

    if st.button("🚀 오전 배정 실행"):
        if not text_m.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff, edu_list, course_list = build_staff_list(text_m)

            st.markdown("#### 추출된 교양 담당자")
            st.json(edu_list)

            st.markdown("#### 추출된 코스 점검자")
            st.json(course_list)

            st.markdown("#### 감독관 목록")
            st.write([s.name for s in staff])

            result = assign_period(staff, period_m, demand, is_morning=True)

            st.markdown("### 📊 계산 결과")
            st.json(result)


    if st.button("🔄 계산 초기화"):
        for s in staff if 'staff' in locals() else []:
            s.reset()
        st.success("초기화 완료")


# ----------------------- 오후 탭 -----------------------------
with tabs[1]:
    st.subheader("📥 오후 텍스트")
    text_a = st.text_area("오후 텍스트 입력", height=200)

    period_a = st.selectbox("배정할 교시 선택", [3,4,5])

    st.subheader("수요 입력")
    c1,c2,c3,c4 = st.columns(4)
    demand2 = {
        "1M": c1.number_input("1종수동", min_value=0),
        "1A": c2.number_input("1종자동", min_value=0),
        "2A": c3.number_input("2종자동", min_value=0),
        "2M": c4.number_input("2종수동", min_value=0),
    }

    if st.button("🚀 오후 배정 실행"):
        if not text_a.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff, edu_list, course_list = build_staff_list(text_a)

            st.markdown("#### 추출된 교양 담당자")
            st.json(edu_list)

            st.markdown("#### 추출된 코스 점검자")
            st.json(course_list)

            st.markdown("#### 감독관 목록")
            st.write([s.name for s in staff])

            result = assign_period(staff, period_a, demand2, is_morning=False)

            st.markdown("### 📊 계산 결과")
            st.json(result)

    if st.button("🔄 오후 계산 초기화"):
        for s in staff if 'staff' in locals() else []:
            s.reset()
        st.success("초기화 완료")


# ----------------------- 랜덤 히스토리 탭 -------------------------
with tabs[2]:
    st.subheader("🎲 최근 3일 랜덤 배정 히스토리")
    history = load_history()
    st.json(history)
