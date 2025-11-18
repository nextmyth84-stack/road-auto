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
# 수동 가능자 설정
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

    # 1종수동: "1종수동: 9호 김주현"
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    for name in m:
        staff.append(name.strip())

    # 2종자동: "• 6호 김지은"
    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    for name in m2:
        staff.append(name.strip())

    return list(dict.fromkeys(staff))


def extract_extra(text):
    # 교양: "1교시: 안유미"
    edu = {}
    m = re.findall(r"(\d)교시\s*:\s*([가-힣]+)", text)
    for gyo, name in m:
        edu[int(gyo)] = name.strip()

    # 코스점검: "코스점검 : • A코스 합격: 이호석 ..."
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

        # 코스 1교시
        if is_morning and period == 1 and s.is_course:
            weight += 1

        # 코스 연장 2교시
        if is_morning and period == 2 and s.need_low_next:
            weight += 1

        # 교양: k교시 담당자 → (k-1)교시에 가중치
        # 1,3교시는 제외(엑셀 로직)
        for k in [2, 4, 5]:
            if period == k-1 and s.is_edu[k]:
                weight += 1

        # 코스+교양 중복 시 최대 1
        if weight > 1:
            weight = 1

        s.load += weight

###########################################################
# 자격 체크
###########################################################
def is_eligible(st, type_code):
    # 수동 가능자는 전 종별 가능
    if st.is_manual:
        return True
    # 자동 전용은 1A,2A만
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
    # 전교시 미배정 보정(이번 교시 시작 시점 가중치)
    for s in staff_list:
        if s.assigned["prev_zero"]:
            s.load += 1
        s.assigned["prev_zero"] = False

    # 코스/교양 가중치 적용
    apply_weights(staff_list, period, is_morning)

    # baseCap: 1·5교시 2명, 나머지 3명
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

            # 1차: 최소 load 찾기
            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if min_load is None or s.load < min_load:
                        min_load = s.load

            if min_load is None:
                continue

            # 2차: 동점자 목록
            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if abs(s.load - min_load) < 1e-9:
                        candidates.append(i)

            if not candidates:
                continue

            # 동점자 랜덤 (3일 히스토리 반영)
            if len(candidates) == 1:
                pick = candidates[0]
            else:
                pick = pick_random_idx(staff_list, candidates, period, type_code, hist)

            assigned[staff_list[pick].name][type_code] += 1
            total[pick] += 1

    # 혼합배정 효과 + 공평성 보정
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

    # Load/prev_zero/코스연장 갱신
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
st.title("🚗 도로주행 자동 배정 (근무자 수정 + 가중치 표시)")

tab_m, tab_a, tab_r = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 랜덤결과"])

############################################################
# 오전 탭
############################################################
with tab_m:
    st.subheader("📥 오전 텍스트 입력")
    text_m = st.text_area("오전 텍스트 입력", height=200, key="txt_m")

    period_m = st.selectbox("교시 선택", [1,2], index=0, key="pm")

    if st.button("1) 근무자 자동 추출", key="m_extract"):
        if not text_m.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text_m)
            edu_map, course_list = extract_extra(text_m)

            st.success("근무자 자동 추출 완료!")
            st.write("자동 추출:", staff_names)

            st.session_state["m_staff_raw"] = staff_names
            st.session_state["m_edu"] = edu_map
            st.session_state["m_course"] = course_list

    if "m_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정 (추가/삭제/변경 가능)")
        df_m = pd.DataFrame({"근무자": st.session_state["m_staff_raw"]})
        edited_m = st.data_editor(df_m, num_rows="dynamic", key="m_edit")
        final_staff_names_m = edited_m["근무자"].dropna().tolist()

        st.session_state["m_staff_final"] = final_staff_names_m
        st.write("최종 근무자:", final_staff_names_m)

        st.subheader("📊 수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand_m = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"m1{period_m}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"m2{period_m}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"m3{period_m}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"m4{period_m}"),
        }

        if st.button("2) 오전 배정 실행", key="m_run"):
            # Staff 객체 생성
            staff_list_m = [Staff(n) for n in final_staff_names_m]

            # 교양/코스 반영
            for gyo,nm in st.session_state["m_edu"].items():
                for s in staff_list_m:
                    if s.name == nm:
                        s.is_edu[gyo] = True

            for nm in st.session_state["m_course"]:
                for s in staff_list_m:
                    if s.name == nm:
                        s.is_course = True

            result_m = assign_one_period(staff_list_m, period_m, demand_m, is_morning=True)

            st.subheader("📌 배정 결과")
            rows = []
            for s in staff_list_m:
                info = result_m[s.name]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        desc.append(f"{t} {info[t]}명")
                rows.append((s.name, " / ".join(desc) if desc else "0"))
            st.table({"감독관":[x[0] for x in rows], "배정":[x[1] for x in rows]})

            # 🔢 최종 가중치(Load) 표시
            st.markdown("#### 🔢 최종 가중치(Load)")
            load_rows = {
                "감독관": [s.name for s in staff_list_m],
                "Load": [float(s.load) for s in staff_list_m],
            }
            st.table(load_rows)

            # 🧽 가중치 초기화 버튼 (랜덤결과는 건드리지 않음)
            if st.button("🧽 가중치 초기화 (오전)", key="m_weight_reset"):
                # 이 앱 구조에선 매 배정마다 Staff를 새로 만들기 때문에
                # 실질적으로는 '다음 배정은 항상 초기 가중치로 계산' 상태임.
                # 추후 멀티교시 연동시 여기에서 session_state에 누적된 load를 초기화하는 용도로 확장 가능.
                st.success("오전 가중치를 초기화했습니다. (다음 배정은 새 가중치로 계산됩니다.)")

