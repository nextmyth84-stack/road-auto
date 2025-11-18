###############################################
# 도로주행 자동 배정 (가중치=1, 근무자 수정 + 코스/교양 수정 + 랜덤결과 우선배정)
###############################################
import streamlit as st
import json, os, re, random
import pandas as pd
from datetime import date
from collections import defaultdict

st.set_page_config(page_title="도로주행 자동 배정", layout="wide")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(DATA_DIR, "random_history.json")  # 우선배정 리스트 (이전에 적게 배정된 사람들)

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

    # 중복 제거(순서 유지)
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
        self.need_low_next = False   # 2교시 코스 연장용 (엑셀 로직 유지)
        self.assigned = {"prev_zero": False}

###########################################################
# "랜덤결과" 리스트 (이제: 적게 배정된 사람 이름 리스트, 다음 교시 우선 배정용)
###########################################################
def load_history():
    """
    random_history.json 포맷 마이그레이션 처리:
    - 예전 버전: [{"date":..., "name":..., "period":..., "type":...}, ...]
    - 지금 버전: ["김성연", "조정래", ...]
    """
    data = load_json(HISTORY_FILE, [])

    # 리스트가 아니면 그냥 빈 리스트로
    if not isinstance(data, list):
        return []

    # 빈 리스트면 그대로
    if not data:
        return []

    # 예전 형식: 리스트 안에 dict가 들어있는 경우 → name만 추출
    if isinstance(data[0], dict):
        names = []
        for item in data:
            if isinstance(item, dict) and "name" in item:
                nm = item["name"]
                if isinstance(nm, str) and nm not in names:
                    names.append(nm)
        # 새 포맷으로 덮어쓰기
        save_history(names)
        return names

    # 새 형식(이름 문자열 리스트)이라고 가정
    cleaned = []
    for v in data:
        if isinstance(v, str) and v not in cleaned:
            cleaned.append(v)
    return cleaned

def save_history(d):
    # d는 이름 문자열 리스트
    save_json(HISTORY_FILE, d)

def reset_history():
    save_history([])

###########################################################
# 가중치 (코스/교양, 중복 시 최대 1)
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
# 한 교시 배정 (새 랜덤/우선배정 로직 포함)
###########################################################
def assign_one_period(staff_list, period, demand, is_morning):
    """
    - 이전 교시에서 '적게 배정된 사람 리스트'(history)를 우선 배정
    - 이번 교시가 끝나면, 이번 교시에서 가장 적게 배정된 사람들을 history에 기록
    - history에 현재 근무자 전원이 한 번씩 들어가면 자동 초기화
    """

    # 🔹 이전 교시에서 적게 배정된 사람 리스트(우선 배정 대상)
    hist = load_history()
    hist_set = set(hist)

    # 전교시 미배정 보정(지금 구조에선 거의 영향 X, 기존 로직 유지)
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

    # 종별별 배정
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

            # 🔥 우선 배정 로직:
            #    - candidates 중에서 history(이전에 적게 배정된 사람 리스트)에 있는 사람들을 먼저 풀로 사용
            priority_cands = [i for i in candidates if staff_list[i].name in hist_set]

            if priority_cands:
                pool = priority_cands
            else:
                pool = candidates

            # 동점자에서 랜덤 선택 (우선 그룹 안에서만)
            if len(pool) == 1:
                pick = pool[0]
            else:
                pick = random.choice(pool)

            assigned[staff_list[pick].name][type_code] += 1
            total[pick] += 1

    # 혼합배정 효과 + 공평성 보정 (기존 로직 유지)
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

    # 🔻 이번 교시에서 "가장 적게 배정된 사람들"을 history에 기록
    low_group = []
    if n > 0:
        min_val = min(total)
        for i, s in enumerate(staff_list):
            if total[i] == min_val:
                low_group.append(s.name)

    # history 업데이트 (중복 없이 추가)
    for name in low_group:
        if name not in hist:
            hist.append(name)

    # 🔁 모든 근무자가 한 번씩 기록되면 자동 초기화
    current_staff_names = [s.name for s in staff_list]
    if set(current_staff_names).issubset(set(hist)) and len(hist) >= len(current_staff_names):
        hist = []

    save_history(hist)

    return assigned, low_group

