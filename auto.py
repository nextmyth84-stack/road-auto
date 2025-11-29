##############################################################
# auto.py — 도로주행 자동 배정 (최종 통합판 - 종별 섞임 최후 순위)
# 공평성 모델 + 코스/교양/섞임(최후순위) 가중치 + 랜덤 3일 제외 + pairing
# [수정] 규칙 3,4 적용 + session_state 키 최적화 + UTF-8 한글 완벽 지원
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
# JSON LOAD / SAVE (히스토리 날짜 검증)
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

def load_history():
    hist = load_json(HISTORY_FILE, [])
    valid_hist = []
    for h in hist:
        try:
            date.fromisoformat(h["date"])
            valid_hist.append(h)
        except:
            continue
    return valid_hist

def save_history(hist):
    save_json(HISTORY_FILE, hist)

##############################################################
# 수동 가능자 (규칙 9)
##############################################################
MANUAL_SET = {
    "권한솔","김남균","김성연",
    "김주현","이호석","조정래"
}

##############################################################
# 텍스트 파싱
##############################################################
def parse_staff(text):
    staff = re.findall(r"1종수동s*:s*[d]+호s*([가-힣]+)|•s*[d]+호s*([가-힣]+)", text)
    return list({name for match in staff for name in match if name})

def parse_extra(text):
    edu = {}
    m = re.findall(r"(d)교시s*:s*([가-힣]+)", text)
    for gyo, nm in m:
        edu[int(gyo)] = nm.strip()

    course = []
    m2 = re.findall(r"코스점검s*:s*(.*)", text)
    if m2:
        body = m2[0]
        mm = re.findall(r"[A-Z]코스.*?:s*([가-힣]+)", body)
        course = [x.strip() for x in mm]
    return edu, course

##############################################################
# Staff 클래스
##############################################################
class Staff:
    def __init__(self, name):
        self.name = name
        self.is_manual = (name in MANUAL_SET)
        self.is_course = False
        self.is_edu = {i:False for i in range(1,6)}
        self.load = 0.0
        self.course_penalty_next = False 
        self.is_mixed_today = False 

##############################################################
# 랜덤 히스토리 (규칙 10)
##############################################################
def used_recently(hist, name):
    today = date.today()
    for h in hist:
        d = date.fromisoformat(h["date"])
        if h.get("type") == "random_pick" and (today - d).days <= 3 and h["name"] == name:
            return True
    return False

def record_random(hist, name, period):
    hist.append({
        "date": date.today().isoformat(),
        "name": name,
        "period": period,
        "type": "random_pick"
    })

def check_history_full(hist, staff_names):
    recent_names = {h["name"] for h in hist if h.get("type") == "random_pick"}
    return recent_names.issuperset(set(staff_names))

def clear_history_if_full(hist, staff_names):
    if check_history_full(hist, staff_names):
        st.warning("랜덤 히스토리 초기화: 전체 인원이 한 번씩 랜덤 배정되어 기록을 초기화합니다.")
        st.rerun()
        hist.clear()
        return True
    return False

##############################################################
# 자격 체크 (규칙 9)
##############################################################
def eligible(st, typecode):
    if st.is_manual:
        return True
    return typecode in ("1A", "2A")

##############################################################
# 가중치 적용 (규칙 3,4,7 수정 완료)
##############################################################
def apply_weights(staff, period):
    for i, s in enumerate(staff):
        w = 0
        # 1교시 코스 담당자만 (규칙 3)
        if period == 1 and s.is_course:
            w += 1
        # 다음 교시 교양자 (1→2, 3→4, 4→5)
        target_edu_period = None
        if period == 1: target_edu_period = 2
        elif period == 3: target_edu_period = 4
        elif period == 4: target_edu_period = 5
        if target_edu_period and s.is_edu.get(target_edu_period):
            w += 1
        # 중복 최대 1 (규칙 7)
        if w > 1:
            w = 1
        s.load += w

##############################################################
# 랜덤 선택
##############################################################
def pick_random_candidate(staff, idx_list, period, hist):
    filtered = [i for i in idx_list if not used_recently(hist, staff[i].name)]
    if filtered:
        pick = random.choice(filtered)
    else:
        pick = random.choice(idx_list)
    record_random(hist, staff[pick].name, period)
    return pick

