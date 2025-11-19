제공해주신 코드에는 **Streamlit의 구조적 특성(세션 상태 초기화, 위젯 키 중복, 중첩 버튼 문제)**으로 인해 실행 시 에러가 발생하거나, 버튼을 눌러도 결과가 유지되지 않는 치명적인 문제들이 있습니다.
이를 해결하여 정상적으로 작동하도록 수정한 코드입니다.
🛠 수정된 주요 내용
 * 위젯 Key 중복 해결: 오전/오후 탭의 number_input 등 입력 필드에 고유한 key를 부여하여 DuplicateWidgetID 에러를 방지했습니다.
 * 세션 상태(Session State) 적용: "배정 실행" 버튼을 누른 후 다른 동작(예: 짝짓기 확인, 초기화 등)을 해도 배정 결과가 사라지지 않도록 st.session_state에 결과를 저장하게 변경했습니다.
 * 중첩 버튼(Nested Button) 제거: if st.button(...) 안에 다른 st.button을 넣으면 내부 버튼이 작동하지 않는 문제를 해결하기 위해 로직을 분리했습니다.
 * 데이터 타입 안정성: st.data_editor 사용 시 데이터가 비어있거나 포맷이 안 맞을 경우를 대비해 예외 처리를 보강했습니다.
###############################################
# 도로주행 자동 배정 vFinal (Fix: 에러 수정 및 안정화)
###############################################
import streamlit as st
import json, os, re, random
import pandas as pd
from datetime import date
from collections import defaultdict

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

def reset_history():
    save_json(HISTORY_FILE, [])

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
    # Regex 수정: 다양한 공백 패턴 대응
    m = re.findall(r"1종수동\s*[:;]?\s*\d+호\s*([가-힣]+)", text)
    staff += [n.strip() for n in m]
    m2 = re.findall(r"•\s*\d+호\s*([가-힣]+)", text)
    staff += [n.strip() for n in m2]
    return list(dict.fromkeys(staff))

def extract_extra(text):
    edu = {}
    m = re.findall(r"(\d)교시\s*[:;]?\s*([가-힣]+)", text)
    for gyo, name in m:
        edu[int(gyo)] = name.strip()
    
    course = []
    # 코스점검 파싱 로직 보완
    body = re.findall(r"코스점검\s*[:;]?\s*(.*)", text)
    if body:
        # A코스 : 홍길동 패턴
        mm = re.findall(r"[A-Z]코스.*?\s*([가-힣]+)", body[0])
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
        self.need_low_next = False
        self.assigned = {"prev_zero": False}

###########################################################
# 가중치 (중복시 최대 1)
###########################################################
def apply_weights(staff_list, period, is_morning):
    for s in staff_list:
        weight = 0
        if is_morning and period == 1 and s.is_course:
            weight += 1
        if is_morning and period == 2 and s.need_low_next:
            weight += 1
        # 직전 교시 교육 여부 체크 (현재 로직상 단일 교시 배정이므로 완벽하진 않음)
        for k in [2,4,5]:
            if period == k-1 and s.is_edu.get(k, False):
                weight += 1
        
        if weight > 1:
            weight = 1
        s.load += weight

###########################################################
# 자격 체크
###########################################################
def is_eligible(st_obj, type_code):
    if st_obj.is_manual:
        return True
    return type_code in ("1A","2A")

