##############################################################
# auto.py — 도로주행 자동 배정 (User Rules Complete Ver.)
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
    # 1종수동 : 00호 홍길동
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    staff.extend([x.strip() for x in m])

    # • 00호 홍길동 (불릿 포인트)
    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    staff.extend([x.strip() for x in m2])

    # 중복 제거 후 리스트 반환
    return list(dict.fromkeys(staff))

def parse_extra(text):
    edu = {}
    # 1교시 : 홍길동
    m = re.findall(r"(\d)교시\s*:\s*([가-힣]+)", text)
    for gyo, nm in m:
        edu[int(gyo)] = nm.strip()

    course = []
    # 코스점검 : A코스 홍길동 ...
    m2 = re.findall(r"코스점검\s*:\s*(.*)", text)
    if m2:
        body = m2[0]
        # A코스 : 홍길동
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
        
        # 속성
        self.is_course = False
        self.is_edu_next = False  # 다음 교시 교양 여부

        # 배정 상태
        self.assigned_counts = {"1M":0, "1A":0, "2A":0, "2M":0}
        self.total_assigned = 0
        
        # 가중치 (가상의 배정 수)
        self.weight_val = 0 

##############################################################
# 히스토리 관리 (최근 3일, 적게 배정된 사람 기록)
##############################################################
def load_history():
    raw = load_json(HISTORY_FILE, [])
    # 날짜 기준 3일 이내만 필터링
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
    # 히스토리에 있는 사람들의 집합
    hist_names = {h["name"] for h in hist}
    current_set = set(current_staff_names)
    # 현재 근무자 전원이 히스토리에 있으면 초기화 대상
    return current_set.issubset(hist_names)

def is_lucky_recently(hist, name):
    # 최근에 '적게 배정(혜택)' 받은 기록이 있는가?
    for h in hist:
        if h["name"] == name:
            return True
    return False

##############################################################
# 배정 로직 (핵심)
##############################################################

def eligible(staff_obj, typecode):
    # 규칙 9: 수동 가능자는 모두 가능, 그 외는 1A/2A만
    if staff_obj.is_manual:
        return True
    return typecode in ("1A", "2A")

