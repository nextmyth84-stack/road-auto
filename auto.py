##############################################################
# auto.py — 도로주행 자동 배정 (Quota+타입 2단계 최적화 버전)
# - 교시별 cap(1:2,2:3,3:3,4:3,5:2)
# - 배정 수 차이 최대 1 (가능한 범위에서 강제)
# - 코스/다음 교시 교양 가중치
# - 종별 섞임(특히 수동/자동 혼합) 최소화
# - 랜덤 히스토리 (최근 3일 min_load)
##############################################################

import streamlit as st
import json, os, re, random
from datetime import date
import pandas as pd

st.set_page_config(page_title="도로주행 자동 배정", layout="wide")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(DATA_DIR, "random_history.json")

##############################################################
# JSON LOAD / SAVE
##############################################################
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

##############################################################
# 수동 가능자 세팅
##############################################################
MANUAL_SET = {
    "권한솔","김남균","김성연",
    "김주현","이호석","조정래"
}

##############################################################
# 텍스트 파싱 함수
##############################################################
def parse_staff(text):
    staff = []
    # 1종수동 : 00호 홍길동
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    staff.extend([x.strip() for x in m])

    # • 00호 홍길동
    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    staff.extend([x.strip() for x in m2])

    # 중복 제거
    return list(dict.fromkeys(staff))

def parse_extra(text):
    edu = {}
    # 1교시 : 홍길동
    m = re.findall(r"(\d)교시\s*:\s*([가-힣]+)", text)
    for gyo, nm in m:
        edu[int(gyo)] = nm.strip()

    # 코스점검 : A코스 홍길동 ...
    course = []
    m2 = re.findall(r"코스점검\s*:\s*(.*)", text)
    if m2:
        body = m2[0]
        mm = re.findall(r"[A-Z]코스.*?:\s*([가-힣]+)", body)
        course = [x.strip() for x in mm]

    return edu, course

##############################################################
# Staff Class
##############################################################
class Staff:
    def __init__(self, name):
        self.name = name
        self.is_manual = (name in MANUAL_SET)

        # 배정 정보
        self.assigned_counts = {"1M":0, "1A":0, "2A":0, "2M":0}
        self.total_assigned = 0  # 이번 교시 내 총 배정수
        self.weight_val = 0      # 코스/다음 교시 교양 가중치 (0 또는 1)

##############################################################
# 히스토리 관리 (최근 3일, min_load 기록)
##############################################################
def load_history():
    raw = load_json(HISTORY_FILE, [])
    today = date.today()
    valid = []
    for h in raw:
        try:
            d = date.fromisoformat(h["date"])
            if (today - d).days <= 3:
                valid.append(h)
        except:
            pass
    return valid

def save_history(hist):
    save_json(HISTORY_FILE, hist)

def check_history_full(hist, current_staff_names):
    hist_names = {h["name"] for h in hist}
    current_set = set(current_staff_names)
    return current_set.issubset(hist_names)

def is_lucky_recently(hist, name):
    for h in hist:
        if h["name"] == name:
            return True
    return False

##############################################################
# 자격 / 변속기
##############################################################
def eligible(staff_obj, typecode):
    # 수동 가능자는 모든 종 가능, 그 외는 1A/2A만
    if staff_obj.is_manual:
        return True
    return typecode in ("1A", "2A")

def get_transmission_type(typecode):
    if "M" in typecode:
        return "Manual"
    if "A" in typecode:
        return "Auto"
    return "Unknown"

##############################################################
# 1단계: quota 계산 (공평성 + cap)
##############################################################
CAP_MAP = {1:2, 2:3, 3:3, 4:3, 5:2}

def compute_quota(staff_objs, period, total_demand, hist):
    """
    이번 교시(period)에 각 감독관이 맡을 총 차량 수(quota)를 계산.
    - 배정 수 차이 최대 1 보장 (가능한 경우)
    - 교시별 cap(1:2,2:3,3:3,4:3,5:2) 적용
    """
    m = len(staff_objs)
    if m == 0 or total_demand == 0:
        return [0] * m, 0

    cap = CAP_MAP.get(period, 3)
    max_possible = m * cap
    assignable = min(total_demand, max_possible)

    base = assignable // m
    rem = assignable % m

    quotas = [base] * m  # 일단 모두 base

    # 남은 rem명을 한 명씩 +1 (cap 이내)
    for _ in range(rem):
        candidates = [i for i in range(m) if quotas[i] < cap]
        if not candidates:
            break

        min_q = min(quotas[i] for i in candidates)
        tied = [i for i in candidates if quotas[i] == min_q]

        # 최근 3일 '행운' 기록 없는 사람 우선
        non_lucky = [i for i in tied if not is_lucky_recently(hist, staff_objs[i].name)]
        pick_pool = non_lucky if non_lucky else tied

        pick = random.choice(pick_pool)
        quotas[pick] += 1

    return quotas, assignable

