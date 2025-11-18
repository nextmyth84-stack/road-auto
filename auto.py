###############################################
# 도로주행 자동 배정 — 공통 로직
###############################################
import streamlit as st
import json, os, random
from datetime import date

st.set_page_config(page_title="도로주행 자동 배정", layout="wide")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

HIST_FILE = os.path.join(DATA_DIR, "random_history.json")

TYPE_LABEL = {
    "1M": "1종수동",
    "1A": "1종자동",
    "2A": "2종자동",
    "2M": "2종수동",
}

############################################
# 파일 I/O
############################################
def load_json(path, default=None):
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

############################################
# 랜덤 히스토리
############################################
def load_history():
    return load_json(HIST_FILE, default=[])

def save_history(hist):
    save_json(HIST_FILE, hist)

def reset_history():
    save_history([])

def get_recent_beneficiaries(hist, days=3):
    """최근 N일간 혜택자 이름 집합"""
    today = date.today()
    names = set()
    for h in hist:
        try:
            d = date.fromisoformat(h["date"])
        except Exception:
            continue
        if (today - d).days <= days and h.get("role") == "beneficiary":
            names.add(h["name"])
    return names

############################################
# Staff 구조
############################################
class Staff:
    def __init__(self, name, is_manual=False):
        self.name = name
        self.is_manual = is_manual  # 수동 가능자
        self.is_course = False      # 코스 담당자
        self.is_edu = {k: False for k in range(1, 6)}  # 교시별 교양
        self.load = 0.0             # 누적 가중치 (배정수 + 보정)
        self.need_low_next = False  # 2교시 코스 연장 플래그
        self.skipped_prev = False   # 직전 교시 미배정 여부

    def can(self, type_code: str) -> bool:
        """종별 가능 여부 (수동 가능자는 전 종별, 그 외는 자동만)"""
        if self.is_manual:
            return True
        return type_code in ("1A", "2A")

############################################
# 텍스트 파싱 (오전/오후 결과에서 감독관 추출)
############################################
def parse_staff_from_text(text: str):
    """
    1종수동/2종자동 블럭에서 '호수 뒤 이름'만 추출
    예) • 6호 김지은  → 김지은
    """
    staff = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "호" in line:
            # '• 6호 김지은' / '6호 김지은' 등
            line = line.replace("•", "").strip()
            parts = line.split()
            if len(parts) >= 2 and "호" in parts[0]:
                name = parts[1].strip()
                # 한글 이름만 필터
                if all("가" <= ch <= "힣" for ch in name):
                    staff.append(name)
    # 순서 유지 중복 제거
    return list(dict.fromkeys(staff))

############################################
# 가중치 적용
############################################
def apply_weights(staff_list, period: int, is_morning: bool):
    """
    - 코스: 오전 1교시 +1, 오전 2교시 need_low_next=True 이면 +1
    - 교양: (k교시 담당자 → k-1교시에 +1), 단 1,3교시는 제외
    - 중복되어도 최대 +1만 적용 (필요하면 여기서 clamp)
    """
    for s in staff_list:
        w = 0

        # 코스
        if is_morning:
            if period == 1 and s.is_course:
                w += 1
            if period == 2 and s.need_low_next:
                w += 1

        # 교양 (k교시 담당자 → k-1교시), 1·3 제외
        for k in (2, 4, 5):
            if s.is_edu.get(k, False) and period == k - 1:
                w += 1

        # 중복 최대 1
        if w > 1:
            w = 1

        s.load += w

