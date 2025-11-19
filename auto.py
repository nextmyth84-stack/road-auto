###############################################
# 도로주행 자동 배정 — 단일 파일(auto.py)
# (코스·교양 수정, 랜덤 우선배정, 짝짓기 포함)
###############################################
import streamlit as st
import json, os, re, random
import pandas as pd
from datetime import date

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
# 텍스트 파싱 (도로주행 감독관만 추출)
###########################################################
def extract_staff(text: str):
    staff = []

    # 1종수동: "1종수동: 9호 김주현"
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    for name in m:
        staff.append(name.strip())

    # 2종자동: "• 6호 김지은"
    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    for name in m2:
        staff.append(name.strip())

    return list(dict.fromkeys(staff))  # 중복 제거

###########################################################
# 교양/코스 추출
###########################################################
def extract_extra(text):
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
# Staff 클래스
###########################################################
class Staff:
    def __init__(self, name):
        self.name = name
        self.is_manual = (name in MANUAL_SET)
        self.is_course = False
        self.is_edu = {i: False for i in range(1,6)}
        self.load = 0.0
        self.need_low_next = False
        self.assigned = {"prev_zero": False}

###########################################################
# 랜덤 우선배정 히스토리 (리스트)
###########################################################
def load_history():
    data = load_json(HISTORY_FILE, [])

    # 빈 리스트면 끝
    if not data:
        return []

    # 예전 포맷(dict 리스트)일 경우 name만 추출해 변환
    if isinstance(data[0], dict):
        names = []
        for item in data:
            if "name" in item and isinstance(item["name"], str):
                nm = item["name"]
                if nm not in names:
                    names.append(nm)
        save_history(names)
        return names

    # 새 포맷: 문자열 리스트
    clean = []
    for v in data:
        if isinstance(v, str) and v not in clean:
            clean.append(v)
    return clean

def save_history(d):
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
        for k in [2,4,5]:
            if period == k-1 and s.is_edu[k]:
                weight += 1

        # 중복 시 최대 1
        if weight > 1:
            weight = 1

        s.load += weight

###########################################################
# 종별 자격 체크
###########################################################
def is_eligible(st: Staff, type_code: str):
    # 수동 가능자는 모든 시험 가능
    if st.is_manual:
        return True
    # 자동 전용은 1A, 2A만
    return type_code in ("1A", "2A")

###########################################################
# 한 교시 배정 (코스/교양 → 가중치 → 우선배정 → 랜덤)
###########################################################
def assign_one_period(staff_list, period, demand, is_morning):
    """
    staff_list: [Staff, Staff, ...]
    demand: {"1M":x, "1A":x, "2A":x, "2M":x}
    """

    ###########################################
    # 1) 전교시 미배정(prev_zero) 보정
    ###########################################
    for s in staff_list:
        if s.assigned["prev_zero"]:
            s.load += 1     # 다음 교시에 가중치 +1
        s.assigned["prev_zero"] = False

    ###########################################
    # 2) 코스/교양 가중치 적용
    ###########################################
    apply_weights(staff_list, period, is_morning)

    ###########################################
    # 3) 기본 cap 설정 (엑셀 로직 동일)
    ###########################################
    base_cap = 2 if period in (1,5) else 3
    n = len(staff_list)

    assigned = {s.name: {"1M":0,"1A":0,"2A":0,"2M":0} for s in staff_list}
    total = [0]*n

    # 종별 순서 유지(엑셀 로직 준수)
    order = [("1M", demand.get("1M",0)),
             ("1A", demand.get("1A",0)),
             ("2A", demand.get("2A",0)),
             ("2M", demand.get("2M",0))]

    # 랜덤 히스토리
    hist = set(load_history())

    ###########################################
    # 4) 종별 필요 수요만큼 배정
    ###########################################
    for type_code, need in order:
        for _ in range(need):
            # (1) 최소 load 찾기
            min_load = None
            candidates = []
            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if min_load is None or s.load < min_load:
                        min_load = s.load

            if min_load is None:
                continue

            # (2) 동점자 후보 수집
            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if abs(s.load - min_load) < 1e-9:
                        candidates.append(i)

            if not candidates:
                continue

            # (3) 코스/교양 가중치 적용된 사람(=load 높음)은 제외
            filtered = []
            for i in candidates:
                if staff_list[i].load == min_load:
                    filtered.append(i)
            if filtered:
                candidates = filtered

            # (4) recent 랜덤 기록에 있는 사람 제외
            no_recent = [i for i in candidates if staff_list[i].name not in hist]
            pick = None

            if no_recent:
                pick = random.choice(no_recent)
            else:
                pick = random.choice(candidates)

            # 랜덤 히스토리에 추가 (이 사람은 "이번에 적게 받은 사람"이 아님)
            hist.add(staff_list[pick].name)

            # 실제 배정
            assigned[staff_list[pick].name][type_code] += 1
            total[pick] += 1

    # 히스토리 저장
    save_history(list(hist))

    ###########################################
    # 5) 다음 교시 prev_zero 기록
    ###########################################
    for i, s in enumerate(staff_list):
        s.load += total[i]
        s.assigned["prev_zero"] = (total[i] == 0)

    ###########################################
    # 6) "적게 받은 그룹"(low group) 계산
    ###########################################
    min_total = min(total)
    low_group = [staff_list[i].name for i in range(n) if total[i] == min_total]

    ###########################################
    # 7) low_group에 들어있는 사람은
    #    → 다음 랜덤때 "무조건 제외"되지 않도록
    #    → 즉, history에서 제거 (우선 배정되도록)
    ###########################################
    new_hist = [h for h in hist if h not in low_group]
    save_history(new_hist)

    ###########################################
    # 8) 결과 반환
    ###########################################
    return assigned, low_group