##############################################################
# 한 교시 배정 (규칙 1-6, 무한루프 방지)
##############################################################
def assign_period(staff, period, demand, is_morning):
    BASE_CAP_MAP = {1: 2, 2: 3, 3: 3, 4: 3, 5: 2}
    base_cap = BASE_CAP_MAP.get(period, 3)

    n = len(staff)
    staff_names = [s.name for s in staff]
    
    hist = load_history()
    clear_history_if_full(hist, staff_names)
    
    # Load 초기화 (규칙 4: 해당 교시에서만)
    for s in staff:
        s.load = 0.0
    
    if period != 2:
        for s in staff:
            s.course_penalty_next = False
    for s in staff:
        s.is_mixed_today = False
    
    apply_weights(staff, period)
    
    assigned = [{"1M":0,"1A":0,"2A":0,"2M":0} for _ in range(n)]
    total = [0]*n
    
    total_demand = sum(demand.values())
    if total_demand == 0:
        save_history(hist)
        return assigned, total
        
    target_base = total_demand // n
    target_rem = total_demand % n
    
    staff_indices_sorted = sorted(range(n), key=lambda i: staff[i].load)
    target_assignment = [target_base] * n
    for i in staff_indices_sorted[:target_rem]:
        target_assignment[i] += 1
    for i in range(n):
        if target_assignment[i] > base_cap:
            target_assignment[i] = base_cap
    
    order = [("1M", demand.get("1M",0)), ("1A", demand.get("1A",0)), 
             ("2A", demand.get("2A",0)), ("2M", demand.get("2M",0))]
    assigned_count = [0] * n 
    
    # 1차 배정
    for typ, need in order:
        current_need = need
        eligible_for_typ = [i for i, s in enumerate(staff) 
                           if eligible(s, typ) and assigned_count[i] < target_assignment[i] 
                           and total[i] < base_cap]
        
        def sort_key(i):
            is_mixing = total[i] > 0 and assigned[i].get(typ, 0) == 0
            mix_cost = 1 if is_mixing else 0
            return (staff[i].load, mix_cost, total[i])

        sorted_indices = sorted(eligible_for_typ, key=sort_key)
        
        for i in sorted_indices:
            if current_need == 0: break
            assigned[i][typ] += 1
            total[i] += 1
            assigned_count[i] += 1
            current_need -= 1
    
    # 2차 잔여 배정 (무한루프 방지)
    for typ, _ in order:
        max_iters = len(staff) * 10
        iters = 0
        while demand.get(typ, 0) > sum(a[typ] for a in assigned) and iters < max_iters:
            iters += 1
            
            current_loads = []
            for i, s in enumerate(staff):
                is_mixing = total[i] > 0 and assigned[i].get(typ, 0) == 0
                mix_penalty = 1 if is_mixing else 0
                current_loads.append(s.load + mix_penalty)
            
            eligible_indices = [i for i, s in enumerate(staff) 
                              if eligible(s, typ) and total[i] < base_cap]
            if not eligible_indices:
                break

            min_load = min(current_loads[i] for i in eligible_indices)
            idx_list = [i for i in eligible_indices 
                       if abs(current_loads[i] - min_load) < 1e-9]
            
            if not idx_list:
                break
            
            pick = idx_list[0] if len(idx_list) == 1 else pick_random_candidate(staff, idx_list, period, hist)
            assigned[pick][typ] += 1
            total[pick] += 1

    # Load 누적 및 코스 연장 설정
    for i, s in enumerate(staff):
        mix_count_final = sum(1 for v in assigned[i].values() if v > 0)
        s.is_mixed_today = (mix_count_final > 1)
        s.load = float(total[i])
        if period == 1 and s.is_course:
            s.course_penalty_next = (total[i] == 0)

    save_history(hist)
    return assigned, total

##############################################################
# Pairing (규칙 11)
##############################################################
def pair_results(staff, total):
    ones = []
    zeros = []
    for i, s in enumerate(staff):
        if total[i] == 1:
            ones.append(s.name)
        elif total[i] == 0:
            zeros.append(s.name)

    pairs = []
    for i in range(0, len(ones), 2):
        if i+1 < len(ones):
            pairs.append(f"{ones[i]} - {ones[i+1]}")
        else:
            if zeros:
                z = zeros.pop(0)
                pairs.append(f"{ones[i]} - {z}(참관)")
            else:
                pairs.append(f"{ones[i]} - (단독)")
    for z in zeros:
        pairs.append(f"{z}(참관)")
    return pairs