############################################
# 한 교시 자동배정 엔진
############################################
def assign_one_period(staff_list, demand_dict, period: int, is_morning: bool):
    """
    demand_dict = {"1M": n, "1A": n, "2A": n, "2M": n}
    return:
      assigned_detail: {이름: {"1M":x,"1A":y,"2A":z,"2M":w}}
      total: {이름: 총배정수}
    """
    # 직전 교시 미배정자 보정 (원하면 여기에서 load -=1 같은 추가 규칙 넣을 수 있음)
    for s in staff_list:
        if s.skipped_prev:
            # 너무 세게는 안 줌, 살짝 우선권만 준다고 가정
            s.load -= 0.5
        s.skipped_prev = False

    # 1) 코스/교양 가중치
    apply_weights(staff_list, period, is_morning)

    # 2) Cap 설정 (1,5교시=2명 / 나머지=3명)
    cap = 2 if period in (1, 5) else 3

    # 3) 결과 구조 초기화
    assigned_detail = {
        s.name: {"1M": 0, "1A": 0, "2A": 0, "2M": 0} for s in staff_list
    }
    total = {s.name: 0 for s in staff_list}

    # 4) 종별 순서대로 배정
    for type_code in ("1M", "1A", "2A", "2M"):
        need = int(demand_dict.get(type_code, 0) or 0)
        if need <= 0:
            continue

        for _ in range(need):
            # 후보 탐색 (cap 미만 + 자격 있음)
            min_load = None
            candidates = []

            for s in staff_list:
                if total[s.name] >= cap:
                    continue
                if not s.can(type_code):
                    continue
                if (min_load is None) or (s.load < min_load):
                    min_load = s.load
                    candidates = [s]
                elif s.load == min_load:
                    candidates.append(s)

            if not candidates:
                continue  # 배정 불가인 경우는 그냥 넘김

            picked = random.choice(candidates)
            assigned_detail[picked.name][type_code] += 1
            total[picked.name] += 1
            # 배정 1회당 load 1씩 누적
            picked.load += 1

    # 5) 미배정 표시
    for s in staff_list:
        if total[s.name] == 0:
            s.skipped_prev = True

    # 6) 코스 연장 플래그(오전 1→2교시)
    if is_morning and period == 1 and staff_list:
        min_assign = min(total.values())
        for s in staff_list:
            s.need_low_next = (s.is_course and total[s.name] > min_assign)
    else:
        for s in staff_list:
            s.need_low_next = False

    # 7) 최저 배정자(혜택자) 기록
    hist = load_history()
    if staff_list:
        min_assign = min(total.values())
        raw_candidates = [nm for nm, cnt in total.items() if cnt == min_assign]

        recent = get_recent_beneficiaries(hist, days=3)
        filtered = [nm for nm in raw_candidates if nm not in recent]

        if filtered:
            final_benefits = filtered
        else:
            # 전원이 최근 혜택자면 → 히스토리 무시, 원본 최저 배정자 전체 혜택자
            final_benefits = raw_candidates

        today_str = date.today().isoformat()
        for nm in final_benefits:
            hist.append({
                "date": today_str,
                "name": nm,
                "period": period,
                "role": "beneficiary",
            })
        save_history(hist)

    return assigned_detail, total
###############################################
# Streamlit UI 틀 / 탭 생성
###############################################
st.title("🚗 도로주행 자동 배정 (파이썬 버전)")

tab_m, tab_a, tab_r = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 랜덤결과"])