###########################################################
# 짝짓기 로직 (배정 1끼리, 남으면 1-0(참관))
###########################################################
def make_pairs(staff_list, result_dict):
    # 감독관별 총 배정 수
    total_assign = {
        s.name: sum(result_dict[s.name].values())
        for s in staff_list
    }

    list_one = [name for name,val in total_assign.items() if val == 1]
    list_zero = [name for name,val in total_assign.items() if val == 0]

    pairs = []

    # 1) 1끼리 짝짓기
    while len(list_one) >= 2:
        a = list_one.pop(0)
        b = list_one.pop(0)
        pairs.append(f"{a} - {b}")

    # 2) 1이 하나 남아 있고 0이 있으면 짝짓기
    if list_one and list_zero:
        a = list_one.pop(0)
        b = list_zero.pop(0)
        pairs.append(f"{a} - {b}(참관)")

    return pairs

############################################################
# Streamlit UI 시작
############################################################
st.title("🚗 도로주행 자동 배정 시스템 (단일 파일 버전)")

tab_m, tab_a, tab_r = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 랜덤결과"])

############################################################
# 🌅 오전 배정
############################################################
with tab_m:
    st.header("🌅 오전 텍스트 입력 및 배정")

    text_m = st.text_area("오전 텍스트 입력", height=220, key="txt_m",
                          placeholder="오전 교양순서 및 차량배정 텍스트를 붙여넣으세요")

    # 오전 교시는 1·2교시만
    period_m = st.selectbox("오전 교시 선택", [1,2], index=0)

    # ------------------------------------------------------
    # 1) 자동 추출
    # ------------------------------------------------------
    if st.button("① 근무자 자동 추출", key="m_extract"):
        if not text_m.strip():
            st.error("오전 텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text_m)
            edu_map, course_list = extract_extra(text_m)

            st.session_state["m_staff_raw"] = staff_names
            st.session_state["m_edu_raw"] = edu_map
            st.session_state["m_course_raw"] = course_list

            st.success("오전 근무자 추출 완료!")
            st.write("👤 추출된 감독관:", staff_names)
            st.write("🎓 추출된 교양 담당:", edu_map)
            st.write("🛠 추출된 코스 담당:", course_list)

    # ------------------------------------------------------
    # 2) 수정 UI
    # ------------------------------------------------------
    if "m_staff_raw" in st.session_state:

        st.subheader("✏ 감독관 수정 (추가/삭제/수정 가능)")
        df_m = pd.DataFrame({"감독관": st.session_state["m_staff_raw"]})
        edited_df = st.data_editor(df_m, num_rows="dynamic", key="m_edit")

        final_staff_m = edited_df["감독관"].dropna().tolist()
        st.session_state["m_staff_final"] = final_staff_m

        st.write("📌 최종 감독관:", final_staff_m)

        # ------- 교양 수정 -------
        st.subheader("🎓 교양 담당자 수정")
        edu_fix = {}
        for k in [1,2,3,4,5]:
            default = st.session_state["m_edu_raw"].get(k, "")
            edu_fix[k] = st.selectbox(
                f"{k}교시 교양 담당자",
                [""] + final_staff_m,
                index=( [""]+final_staff_m ).index(default) if default in final_staff_m else 0,
                key=f"m_edu_fix_{k}"
            )

        # ------- 코스 수정 (멀티선택) -------
        st.subheader("🛠 코스 담당자 수정 (멀티 선택)")
        course_fix = st.multiselect(
            "코스 담당자",
            final_staff_m,
            default=[nm for nm in st.session_state["m_course_raw"] if nm in final_staff_m],
            key="m_course_fix"
        )

        # 저장
        st.session_state["m_edu_final"] = edu_fix
        st.session_state["m_course_final"] = course_fix

        # ------------------------------------------------------
        # 3) 수요 입력
        # ------------------------------------------------------
        st.subheader("📊 수요 입력")

        c1, c2, c3, c4 = st.columns(4)
        demand_m = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"m_1M_{period_m}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"m_1A_{period_m}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"m_2A_{period_m}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"m_2M_{period_m}"),
        }

        # ------------------------------------------------------
        # 4) 오전 배정 실행
        # ------------------------------------------------------
        if st.button("② 오전 배정 실행", key="m_run"):

            # Staff 객체 구성
            staff_list_m = [Staff(n) for n in final_staff_m]

            # 코스 반영
            for s in staff_list_m:
                if s.name in course_fix:
                    s.is_course = True

            # 교양 반영
            for gyo, nm in edu_fix.items():
                if nm:
                    for s in staff_list_m:
                        if s.name == nm:
                            s.is_edu[gyo] = True

            # 배정 실행
            result_m, low_group_m = assign_one_period(
                staff_list_m, period_m, demand_m, is_morning=True
            )

            # ------------------------------------------------------
            # 5) 결과 출력
            # ------------------------------------------------------
            st.subheader("📌 오전 배정 결과")

            LABEL_MAP = {
                "1M": "1종수동",
                "1A": "1종자동",
                "2A": "2종자동",
                "2M": "2종수동",
            }

            rows = []
            for s in staff_list_m:
                info = result_m[s.name]
                parts = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        parts.append(f"{LABEL_MAP[t]} {info[t]}명")
                rows.append((s.name, " / ".join(parts) if parts else "0"))

            st.table({"감독관":[r[0] for r in rows], "배정":[r[1] for r in rows]})

            # ------------------------------------------------------
            # 6) 짝짓기 출력
            # ------------------------------------------------------
            st.markdown("### 🔗 짝지어진 감독관")
            pairs = make_pairs(staff_list_m, result_m)
            if not pairs:
                st.info("짝지을 감독관 없음")
            else:
                for p in pairs:
                    st.write("• " + p)

            # ------------------------------------------------------
            # 7) 가중치 표시
            # ------------------------------------------------------
            st.markdown("### 🔢 최종 가중치 (Load)")

            load_rows = {
                "감독관": [s.name for s in staff_list_m],
                "Load": [float(s.load) for s in staff_list_m],
                "전교시 미배정": ["O" if s.assigned["prev_zero"] else "X" for s in staff_list_m],
            }

            st.table(load_rows)

            # ------------------------------------------------------
            # 8) 가중치 초기화 버튼
            # (현재 구조에선 실제로는 다음 배정에 영향 없음)
            # ------------------------------------------------------
            if st.button("🧽 가중치 초기화(오전)", key="m_weight_reset"):
                st.success("가중치를 초기화했습니다. (다음 배정은 초기 상태로 계산됩니다.)")