##############################################################
# STREAMLIT UI (session_state 키 최적화)
##############################################################
st.title("도로주행 자동 배정 (최종 공평성 모델)")

tab_m, tab_a, tab_r = st.tabs(["오전 배정", "오후 배정", "랜덤결과"])

with tab_m:
    st.subheader("오전 텍스트 입력")
    txt_m = st.text_area("오전 텍스트 입력", height=220, key="txt_m_input")
    period_m = st.selectbox("교시 선택", [1,2], index=0, key="period_m_select")

    if st.button("1) 근무자 자동 추출", key="extract_m"):
        if not txt_m.strip():
            st.error("텍스트가 비어 있습니다.")
        else:
            staff_raw = parse_staff(txt_m)
            edu_map, course_list = parse_extra(txt_m)
            st.session_state["m_staff_raw"] = staff_raw
            st.session_state["m_edu"] = edu_map
            st.session_state["m_course"] = course_list
            st.success("자동 추출 완료!")
            st.write("근무자:", staff_raw)
            st.write("2교시 교양자:", edu_map.get(2) if edu_map.get(2) else "없음")
            st.write("코스 담당자:", course_list)

    if "m_staff_raw" in st.session_state:
        st.subheader("근무자 수정")
        df = pd.DataFrame({"근무자": st.session_state["m_staff_raw"]})
        edited = st.data_editor(df, num_rows="dynamic", key="m_edit")
        final_m = edited["근무자"].dropna().tolist()
        st.session_state["m_staff"] = final_m

        st.subheader("코스 / 교양 수정")
        course_sel = st.multiselect("코스 담당자", final_m, default=st.session_state["m_course"], key="m_course_sel")
        st.session_state["m_course_sel"] = course_sel

        edu2_nm = st.session_state["m_edu"].get(2)
        default_index = 0
        if edu2_nm in final_m:
            default_index = final_m.index(edu2_nm) + 1
        edu2_sel = st.selectbox("2교시 교양 담당자", ["없음"] + final_m,
                                index=default_index, key="m_edu_sel_2")
        st.session_state["m_edu_sel"] = {2: edu2_sel if edu2_sel != "없음" else None}

        st.subheader("수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand_m = {
            "1M": c1.number_input("1종수동", min_value=0, key="m_demand_1M"),
            "1A": c2.number_input("1종자동", min_value=0, key="m_demand_1A"),
            "2A": c3.number_input("2종자동", min_value=0, key="m_demand_2A"),
            "2M": c4.number_input("2종수동", min_value=0, key="m_demand_2M"),
        }

        if st.button("2) 오전 배정 실행", key="run_m"):
            staff_list = [Staff(nm) for nm in st.session_state["m_staff"]]
            for s in staff_list:
                if s.name in st.session_state["m_course_sel"]:
                    s.is_course = True
            if st.session_state["m_edu_sel"].get(2):
                edu_nm = st.session_state["m_edu_sel"][2]
                for s in staff_list:
                    if s.name == edu_nm:
                        s.is_edu[2] = True

            assigned, total = assign_period(staff_list, period_m, demand_m, is_morning=True)

            st.subheader("배정 결과")
            rows = []
            for i,s in enumerate(staff_list):
                info = assigned[i]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        tt = {"1M":"1종수동", "1A":"1종자동", "2A":"2종자동", "2M":"2종수동"}[t]
                        desc.append(f"{tt} {info[t]}명")
                rows.append([s.name, " / ".join(desc) if desc else "0"])
            st.table(pd.DataFrame(rows, columns=["감독관","배정"]))

            st.subheader("최종 Load")
            st.table(pd.DataFrame({
                "감독관":[s.name for s in staff_list],
                "Load":[s.load for s in staff_list]
            }))

            st.subheader("Pairing 결과")
            pairs = pair_results(staff_list, total)
            st.write(" ".join(pairs) if pairs else "pairing 없음")

with tab_a:
    st.subheader("오후 텍스트 입력")
    txt_a = st.text_area("오후 텍스트 입력", height=220, key="txt_a_input")
    period_a = st.selectbox("교시
