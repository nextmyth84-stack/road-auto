##############################################################
# auto.py — 도로주행 자동 배정 (최종 통합판)
# 공평성 모델 + 코스/교양/섞임(현재 교시 반영) 가중치 + 랜덤 3일 제외 + pairing
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
# 수동 가능자 (규칙 9 유지)
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

    # 1종수동: "1종수동: 7호 김남균"
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    for name in m:
        staff.append(name.strip())

    # 2종자동 bullet
    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    for name in m2:
        staff.append(name.strip())

    return list(dict.fromkeys(staff))


def parse_extra(text):
    edu = {}
    m = re.findall(r"(\d)교시\s*:\s*([가-힣]+)", text)
    for gyo, nm in m:
        edu[int(gyo)] = nm.strip()

    # 코스점검자
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
        self.is_mixed_today = False # 현재 교시 종별 섞임 여부 (가중치 계산용)

##############################################################
# 랜덤 히스토리 (규칙 10)
##############################################################
def load_history():
    return load_json(HISTORY_FILE, [])

def save_history(hist):
    save_json(HISTORY_FILE, hist)

# 최근 3일 동안 랜덤 당첨된 적이 있는지 체크 (규칙 10)
def used_recently(hist, name):
    today = date.today()
    for h in hist:
        d = date.fromisoformat(h["date"])
        if h.get("type") == "random_pick" and (today - d).days <= 3 and h["name"] == name:
            return True
    return False

# 랜덤 당첨 기록 (규칙 10)
def record_random(hist, name, period):
    hist.append({
        "date": date.today().isoformat(),
        "name": name,
        "period": period,
        "type": "random_pick"
    })

def check_history_full(hist, staff_names):
    """히스토리가 전체 인원을 다 포함하면 True 반환 (규칙 10)"""
    recent_names = {h["name"] for h in hist if h.get("type") == "random_pick"}
    return recent_names.issuperset(set(staff_names))

def clear_history_if_full(hist, staff_names):
    """히스토리가 전체 인원을 다 포함하면 초기화 (규칙 10)"""
    if check_history_full(hist, staff_names):
        st.warning("🚨 **랜덤 히스토리 초기화**: 전체 인원이 한 번씩 랜덤 배정되어 기록을 초기화합니다.")
        hist.clear()
        return True
    return False

##############################################################
# 자격 체크 (규칙 9 유지)
##############################################################
def eligible(st, typecode):
    if st.is_manual:
        return True
    return typecode in ("1A", "2A")

##############################################################
# 가중치 적용 (코스/교양 가중치 - 배정 시작 전 Load에 반영)
##############################################################
def apply_weights(staff, period):
    """
    Load에 현재 교시의 코스/교양 가중치를 누적합니다. (종별 섞임 제외)
    """
    for i, s in enumerate(staff):
        w = 0

        # 1. 코스 담당자 가중치 (1교시 적용, 2교시 연장)
        if s.is_course:
            if period == 1:
                w += 1
            elif period == 2 and s.course_penalty_next:
                w += 1

        # 2. 다음 교시 교양 담당자 가중치
        target_edu_period = None
        if period == 1: target_edu_period = 2
        elif period == 3: target_edu_period = 4
        elif period == 4: target_edu_period = 5
        
        if target_edu_period and s.is_edu.get(target_edu_period):
            w += 1

        # 가중치 중복 최대 1 (규칙 7)
        if w > 1:
            w = 1

        # Load에 가중치 누적 (규칙 5, 6)
        s.load = float(s.load) + w

##############################################################
# 랜덤 선택 (최근 3일 제외) (규칙 10)
##############################################################
def pick_random_candidate(staff, idx_list, period, hist):
    """
    동점자 중 랜덤 선정. 최근 3일 랜덤 당첨자는 제외 후 선택.
    """
    filtered = [i for i in idx_list if not used_recently(hist, staff[i].name)]
    
    if filtered:
        pick = random.choice(filtered)
    else:
        pick = random.choice(idx_list)
        
    record_random(hist, staff[pick].name, period)
    return pick