###########################################################
# 한 교시 배정 (B안 공평성)
###########################################################
def assign_one_period(staff_list, period, demand, is_morning):
    # 이전 배정 정보 초기화 (단일 실행 시)
    for s in staff_list:
        if s.assigned.get("prev_zero", False):
            s.load += 1
        s.assigned["prev_zero"] = False

    apply_weights(staff_list, period, is_morning)
    
    # 인원별 상한선 설정
    base_cap = 2 if period in (1,5) else 3
    n = len(staff_list)
    
    # 결과 저장소
    assigned = {s.name: {"1M":0,"1A":0,"2A":0,"2M":0} for s in staff_list}
    total = [0]*n

    # 랜덤 기록 로드
    hist = set(load_json(HISTORY_FILE, []))
    
    # 배정 순서
    order = [("1M", demand.get("1M",0)),("1A", demand.get("1A",0)),
             ("2A", demand.get("2A",0)),("2M", demand.get("2M",0))]

    for type_code, need in order:
        for _ in range(int(need)): # int 형변환 안전장치
            min_load = None
            candidates = []
            
            # 1차: 자격 되고 Cap 여유 있는 사람 중 Load가 가장 적은 사람 찾기
            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if min_load is None or s.load < min_load:
                        min_load = s.load
            
            # 2차: 최소 Load인 후보군 수집
            for i, s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s, type_code):
                    if min_load is None or abs(s.load - min_load) < 1e-9:
                        candidates.append(i)
            
            if not candidates: 
                continue # 배정 불가능하면 스킵

            # 최근 배정 이력 고려 (Random Rotation)
            no_recent = [i for i in candidates if staff_list[i].name not in hist]
            pick = random.choice(no_recent if no_recent else candidates)
            
            staff_name = staff_list[pick].name
            hist.add(staff_name)
            assigned[staff_name][type_code] += 1
            total[pick] += 1

    # 🔧 B안 공평성 보정 (최대-최소 격차 줄이기)
    for _ in range(40):
        if not total: break # 예외처리
        max_val, min_val = max(total), min(total)
        if max_val - min_val < 2:
            break
        
        idx_max = total.index(max_val)
        idx_min = total.index(min_val)
        
        moved = False
        s_max = staff_list[idx_max]
        s_min = staff_list[idx_min]
        
        for t in ["1M","1A","2A","2M"]:
            # Max인 사람에게 해당 차종 배정이 있고, Min인 사람이 그 차종 자격이 될 때
            if assigned[s_max.name][t] > 0 and is_eligible(s_min, t):
                assigned[s_max.name][t] -= 1
                assigned[s_min.name][t] += 1
                total[idx_max] -= 1
                total[idx_min] += 1
                moved = True
                break
        if not moved: 
            break

    # 최종 상태 업데이트
    for i, s in enumerate(staff_list):
        s.load += total[i]
        s.assigned["prev_zero"] = (total[i] == 0)

    # 다음 교시를 위한 플래그 설정 (코스 담당자가 배정을 많이 받았다면)
    if is_morning and period == 1:
        if total:
            min_assign = min(total)
            for i, s in enumerate(staff_list):
                s.need_low_next = (s.is_course and total[i] > min_assign)
    
    save_json(HISTORY_FILE, list(hist))
    return assigned, staff_list # 객체 상태 반환

###########################################################
# 짝짓기 로직
###########################################################
def make_pairs(staff_list, result_dict):
    total_assign = {s.name: sum(result_dict[s.name].values()) for s in staff_list}
    # 1명 배정자
    list_one = [n for n, v in total_assign.items() if v == 1]
    # 0명 배정자
    list_zero = [n for n, v in total_assign.items() if v == 0]
    
    pairs = []
    # 1끼리 묶기
    while len(list_one) >= 2:
        a = list_one.pop(0)
        b = list_one.pop(0)
        pairs.append(f"{a} - {b}")
    
    # 남은 1과 0(참관) 묶기
    if list_one and list_zero:
        a = list_one.pop(0)
        b = list_zero.pop(0)
        pairs.append(f"{a} - {b} (참관)")
        
    return pairs

############################################################
# Streamlit UI
############################################################
st.title("🚗 도로주행 자동 배정 (오류 수정판)")

tab_m, tab_a, tab_r = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 랜덤결과"])