##############################################################
# 2단계: quota 안에서 타입(1M/1A/2A/2M) 배정
##############################################################
def assign_types_within_quota(staff_objs, period, quotas, demand):
    """
    이미 정해진 quota 안에서 종별/섞임/자격/가중치를 고려해 타입 배정.
    - quota[i] 개수 이내에서만 배정 → 공평성 유지
    """
    type_order = ["1M", "1A", "2A", "2M"]
    remaining_quota = quotas[:]
    total_before = sum(demand.values())

    def type_score(staff, tcode):
        # 1) 섞임 페널티
        current_types = [k for k, v in staff.assigned_counts.items() if v > 0]
        mix_penalty = 0
        new_tr = get_transmission_type(tcode)

        if current_types:
            existing_trs = {get_transmission_type(ct) for ct in current_types}
            if tcode in current_types:
                mix_penalty = 0         # 같은 종별
            else:
                if new_tr in existing_trs:
                    mix_penalty = 1     # 같은 변속기 다른 종 (허용)
                else:
                    mix_penalty = 10    # Manual vs Auto 혼합 (최대한 회피)

        # 2) 남은 수요가 큰 타입 우선 (tail 줄이기) → 음수로(적을수록 좋음)
        demand_penalty = -demand[tcode]

        # 3) 가중치(코스/교양)는 동점 시 일반인 먼저 뽑도록 마지막에 둠
        weight_penalty = staff.weight_val

        return (mix_penalty, demand_penalty, weight_penalty)

    while True:
        progress = False

        for i, s in enumerate(staff_objs):
            if remaining_quota[i] <= 0:
                continue

            # 이 감독관이 받을 수 있는 타입 후보
            candidates = [
                t for t in type_order
                if demand.get(t, 0) > 0 and eligible(s, t)
            ]
            if not candidates:
                continue

            best_t = min(candidates, key=lambda t: type_score(s, t))

            # 배정 실행
            s.assigned_counts[best_t] += 1
            s.total_assigned += 1
            remaining_quota[i] -= 1
            demand[best_t] -= 1
            progress = True

        if not progress:
            break
        if sum(demand.values()) <= 0:
            break

    total_assigned = sum(s.total_assigned for s in staff_objs)
    if total_assigned < total_before:
        st.warning(
            f"⚠️ 전체 수요 {total_before}명 중 {total_assigned}명만 배정되었습니다. "
            "수동/자동 자격 및 종별 조합 제한으로 모든 수요를 채우지 못했습니다."
        )

    return staff_objs

##############################################################
# assign_logic 통합 (2단계 호출)
##############################################################
def assign_logic(staff_names, period, demand, edu_map, course_list):
    # 0) Staff 객체 및 가중치 세팅
    staff_objs = [Staff(nm) for nm in staff_names]

    target_edu_period = None
    if period == 1: target_edu_period = 2
    elif period == 3: target_edu_period = 4
    elif period == 4: target_edu_period = 5

    next_edu_name = edu_map.get(target_edu_period)

    for s in staff_objs:
        w = 0
        if s.name in course_list:
            w += 1
        if next_edu_name and s.name == next_edu_name:
            w += 1
        if w > 1: w = 1
        s.weight_val = w

    total_demand = sum(demand.values())
    hist = load_history()
    if check_history_full(hist, staff_names):
        hist = []
        st.toast("🔄 랜덤 히스토리가 한 바퀴 돌아 초기화되었습니다.")

    # 1단계: quota 계산
    quotas, assignable = compute_quota(staff_objs, period, total_demand, hist)
    if assignable < total_demand:
        st.error(
            f"🚨 이 교시 최대 처리 가능 인원({assignable}명)을 초과하는 수요({total_demand}명)가 있습니다. "
            "근무자 수 또는 교시별 최대 배정 인원을 확인하세요."
        )

    # 2단계: quota 안에서 타입 배정
    demand_copy = dict(demand)
    staff_objs = assign_types_within_quota(staff_objs, period, quotas, demand_copy)

    # 히스토리 업데이트 (이번 교시에서 가장 적게 배정된 사람들)
    if staff_objs:
        min_assigned = min(s.total_assigned for s in staff_objs)
        lucky_people = [s.name for s in staff_objs if s.total_assigned == min_assigned]
        today_str = date.today().isoformat()
        for name in lucky_people:
            hist.append({"date": today_str, "name": name, "type": "min_load"})
        save_history(hist)

    return staff_objs, hist