##############################################################
# 한 교시 배정
##############################################################
def assign_period(staff, period, demand, is_morning):

    # 교시별 최대 배정 인원 수 (규칙 2)
    BASE_CAP_MAP = {1: 2, 2: 3, 3: 3, 4: 3, 5: 2}
    base_cap = BASE_CAP_MAP.get(period, 3)

    n = len(staff)
    staff_names = [s.name for s in staff]
    
    # 1. 랜덤 히스토리 로드 및 초기화 체크
    hist = load_history()
    clear_history_if_full(hist, staff_names)
    
    # 2. Load 초기화 및 코스 연장/섞임 여부 초기화
    if period != 2:
        for s in staff:
            s.course_penalty_next = False
    for s in staff:
        s.is_mixed_today = False
    
    # 3. 현재 교시의 코스/교양 가중치 적용 (Load 누적 포함)
    apply_weights(staff, period)
    
    # 4. 배정 결과 딕셔너리 및 총 배정 수
    assigned = [
        {"1M":0,"1A":0,"2A":0,"2M":0}
        for _ in range(n)
    ]
    total = [0]*n
    
    # 5. 총 수요 및 목표 횟수 계산 (규칙 1, 6)
    total_demand = sum(demand.values())
    target_base = total_demand // n
    target_rem = total_demand % n
    
    # Load가 낮은 순서 (우선순위가 높은 순서)
    staff_indices_sorted = sorted(range(n), key=lambda i: staff[i].load)
    
    target_assignment = [target_base] * n
    
    for i in staff_indices_sorted[:target_rem]:
        target_assignment[i] += 1
    
    for i in range(n):
        if target_assignment[i] > base_cap:
             target_assignment[i] = base_cap
    
    # 6. 실제 배정 (목표 횟수까지)
    order = [
        ("1M", demand.get("1M",0)),
        ("1A", demand.get("1A",0)),
        ("2A", demand.get("2A",0)),
        ("2M", demand.get("2M",0)),
    ]

    assigned_count = [0] * n 
    
    # 1차 배정: 목표 횟수까지
    for typ, need in order:
        current_need = need
        
        for i in staff_indices_sorted:
            if current_need == 0:
                break

            s = staff[i]
            
            if eligible(s, typ) and assigned_count[i] < target_assignment[i] and total[i] < base_cap:
                
                # 배정
                assigned[i][typ] += 1
                total[i] += 1
                assigned_count[i] += 1
                current_need -= 1
    
    # 7. 잔여 수요 재배정 (최소 Load & max cap 미만에게)
    # 종별 섞임 가중치(1)가 현재 교시 배정에 반영되어야 하므로,
    # 배정이 추가될 때마다 is_mixed_today를 확인하여 Load를 동적으로 조정하며 재배정합니다.
    
    for typ, _ in order:
        while demand.get(typ, 0) > sum(a[typ] for a in assigned):
            
            # 현재 시점의 Load 계산: 기존 Load + 종별 섞임 가중치
            current_loads = []
            for i, s in enumerate(staff):
                mix_count_now = sum(1 for t, count in assigned[i].items() if count > 0)
                # 현재 배정 시 섞이게 될 경우를 예측하여 Load에 반영
                mix_penalty = 1 if mix_count_now >= 1 and assigned[i].get(typ, 0) == 0 else 0
                
                # 섞임 패널티는 한 번만 적용되도록 is_mixed_today를 사용 (옵션)
                # 여기서는 동적으로 계산하기 위해 mix_penalty만 사용
                
                # **핵심**: Load = 기본 Load + 현재 교시 종별 섞임 패널티
                current_loads.append(float(s.load) + mix_penalty)

            
            min_load = None
            eligible_indices = [
                i for i, s in enumerate(staff)
                if eligible(s, typ) and total[i] < base_cap
            ]
            
            if not eligible_indices:
                break

            for i in eligible_indices:
                if min_load is None or current_loads[i] < min_load:
                    min_load = current_loads[i]

            if min_load is None:
                break
                
            # 최소 Load 동점자 리스트
            idx_list = [
                i for i in eligible_indices
                if abs(current_loads[i] - min_load) < 1e-9
            ]
            
            # 더 낮은 Load를 가진 사람이 모두 cap을 채웠을 경우를 고려하여 min_load를 갱신
            if not idx_list:
                current_min_load = min_load
                next_min_load = None
                
                for i in eligible_indices:
                    if current_loads[i] > current_min_load:
                        if next_min_load is None or current_loads[i] < next_min_load:
                            next_min_load = current_loads[i]
                            
                if next_min_load is None:
                    break
                
                min_load = next_min_load
                
                idx_list = [
                    i for i in eligible_indices
                    if abs(current_loads[i] - min_load) < 1e-9
                ]

                if not idx_list:
                    break
            
            # (3) 랜덤 선정 (규칙 10)
            if len(idx_list) == 1:
                pick = idx_list[0]
            else:
                pick = pick_random_candidate(staff, idx_list, period, hist)

            # 배정
            assigned[pick][typ] += 1
            total[pick] += 1
            assigned_count[pick] += 1


    # 8. 다음 교시를 위한 Load 누적 및 코스 연장 가중치 설정
    for i,s in enumerate(staff):
        # 1. 종별 섞임 가중치 추가 (다음 교시 Load에 적용되는 종별 섞임 가중치는 이제 없습니다.
        #    대신, 현재 교시에서 섞임이 발생했다는 표시만 남깁니다.)
        mix_count_final = sum(1 for v in assigned[i].values() if v > 0)
        s.is_mixed_today = (mix_count_final > 1)

        # 2. Load 초기화 후 현재 교시의 배정수 누적
        # (현재 교시 가중치는 이미 배정에 사용되었으므로 제거하고, 누적 배정수만 남깁니다.)
        s.load = float(total[i]) 
        
        # 3. 코스 연장 가중치 설정 (1교시 → 2교시)
        if period == 1 and s.is_course:
            s.course_penalty_next = (total[i] == 0)

    # 9. 히스토리 저장
    save_history(hist)
    return assigned, total