###############################################
# 🌅 오전 탭
###############################################
with tab_m:
    st.subheader("📥 오전 결과 텍스트 입력")

    morning_text = st.text_area("오전 교양/차량배정 텍스트를 붙여넣기", height=200, key="txt_m")

    if st.button("① 오전 근무자 자동 추출", key="m_extract"):
        if not morning_text.strip():
            st.warning("텍스트를 입력해주세요.")
        else:
            staff_names = parse_staff_from_text(morning_text)
            st.session_state["m_staff_raw"] = staff_names
            st.success("오전 근무자 추출 완료")
            st.write("추출된 근무자:", ", ".join(staff_names))

    if "m_staff_raw" not in st.session_state:
        st.info("먼저 위에서 근무자 자동 추출을 해주세요.")
    else:
        st.subheader("✏ 오전 근무자 수정")

        df = {"근무자": st.session_state["m_staff_raw"]}
        edited = st.data_editor(df, num_rows="dynamic", key="m_staff_edit")
        final_staff = [nm for nm in edited["근무자"].dropna().tolist()]
        st.write("최종 근무자:", ", ".join(final_staff))

        # 수동 가능자 고정
        manual_list = ["권한솔", "김남균", "김성연", "김주현", "이호석", "조정래"]

        # Staff 리스트 생성
        staff_objs_m = [Staff(name=nm, is_manual=(nm in manual_list)) for nm in final_staff]

        # 코스 담당자 선택
        st.markdown("### 🎯 코스 담당자 선택")
        course_selected_m = st.multiselect(
            "오전 코스 담당자(복수 선택 가능)",
            options=final_staff,
            default=[],
            key="m_course_select",
        )
        for s in staff_objs_m:
            s.is_course = (s.name in course_selected_m)

        # 2교시 교양 담당자 선택
        st.markdown("### 📘 2교시 교양 담당자 선택")
        edu2_options = ["(선택 없음)"] + final_staff
        edu2_selected = st.selectbox(
            "2교시 교양",
            options=edu2_options,
            key="m_edu2_select",
        )
        for s in staff_objs_m:
            s.is_edu = {k: False for k in range(1, 6)}
            if edu2_selected != "(선택 없음)" and s.name == edu2_selected:
                s.is_edu[2] = True

        # 수요 입력
        st.markdown("### 📊 수요 입력 (1·2교시)")
        c1, c2 = st.columns(2)
        with c1:
            d1_1M = st.number_input("1교시 1종수동", min_value=0, key="m_1_1M")
            d1_1A = st.number_input("1교시 1종자동", min_value=0, key="m_1_1A")
            d1_2A = st.number_input("1교시 2종자동", min_value=0, key="m_1_2A")
            d1_2M = st.number_input("1교시 2종수동", min_value=0, key="m_1_2M")
        with c2:
            d2_1M = st.number_input("2교시 1종수동", min_value=0, key="m_2_1M")
            d2_1A = st.number_input("2교시 1종자동", min_value=0, key="m_2_1A")
            d2_2A = st.number_input("2교시 2종자동", min_value=0, key="m_2_2A")
            d2_2M = st.number_input("2교시 2종수동", min_value=0, key="m_2_2M")

        demand_m = {
            1: {"1M": d1_1M, "1A": d1_1A, "2A": d1_2A, "2M": d1_2M},
            2: {"1M": d2_1M, "1A": d2_1A, "2A": d2_2A, "2M": d2_2M},
        }

        st.markdown("### 🧽 가중치 초기화 (설명용)")
        if st.button("가중치 0으로 초기화", key="m_reset_weight"):
            for s in staff_objs_m:
                s.load = 0.0
                s.need_low_next = False
                s.skipped_prev = False
            st.success("이 세션의 가중치를 초기화했습니다. (실행 시 다시 계산됨)")

        # 배정 실행
        if st.button("② 오전 배정 실행", key="m_run"):
            result_rows = []
            # 1교시 → 2교시 순서로 같은 staff_objs_m 사용 (load 누적)
            for period in (1, 2):
                assigned_detail, total = assign_one_period(
                    staff_list=staff_objs_m,
                    demand_dict=demand_m[period],
                    period=period,
                    is_morning=True,
                )

                # 화면 표시용 정리
                data = {
                    "감독관": [],
                    "배정": [],
                    "총합": [],
                    "Load": [],
                }
                for s in staff_objs_m:
                    info = assigned_detail[s.name]
                    parts = []
                    for tc in ("1M", "1A", "2A", "2M"):
                        if info[tc] > 0:
                            parts.append(f"{TYPE_LABEL[tc]} {info[tc]}명")
                    data["감독관"].append(s.name)
                    data["배정"].append(" / ".join(parts) if parts else "0")
                    data["총합"].append(total[s.name])
                    data["Load"].append(round(s.load, 3))

                st.markdown(f"#### 🕒 {period}교시 결과")
                st.table(data)

            st.info("오전 1·2교시 배정이 완료되었습니다.")