##############################################################
# 페어링 문자열
##############################################################
def make_pairing_text(staff_objs):
    ones = [s.name for s in staff_objs if s.total_assigned == 1]
    zeros = [s.name for s in staff_objs if s.total_assigned == 0]
    multi = [f"{s.name}({s.total_assigned}명)" for s in staff_objs if s.total_assigned > 1]

    pairs = []
    while len(ones) >= 2:
        p1 = ones.pop(0)
        p2 = ones.pop(0)
        pairs.append(f"{p1} - {p2}")

    if ones:
        p1 = ones.pop(0)
        if zeros:
            z = zeros.pop(0)
            pairs.append(f"{p1} - {z}(참관)")
        else:
            pairs.append(f"{p1} - (단독)")

    for z in zeros:
        pairs.append(f"{z}(참관)")

    if multi:
        return multi + pairs
    return pairs

##############################################################
# UI 구성
##############################################################
tab1, tab2, tab3 = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 데이터 관리"])

if "m_staff" not in st.session_state: st.session_state["m_staff"] = []
if "a_staff" not in st.session_state: st.session_state["a_staff"] = []
if "m_edu" not in st.session_state: st.session_state["m_edu"] = {}
if "a_edu" not in st.session_state: st.session_state["a_edu"] = {}
if "m_course" not in st.session_state: st.session_state["m_course"] = []
if "a_course" not in st.session_state: st.session_state["a_course"] = []

##############################################################
# 오전 탭
##############################################################
with tab1:
    st.header("오전 배정 (1, 2교시)")
    col_txt, col_opt = st.columns([3, 1])
    with col_txt:
        txt_m = st.text_area("오전 근무자/코스 텍스트 붙여넣기", height=150)
    with col_opt:
        period_m = st.radio("오전 교시", [1, 2], index=0, horizontal=True)

    if st.button("1. 텍스트 분석", key="btn_m_parse"):
        st.session_state["m_staff"] = parse_staff(txt_m)
        e, c = parse_extra(txt_m)
        st.session_state["m_edu"] = e
        st.session_state["m_course"] = c
        st.success(f"근무자 {len(st.session_state['m_staff'])}명 추출 완료")

    st.subheader("근무자 및 담당 확인")
    m_df = pd.DataFrame({"이름": st.session_state["m_staff"]})
    edited_m = st.data_editor(m_df, num_rows="dynamic", key="editor_m")
    final_m_staff = edited_m["이름"].dropna().unique().tolist()

    col_c, col_e = st.columns(2)
    with col_c:
        m_course_real = st.multiselect(
            "코스 담당자",
            final_m_staff,
            default=[x for x in st.session_state["m_course"] if x in final_m_staff]
        )
    with col_e:
        target_edu_p = 2 if period_m == 1 else 0
        def_idx = 0
        edu_cand = st.session_state["m_edu"].get(target_edu_p)
        if edu_cand in final_m_staff:
            def_idx = final_m_staff.index(edu_cand) + 1

        m_edu_real = st.selectbox(
            f"{target_edu_p}교시 교양 담당자 (가중치 대상)",
            ["없음"] + final_m_staff,
            index=def_idx,
            disabled=(target_edu_p == 0),
            key=f"m_edu_sel_{period_m}"
        )

    st.subheader("수요 입력")
    c1, c2, c3, c4 = st.columns(4)
    d_m = {
        "1M": c1.number_input("1종수동", 0, 20, 0, key="m1m"),
        "1A": c2.number_input("1종자동", 0, 40, 0, key="m1a"),
        "2A": c3.number_input("2종자동", 0, 40, 0, key="m2a"),
        "2M": c4.number_input("2종수동", 0, 20, 0, key="m2m")
    }

    if st.button("2. 오전 배정 실행", type="primary"):
        edu_map_input = {}
        if target_edu_p > 0 and m_edu_real != "없음":
            edu_map_input[target_edu_p] = m_edu_real

        results, _ = assign_logic(final_m_staff, period_m, d_m, edu_map_input, m_course_real)

        st.divider()
        st.subheader(f"📋 {period_m}교시 배정 결과")
        res_data = []
        for s in results:
            details = []
            for k, v in s.assigned_counts.items():
                if v > 0:
                    details.append(f"{k}:{v}")
            res_data.append({
                "이름": s.name,
                "총 배정": s.total_assigned,
                "상세": ", ".join(details) if details else "-",
                "비고": "가중치 적용" if s.weight_val > 0 else ""
            })
        st.dataframe(pd.DataFrame(res_data))

        st.subheader("🤝 페어링")
        pairs = make_pairing_text(results)
        for p in pairs:
            st.markdown(f"- **{p}**")