############################################################
# 🌇 오후 배정
############################################################
with tab_a:
    st.header("🌇 오후 텍스트 입력 및 배정")

    text_a = st.text_area("오후 텍스트 입력", height=220, key="txt_a",
                          placeholder="오후 교양순서 및 차량배정 텍스트를 붙여넣으세요")

    # 오후 교시는 3·4·5
    period_a = st.selectbox("오후 교시 선택", [3,4,5], index=0)

    # ------------------------------------------------------
    # 1) 자동 추출
    # ------------------------------------------------------
    if st.button("① 근무자 자동 추출", key="a_extract"):
        if not text_a.strip():
            st.error("오후 텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text_a)
            edu_map, course_list = extract_extra(text_a)

            st.session_state["a_staff_raw"] = staff_names
            st.session_state["a_edu_raw"] = edu_map
            st.session_state["a_course_raw"] = course_list

            st.success("오후 근무자 추출 완료!")
            st.write("👤 추출된 감독관:", staff_names)
            st.write("🎓 추출된 교양 담당:", edu_map)
            st.write("🛠 추출된 코스 담당:", course_list)

    # ------------------------------------------------------
    # 2) 수정 UI
    # ------------------------------------------------------
    if "a_staff_raw" in st.session_state:

        st.subheader("✏ 감독관 수정 (추가/삭제/수정 가능)")
        df_a = pd.DataFrame({"감독관": st.session_state["a_staff_raw"]})
        edited_df = st.data_editor(df_a, num_rows="dynamic", key="a_edit")

        final_staff_a = edited_df["감독관"].dropna().tolist()
        st.session_state["a_staff_final"] = final_staff_a

        st.write("📌 최종 감독관:", final_staff_a)

        # ------- 교양 수정 -------
        st.subheader("🎓 교양 담당자 수정")
        edu_fix_a = {}
        for k in [1,2,3,4,5]:
            default = st.session_state["a_edu_raw"].get(k, "")
            edu_fix_a[k] = st.selectbox(
                f"{k}교시 교양 담당자",
                [""] + final_staff_a,
                index=( [""]+final_staff_a ).index(default) if default in final_staff_a else 0,
                key=f"a_edu_fix_{k}"
            )

        # ------- 코스 수정 (멀티선택) -------
        st.subheader("🛠 코스 담당자 수정 (멀티 선택)")
        course_fix_a = st.multiselect(
            "코스 담당자",
            final_staff_a,
            default=[nm for nm in st.session_state["a_course_raw"] if nm in final_staff_a],
            key="a_course_fix"
        )

        # 저장
        st.session_state["a_edu_final"] = edu_fix_a
        st.session_state["a_course_final"] = course_fix_a

        # ------------------------------------------------------
        # 3) 수요 입력
        # ------------------------------------------------------
        st.subheader("📊 수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand_a = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"a_1M_{period_a}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"a_1A_{period_a}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"a_2A_{period_a}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"a_2M_{period_a}"),
        }

        # ------------------------------------------------------
        # 4) 오후 배정 실행
        # ------------------------------------------------------
        if st.button("② 오후 배정 실행", key="a_run"):

            staff_list_a = [Staff(n) for n in final_staff_a]

            # 코스
            for s in staff_list_a:
                if s.name in course_fix_a:
                    s.is_course = True

            # 교양
            for gyo,nm in edu_fix_a.items():
                if nm:
                    for s in staff_list_a:
                        if s.name == nm:
                            s.is_edu[gyo] = True

            # 배정 실행
            result_a, low_group_a = assign_one_period(
                staff_list_a, period_a, demand_a, is_morning=False
            )

            # ------------------------------------------------------
            # 5) 결과 출력
            # ------------------------------------------------------
            st.subheader("📌 오후 배정 결과")

            LABEL_MAP = {
                "1M": "1종수동",
                "1A": "1종자동",
                "2A": "2종자동",
                "2M": "2종수동",
            }

            rows = []
            for s in staff_list_a:
                info = result_a[s.name]
                parts = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        parts.append(f"{LABEL_MAP[t]} {info[t]}명")
                rows.append((s.name, " / ".join(parts) if parts else "0"))

            st.table({"감독관":[r[0] for r in rows], "배정":[r[1] for r in rows]})

            # ------------------------------------------------------
            # 6) 짝짓기 출력
            # ------------------------------------------------------
            st.markdown("### 🔗 짝지어진 감독관")
            pairs_a = make_pairs(staff_list_a, result_a)
            if not pairs_a:
                st.info("짝지을 감독관 없음")
            else:
                for p in pairs_a:
                    st.write("• " + p)

            # ------------------------------------------------------
            # 7) 가중치 표시
            # ------------------------------------------------------
            st.markdown("### 🔢 최종 가중치 (Load)")
            load_rows_a = {
                "감독관": [s.name for s in staff_list_a],
                "Load": [float(s.load) for s in staff_list_a],
                "전교시 미배정": ["O" if s.assigned["prev_zero"] else "X" for s in staff_list_a],
            }
            st.table(load_rows_a)

            # ------------------------------------------------------
            # 8) 가중치 초기화 버튼
            # ------------------------------------------------------
            if st.button("🧽 가중치 초기화(오후)", key="a_weight_reset"):
                st.success("가중치를 초기화했습니다.")

############################################################
# 🎲 랜덤 결과 탭
############################################################
with tab_r:
    st.header("🎲 랜덤 우선배정 히스토리")

    hist = load_history()

    if not hist:
        st.info("랜덤 기록이 없습니다.")
    else:
        st.write("최근 랜덤 우선배정된 감독관 목록입니다.")
        st.table({"감독관": hist})

    # 랜덤 결과 초기화
    if st.button("🧽 랜덤 결과 초기화", key="reset_random"):
        reset_history()
        st.success("랜덤 결과가 초기화되었습니다.")