###############################################
# 🌇 오후 탭
###############################################
with tab_a:
    st.subheader("📥 오후 결과 텍스트 입력")

    afternoon_text = st.text_area("오후 교양/차량배정 텍스트를 붙여넣기", height=200, key="txt_a")

    if st.button("① 오후 근무자 자동 추출", key="a_extract"):
        if not afternoon_text.strip():
            st.warning("텍스트를 입력해주세요.")
        else:
            staff_names = parse_staff_from_text(afternoon_text)
            st.session_state["a_staff_raw"] = staff_names
            st.success("오후 근무자 추출 완료")
            st.write("추출된 근무자:", ", ".join(staff_names))

    if "a_staff_raw" not in st.session_state:
        st.info("먼저 위에서 근무자 자동 추출을 해주세요.")
    else:
        st.subheader("✏ 오후 근무자 수정")
        df_a = {"근무자": st.session_state["a_staff_raw"]}
        edited_a = st.data_editor(df_a, num_rows="dynamic", key="a_staff_edit")
        final_staff_a = [nm for nm in edited_a["근무자"].dropna().tolist()]
        st.write("최종 근무자:", ", ".join(final_staff_a))

        # 수동 가능자 고정
        manual_list = ["권한솔", "김남균", "김성연", "김주현", "이호석", "조정래"]
        staff_objs_a = [Staff(name=nm, is_manual=(nm in manual_list)) for nm in final_staff_a]

        # 코스 담당자 (형식 통일용 — 실제 가중치에는 안 써도 되고, 원하면 is_course 사용 가능)
        st.markdown("### 🎯 코스 담당자 선택 (오후)")
        course_selected_a = st.multiselect(
            "오후 코스 담당자(복수 선택 가능)",
            options=final_staff_a,
            default=[],
            key="a_course_select",
        )
        for s in staff_objs_a:
            s.is_course = (s.name in course_selected_a)

        # 4·5교시 교양 담당자
        st.markdown("### 📕 4교시 교양 담당자 선택")
        edu4_options = ["(선택 없음)"] + final_staff_a
        edu4_selected = st.selectbox(
            "4교시 교양",
            options=edu4_options,
            key="a_edu4_select",
        )

        st.markdown("### 📗 5교시 교양 담당자 선택")
        edu5_options = ["(선택 없음)"] + final_staff_a
        edu5_selected = st.selectbox(
            "5교시 교양",
            options=edu5_options,
            key="a_edu5_select",
        )

        for s in staff_objs_a:
            s.is_edu = {k: False for k in range(1, 6)}
            if edu4_selected != "(선택 없음)" and s.name == edu4_selected:
                s.is_edu[4] = True
            if edu5_selected != "(선택 없음)" and s.name == edu5_selected:
                s.is_edu[5] = True

        # 수요 입력
        st.markdown("### 📊 수요 입력 (3·4·5교시)")
        c1, c2, c3 = st.columns(3)
        with c1:
            d3_1M = st.number_input("3교시 1종수동", min_value=0, key="a_3_1M")
            d3_1A = st.number_input("3교시 1종자동", min_value=0, key="a_3_1A")
            d3_2A = st.number_input("3교시 2종자동", min_value=0, key="a_3_2A")
            d3_2M = st.number_input("3교시 2종수동", min_value=0, key="a_3_2M")
        with c2:
            d4_1M = st.number_input("4교시 1종수동", min_value=0, key="a_4_1M")
            d4_1A = st.number_input("4교시 1종자동", min_value=0, key="a_4_1A")
            d4_2A = st.number_input("4교시 2종자동", min_value=0, key="a_4_2A")
            d4_2M = st.number_input("4교시 2종수동", min_value=0, key="a_4_2M")
        with c3:
            d5_1M = st.number_input("5교시 1종수동", min_value=0, key="a_5_1M")
            d5_1A = st.number_input("5교시 1종자동", min_value=0, key="a_5_1A")
            d5_2A = st.number_input("5교시 2종자동", min_value=0, key="a_5_2A")
            d5_2M = st.number_input("5교시 2종수동", min_value=0, key="a_5_2M")

        demand_a = {
            3: {"1M": d3_1M, "1A": d3_1A, "2A": d3_2A, "2M": d3_2M},
            4: {"1M": d4_1M, "1A": d4_1A, "2A": d4_2A, "2M": d4_2M},
            5: {"1M": d5_1M, "1A": d5_1A, "2A": d5_2A, "2M": d5_2M},
        }

        if st.button("가중치 0으로 초기화(오후)", key="a_reset_weight"):
            for s in staff_objs_a:
                s.load = 0.0
                s.need_low_next = False
                s.skipped_prev = False
            st.success("이 세션의 가중치를 초기화했습니다. (실행 시 다시 계산됨)")

        if st.button("② 오후 배정 실행", key="a_run"):
            # 3→4→5교시 순서
            for period in (3, 4, 5):
                assigned_detail, total = assign_one_period(
                    staff_list=staff_objs_a,
                    demand_dict=demand_a[period],
                    period=period,
                    is_morning=False,
                )

                data = {
                    "감독관": [],
                    "배정": [],
                    "총합": [],
                    "Load": [],
                }
                for s in staff_objs_a:
                    info = assigned_detail[s.name]
                    parts = []
                    for tc in ("1M", "1A", "2A", "2M"):
                        if info[tc] > 0:
                            parts.append(f"{TYPE_LABEL[tc]} {info[tc]}명")
                    data["감독관"].append(s.name)
                    data["배정"].append(" / ".join(parts) if parts else "0")
                    data["총합"].append(total[s.name])
                    data["Load"].append(round(s.load, 3))

                st.markdown(f"#### 🕒 {period}교시 결과")
                st.table(data)

            st.info("오후 3·4·5교시 배정이 완료되었습니다.")

###############################################
# 🎲 랜덤결과 탭
###############################################
with tab_r:
    st.subheader("🎲 최근 랜덤 혜택자(최저 배정자) 기록")

    hist = load_history()
    if not hist:
        st.info("랜덤 혜택자 기록이 없습니다.")
    else:
        # 최신순 정렬
        hist_sorted = sorted(hist, key=lambda x: x.get("date", ""), reverse=True)
        table = {
            "날짜": [],
            "이름": [],
            "교시": [],
            "역할": [],
        }
        for h in hist_sorted:
            table["날짜"].append(h.get("date", ""))
            table["이름"].append(h.get("name", ""))
            table["교시"].append(h.get("period", ""))
            table["역할"].append("혜택자(최저 배정자)" if h.get("role") == "beneficiary" else h.get("role", ""))

        st.table(table)

    if st.button("🗑 랜덤결과 초기화", key="rand_reset"):
        reset_history()
        st.success("랜덤 혜택자 기록을 초기화했습니다.")
