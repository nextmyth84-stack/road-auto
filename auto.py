##############################################################
# auto.py — 도로주행 자동 배정 (최종 통합판 - 100% 매칭 + 몰아주기)
# 공평성 모델 + 코스/교양/섞임방지(Stacking) + 숫자 불일치 해결
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
# 수동 가능자
##############################################################
MANUAL_SET = {
    "권한솔","김남균","김성연",
    "김주현","이호석","조정래"
}

##############################################################
# 텍스트 파싱
##############################################################
def parse_staff(text):
    staff = []
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    for name in m:
        staff.append(name.strip())

    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    for name in m2:
        staff.append(name.strip())

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
# Staff CLASS
##############################################################
class Staff:
    def __init__(self, name):
        self.name = name
        self.is_manual = (name in MANUAL_SET)
        self.is_course = False
        self.is_edu = {i:False for i in range(1,6)}

        self.load = 0
        self.course_penalty_next = False 
        self.is_mixed_today = False 

##############################################################
# 랜덤 히스토리
##############################################################
def load_history():
    return load_json(HISTORY_FILE, [])

def save_history(hist):
    save_json(HISTORY_FILE, hist)

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
        st.warning("🚨 **랜덤 히스토리 초기화**: 전체 인원이 한 번씩 랜덤 배정되어 기록을 초기화합니다.")
        hist.clear()
        return True
    return False

##############################################################
# 자격 체크
##############################################################
def eligible(st, typecode):
    if st.is_manual:
        return True
    return typecode in ("1A", "2A")

##############################################################
# 가중치 적용
##############################################################
def apply_weights(staff, period):
    for i, s in enumerate(staff):
        w = 0
        if s.is_course:
            if period == 1: w += 1
            elif period == 2 and s.course_penalty_next: w += 1

        target_edu_period = None
        if period == 1: target_edu_period = 2
        elif period == 3: target_edu_period = 4
        elif period == 4: target_edu_period = 5
        
        if target_edu_period and s.is_edu.get(target_edu_period):
            w += 1

        if w > 1: w = 1
        s.load = float(s.load) + w

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
# 한 교시 배정 (Iterative Allocation - 100% 매칭 보장)
##############################################################
def assign_period(staff, period, demand, is_morning):

    BASE_CAP_MAP = {1: 2, 2: 3, 3: 3, 4: 3, 5: 2}
    base_cap = BASE_CAP_MAP.get(period, 3)

    n = len(staff)
    staff_names = [s.name for s in staff]
    
    hist = load_history()
    clear_history_if_full(hist, staff_names)
    
    if period != 2:
        for s in staff: s.course_penalty_next = False
    for s in staff: s.is_mixed_today = False
    
    # 배정 전 가중치(Load) 적용
    apply_weights(staff, period)
    
    assigned = [{"1M":0,"1A":0,"2A":0,"2M":0} for _ in range(n)]
    total = [0]*n
    
    # 배정 순서
    order = [
        ("1M", demand.get("1M",0)),
        ("1A", demand.get("1A",0)),
        ("2A", demand.get("2A",0)),
        ("2M", demand.get("2M",0)),
    ]

    # ------------------------------------------------------------------
    # 수요 하나씩 처리 (Iterative) - 숫자가 맞을 때까지 반복
    # ------------------------------------------------------------------
    for typ, need in order:
        while need > 0:
            # 1. 배정 가능한 후보군 찾기 (자격 O, Cap 여유 O)
            candidates = [
                i for i in range(n)
                if eligible(staff[i], typ) and total[i] < base_cap
            ]
            
            # 🚨 물리적 Cap 초과 시 중단
            if not candidates:
                break 

            # 2. 정렬 키 (우선순위 결정)
            def sort_key(i):
                # (1) 섞임 방지 (Mixing Penalty)
                #     Total은 있는데 이 type은 없으면 -> 섞이는 상태 (1점 penalty)
                is_mixing = 1 if (total[i] > 0 and assigned[i].get(typ, 0) == 0) else 0
                
                # (2) 몰아주기 (Stacking Priority)
                #     이미 이 type을 가지고 있으면 -> 0 (최우선)
                #     아무것도 없으면 -> 1 (차선)
                #     (섞이는 경우는 (1)에서 이미 걸러짐)
                if assigned[i].get(typ, 0) > 0:
                    stacking_score = 0
                elif total[i] == 0:
                    stacking_score = 1
                else:
                    stacking_score = 2 # 섞임
                
                # (3) Load (Fairness) - 적은 사람 우선
                return (is_mixing, stacking_score, staff[i].load)

            # 3. 최적 후보 선정
            #    정렬하여 1순위 그룹을 찾음
            candidates.sort(key=sort_key)
            
            best_candidates = []
            best_val = sort_key(candidates[0])
            
            for c in candidates:
                if sort_key(c) == best_val:
                    best_candidates.append(c)
                else:
                    break
            
            # 4. 동점자 처리 (랜덤)
            if len(best_candidates) == 1:
                pick = best_candidates[0]
            else:
                pick = pick_random_candidate(staff, best_candidates, period, hist)
            
            # 5. 배정 실행
            assigned[pick][typ] += 1
            total[pick] += 1
            need -= 1

    # ------------------------------------------------------------------
    # 결과 및 상태 업데이트
    # ------------------------------------------------------------------
    
    # Cap 부족 등으로 배정되지 못한 수요 체크
    total_assigned_count = sum(total)
    total_demand_count = sum(demand.values())
    
    if total_assigned_count < total_demand_count:
        st.error(f"⚠️ **주의**: 전체 수요({total_demand_count}명)보다 배정 가능 인원({total_assigned_count}명)이 부족합니다. (근무자 부족 또는 최대 배정 인원 제한)")

    for i,s in enumerate(staff):
        mix_count_final = sum(1 for v in assigned[i].values() if v > 0)
        s.is_mixed_today = (mix_count_final > 1)
        s.load = float(total[i]) 
        
        if period == 1 and s.is_course:
            s.course_penalty_next = (total[i] == 0)

    save_history(hist)
    return assigned, total

