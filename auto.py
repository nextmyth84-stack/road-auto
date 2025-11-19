###############################################
# 도로주행 자동 배정 vFinal (B안 공평성 + 교양범위 수정)
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
    m = re.findall(r"1종수동\s*:\s*[\d]+호\s*([가-힣]+)", text)
    staff += [n.strip() for n in m]
    m2 = re.findall(r"•\s*[\d]+호\s*([가-힣]+)", text)
    staff += [n.strip() for n in m2]
    return list(dict.fromkeys(staff))

def extract_extra(text):
    edu = {}
    m = re.findall(r"(\d)교시\s*:\s*([가-힣]+)", text)
    for gyo, name in m:
        edu[int(gyo)] = name.strip()
    course = []
    body = re.findall(r"코스점검\s*:\s*(.*)", text)
    if body:
        mm = re.findall(r"[A-Z]코스.*?:\s*([가-힣]+)", body[0])
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
        for k in [2,4,5]:
            if period == k-1 and s.is_edu[k]:
                weight += 1
        if weight > 1:
            weight = 1
        s.load += weight

###########################################################
# 자격 체크
###########################################################
def is_eligible(st, type_code):
    if st.is_manual:
        return True
    return type_code in ("1A","2A")

###########################################################
# 한 교시 배정 (B안 공평성)
###########################################################
def assign_one_period(staff_list, period, demand, is_morning):
    for s in staff_list:
        if s.assigned["prev_zero"]:
            s.load += 1
        s.assigned["prev_zero"] = False

    apply_weights(staff_list, period, is_morning)
    base_cap = 2 if period in (1,5) else 3
    n = len(staff_list)
    assigned = {s.name: {"1M":0,"1A":0,"2A":0,"2M":0} for s in staff_list}
    total = [0]*n

    hist = set(load_json(HISTORY_FILE, []))
    order = [("1M", demand.get("1M",0)),("1A", demand.get("1A",0)),
             ("2A", demand.get("2A",0)),("2M", demand.get("2M",0))]

    for type_code, need in order:
        for _ in range(need):
            min_load = None
            candidates = []
            for i,s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s,type_code):
                    if min_load is None or s.load < min_load:
                        min_load = s.load
            for i,s in enumerate(staff_list):
                if total[i] < base_cap and is_eligible(s,type_code):
                    if abs(s.load-min_load)<1e-9:
                        candidates.append(i)
            if not candidates: continue
            no_recent = [i for i in candidates if s.name not in hist]
            pick = random.choice(no_recent or candidates)
            hist.add(staff_list[pick].name)
            assigned[staff_list[pick].name][type_code]+=1
            total[pick]+=1

    # 🔧 B안 공평성 보정
    for _ in range(40):
        max_val, min_val = max(total), min(total)
        if max_val - min_val < 2:
            break
        idx_max, idx_min = total.index(max_val), total.index(min_val)
        moved=False
        for t in ("1M","1A","2A","2M"):
            if assigned[staff_list[idx_max].name][t]>0 and is_eligible(staff_list[idx_min],t):
                assigned[staff_list[idx_max].name][t]-=1
                assigned[staff_list[idx_min].name][t]+=1
                total[idx_max]-=1
                total[idx_min]+=1
                moved=True
                break
        if not moved: break

    for i,s in enumerate(staff_list):
        s.load += total[i]
        s.assigned["prev_zero"] = (total[i]==0)
    if is_morning and period==1:
        min_assign=min(total)
        for i,s in enumerate(staff_list):
            s.need_low_next=(s.is_course and total[i]>min_assign)
    save_json(HISTORY_FILE, list(hist))
    return assigned

###########################################################
# 짝짓기 로직 (1끼리 / 1-0 참관)
###########################################################
def make_pairs(staff_list,result_dict):
    total_assign={s.name:sum(result_dict[s.name].values()) for s in staff_list}
    list_one=[n for n,v in total_assign.items() if v==1]
    list_zero=[n for n,v in total_assign.items() if v==0]
    pairs=[]
    while len(list_one)>=2:
        a,b=list_one.pop(0),list_one.pop(0)
        pairs.append(f"{a} - {b}")
    if list_one and list_zero:
        a,b=list_one.pop(0),list_zero.pop(0)
        pairs.append(f"{a} - {b}(참관)")
    return pairs

############################################################
# Streamlit UI
############################################################
st.title("🚗 도로주행 자동 배정 (B안 공평성)")

tab_m, tab_a, tab_r = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 랜덤결과"])

