###############################################
# 🚗 도로주행 자동 배정 (오전/오후 분리 + 하루 총합 우선 + 랜덤 Fallback)
# - 오전/오후 가중치는 완전 분리
# - 하루 총합(total_history)은 오전·오후 공통 사용
# - 오후 신규 근무자는 오전 total의 "평균값"으로 시작(B안)
# - 배정 우선순위: 하루 총합 → 가중치(코스/교양) → 랜덤
# - 랜덤 히스토리는 오전·오후 공용 (오늘 중복 랜덤 방지)
# - 코스: 1교시에서 혜택 못 받으면 2교시까지 연장
###############################################
import streamlit as st
import json, os, re, random
import pandas as pd

st.set_page_config(page_title="도로주행 자동 배정", layout="wide")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

RANDOM_HISTORY_FILE = os.path.join(DATA_DIR, "random_history.json")
TOTAL_HISTORY_FILE = os.path.join(DATA_DIR, "total_history.json")

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
# 히스토리 관리
###########################################################
def load_random_history():
    # 랜덤으로 "혜택 받은 사람" 이름 리스트
    return load_json(RANDOM_HISTORY_FILE, [])

def save_random_history(names_list):
    save_json(RANDOM_HISTORY_FILE, names_list)

def reset_random_history():
    save_json(RANDOM_HISTORY_FILE, [])

def load_total_history():
    # 하루 총합 배정 수: {이름: 배정횟수}
    return load_json(TOTAL_HISTORY_FILE, {})

def save_total_history(total_dict):
    save_json(TOTAL_HISTORY_FILE, total_dict)

def reset_total_history():
    save_json(TOTAL_HISTORY_FILE, {})

###########################################################
# 수동 가능자 설정
###########################################################
MANUAL_SET = {
    "권한솔", "김남균", "김성연",
    "김주현", "이호석", "조정래"
}

###########################################################
# 텍스트 파싱 (1종수동 + 2종자동 감독관 추출)
###########################################################
def extract_staff(text: str):
    """
    오전/오후 텍스트에서 도로주행 감독관 이름만 추출
    - 1종수동: "1종수동: 7호 김남균"
    - 2종자동: "• 5호 김병욱"
    열쇠, 1종자동(이름 없는 차량호수) 등은 무시
    """
    staff = []

    # 1종수동 감독관
    m1 = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    staff += [n.strip() for n in m1]

    # 2종자동 감독관
    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    staff += [n.strip() for n in m2]

    # 중복 제거(순서 유지)
    return list(dict.fromkeys(staff))

###########################################################
# Staff 클래스
###########################################################
class Staff:
    def __init__(self, name: str):
        self.name = name
        self.is_manual = (name in MANUAL_SET)  # 수동 가능자 여부
        self.is_course = False                 # 코스 담당자(오전)
        self.is_edu = False                    # 이 교시에서 가중치 받는 교양 담당자(다음 교시 교양자)

        # 이 교시에서만 사용하는 가중치
        self.load = 0.0

###########################################################
# 자격 체크
###########################################################
def is_eligible(staff: Staff, type_code: str) -> bool:
    """
    - 수동 가능자는 4종 모두 가능: 1M, 1A, 2A, 2M
    - 그 외 자동 전용: 1A, 2A만 가능
    """
    if staff.is_manual:
        return True
    return type_code in ("1A", "2A")

###########################################################
# 가중치 계산 (코스/교양, 최대 1)
###########################################################
def apply_session_weights(staff_list, is_morning: bool, period: int, course_carry=None):
    """
    - 오전:
        * 1교시: 코스 담당자 +1
        * 2교시: 1교시에서 '많이 배정 받아서' 혜택 연장된 코스 담당자 +1 (course_carry)
    - 교양:
        * 이 교시에서 배정을 덜 받게 할 사람(=다음 교시 교양자) +1
    - 코스 + 교양이 겹쳐도 최대 1만 적용 (w > 1 이면 1로 캡)
    """
    course_carry = course_carry or []

    for s in staff_list:
        w = 0.0

        if is_morning:
            # 1교시에서 코스 담당자는 +1
            if period == 1 and s.is_course:
                w += 1.0
            # 2교시에서 '코스 혜택 연장 대상'이면 +1
            if period == 2 and s.name in course_carry:
                w += 1.0

        # 교양 가중치 (다음 교시 교양자 역할)
        if s.is_edu:
            w += 1.0

        # 코스 + 교양 중복 시 최대 1만
        if w > 1.0:
            w = 1.0

        s.load = w