###########################################################
# Streamlit UI
###########################################################
st.title("🚗 도로주행 자동 배정 (근무자 수정 + 코스/교양 수정 + 랜덤 우선배정)")

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
            st.session_state["m_edu"] = edu_map        # {교시:이름}
            st.session_state["m_course"] = course_list # [이름,이름...]

    if "m_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정 (추가/삭제/변경 가능)")
        df_m = pd.DataFrame({"근무자": st.session_state["m_staff_raw"]})
        edited_m = st.data_editor(df_m, num_rows="dynamic", key="m_edit")
        final_staff_names_m = edited_m["근무자"].dropna().tolist()

        st.session_state["m_staff_final"] = final_staff_names_m
        st.write("최종 근무자:", final_staff_names_m)

        # ---------------- 코스 / 교양 수정 UI (오전: 1,2교시) ----------------
        st.subheader("🛠 코스·교양 담당자 수정")

        staff_options_m = final_staff_names_m
        edu_raw_m = st.session_state.get("m_edu", {})
        course_raw_m = st.session_state.get("m_course", [])

        # 코스 담당자 멀티 선택
        default_courses_m = [nm for nm in course_raw_m if nm in staff_options_m]
        selected_course_m = st.multiselect(
            "코스 담당자 (여러 명 선택 가능)",
            staff_options_m,
            default=default_courses_m,
            key="m_course_sel"
        )

        # 1교시 교양
        options_m_with_none = ["없음"] + staff_options_m
        cur_edu1 = edu_raw_m.get(1)
        default_label_1 = cur_edu1 if cur_edu1 in staff_options_m else "없음"
        selected_edu1_label = st.selectbox(
            "1교시 교양 담당자",
            options_m_with_none,
            index=options_m_with_none.index(default_label_1),
            key="m_edu1_sel"
        )

        # 2교시 교양
        cur_edu2 = edu_raw_m.get(2)
        default_label_2 = cur_edu2 if cur_edu2 in staff_options_m else "없음"
        selected_edu2_label = st.selectbox(
            "2교시 교양 담당자",
            options_m_with_none,
            index=options_m_with_none.index(default_label_2),
            key="m_edu2_sel"
        )

        # 세션에 저장
        st.session_state["m_course_manual"] = selected_course_m  # list
        edu_manual_m = {}
        if selected_edu1_label != "없음":
            edu_manual_m[1] = selected_edu1_label
        if selected_edu2_label != "없음":
            edu_manual_m[2] = selected_edu2_label
        st.session_state["m_edu_manual_m"] = edu_manual_m
        # ----------------------------------------------------

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

            # 코스/교양 수동 반영
            course_manual = st.session_state.get("m_course_manual", [])
            edu_manual_m = st.session_state.get("m_edu_manual_m", {})

            for s in staff_list_m:
                # 코스
                if s.name in course_manual:
                    s.is_course = True

            for gyo, nm in edu_manual_m.items():
                for s in staff_list_m:
                    if s.name == nm:
                        s.is_edu[gyo] = True

            result_m, low_group_m = assign_one_period(staff_list_m, period_m, demand_m, is_morning=True)

            st.subheader("📌 배정 결과")
            rows = []
            for s in staff_list_m:
                info = result_m[s.name]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        # 표시는 1종수동/1종자동/2종자동/2종수동으로
                        label_map = {"1M":"1종수동","1A":"1종자동","2A":"2종자동","2M":"2종수동"}
                        desc.append(f"{label_map[t]} {info[t]}명")
                rows.append((s.name, " / ".join(desc) if desc else "0"))
            st.table({"감독관":[x[0] for x in rows], "배정":[x[1] for x in rows]})

            st.markdown("#### 🔻 이번 교시에서 가장 적게 배정된 감독관")
            st.write(low_group_m)

            # 🔢 최종 가중치(Load) 표시
            st.markdown("#### 🔢 최종 가중치(Load)")
            load_rows = {
                "감독관": [s.name for s in staff_list_m],
                "Load": [float(s.load) for s in staff_list_m],
            }
            st.table(load_rows)

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

        # ---------------- 코스 / 교양 수정 UI (오후: 3,4,5교시) ----------------
        st.subheader("🛠 코스·교양 담당자 수정")

        staff_options_a = final_staff_names_a
        edu_raw_a = st.session_state.get("a_edu", {})
        course_raw_a = st.session_state.get("a_course", [])

        # 코스 담당자 멀티 선택
        default_courses_a = [nm for nm in course_raw_a if nm in staff_options_a]
        selected_course_a = st.multiselect(
            "코스 담당자 (여러 명 선택 가능)",
            staff_options_a,
            default=default_courses_a,
            key="a_course_sel"
        )

        options_a_with_none = ["없음"] + staff_options_a

        # 3교시 교양
        cur_edu3 = edu_raw_a.get(3)
        default_label_3 = cur_edu3 if cur_edu3 in staff_options_a else "없음"
        selected_edu3_label = st.selectbox(
            "3교시 교양 담당자",
            options_a_with_none,
            index=options_a_with_none.index(default_label_3),
            key="a_edu3_sel"
        )

        # 4교시 교양
        cur_edu4 = edu_raw_a.get(4)
        default_label_4 = cur_edu4 if cur_edu4 in staff_options_a else "없음"
        selected_edu4_label = st.selectbox(
            "4교시 교양 담당자",
            options_a_with_none,
            index=options_a_with_none.index(default_label_4),
            key="a_edu4_sel"
        )

        # 5교시 교양
        cur_edu5 = edu_raw_a.get(5)
        default_label_5 = cur_edu5 if cur_edu5 in staff_options_a else "없음"
        selected_edu5_label = st.selectbox(
            "5교시 교양 담당자",
            options_a_with_none,
            index=options_a_with_none.index(default_label_5),
            key="a_edu5_sel"
        )

        st.session_state["a_course_manual"] = selected_course_a  # list
        edu_manual_a = {}
        if selected_edu3_label != "없음":
            edu_manual_a[3] = selected_edu3_label
        if selected_edu4_label != "없음":
            edu_manual_a[4] = selected_edu4_label
        if selected_edu5_label != "없음":
            edu_manual_a[5] = selected_edu5_label
        st.session_state["a_edu_manual_a"] = edu_manual_a
        # ----------------------------------------------------

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

            course_manual_a = st.session_state.get("a_course_manual", [])
            edu_manual_a = st.session_state.get("a_edu_manual_a", {})

            for s in staff_list_a:
                if s.name in course_manual_a:
                    s.is_course = True

            for gyo, nm in edu_manual_a.items():
                for s in staff_list_a:
                    if s.name == nm:
                        s.is_edu[gyo] = True

            result_a, low_group_a = assign_one_period(staff_list_a, period_a, demand_a, is_morning=False)

            st.subheader("📌 배정 결과")
            rows = []
            for s in staff_list_a:
                info = result_a[s.name]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        label_map = {"1M":"1종수동","1A":"1종자동","2A":"2종자동","2M":"2종수동"}
                        desc.append(f"{label_map[t]} {info[t]}명")
                rows.append((s.name, " / ".join(desc) if desc else "0"))
            st.table({"감독관":[x[0] for x in rows], "배정":[x[1] for x in rows]})

            st.markdown("#### 🔻 이번 교시에서 가장 적게 배정된 감독관")
            st.write(low_group_a)

            # 🔢 최종 가중치(Load) 표시
            st.markdown("#### 🔢 최종 가중치(Load)")
            load_rows_a = {
                "감독관": [s.name for s in staff_list_a],
                "Load": [float(s.load) for s in staff_list_a],
            }
            st.table(load_rows_a)

############################################################
# 랜덤 히스토리 탭 (우선 배정 대상 + 초기화 버튼)
############################################################
with tab_r:
    st.subheader("🎲 우선 배정 대상(이전에 적게 배정된 감독관 리스트)")
    hist = load_history()
    if not hist:
        st.info("우선 배정 대상 없음")
    else:
        st.table({
            "순번": list(range(1, len(hist)+1)),
            "감독관": hist,
        })

    if st.button("🧽 랜덤결과 초기화", key="r_reset"):
        reset_history()
        st.success("랜덤결과(우선 배정 리스트)를 초기화했습니다.")
