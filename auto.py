###############################################
# 도로주행 자동 배정 (가중치=1, 근무자 수정 가능)
###############################################
import streamlit as st
import json, os, re, random
import pandas as pd
from datetime import date
from collections import defaultdict

st.set_page_config(page_title="도로주행 자동 배정", layout="wide")

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
# 수동 가능자 (변경되면 여기 수정)
###########################################################
MANUAL_SET = {
    "권한솔","김남균","김성연",
    "김주현","이호석","조정래"
}

###########################################################
# 텍스트 파싱
###########################################################
def extract_staff(text):
    staff = []

    # 1종수동
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    for name in m:
        staff.append(name.strip())

    # 1종자동 = 감독관 미기재 → 패스

    # 2종자동 (• 숫자호 이름)
    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    for name in m2:
        staff.append(name.strip())

    return list(dict.fromkeys(staff))


def extract_extra(text):
    # 교양
    edu = {}
    m = re.findall(r"(\d)교시\s*:\s*([가-힣]+)", text)
    for gyo, name in m:
        edu[int(gyo)] = name.strip()

    # 코스점검
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
    def __init__(self, name):
        self.name = name
        self.is_manual = (name in MANUAL_SET)
        self.is_course = False
        self.is_edu = {i:False for i in range(1,6)}

        self.load = 0
        self.need_low_next = False
        self.assigned = {"prev_zero": False}

###########################################################
# 랜덤 히스토리
###########################################################
def load_history():
    return load_json(HISTORY_FILE, [])

def save_history(d):
    save_json(HISTORY_FILE, d)

def is_recent_random(hist, name):
    today = date.today()
    for h in hist:
        if (today - date.fromisoformat(h["date"])).days <= 3:
            if h["name"] == name:
                return True
    return False

def add_random(hist, name, period, typecode):
    hist.append({
        "date": date.today().isoformat(),
        "name": name,
        "period": period,
        "type": typecode
    })
    save_history(hist)

###########################################################
# 가중치 (중복시 최대 1)
###########################################################
def apply_weights(staff_list, period, is_morning):
    for s in staff_list:
        weight = 0

        # 코스 (1교시)
        if is_morning and period == 1 and s.is_course:
            weight += 1

        # 코스 연장 (2교시)
        if is_morning and period == 2 and s.need_low_next:
            weight += 1

        # 교양: k교시 담당자 → (k-1)교시 적용
        for k in [2,4,5]:       # 1,3교시는 제외
            if period == k-1 and s.is_edu[k]:
                weight += 1

        # 중복 제한: 1
        if weight > 1:
            weight = 1

        s.load += weight

###########################################################
# 자격 체크
###########################################################
def is_eligible(st, type_code):
    if st.is_manual:
        return True
    return type_code in ("1A","2A")

###########################################################
# 랜덤 선택
###########################################################
def pick_random_idx(staff_list, idx_list, period, type_code, hist):
    filtered = [i for i in idx_list if not is_recent_random(hist, staff_list[i].name)]
    if filtered:
        pick = random.choice(filtered)
        add_random(hist, staff_list[pick].name, period, type_code)
        return pick

    pick = random.choice(idx_list)
    add_random(hist, staff_list[pick].name, period, type_code)
    return pick

###########################################################
# 한 교시 배정
###########################################################
def assign_one_period(staff_list, period, demand, is_morning):
    for s in staff_list:
        if s.assigned["prev_zero"]:
            s.load += 1
        s.assigned["prev_zero"] = False

    apply_weights(staff_list, period, is_morning)

    base_cap = 2 if period in (1,5) else 3
    n = len(staff_list)

    assigned = {s.name: {"1M":0,"1A":0,"2A":0,"2M":0} for s in staff_list}
    total = [0]*n

    order = [("1M", demand.get("1M",0)),
             ("1A", demand.get("1A",0)),
             ("2A", demand.get("2A",0)),
             ("2M", demand.get("2M",0))]

    hist = load_history()

    for type_code, need in order:
        for _ in range(need):
            candidates = []
            min_load = None

            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if min_load is None or s.load < min_load:
                        min_load = s.load

            if min_load is None:
                continue

            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if abs(s.load - min_load) < 1e-9:
                        candidates.append(i)

            if not candidates:
                continue

            if len(candidates) == 1:
                pick = candidates[0]
            else:
                pick = pick_random_idx(staff_list, candidates, period, type_code, hist)

            assigned[staff_list[pick].name][type_code] += 1
            total[pick] += 1

    # 공평성 재조정
    def mix(i):
        info = assigned[staff_list[i].name]
        c = sum(1 for v in info.values() if v>0)
        return 1 if c>=2 else 0

    def fairness(i):
        return total[i] + mix(i)

    for _ in range(40):
        scores = [fairness(i) for i in range(n)]
        if max(scores) - min(scores) <= 1:
            break
        idx_max = scores.index(max(scores))
        idx_min = scores.index(min(scores))

        moved = False
        for t in ("1M","1A","2A","2M"):
            if assigned[staff_list[idx_max].name][t] > 0 and is_eligible(staff_list[idx_min], t) and total[idx_min] < base_cap:
                assigned[staff_list[idx_max].name][t] -= 1
                assigned[staff_list[idx_min].name][t] += 1
                total[idx_max] -= 1
                total[idx_min] += 1
                moved = True
                break
        if not moved:
            break

    # load/prev_zero/코스연장 판단
    for i,s in enumerate(staff_list):
        s.load += total[i]
        s.assigned["prev_zero"] = (total[i]==0)

    if is_morning and period == 1 and n > 0:
        min_assign = min(total)
        for i,s in enumerate(staff_list):
            s.need_low_next = (s.is_course and total[i] > min_assign)
    else:
        for s in staff_list:
            s.need_low_next = False

    save_history(hist)
    return assigned