###########################################################
# 한 교시 배정 (하루 총합 우선 + 가중치 + 랜덤 Fallback)
###########################################################
def assign_one_period(staff_list, period: int, demand: dict,
                      is_morning: bool, session_key: str,
                      course_carry=None):
    """
    staff_list : 이 교시의 감독관 리스트(Staff)
    demand     : {"1M": x, "1A": y, "2A": z, "2M": w}
    is_morning : 오전 여부
    session_key: "morning" 또는 "afternoon" (의미상 태그)
    course_carry: 2교시에서 코스 혜택 연장 대상 이름 리스트

    우선순위:
    1) 하루 총합(total_history) 적게 받은 사람
    2) 현재 교시 가중치(load) 낮은 사람(코스/교양 적용)
    3) 그래도 동점이면 랜덤 (이미 랜덤 혜택받은 이름은 최대한 제외)
    """

    n = len(staff_list)
    result = {s.name: {"1M": 0, "1A": 0, "2A": 0, "2M": 0} for s in staff_list}
    if n == 0:
        return result, []

    # 1,5교시: 최대 2명, 그 외: 최대 3명
    base_cap = 2 if period in (1, 5) else 3

    # 하루 총합 로드
    total_history = load_total_history()
    random_history = load_random_history()
    rh_set = set(random_history)

    # 1) 이 교시에서 사용할 day_total 초기값 세팅
    existing_totals = list(total_history.values())
    avg_total = 0
    if existing_totals:
        avg_total = round(sum(existing_totals) / len(existing_totals))

    day_total = {}
    for s in staff_list:
        if s.name in total_history:
            day_total[s.name] = total_history[s.name]
        else:
            if is_morning:
                day_total[s.name] = 0
            else:
                day_total[s.name] = avg_total

    # 2) 코스/교양 가중치 적용
    apply_session_weights(staff_list, is_morning=is_morning,
                          period=period, course_carry=course_carry)

    name_list = [s.name for s in staff_list]
    load_list = [s.load for s in staff_list]
    assigned_period = [0] * n

    # 3) 종별별로 배정
    order = [
        ("1M", demand.get("1M", 0)),
        ("1A", demand.get("1A", 0)),
        ("2A", demand.get("2A", 0)),
        ("2M", demand.get("2M", 0)),
    ]

    for type_code, need in order:
        for _ in range(need):
            # (1) 배정 가능한 후보
            candidates = []
            for i, s in enumerate(staff_list):
                if assigned_period[i] >= base_cap:
                    continue
                if not is_eligible(s, type_code):
                    continue
                candidates.append(i)

            if not candidates:
                break

            # (2) 하루 총합 기준 최소값
            min_total = min(day_total[name_list[i]] for i in candidates)
            c1 = [i for i in candidates if day_total[name_list[i]] == min_total]

            # (3) 가중치(load) 기준 최소값
            min_load = min(load_list[i] for i in c1)
            c2 = [i for i in c1 if abs(load_list[i] - min_load) < 1e-9]

            # (4) 그래도 동점이면 랜덤 Fallback
            if len(c2) == 1:
                pick = c2[0]
            else:
                # 랜덤 히스토리에 없는 사람 우선
                no_hist = [i for i in c2 if name_list[i] not in rh_set]
                pool = no_hist if no_hist else c2
                pick = random.choice(pool)
                # 랜덤 혜택 받은 사람 기록
                if name_list[pick] not in rh_set:
                    random_history.append(name_list[pick])
                    rh_set.add(name_list[pick])

            # (5) 배정 반영
            pname = name_list[pick]
            result[pname][type_code] += 1
            assigned_period[pick] += 1
            day_total[pname] += 1  # 하루 총합도 즉시 증가

    # 4) total_history / random_history 업데이트
    total_history.update(day_total)
    save_total_history(total_history)
    save_random_history(random_history)

    return result, assigned_period