############################################################
# 🌅 오전 탭
############################################################
with tab_m:
    st.subheader("📥 오전 텍스트 입력")
    text_m = st.text_area("오전 텍스트 입력 (붙여넣기)", height=150, key="txt_m")
    period_m = st.selectbox("교시 선택", [1,2], index=0, key="sel_period_m")

    if st.button("1) 근무자 자동 추출", key="m_extract"):
        staff_names = extract_staff(text_m)
        edu_map, course_list = extract_extra(text_m)
        st.session_state["m_staff"] = staff_names
        st.session_state["m_edu"] = edu_map
        st.session_state["m_course"] = course_list
        st.success(f"근무자 {len(staff_names)}명 추출 완료")

    if "m_staff" in st.session_state:
        st.divider()
        col_edit, col_demand = st.columns([1, 2])
        
        with col_edit:
            st.subheader("✏ 근무자 확인")
            df_m = pd.DataFrame({"근무자": st.session_state["m_staff"]})
            edited_m = st.data_editor(df_m, num_rows="dynamic", key="edit_m")
            final_staff_m = edited_m["근무자"].dropna().astype(str).tolist()

            # 교양/코스 설정
            edu_curr = st.session_state.get("m_edu", {})
            course_curr = st.session_state.get("m_course", [])
            
            st.markdown("**옵션 설정**")
            edu_fix = st.selectbox("교양 담당자", ["(없음)"]+final_staff_m, key="m_edu_fix")
            course_fix = st.multiselect("코스 담당자", final_staff_m,
                default=[x for x in course_curr if x in final_staff_m],
                key="m_course_fix")

        with col_demand:
            st.subheader("📊 차량 수요 입력")
            c1, c2, c3, c4 = st.columns(4)
            # Key 값을 유니크하게 지정 (m_1M 등)
            demand_m = {
                "1M": c1.number_input("1종수동", 0, key="m_1M"),
                "1A": c2.number_input("1종자동", 0, key="m_1A"),
                "2A": c3.number_input("2종자동", 0, key="m_2A"),
                "2M": c4.number_input("2종수동", 0, key="m_2M")
            }

        st.divider()
        
        # 실행 버튼
        if st.button("2) 오전 배정 실행", key="m_run"):
            staff_objects = [Staff(n) for n in final_staff_m]
            for s in staff_objects:
                if edu_fix == s.name:
                    s.is_edu[period_m] = True
                if s.name in course_fix:
                    s.is_course = True
            
            result_m, updated_staff_m = assign_one_period(staff_objects, period_m, demand_m, True)
            pairs_m = make_pairs(updated_staff_m, result_m)
            
            # 결과 세션 저장
            st.session_state["result_m_data"] = result_m
            st.session_state["result_m_pairs"] = pairs_m
            st.session_state["result_m_staff"] = updated_staff_m

        # 결과 출력 (세션에 데이터가 있으면 표시)
        if "result_m_data" in st.session_state:
            st.subheader(f"📌 오전 {period_m}교시 배정 결과")
            
            res_staff = st.session_state["result_m_staff"]
            res_data = st.session_state["result_m_data"]
            
            rows = []
            for s in res_staff:
                info = res_data[s.name]
                desc = [f"{k.replace('1M','1종수동').replace('1A','1종자동').replace('2A','2종자동').replace('2M','2종수동')} {v}명"
                        for k, v in info.items() if v > 0]
                rows.append((s.name, " / ".join(desc) if desc else "-"))
            
            st.table(pd.DataFrame(rows, columns=["감독관", "배정 내역"]))

            if st.session_state["result_m_pairs"]:
                st.markdown("#### 👥 탑승 짝짓기")
                for p in st.session_state["result_m_pairs"]:
                    st.success(p)

            # 초기화 버튼 (Nested Button 문제 해결)
            if st.button("🔄 결과 지우기 (오전)", key="m_reset"):
                del st.session_state["result_m_data"]
                st.rerun()