def assign_logic(staff_names, period, demand, edu_map, course_list):
    # 1. Staff 객체 생성
    staff_objs = [Staff(nm) for nm in staff_names]
    
    # 2. 가중치(Weight) 계산 - 규칙 3, 4, 5, 7
    #    다음 교시 교양자 찾기
    target_edu_period = None
    if period == 1: target_edu_period = 2
    elif period == 3: target_edu_period = 4
    elif period == 4: target_edu_period = 5
    
    next_edu_name = edu_map.get(target_edu_period)

    for s in staff_objs:
        w = 0
        # 코스 담당자
        if s.name in course_list:
            w += 1
        # 다음 교시 교양자
        if next_edu_name and s.name == next_edu_name:
            w += 1
        
        # 규칙 7: 중복 적용 X (최대 1)
        if w > 1: w = 1
        s.weight_val = w

    # 3. Cap 설정 - 규칙 2
    # 1:2, 2:3, 3:3, 4:3, 5:2
    CAP_MAP = {1:2, 2:3, 3:3, 4:3, 5:2}
    limit_per_person = CAP_MAP.get(period, 3)

    # 4. 히스토리 로드 & 초기화 체크 - 규칙 10
    hist = load_history()
    if check_history_full(hist, staff_names):
        hist = [] # 메모리상 초기화 (저장은 나중에)
        st.toast("🔄 랜덤 히스토리가 한 바퀴 돌아 초기화되었습니다.")

    # 5. 배정 순서 (수요 루프)
    #    일반적으로 까다로운 수동 -> 자동 순으로 배정
    order = [
        ("1M", demand["1M"]),
        ("1A", demand["1A"]),
        ("2A", demand["2A"]),
        ("2M", demand["2M"])
    ]

    # 6. 수요 1명씩 채우기 (Iterative) - 규칙 1 (배정차이 최소화)
    for typecode, count in order:
        for _ in range(count):
            # A. 자격 되고 & Cap 남은 후보 필터링
            candidates = [
                s for s in staff_objs 
                if eligible(s, typecode) and s.total_assigned < limit_per_person
            ]

            if not candidates:
                st.error(f"🚨 배정 불가: {typecode} 수요를 감당할 인원이 없습니다.")
                break

            # B. 정렬 키 함수 (Penalty 점수가 낮을수록 우선 배정)
            def get_penalty_score(s):
                # (1) 섞임 방지/가중치 (규칙 5)
                #     이미 다른 종별을 가지고 있는데, 현재 typecode와 다르다면? -> 섞임(Mixing)
                #     섞임이 발생하면 가중치(+1)를 부여하여 후순위로 밀어버림
                current_types = [t for t, c in s.assigned_counts.items() if c > 0]
                is_mixing = False
                if current_types and typecode not in current_types:
                    is_mixing = True
                
                mix_penalty = 1 if is_mixing else 0
                
                # (2) 총 부하 (Total Load) + 기본 가중치 (Weight)
                #     규칙 6: 가중치 적용 시 배정수 적게 받는 그룹에 포함됨
                #     -> (실제배정 + 가중치)가 기준이 됨.
                effective_load = s.total_assigned + s.weight_val + mix_penalty
                
                return effective_load

            # C. 최우선 그룹 찾기
            candidates.sort(key=get_penalty_score)
            min_score = get_penalty_score(candidates[0])
            best_group = [c for c in candidates if get_penalty_score(c) == min_score]

            # D. 동점자 처리 (랜덤) - 규칙 10
            #    히스토리에 있는(최근 혜택받은) 사람은 배제 시도
            final_pick = None
            
            # 히스토리에 없는 사람(혜택 못받은 사람) 우선
            not_lucky_group = [c for c in best_group if not is_lucky_recently(hist, c.name)]
            
            if not_lucky_group:
                final_pick = random.choice(not_lucky_group)
            else:
                # 전원 히스토리에 있으면 그냥 그 중에서 랜덤
                final_pick = random.choice(best_group)
            
            # 배정 확정
            final_pick.assigned_counts[typecode] += 1
            final_pick.total_assigned += 1

    # 7. 결과 처리 & 히스토리 업데이트 - 규칙 10
    #    "한 교시 처리 후 적게 배정된 사람들(Lucky)을 기록"
    if staff_objs:
        min_assigned = min(s.total_assigned for s in staff_objs)
        # 배정된 인원이 0명인 경우는 제외하고 싶은 경우: if min_assigned > 0 조건 추가 가능
        # 하지만 규칙상 "적게 배정된" 이므로 0명도 포함 (휴식자)
        lucky_people = [s.name for s in staff_objs if s.total_assigned == min_assigned]
        
        # 이번에 혜택받은 사람들을 히스토리에 추가
        today_str = date.today().isoformat()
        for name in lucky_people:
            # 중복 방지: 오늘 날짜로 이미 있으면 패스 (혹은 그냥 추가해도 로직상 무방)
            hist.append({"date": today_str, "name": name, "type": "min_load"})
        
        save_history(hist)

    return staff_objs, hist

##############################################################
# 페어링 문자열 생성 - 규칙 11
##############################################################
def make_pairing_text(staff_objs):
    # 1개 배정자, 0개 배정자 분리
    ones = [s.name for s in staff_objs if s.total_assigned == 1]
    zeros = [s.name for s in staff_objs if s.total_assigned == 0]
    multi = [f"{s.name}({s.total_assigned}명)" for s in staff_objs if s.total_assigned > 1]
    
    pairs = []
    
    # 1) 다수 배정자는 별도 표기보다는 그냥 둠 (규칙 11은 1명, 0명 처리에 집중)
    #    하지만 결과 출력엔 포함되어야 하므로.
    
    # 2) 1명 배정자끼리 묶기
    while len(ones) >= 2:
        p1 = ones.pop(0)
        p2 = ones.pop(0)
        pairs.append(f"{p1} - {p2}")
        
    # 3) 1명 남았을 때 -> 0명(참관)과 매칭
    if ones:
        p1 = ones.pop(0)
        if zeros:
            z = zeros.pop(0)
            pairs.append(f"{p1} - {z}(참관)")
        else:
            pairs.append(f"{p1} - (단독)")
            
    # 4) 남은 0명들
    for z in zeros:
        pairs.append(f"{z}(참관)")
        
    # 다수 배정자 정보도 보여주기 위해
    if multi:
        return multi + pairs
    return pairs