###########################################################
# 짝짓기 로직 + 참관자 표시
###########################################################
def make_pairs(staff_list, result_dict):
    """
    - 배정 합계가 1인 사람끼리 둘씩 짝: "A - B"
    - 1과 0이 섞이면: 1 - 0(참관) 형태로 짝
    """
    total_assign = {
        s.name: sum(result_dict[s.name].values()) for s in staff_list
    }
    ones = [n for n, v in total_assign.items() if v == 1]
    zeros = [n for n, v in total_assign.items() if v == 0]

    pairs = []

    # 1-1 짝
    while len(ones) >= 2:
        a = ones.pop(0)
        b = ones.pop(0)
        pairs.append(f"{a} - {b}")

    # 남은 1과 0 짝: 1 - 0(참관)
    while ones and zeros:
        a = ones.pop(0)
        b = zeros.pop(0)
        pairs.append(f"{a} - {b}(참관)")

    return pairs, total_assign

###########################################################
# Streamlit UI
###########################################################
st.title("🚗 도로주행 자동 배정 (오전/오후 분리 + 하루 총합 우선)")

tab_m, tab_a, tab_h = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "📊 히스토리/현황"])

############################################################
# 🌅 오전 탭
############################################################
with tab_m:
    st.subheader("📥 오전 텍스트 입력")
    text_m = st.text_area(
        "오전 텍스트",
        height=220,
        key="txt_m",
        placeholder="예) 25.11.18(화) 오전 교양순서 및 차량배정 ...",
    )

    period_m = st.selectbox("오전 교시 선택", [1, 2], index=0, key="period_m")

    if st.button("1) 오전 근무자 자동 추출", key="m_extract"):
        if not text_m.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text_m)
            st.session_state["m_staff_raw"] = staff_names
            st.success(f"오전 근무자 {len(staff_names)}명 추출 완료")
            st.write("👤 추출된 감독관:", staff_names)

    if "m_staff_raw" in st.session_state:
        st.subheader("✏ 오전 근무자 수정 (추가/삭제/변경 가능)")
        df_m = pd.DataFrame({"감독관": st.session_state["m_staff_raw"]})
        edited_m = st.data_editor(df_m, num_rows="dynamic", key="m_edit")
        final_staff_m = edited_m["감독관"].dropna().tolist()

        st.write("📌 최종 오전 감독관:", final_staff_m)

        # 🔹 교양: 항상 "다음 교시 교양자"만 선택
        if period_m == 1:
            st.subheader("🎓 2교시 교양 담당자 선택 (1교시에 가중치 적용)")
            edu_sel = st.selectbox(
                "2교시 교양 담당자",
                ["(없음)"] + final_staff_m,
                key="m_edu_sel",
            )
            edu_m_name = None if edu_sel == "(없음)" else edu_sel
        else:  # period_m == 2
            st.subheader("🎓 3교시 교양 담당자 없음 (오전에 가중치 없음)")
            edu_m_name = None

        st.subheader("🛠 코스 담당자 선택 (복수 선택 가능, 오전 전용)")
        course_m = st.multiselect(
            "코스 담당자 (오전용)",
            final_staff_m,
            key="m_course_sel",
        )

        st.subheader("📊 오전 수요 입력")
        c1, c2, c3, c4 = st.columns(4)
        demand_m = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"m_1M_{period_m}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"m_1A_{period_m}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"m_2A_{period_m}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"m_2M_{period_m}"),
        }

        if st.button("2) 오전 배정 실행", key="m_run"):
            # Staff 객체 생성
            staff_list_m = []
            for name in final_staff_m:
                s = Staff(name)
                if edu_m_name and name == edu_m_name:
                    s.is_edu = True          # "다음 교시 교양자" → 이번 교시 가중치
                if name in course_m:
                    s.is_course = True       # 코스 담당자
                staff_list_m.append(s)

            # 2교시라면 1교시 코스 혜택 연장 대상 가져오기
            course_carry = None
            if period_m == 2:
                course_carry = st.session_state.get("course_carry_m", [])

            # 한 교시 배정
            result_m, period_total_m = assign_one_period(
                staff_list_m,
                period=period_m,
                demand=demand_m,
                is_morning=True,
                session_key="morning",
                course_carry=course_carry,
            )

            # 1교시 배정 후 → 코스 혜택 연장 대상 계산 (2교시용)
            if period_m == 1:
                if period_total_m:
                    min_assign = min(period_total_m)
                    carry_names = []
                    for i, s in enumerate(staff_list_m):
                        if s.name in course_m and period_total_m[i] > min_assign:
                            carry_names.append(s.name)
                    st.session_state["course_carry_m"] = carry_names
                else:
                    st.session_state["course_carry_m"] = []
            elif period_m == 2:
                # 2교시까지 끝났으면 코스 혜택 연장 정보 제거
                st.session_state["course_carry_m"] = []

            LABEL = {
                "1M": "1종수동",
                "1A": "1종자동",
                "2A": "2종자동",
                "2M": "2종수동",
            }

            st.subheader("📌 오전 배정 결과")
            rows = []
            for i, s in enumerate(staff_list_m):
                info = result_m[s.name]
                parts = []
                for t in ("1M", "1A", "2A", "2M"):
                    if info[t] > 0:
                        parts.append(f"{LABEL[t]} {info[t]}명")
                rows.append((s.name, " / ".join(parts) if parts else "0", period_total_m[i]))

            st.table({
                "감독관": [r[0] for r in rows],
                "배정": [r[1] for r in rows],
                "해당 교시 배정합계": [r[2] for r in rows],
            })

            st.markdown("#### 👥 짝지기 결과 (1명/0명 기준)")
            pairs_m, total_assign_m = make_pairs(staff_list_m, result_m)
            if not pairs_m:
                st.info("짝지을 감독관 없음")
            else:
                for p in pairs_m:
                    st.write("• " + p)

            st.markdown("#### 📈 이 교시 기준 감독관별 배정 합계")
            st.table({
                "감독관": list(total_assign_m.keys()),
                "배정합계": list(total_assign_m.values()),
            })

        if st.button("🧹 오늘 하루 총합/랜덤 히스토리 초기화", key="reset_all_m"):
            reset_total_history()
            reset_random_history()
            st.session_state.pop("course_carry_m", None)
            st.success("오늘 하루 누적 배정(total_history)와 랜덤 히스토리, 코스 연장 정보를 초기화했습니다.")