############################################################
# 🌅 오전 탭
############################################################
with tab_m:
    st.subheader("📥 오전 텍스트 입력")
    text_m = st.text_area("오전 텍스트 입력", height=200, key="txt_m")
    period_m = st.selectbox("교시 선택", [1,2], index=0)

    if st.button("1) 근무자 자동 추출", key="m_extract"):
        staff_names=extract_staff(text_m)
        edu_map,course_list=extract_extra(text_m)
        st.session_state["m_staff"]=staff_names
        st.session_state["m_edu"]=edu_map
        st.session_state["m_course"]=course_list
        st.success(f"근무자 {len(staff_names)}명 추출 완료")

    if "m_staff" in st.session_state:
        st.subheader("✏ 근무자 수정")
        df_m=pd.DataFrame({"근무자":st.session_state["m_staff"]})
        edited_m=st.data_editor(df_m,num_rows="dynamic",key="edit_m")
        final_staff=edited_m["근무자"].dropna().tolist()

        st.subheader("🎓 교양 담당자 수정 (1·2교시만)")
        edu_fix=st.selectbox("교양 담당자 선택",["(없음)"]+final_staff,key="m_edu_fix")
        st.session_state["m_edu_fix"]=edu_fix if edu_fix!="(없음)" else None

        st.subheader("🛠 코스 담당자 수정 (복수 선택 가능)")
        course_fix=st.multiselect("코스 담당자 선택",final_staff,
            default=[x for x in st.session_state.get("m_course",[]) if x in final_staff],
            key="m_course_fix")

        st.subheader("📊 수요 입력")
        c1,c2,c3,c4=st.columns(4)
        demand={"1M":c1.number_input("1종수동",0),
                "1A":c2.number_input("1종자동",0),
                "2A":c3.number_input("2종자동",0),
                "2M":c4.number_input("2종수동",0)}

        if st.button("2) 오전 배정 실행", key="m_run"):
            staff_list=[Staff(n) for n in final_staff]
            for s in staff_list:
                if st.session_state.get("m_edu_fix")==s.name:
                    s.is_edu[period_m]=True
                if s.name in course_fix:
                    s.is_course=True
            result=assign_one_period(staff_list,period_m,demand,True)
            pairs=make_pairs(staff_list,result)

            st.subheader("📌 배정 결과")
            rows=[]
            for s in staff_list:
                info=result[s.name]
                desc=[f"{t.replace('1M','1종수동').replace('1A','1종자동').replace('2A','2종자동').replace('2M','2종수동')} {info[t]}명"
                      for t in info if info[t]>0]
                rows.append((s.name," / ".join(desc) if desc else "0"))
            st.table({"감독관":[x[0] for x in rows],"배정":[x[1] for x in rows]})
            if pairs:
                st.markdown("#### 👥 짝짓기 결과")
                for p in pairs: st.write(p)
            st.markdown("#### 🔢 최종 가중치")
            st.table({"감독관":[s.name for s in staff_list],"Load":[s.load for s in staff_list]})
            if st.button("🧽 가중치 초기화(오전)"):
                st.success("초기화 완료(다음 배정부터 새 계산)")

############################################################
# 🌇 오후 탭 (코스 담당자 없음)
############################################################
with tab_a:
    st.subheader("📥 오후 텍스트 입력")
    text_a=st.text_area("오후 텍스트 입력",height=200,key="txt_a")
    period_a=st.selectbox("교시 선택",[3,4,5],index=0)

    if st.button("1) 근무자 자동 추출",key="a_extract"):
        staff_names=extract_staff(text_a)
        edu_map,_=extract_extra(text_a)
        st.session_state["a_staff"]=staff_names
        st.session_state["a_edu"]=edu_map
        st.success(f"근무자 {len(staff_names)}명 추출 완료")

    if "a_staff" in st.session_state:
        st.subheader("✏ 근무자 수정")
        df_a=pd.DataFrame({"근무자":st.session_state["a_staff"]})
        edited_a=st.data_editor(df_a,num_rows="dynamic",key="edit_a")
        final_staff=edited_a["근무자"].dropna().tolist()

        st.subheader("🎓 교양 담당자 수정 (3~5교시만)")
        edu_fix=st.selectbox("교양 담당자 선택",["(없음)"]+final_staff,key="a_edu_fix")
        st.session_state["a_edu_fix"]=edu_fix if edu_fix!="(없음)" else None

        st.subheader("📊 수요 입력")
        c1,c2,c3,c4=st.columns(4)
        demand={"1M":c1.number_input("1종수동",0),
                "1A":c2.number_input("1종자동",0),
                "2A":c3.number_input("2종자동",0),
                "2M":c4.number_input("2종수동",0)}

        if st.button("2) 오후 배정 실행",key="a_run"):
            staff_list=[Staff(n) for n in final_staff]
            for s in staff_list:
                if st.session_state.get("a_edu_fix")==s.name:
                    s.is_edu[period_a]=True
            result=assign_one_period(staff_list,period_a,demand,False)
            pairs=make_pairs(staff_list,result)
            st.subheader("📌 배정 결과")
            rows=[]
            for s in staff_list:
                info=result[s.name]
                desc=[f"{t.replace('1M','1종수동').replace('1A','1종자동').replace('2A','2종자동').replace('2M','2종수동')} {info[t]}명"
                      for t in info if info[t]>0]
                rows.append((s.name," / ".join(desc) if desc else "0"))
            st.table({"감독관":[x[0] for x in rows],"배정":[x[1] for x in rows]})
            if pairs:
                st.markdown("#### 👥 짝짓기 결과")
                for p in pairs: st.write(p)
            st.markdown("#### 🔢 최종 가중치")
            st.table({"감독관":[s.name for s in staff_list],"Load":[s.load for s in staff_list]})
            if st.button("🧽 가중치 초기화(오후)"):
                st.success("초기화 완료")

############################################################
# 🎲 랜덤 결과 탭
############################################################
with tab_r:
    st.subheader("🎲 랜덤 우선배정 결과")
    hist=load_json(HISTORY_FILE,[])
    if not hist: st.info("랜덤 기록이 없습니다.")
    else:
        st.table({"감독관":hist})
    if st.button("🧹 랜덤 결과 초기화"):
        reset_history()
        st.success("랜덤 결과 초기화 완료!")