##############################################################
# 배정 결과 pairing 표시 (규칙 11)
##############################################################
def pair_results(staff, total):
    """
    배정 1 또는 0일 때 짝지어 표시 (규칙 11)
    """
    ones = []
    zeros = []
    for i,s in enumerate(staff):
        if total[i] == 1:
            ones.append(s.name)
        elif total[i] == 0:
            zeros.append(s.name)

    pairs = []
    
    # 1명끼리 pairing
    for i in range(0, len(ones), 2):
        if i+1 < len(ones):
            pairs.append(f"{ones[i]} - {ones[i+1]}")
        else:
            # 홀수 1명 발생 → 0명과 pairing
            if zeros:
                z = zeros.pop(0)
                pairs.append(f"{ones[i]} - {z}(참관)")
            else:
                pairs.append(f"{ones[i]} - (단독)")

    # 남은 0명은 모두 참관으로 표시
    for z in zeros:
        pairs.append(f"{z}(참관)")

    return pairs

##############################################################
# STREAMLIT UI (UI는 기존 코드를 유지하며, 로직 호출만 수정)
##############################################################
st.title("🚗 도로주행 자동 배정 (최종 공평성 모델)")

tab_m, tab_a, tab_r = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 랜덤결과"])

##############################################################
# 오전 탭
##############################################################
with tab_m:
    st.subheader("📥 오전 텍스트 입력")

    txt_m = st.text_area("오전 텍스트 입력", height=220, key="txt_m_input")
    period_m = st.selectbox("교시 선택", [1,2], index=0, key="period_m")

    # 1) 자동 추출
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

    # 2) 근무자 수정 UI
    if "m_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정(추가/삭제 가능)")
        df = pd.DataFrame({"근무자": st.session_state["m_staff_raw"]})
        edited = st.data_editor(df, num_rows="dynamic", key="m_edit")
        final_m = edited["근무자"].dropna().tolist()
        st.session_state["m_staff"] = final_m

        # 코스/교양 수정 UI 추가
        st.subheader("🛠 코스 / 교양 수정")

        # 코스는 MULTI 선택
        course_sel = st.multiselect("코스 담당자", final_m, default=st.session_state["m_course"])
        st.session_state["m_course_sel"] = course_sel

        # 교양(다음교시 적용) → 2교시 교양자만 해당 (1교시 배정 시 가중치)
        edu2_nm = st.session_state["m_edu"].get(2)
        default_index = 0
        if edu2_nm in final_m:
            default_index = final_m.index(edu2_nm) + 1
            
        edu2_sel = st.selectbox("2교시 교양 담당자", ["없음"] + final_m,
                                index=default_index, key="m_edu_sel_2")

        st.session_state["m_edu_sel"] = {2: edu2_sel if edu2_sel != "없음" else None}

        # 3) 수요 입력
        st.subheader("📊 수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand_m = {
            "1M": c1.number_input("1종수동", min_value=0, key="m_1M"),
            "1A": c2.number_input("1종자동", min_value=0, key="m_1A"),
            "2A": c3.number_input("2종자동", min_value=0, key="m_2A"),
            "2M": c4.number_input("2종수동", min_value=0, key="m_2M"),
        }

        # 4) 배정 실행
        if st.button("2) 오전 배정 실행", key="run_m"):
            # Staff 생성
            staff_list = []
            for nm in st.session_state["m_staff"]:
                s = Staff(nm)
                staff_list.append(s)

            # 코스 세팅
            for s in staff_list:
                if s.name in st.session_state["m_course_sel"]:
                    s.is_course = True

            # 교양(2교시)
            if st.session_state["m_edu_sel"].get(2):
                edu_nm = st.session_state["m_edu_sel"][2]
                for s in staff_list:
                    if s.name == edu_nm:
                        s.is_edu[2] = True

            # 배정
            assigned, total = assign_period(staff_list, period_m, demand_m, is_morning=True)

            # ---------------------
            # 출력
            # ---------------------
            st.subheader("📌 배정 결과")
            rows = []
            for i,s in enumerate(staff_list):
                info = assigned[i]
                desc = []
                for t in ("1M","1A","2A","2M"):
                    if info[t] > 0:
                        # 보기 좋게 변환
                        tt = {"1M":"1종수동", "1A":"1종자동",
                              "2A":"2종자동", "2M":"2종수동"}[t]
                        desc.append(f"{tt} {info[t]}명")
                rows.append([s.name, " / ".join(desc) if desc else "0"])
            st.table(pd.DataFrame(rows, columns=["감독관","배정"]))

            # 가중치 표시
            st.subheader("🔢 최종 Load(누적 배정수)")
            st.table(pd.DataFrame({
                "감독관":[s.name for s in staff_list],
                "Load":[float(s.load) for s in staff_list]
            }))

            # pairing
            st.subheader("🤝 Pairing 결과(배정 1·0 대상)")
            pairs = pair_results(staff_list, total)
            if pairs:
                st.write("\n".join(pairs))
            else:
                st.write("pairing 없음")