############################################################
# 🌇 오후 탭
############################################################
with tab_a:
    st.subheader("📥 오후 텍스트 입력")
    text_a = st.text_area(
        "오후 텍스트",
        height=220,
        key="txt_a",
        placeholder="예) 25.11.18(화) 오후 교양순서 및 차량배정 ...",
    )

    period_a = st.selectbox("오후 교시 선택", [3, 4, 5], index=0, key="period_a")

    if st.button("1) 오후 근무자 자동 추출", key="a_extract"):
        if not text_a.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text_a)
            st.session_state["a_staff_raw"] = staff_names
            st.success(f"오후 근무자 {len(staff_names)}명 추출 완료")
            st.write("👤 추출된 감독관:", staff_names)

    if "a_staff_raw" in st.session_state:
        st.subheader("✏ 오후 근무자 수정 (추가/삭제/변경 가능)")
        df_a = pd.DataFrame({"감독관": st.session_state["a_staff_raw"]})
        edited_a = st.data_editor(df_a, num_rows="dynamic", key="a_edit")
        final_staff_a = edited_a["감독관"].dropna().tolist()

        st.write("📌 최종 오후 감독관:", final_staff_a)

        # 🔹 오후도 "다음 교시 교양자"만 선택
        if period_a == 3:
            st.subheader("🎓 4교시 교양 담당자 선택 (3교시에 가중치 적용)")
            edu_sel_a = st.selectbox(
                "4교시 교양 담당자",
                ["(없음)"] + final_staff_a,
                key="a_edu_sel_3",
            )
            edu_a_name = None if edu_sel_a == "(없음)" else edu_sel_a
        elif period_a == 4:
            st.subheader("🎓 5교시 교양 담당자 선택 (4교시에 가중치 적용)")
            edu_sel_a = st.selectbox(
                "5교시 교양 담당자",
                ["(없음)"] + final_staff_a,
                key="a_edu_sel_4",
            )
            edu_a_name = None if edu_sel_a == "(없음)" else edu_sel_a
        else:  # period_a == 5
            st.subheader("🎓 6교시 없음 (5교시는 교양 가중치 없음)")
            edu_a_name = None

        st.subheader("📊 오후 수요 입력")
        c1, c2, c3, c4 = st.columns(4)
        demand_a = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"a_1M_{period_a}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"a_1A_{period_a}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"a_2A_{period_a}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"a_2M_{period_a}"),
        }

        if st.button("2) 오후 배정 실행", key="a_run"):
            staff_list_a = []
            for name in final_staff_a:
                s = Staff(name)
                if edu_a_name and name == edu_a_name:
                    s.is_edu = True
                # 오후에는 코스 담당 개념 없음
                staff_list_a.append(s)

            result_a, period_total_a = assign_one_period(
                staff_list_a,
                period=period_a,
                demand=demand_a,
                is_morning=False,
                session_key="afternoon",
                course_carry=None,
            )

            LABEL = {
                "1M": "1종수동",
                "1A": "1종자동",
                "2A": "2종자동",
                "2M": "2종수동",
            }

            st.subheader("📌 오후 배정 결과")
            rows = []
            for i, s in enumerate(staff_list_a):
                info = result_a[s.name]
                parts = []
                for t in ("1M", "1A", "2A", "2M"):
                    if info[t] > 0:
                        parts.append(f"{LABEL[t]} {info[t]}명")
                rows.append((s.name, " / ".join(parts) if parts else "0", period_total_a[i]))

            st.table({
                "감독관": [r[0] for r in rows],
                "배정": [r[1] for r in rows],
                "해당 교시 배정합계": [r[2] for r in rows],
            })

            st.markdown("#### 👥 짝지기 결과 (1명/0명 기준)")
            pairs_a, total_assign_a = make_pairs(staff_list_a, result_a)
            if not pairs_a:
                st.info("짝지을 감독관 없음")
            else:
                for p in pairs_a:
                    st.write("• " + p)

            st.markdown("#### 📈 이 교시 기준 감독관별 배정 합계")
            st.table({
                "감독관": list(total_assign_a.keys()),
                "배정합계": list(total_assign_a.values()),
            })

        if st.button("🧹 오늘 하루 총합/랜덤 히스토리 초기화", key="reset_all_a"):
            reset_total_history()
            reset_random_history()
            st.session_state.pop("course_carry_m", None)
            st.success("오늘 하루 누적 배정(total_history)와 랜덤 히스토리, 코스 연장 정보를 초기화했습니다.")

