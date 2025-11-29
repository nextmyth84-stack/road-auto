##############################################################
# auto.py — 도로주행 자동 배정 (최종 수정: 코스 점검자 방어 로직 강화)
##############################################################

import streamlit as st
import json, os, re, random
from datetime import date, timedelta
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
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    staff.extend([x.strip() for x in m])

    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    staff.extend([x.strip() for x in m2])

    return list(dict.fromkeys(staff))

def parse_extra(text):
    edu = {}
    m = re.findall(r"(\d)교시\s*:\s*([가-힣]+)", text)
    for gyo, nm in m:
        edu[int(gyo)] = nm.strip()

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
        self.is_course = False
        self.is_edu_next = False  
        self.assigned_counts = {"1M":0, "1A":0, "2A":0, "2M":0}
        self.total_assigned = 0
        self.weight_val = 0 

##############################################################
# 히스토리 관리
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
# 배정 로직
##############################################################
def eligible(staff_obj, typecode):
    if staff_obj.is_manual:
        return True
    return typecode in ("1A", "2A")

def assign_logic(staff_names, period, demand, edu_map, course_list):
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
        # 규칙 7: 가중치 중복 적용 X (최대 1)
        if w > 1: w = 1
        s.weight_val = w

    CAP_MAP = {1:2, 2:3, 3:3, 4:3, 5:2}
    limit_per_person = CAP_MAP.get(period, 3)

    hist = load_history()
    if check_history_full(hist, staff_names):
        hist = [] 
        st.toast("🔄 랜덤 히스토리가 한 바퀴 돌아 초기화되었습니다.")

    order = [
        ("1M", demand["1M"]),
        ("1A", demand["1A"]),
        ("2A", demand["2A"]),
        ("2M", demand["2M"])
    ]

    for typecode, count in order:
        for _ in range(count):
            candidates = [
                s for s in staff_objs 
                if eligible(s, typecode) and s.total_assigned < limit_per_person
            ]

            if not candidates:
                st.error(f"🚨 배정 불가: {typecode} 수요를 감당할 인원이 없습니다.")
                break

            # [핵심 수정] 정렬 기준 강화
            def get_penalty_score(s):
                current_types = [t for t, c in s.assigned_counts.items() if c > 0]
                is_mixing = False
                if current_types and typecode not in current_types:
                    is_mixing = True
                
                mix_penalty = 1 if is_mixing else 0
                effective_load = s.total_assigned + s.weight_val + mix_penalty
                
                # (Load점수, 가중치값) 튜플 반환
                # 1순위: Load점수(낮은순), 2순위: 순수 가중치값(낮은순)
                # -> Load점수가 같으면 가중치(0)인 사람이 가중치(1)인 사람보다 우선됨
                return (effective_load, s.weight_val)

            candidates.sort(key=get_penalty_score)
            
            # 1등 그룹 추출 (튜플 비교)
            min_score_tuple = get_penalty_score(candidates[0])
            best_group = [c for c in candidates if get_penalty_score(c) == min_score_tuple]

            final_pick = None
            not_lucky_group = [c for c in best_group if not is_lucky_recently(hist, c.name)]
            
            if not_lucky_group:
                final_pick = random.choice(not_lucky_group)
            else:
                final_pick = random.choice(best_group)
            
            final_pick.assigned_counts[typecode] += 1
            final_pick.total_assigned += 1

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

# --- 오전 탭 ---
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
        m_course_real = st.multiselect("코스 담당자", final_m_staff, default=[x for x in st.session_state["m_course"] if x in final_m_staff])
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
            disabled=(target_edu_p==0),
            key=f"m_edu_sel_{period_m}" 
        )
        
    st.subheader("수요 입력")
    c1, c2, c3, c4 = st.columns(4)
    d_m = {
        "1M": c1.number_input("1종수동", 0, 10, 0, key="m1m"),
        "1A": c2.number_input("1종자동", 0, 20, 0, key="m1a"),
        "2A": c3.number_input("2종자동", 0, 20, 0, key="m2a"),
        "2M": c4.number_input("2종수동", 0, 10, 0, key="m2m")
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
                if v > 0: details.append(f"{k}:{v}")
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


# --- 오후 탭 ---
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
        a_course_real = st.multiselect("코스 담당자", final_a_staff, default=[x for x in st.session_state["a_course"] if x in final_a_staff], key="a_crs")
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
            disabled=(target_edu_p_a==0), 
            key=f"a_edu_sel_{period_a}"
        )

    st.subheader("수요 입력")
    c1a, c2a, c3a, c4a = st.columns(4)
    d_a = {
        "1M": c1a.number_input("1종수동", 0, 10, 0, key="a1m"),
        "1A": c2a.number_input("1종자동", 0, 20, 0, key="a1a"),
        "2A": c3a.number_input("2종자동", 0, 20, 0, key="a2a"),
        "2M": c4a.number_input("2종수동", 0, 10, 0, key="a2m")
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
                if v > 0: details.append(f"{k}:{v}")
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

# --- 관리 탭 ---
with tab3:
    st.header("데이터 관리")
    col_reset, col_view = st.columns(2)
    with col_reset:
        if st.button("🗑️ 랜덤 히스토리 초기화", type="secondary"):
            save_history([])
            st.warning("모든 랜덤 기록이 초기화되었습니다.")
            st.rerun()
            
    st.subheader("현재 랜덤 히스토리 (혜택 받은 사람 목록)")
    hist_data = load_history()
    if hist_data:
        st.dataframe(pd.DataFrame(hist_data))
    else:
        st.info("기록이 없습니다.")