##############################################################
# 오후 탭
##############################################################
with tab_a:
    st.subheader("📥 오후 텍스트 입력")

    txt_a = st.text_area("오후 텍스트 입력", height=220, key="txt_a_input")
    period_a = st.selectbox("교시 선택", [3,4,5], index=0, key="period_a")

    # 1) 자동 추출
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

    # 수정
    if "a_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정")
        df = pd.DataFrame({"근무자": st.session_state["a_staff_raw"]})
        edited = st.data_editor(df, num_rows="dynamic", key="a_edit")
        final_a = edited["근무자"].dropna().tolist()
        st.session_state["a_staff"] = final_a

        # 오후는 코스 제외, 4·5교시 교양만 존재
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

        # 수요
        st.subheader("📊 수요 입력")
        c1,c2,c3,c4 = st.columns(4)
        demand_a = {
            "1M": c1.number_input("1종수동", min_value=0, key="a_1M"),
            "1A": c2.number_input("1종자동", min_value=0, key="a_1A"),
            "2A": c3.number_input("2종자동", min_value=0, key="a_2A"),
            "2M": c4.number_input("2종수동", min_value=0, key="a_2M"),
        }

        # 실행
        if st.button("2) 오후 배정 실행", key="run_a"):
            # staff 생성
            staff_list = [Staff(nm) for nm in final_a]

            # 교양 반영
            for k,nm in st.session_state["a_edu_sel"].items():
                if nm:
                    for s in staff_list:
                        if s.name == nm:
                            s.is_edu[k] = True

            # 오후는 코스 담당자 가중치 없음 (룰상 제외)

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

            # load
            st.subheader("🔢 최종 Load(누적 배정수)")
            st.table(pd.DataFrame({
                "감독관":[s.name for s in staff_list],
                "Load":[float(s.load) for s in staff_list]
            }))

            # pairing
            st.subheader("🤝 Pairing 결과")
            pairs = pair_results(staff_list, total)
            st.write("\n".join(pairs) if pairs else "pairing 없음")

##############################################################
# 랜덤 히스토리
##############################################################
with tab_r:
    st.subheader("🎲 랜덤 배정 히스토리(최근 3일)")
    hist = load_history()
    
    # 랜덤 당첨 기록만 표시
    random_picks = [h for h in hist if h.get("type") == "random_pick"]
    
    if not random_picks:
        st.info("랜덤 기록 없음")
    else:
        st.table(pd.DataFrame(random_picks))