##############################################################
# UI 구성
##############################################################
tab1, tab2, tab3 = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 데이터 관리"])

# 세션 스테이트 초기화
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

    # 근무자 편집
    st.subheader("근무자 및 담당 확인")
    m_df = pd.DataFrame({"이름": st.session_state["m_staff"]})
    edited_m = st.data_editor(m_df, num_rows="dynamic", heading_fixed=True, key="editor_m")
    final_m_staff = edited_m["이름"].dropna().unique().tolist()
    
    # 옵션 설정
    col_c, col_e = st.columns(2)
    with col_c:
        m_course_real = st.multiselect("코스 담당자", final_m_staff, default=[x for x in st.session_state["m_course"] if x in final_m_staff])
    with col_e:
        # 1교시면 2교시 교양자가 필요
        target_edu_p = 2 if period_m == 1 else 0
        def_idx = 0
        edu_cand = st.session_state["m_edu"].get(target_edu_p)
        if edu_cand in final_m_staff:
            def_idx = final_m_staff.index(edu_cand) + 1
            
        m_edu_real = st.selectbox(f"{target_edu_p}교시 교양 담당자 (가중치 대상)", ["없음"] + final_m_staff, index=def_idx, disabled=(target_edu_p==0))
        
    st.subheader("수요 입력")
    c1, c2, c3, c4 = st.columns(4)
    d_m = {
        "1M": c1.number_input("1종수동", 0, 10, 0, key="m1m"),
        "1A": c2.number_input("1종자동", 0, 20, 0, key="m1a"),
        "2A": c3.number_input("2종자동", 0, 20, 0, key="m2a"),
        "2M": c4.number_input("2종수동", 0, 10, 0, key="m2m")
    }
    
    if st.button("2. 오전 배정 실행", type="primary"):
        # 교양 맵 구성
        edu_map_input = {}
        if target_edu_p > 0 and m_edu_real != "없음":
            edu_map_input[target_edu_p] = m_edu_real
            
        results, _ = assign_logic(final_m_staff, period_m, d_m, edu_map_input, m_course_real)
        
        st.divider()
        st.subheader(f"📋 {period_m}교시 배정 결과")
        
        # 결과 테이블
        res_data = []
        for s in results:
            # 배정 내역 문자열
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
        
        st.subheader("🤝 페어링 (규칙 11)")
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

    # 근무자 편집
    st.subheader("근무자 및 담당 확인")
    a_df = pd.DataFrame({"이름": st.session_state["a_staff"]})
    edited_a = st.data_editor(a_df, num_rows="dynamic", heading_fixed=True, key="editor_a")
    final_a_staff = edited_a["이름"].dropna().unique().tolist()
    
    # 옵션 설정
    col_c2, col_e2 = st.columns(2)
    with col_c2:
        a_course_real = st.multiselect("코스 담당자", final_a_staff, default=[x for x in st.session_state["a_course"] if x in final_a_staff], key="a_crs")
    with col_e2:
        # 3->4, 4->5 가중치
        target_edu_p_a = 0
        if period_a == 3: target_edu_p_a = 4
        elif period_a == 4: target_edu_p_a = 5
        
        def_idx_a = 0
        edu_cand_a = st.session_state["a_edu"].get(target_edu_p_a)
        if edu_cand_a in final_a_staff:
            def_idx_a = final_a_staff.index(edu_cand_a) + 1
            
        a_edu_real = st.selectbox(f"{target_edu_p_a}교시 교양 담당자 (가중치 대상)", ["없음"] + final_a_staff, index=def_idx_a, disabled=(target_edu_p_a==0), key="a_edu")

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
        
        st.subheader("🤝 페어링 (규칙 11)")
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