############################################################
# 오후 탭
############################################################
with tab_a:
    st.subheader("📥 오후 텍스트 입력")
    text_a = st.text_area("오후 텍스트 입력", height=200, key="txt_a")

    period_a = st.selectbox("교시 선택", [3,4,5], index=0, key="pa")

    if st.button("1) 근무자 자동 추출", key="a_extract"):
        if not text_a.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text_a)
            edu_map, course_list = extract_extra(text_a)

            st.success("근무자 자동 추출 완료!")
            st.write("자동 추출:", staff_names)

            st.session_state["a_staff_raw"] = staff_names
            st.session_state["a_edu"] = edu_map
            st.session_state["a_course"] = course_list

    if "a_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정 (추가/삭제/변경 가능)")
        df_a = pd.DataFrame({"근무자": st.session_state["a_staff_raw"]})
        edited_a = st.data_editor(df_a, num_rows="dynamic", key="a_edit")
        final_staff_names_a = edited_a["근무자"].dropna().tolist()

        st.session_state["a_staff_final"] = final_staff_names_a
        st.write("최종 근무자:", final_staff_names_a)

        st.subheader("📊 수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand_a = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"a1{period_a}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"a2{period_a}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"a3{period_a}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"a4{period_a}"),
        }

        if st.button("2) 오후 배정 실행", key="a_run"):
            staff_list_a = [Staff(n) for n in final_staff_names_a]

            for gyo,nm in st.session_state["a_edu"].items():
                for s in staff_list_a:
                    if s.name == nm:
                        s.is_edu[gyo] = True

            for nm in st.session_state["a_course"]:
                for s in staff_list_a:
                    if s.name == nm:
                        s.is_course = True

            result_a = assign_one_period(staff_list_a, period_a, demand_a, is_morning=False)

            st.subheader("📌 배정 결과")
            rows = []
            for s in staff_list_a:
                info = result_a[s.name]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        desc.append(f"{t} {info[t]}명")
                rows.append((s.name, " / ".join(desc) if desc else "0"))
            st.table({"감독관":[x[0] for x in rows], "배정":[x[1] for x in rows]})

            # 🔢 최종 가중치(Load) 표시
            st.markdown("#### 🔢 최종 가중치(Load)")
            load_rows_a = {
                "감독관": [s.name for s in staff_list_a],
                "Load": [float(s.load) for s in staff_list_a],
            }
            st.table(load_rows_a)

            # 🧽 가중치 초기화 버튼 (랜덤결과는 건드리지 않음)
            if st.button("🧽 가중치 초기화 (오후)", key="a_weight_reset"):
                st.success("오후 가중치를 초기화했습니다. (다음 배정은 새 가중치로 계산됩니다.)")

############################################################
# 랜덤 히스토리 탭 (조회만, 초기화 버튼 제거)
############################################################
with tab_r:
    st.subheader("🎲 최근 랜덤 배정 히스토리 (3일 이내 기준)")
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