##############################################################
# 오후 탭
##############################################################
with tab2:
    st.header("오후 배정 (3, 4, 5교시)")
    col_txt2, col_opt2 = st.columns([3, 1])
    with col_txt2:
        txt_a = st.text_area("오후 근무자/코스 텍스트 붙여넣기", height=150)
    with col_opt2:
        period_a = st.radio("오후 교시", [3, 4, 5], index=0, horizontal=True)

    if st.button("1. 텍스트 분석", key="btn_a_parse"):
        st.session_state["a_staff"] = parse_staff(txt_a)
        e, c = parse_extra(txt_a)
        st.session_state["a_edu"] = e
        st.session_state["a_course"] = c
        st.success(f"근무자 {len(st.session_state['a_staff'])}명 추출 완료")

    st.subheader("근무자 및 담당 확인")
    a_df = pd.DataFrame({"이름": st.session_state["a_staff"]})
    edited_a = st.data_editor(a_df, num_rows="dynamic", key="editor_a")
    final_a_staff = edited_a["이름"].dropna().unique().tolist()

    col_c2, col_e2 = st.columns(2)
    with col_c2:
        a_course_real = st.multiselect(
            "코스 담당자",
            final_a_staff,
            default=[x for x in st.session_state["a_course"] if x in final_a_staff],
            key="a_crs"
        )
    with col_e2:
        target_edu_p_a = 0
        if period_a == 3: target_edu_p_a = 4
        elif period_a == 4: target_edu_p_a = 5

        def_idx_a = 0
        edu_cand_a = st.session_state["a_edu"].get(target_edu_p_a)
        if edu_cand_a in final_a_staff:
            def_idx_a = final_a_staff.index(edu_cand_a) + 1

        a_edu_real = st.selectbox(
            f"{target_edu_p_a}교시 교양 담당자 (가중치 대상)",
            ["없음"] + final_a_staff,
            index=def_idx_a,
            disabled=(target_edu_p_a == 0),
            key=f"a_edu_sel_{period_a}"
        )

    st.subheader("수요 입력")
    c1a, c2a, c3a, c4a = st.columns(4)
    d_a = {
        "1M": c1a.number_input("1종수동", 0, 20, 0, key="a1m"),
        "1A": c2a.number_input("1종자동", 0, 40, 0, key="a1a"),
        "2A": c3a.number_input("2종자동", 0, 40, 0, key="a2a"),
        "2M": c4a.number_input("2종수동", 0, 20, 0, key="a2m")
    }

    if st.button("2. 오후 배정 실행", type="primary"):
        edu_map_input_a = {}
        if target_edu_p_a > 0 and a_edu_real != "없음":
            edu_map_input_a[target_edu_p_a] = a_edu_real

        results_a, _ = assign_logic(final_a_staff, period_a, d_a, edu_map_input_a, a_course_real)

        st.divider()
        st.subheader(f"📋 {period_a}교시 배정 결과")
        res_data_a = []
        for s in results_a:
            details = []
            for k, v in s.assigned_counts.items():
                if v > 0:
                    details.append(f"{k}:{v}")
            res_data_a.append({
                "이름": s.name,
                "총 배정": s.total_assigned,
                "상세": ", ".join(details) if details else "-",
                "비고": "가중치 적용" if s.weight_val > 0 else ""
            })
        st.dataframe(pd.DataFrame(res_data_a))

        st.subheader("🤝 페어링")
        pairs_a = make_pairing_text(results_a)
        for p in pairs_a:
            st.markdown(f"- **{p}**")

##############################################################
# 관리/랜덤 탭
##############################################################
with tab3:
    st.header("랜덤 히스토리 / 관리")

    col_reset, col_view = st.columns(2)
    with col_reset:
        if st.button("🗑️ 랜덤 히스토리 초기화", type="secondary"):
            save_history([])
            st.warning("모든 랜덤 기록이 초기화되었습니다.")
            st.rerun()

    st.subheader("현재 랜덤 히스토리 (최근 3일)")
    hist_data = load_history()
    if hist_data:
        st.dataframe(pd.DataFrame(hist_data))
    else:
        st.info("기록이 없습니다.")