############################################################
# 🌇 오후 탭
############################################################
with tab_a:
    st.subheader("📥 오후 텍스트 입력")
    text_a = st.text_area("오후 텍스트 입력 (붙여넣기)", height=150, key="txt_a")
    period_a = st.selectbox("교시 선택", [3,4,5], index=0, key="sel_period_a")

    if st.button("1) 근무자 자동 추출", key="a_extract"):
        staff_names = extract_staff(text_a)
        edu_map, _ = extract_extra(text_a)
        st.session_state["a_staff"] = staff_names
        st.session_state["a_edu"] = edu_map
        st.success(f"근무자 {len(staff_names)}명 추출 완료")

    if "a_staff" in st.session_state:
        st.divider()
        col_edit_a, col_demand_a = st.columns([1, 2])
        
        with col_edit_a:
            st.subheader("✏ 근무자 확인")
            df_a = pd.DataFrame({"근무자": st.session_state["a_staff"]})
            edited_a = st.data_editor(df_a, num_rows="dynamic", key="edit_a")
            final_staff_a = edited_a["근무자"].dropna().astype(str).tolist()

            st.markdown("**옵션 설정**")
            edu_fix_a = st.selectbox("교양 담당자", ["(없음)"]+final_staff_a, key="a_edu_fix")

        with col_demand_a:
            st.subheader("📊 차량 수요 입력")
            c1, c2, c3, c4 = st.columns(4)
            # Key 값을 유니크하게 지정 (a_1M 등)
            demand_a = {
                "1M": c1.number_input("1종수동", 0, key="a_1M"),
                "1A": c2.number_input("1종자동", 0, key="a_1A"),
                "2A": c3.number_input("2종자동", 0, key="a_2A"),
                "2M": c4.number_input("2종수동", 0, key="a_2M")
            }

        st.divider()

        if st.button("2) 오후 배정 실행", key="a_run"):
            staff_objects = [Staff(n) for n in final_staff_a]
            for s in staff_objects:
                if edu_fix_a == s.name:
                    s.is_edu[period_a] = True
            
            result_a, updated_staff_a = assign_one_period(staff_objects, period_a, demand_a, False)
            pairs_a = make_pairs(updated_staff_a, result_a)
            
            st.session_state["result_a_data"] = result_a
            st.session_state["result_a_pairs"] = pairs_a
            st.session_state["result_a_staff"] = updated_staff_a

        if "result_a_data" in st.session_state:
            st.subheader(f"📌 오후 {period_a}교시 배정 결과")
            
            res_staff = st.session_state["result_a_staff"]
            res_data = st.session_state["result_a_data"]
            
            rows = []
            for s in res_staff:
                info = res_data[s.name]
                desc = [f"{k.replace('1M','1종수동').replace('1A','1종자동').replace('2A','2종자동').replace('2M','2종수동')} {v}명"
                        for k, v in info.items() if v > 0]
                rows.append((s.name, " / ".join(desc) if desc else "-"))
            
            st.table(pd.DataFrame(rows, columns=["감독관", "배정 내역"]))

            if st.session_state["result_a_pairs"]:
                st.markdown("#### 👥 탑승 짝짓기")
                for p in st.session_state["result_a_pairs"]:
                    st.success(p)

            if st.button("🔄 결과 지우기 (오후)", key="a_reset"):
                del st.session_state["result_a_data"]
                st.rerun()

############################################################
# 🎲 랜덤 결과 탭
############################################################
with tab_r:
    st.subheader("🎲 랜덤 우선배정 기록")
    st.info("공평성을 위해 최근에 배정된 사람은 다음 배정 시 우선순위가 밀립니다.")
    hist = load_json(HISTORY_FILE, [])
    if not hist: 
        st.write("기록이 없습니다.")
    else:
        st.table(pd.DataFrame({"최근 배정된 감독관": hist}))
        
    if st.button("🧹 랜덤 결과 초기화", key="r_reset"):
        reset_history()
        st.success("랜덤 결과 초기화 완료!")
        st.rerun()