##############################################################
# 배정 결과 pairing
##############################################################
def pair_results(staff, total):
    ones = []
    zeros = []
    for i,s in enumerate(staff):
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
# STREAMLIT UI
##############################################################
st.title("🚗 도로주행 자동 배정 (최종 공평성 모델)")

tab_m, tab_a, tab_r = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 랜덤결과"])

with tab_m:
    st.subheader("📥 오전 텍스트 입력")
    txt_m = st.text_area("오전 텍스트 입력", height=220, key="txt_m_input")
    period_m = st.selectbox("교시 선택", [1,2], index=0, key="period_m")

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
        st.subheader("✏ 근무자 수정(추가/삭제 가능)")
        df = pd.DataFrame({"근무자": st.session_state["m_staff_raw"]})
        edited = st.data_editor(df, num_rows="dynamic", key="m_edit")
        final_m = edited["근무자"].dropna().tolist()
        st.session_state["m_staff"] = final_m

        st.subheader("🛠 코스 / 교양 수정")
        course_sel = st.multiselect("코스 담당자", final_m, default=st.session_state["m_course"])
        st.session_state["m_course_sel"] = course_sel

        edu2_nm = st.session_state["m_edu"].get(2)
        default_index = 0
        if edu2_nm in final_m:
            default_index = final_m.index(edu2_nm) + 1
        edu2_sel = st.selectbox("2교시 교양 담당자", ["없음"] + final_m,
                                index=default_index, key="m_edu_sel_2")
        st.session_state["m_edu_sel"] = {2: edu2_sel if edu2_sel != "없음" else None}

        st.subheader("📊 수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand_m = {
            "1M": c1.number_input("1종수동", min_value=0, key="m_1M"),
            "1A": c2.number_input("1종자동", min_value=0, key="m_1A"),
            "2A": c3.number_input("2종자동", min_value=0, key="m_2A"),
            "2M": c4.number_input("2종수동", min_value=0, key="m_2M"),
        }

        if st.button("2) 오전 배정 실행", key="run_m"):
            staff_list = []
            for nm in st.session_state["m_staff"]:
                s = Staff(nm)
                staff_list.append(s)
            for s in staff_list:
                if s.name in st.session_state["m_course_sel"]:
                    s.is_course = True
            if st.session_state["m_edu_sel"].get(2):
                edu_nm = st.session_state["m_edu_sel"][2]
                for s in staff_list:
                    if s.name == edu_nm:
                        s.is_edu[2] = True

            assigned, total = assign_period(staff_list, period_m, demand_m, is_morning=True)

            st.subheader("📌 배정 결과")
            rows = []
            for i,s in enumerate(staff_list):
                info = assigned[i]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        tt = {"1M":"1종수동", "1A":"1종자동",
                              "2A":"2종자동", "2M":"2종수동"}[t]
                        desc.append(f"{tt} {info[t]}명")
                rows.append([s.name, " / ".join(desc) if desc else "0"])
            st.table(pd.DataFrame(rows, columns=["감독관","배정"]))

            st.subheader("🔢 최종 Load(누적 배정수)")
            st.table(pd.DataFrame({
                "감독관":[s.name for s in staff_list],
                "Load":[float(s.load) for s in staff_list]
            }))

            st.subheader("🤝 Pairing 결과(배정 1·0 대상)")
            pairs = pair_results(staff_list, total)
            if pairs:
                st.write("\n".join(pairs))
            else:
                st.write("pairing 없음")