############################################################
# 📊 히스토리/현황 탭
############################################################
with tab_h:
    st.subheader("🎲 랜덤 히스토리 (오늘 랜덤 혜택 받은 감독관)")

    rh = load_random_history()
    if not rh:
        st.info("랜덤 기록이 없습니다.")
    else:
        st.table({"순번": list(range(1, len(rh) + 1)), "감독관": rh})

    if st.button("🧹 랜덤 히스토리만 초기화", key="reset_rh_only"):
        reset_random_history()
        st.success("랜덤 히스토리를 초기화했습니다.")

    st.subheader("📊 오늘 하루 누적 배정(total_history)")
    th = load_total_history()
    if not th:
        st.info("아직 누적 배정 기록이 없습니다.")
    else:
        names = sorted(th.keys())
        st.table({
            "감독관": names,
            "하루 누적 배정횟수": [th[n] for n in names],
        })

    if st.button("🧹 하루 총합만 초기화", key="reset_th_only"):
        reset_total_history()
        st.success("하루 누적 배정 기록(total_history)을 초기화했습니다.")

    if st.button("🧹 하루 총합 + 랜덤 모두 초기화", key="reset_all_both"):
        reset_total_history()
        reset_random_history()
        st.session_state.pop("course_carry_m", None)
        st.success("하루 누적 배정과 랜덤 히스토리, 코스 연장 정보를 모두 초기화했습니다.")