###########################################################
# Streamlit UI
###########################################################
st.title("🚗 도로주행 자동 배정 (근무자 수정 가능 버전)")

tab_m, tab_a, tab_r = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 랜덤결과"])

############################################################
# 오전 탭
############################################################
with tab_m:
    st.subheader("📥 오전 텍스트 입력")
    text = st.text_area("오전 텍스트 입력", height=200, key="txt_m")

    period = st.selectbox("교시 선택", [1,2], index=0, key="pm")

    if st.button("1) 근무자 자동 추출", key="m_extract"):
        if not text.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text)
            edu_map, course_list = extract_extra(text)

            st.success("근무자 자동 추출 완료!")
            st.write("자동 추출:", staff_names)

            st.session_state["m_staff_raw"] = staff_names
            st.session_state["m_edu"] = edu_map
            st.session_state["m_course"] = course_list

    if "m_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정 (추가/삭제/변경 가능)")
        df = pd.DataFrame({"근무자": st.session_state["m_staff_raw"]})
        edited = st.experimental_data_editor(df, num_rows="dynamic", key="m_edit")
        final_staff_names = edited["근무자"].dropna().tolist()

        st.session_state["m_staff_final"] = final_staff_names

        st.write("최종 근무자:", final_staff_names)

        st.subheader("📊 수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"m1{period}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"m2{period}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"m3{period}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"m4{period}"),
        }

        if st.button("2) 오전 배정 실행", key="m_run"):
            staff_list = [Staff(n) for n in final_staff_names]

            # 교양/코스 반영
            for gyo,nm in st.session_state["m_edu"].items():
                for s in staff_list:
                    if s.name == nm:
                        s.is_edu[gyo] = True

            for nm in st.session_state["m_course"]:
                for s in staff_list:
                    if s.name == nm:
                        s.is_course = True

            result = assign_one_period(staff_list, period, demand, is_morning=True)

            st.subheader("📌 배정 결과")
            rows = []
            for s in staff_list:
                info = result[s.name]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        desc.append(f"{t} {info[t]}명")
                rows.append((s.name, " / ".join(desc) if desc else "0"))
            st.table({"감독관":[x[0] for x in rows], "배정":[x[1] for x in rows]})

############################################################
# 오후 탭
############################################################
with tab_a:
    st.subheader("📥 오후 텍스트 입력")
    text = st.text_area("오후 텍스트 입력", height=200, key="txt_a")

    period = st.selectbox("교시 선택", [3,4,5], index=0, key="pa")

    if st.button("1) 근무자 자동 추출", key="a_extract"):
        if not text.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text)
            edu_map, course_list = extract_extra(text)

            st.success("근무자 자동 추출 완료!")
            st.write("자동 추출:", staff_names)

            st.session_state["a_staff_raw"] = staff_names
            st.session_state["a_edu"] = edu_map
            st.session_state["a_course"] = course_list

    if "a_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정 (추가/삭제/변경 가능)")
        df = pd.DataFrame({"근무자": st.session_state["a_staff_raw"]})
        edited = st.experimental_data_editor(df, num_rows="dynamic", key="a_edit")
        final_staff_names = edited["근무자"].dropna().tolist()

        st.session_state["a_staff_final"] = final_staff_names

        st.write("최종 근무자:", final_staff_names)

        st.subheader("📊 수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"a1{period}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"a2{period}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"a3{period}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"a4{period}"),
        }

        if st.button("2) 오후 배정 실행", key="a_run"):
            staff_list = [Staff(n) for n in final_staff_names]

            for gyo,nm in st.session_state["a_edu"].items():
                for s in staff_list:
                    if s.name == nm:
                        s.is_edu[gyo] = True

            for nm in st.session_state["a_course"]:
                for s in staff_list:
                    if s.name == nm:
                        s.is_course = True

            result = assign_one_period(staff_list, period, demand, is_morning=False)

            st.subheader("📌 배정 결과")
            rows = []
            for s in staff_list:
                info = result[s.name]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        desc.append(f"{t} {info[t]}명")
                rows.append((s.name, " / ".join(desc) if desc else "0"))
            st.table({"감독관":[x[0] for x in rows], "배정":[x[1] for x in rows]})

############################################################
# 랜덤 히스토리 탭
############################################################
with tab_r:
    st.subheader("🎲 최근 3일 랜덤 배정 히스토리")
    hist = load_history()
    if not hist:
        st.info("랜덤 기록 없음")
    else:
        st.table({
            "날짜": [h["date"] for h in hist],
            "이름": [h["name"] for h in hist],
            "교시": [h["period"] for h in hist],
            "종별": [h["type"] for h in hist],
        })

    if st.button("랜덤 기록 초기화", key="reset_hist"):
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
        st.success("초기화 완료!")