with tab_a:
    st.subheader("📥 오후 텍스트 입력")
    txt_a = st.text_area("오후 텍스트 입력", height=220, key="txt_a_input")
    period_a = st.selectbox("교시 선택", [3,4,5], index=0, key="period_a")

    if st.button("1) 근무자 자동 추출", key="extract_a"):
        if not txt_a.strip():
            st.error("텍스트가 비어 있습니다.")
        else:
            staff_raw = parse_staff(txt_a)
            edu_map, course_list = parse_extra(txt_a)
            st.session_state["a_staff_raw"] = staff_raw
            st.session_state["a_edu"] = edu_map
            st.session_state["a_course"] = course_list 
            st.success("자동 추출 완료!")
            st.write("근무자:", staff_raw)
            st.write("4교시 교양자:", edu_map.get(4) if edu_map.get(4) else "없음")
            st.write("5교시 교양자:", edu_map.get(5) if edu_map.get(5) else "없음")

    if "a_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정")
        df = pd.DataFrame({"근무자": st.session_state["a_staff_raw"]})
        edited = st.data_editor(df, num_rows="dynamic", key="a_edit")
        final_a = edited["근무자"].dropna().tolist()
        st.session_state["a_staff"] = final_a

        st.subheader("🛠 교양 수정 (다음 교시 적용)")
        edu_sel = {}
        for k in [4,5]:
            edu_nm = st.session_state["a_edu"].get(k)
            default_index = 0
            if edu_nm in final_a:
                default_index = final_a.index(edu_nm) + 1
            sel = st.selectbox(f"{k}교시 교양 담당자", ["없음"]+final_a,
                               key=f"a_edu_sel_{k}", index=default_index)
            edu_sel[k] = sel if sel!="없음" else None
        st.session_state["a_edu_sel"] = edu_sel

        st.subheader("📊 수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand_a = {
            "1M": c1.number_input("1종수동", min_value=0, key="a_1M"),
            "1A": c2.number_input("1종자동", min_value=0, key="a_1A"),
            "2A": c3.number_input("2종자동", min_value=0, key="a_2A"),
            "2M": c4.number_input("2종수동", min_value=0, key="a_2M"),
        }

        if st.button("2) 오후 배정 실행", key="run_a"):
            staff_list = [Staff(nm) for nm in final_a]
            for k,nm in st.session_state["a_edu_sel"].items():
                if nm:
                    for s in staff_list:
                        if s.name == nm:
                            s.is_edu[k] = True

            assigned, total = assign_period(staff_list, period_a, demand_a, is_morning=False)

            st.subheader("📌 배정 결과")
            rows = []
            for i,s in enumerate(staff_list):
                info = assigned[i]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        tt = {"1M":"1종수동", "1A":"1종자동",
                              "2A":"2종자동", "2M":"2종수동"}[t]
                        desc.append(f"{tt} {info[t]}명")
                rows.append([s.name, " / ".join(desc) if desc else "0"])
            st.table(pd.DataFrame(rows, columns=["감독관","배정"]))

            st.subheader("🔢 최종 Load(누적 배정수)")
            st.table(pd.DataFrame({
                "감독관":[s.name for s in staff_list],
                "Load":[float(s.load) for s in staff_list]
            }))

            st.subheader("🤝 Pairing 결과")
            pairs = pair_results(staff_list, total)
            st.write("\n".join(pairs) if pairs else "pairing 없음")

with tab_r:
    st.subheader("🎲 랜덤 배정 히스토리(최근 3일)")
    hist = load_history()
    random_picks = [h for h in hist if h.get("type") == "random_pick"]
    if not random_picks:
        st.info("랜덤 기록 없음")
    else:
        st.table(pd.DataFrame(random_picks))
