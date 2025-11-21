##############################################################
# auto.py — 도로주행 자동 배정 (최종 통합판)
# 공평성 모델 + 코스/교양 가중치 + 랜덤 3일 제외 + pairing
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
        self.prev_zero = False
        self.need_low_next = False

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
        if (today - d).days <= 3 and h["name"] == name:
            return True
    return False

def record_random(hist, name, period):
    hist.append({
        "date": date.today().isoformat(),
        "name": name,
        "period": period
    })
    save_history(hist)

##############################################################
# 자격 체크
##############################################################
def eligible(st, typecode):
    # 수동 가능 → 모든 시험 가능
    if st.is_manual:
        return True
    # 자동 전용 → 1A, 2A만 가능
    return typecode in ("1A", "2A")

##############################################################
# 가중치 적용 (코스/교양은 우선순위만 낮춤)
##############################################################
def apply_weights(staff, period, is_morning):
    for s in staff:
        w = 0

        # 코스 패널티: 오전 1교시
        if is_morning and period == 1 and s.is_course:
            w += 1
        
        # 코스 연장: 오전 2교시
        if is_morning and period == 2 and s.need_low_next:
            w += 1

        # 교양 패널티: (k-1)교시
        for k in [2,4,5]:
            if s.is_edu[k] and period == k-1:
                w += 1

        # 중복 최대 1
        if w > 1:
            w = 1

        s.load += w

##############################################################
# 랜덤 선택 (최근 3일 제외)
##############################################################
def pick_random_candidate(staff, idx_list, period, hist):
    filtered = [i for i in idx_list if not used_recently(hist, staff[i].name)]
    if filtered:
        pick = random.choice(filtered)
        record_random(hist, staff[pick].name, period)
        return pick

    pick = random.choice(idx_list)
    record_random(hist, staff[pick].name, period)
    return pick

##############################################################
# 공평성 강제: 최대–최소 ≤ 1
##############################################################
def enforce_fairness(staff, assigned, total, base_cap):
    n = len(staff)

    def mix(idx):
        c = sum( 1 for v in assigned[idx].values() if v > 0 )
        return 1 if c >= 2 else 0

    def fair(idx):
        return total[idx] + mix(idx)

    for _ in range(60):
        scores = [fair(i) for i in range(n)]
        mx = max(scores)
        mn = min(scores)
        if mx - mn <= 1:
            return

        i_max = scores.index(mx)
        i_min = scores.index(mn)

        moved = False
        for t in ("1M","1A","2A","2M"):
            if assigned[i_max][t] > 0 and eligible(staff[i_min], t) and total[i_min] < base_cap:
                assigned[i_max][t] -= 1
                assigned[i_min][t] += 1
                total[i_max] -= 1
                total[i_min] += 1
                moved = True
                break

        if not moved:
            return

##############################################################
# 한 교시 배정
##############################################################
def assign_period(staff, period, demand, is_morning):

    # prev_zero 적용
    for s in staff:
        if s.prev_zero:
            s.load += 1
        s.prev_zero = False

    # 코스/교양 가중치
    apply_weights(staff, period, is_morning)

    # cap
    base_cap = 2 if period in (1,5) else 3

    n = len(staff)
    assigned = [
        {"1M":0,"1A":0,"2A":0,"2M":0}
        for _ in range(n)
    ]
    total = [0]*n

    hist = load_history()

    order = [
        ("1M", demand.get("1M",0)),
        ("1A", demand.get("1A",0)),
        ("2A", demand.get("2A",0)),
        ("2M", demand.get("2M",0)),
    ]

    # Load 낮은 순서 배정
    for typ, need in order:
        for _ in range(need):

            # (1) 최소 load 찾기
            min_load = None
            for i,s in enumerate(staff):
                if total[i] < base_cap and eligible(s,typ):
                    if min_load is None or s.load < min_load:
                        min_load = s.load

            if min_load is None:
                continue

            # (2) 동점자
            idx_list = [
                i for i,s in enumerate(staff)
                if total[i] < base_cap
                and eligible(s,typ)
                and abs(s.load - min_load) < 1e-9
            ]

            # (3) 랜덤 선정
            if len(idx_list) == 1:
                pick = idx_list[0]
            else:
                pick = pick_random_candidate(staff, idx_list, period, hist)

            assigned[pick][typ] += 1
            total[pick] += 1

    # (4) 공평성 강제
    enforce_fairness(staff, assigned, total, base_cap)

    # (5) load + prev_zero
    for i,s in enumerate(staff):
        s.load += total[i]
        s.prev_zero = (total[i] == 0)

    # (6) 코스 연장 (1→2교시)
    if is_morning and period == 1:
        min_val = min(total)
        for i,s in enumerate(staff):
            s.need_low_next = (s.is_course and total[i] > min_val)
    else:
        for s in staff:
            s.need_low_next = False

    save_history(hist)
    return assigned, total

##############################################################
# 배정 결과 pairing 표시
##############################################################
def pair_results(staff, total):
    """
    배정 1 또는 0일 때 짝지어 표시
    예) 배정1 vs 배정1 → 김병욱-김성연
        배정1 vs 배정0 → 김병욱-김성연(참관)
    """
    ones = []
    zeros = []
    for i,s in enumerate(staff):
        if total[i] == 1:
            ones.append(s.name)
        elif total[i] == 0:
            zeros.append(s.name)

    pairs = []
    used0 = set()

    # 1명끼리 pairing
    for i in range(0, len(ones), 2):
        if i+1 < len(ones):
            pairs.append(f"{ones[i]} - {ones[i+1]}")
        else:
            # 홀수 1명 발생 → 0명과 pairing
            if zeros:
                z = zeros.pop(0)
                pairs.append(f"{ones[i]} - {z}(참관)")
                used0.add(z)
            else:
                pairs.append(f"{ones[i]} - (단독)")

    # 남은 0명
    for z in zeros:
        if z not in used0:
            pairs.append(f"{z}(참관)")

    return pairs

##############################################################
# STREAMLIT UI
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
            st.write("다음교시 교양자:", edu_map)
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

        # 교양(다음교시 적용) → 2교시만 해당
        edu2_sel = st.selectbox("2교시 교양 담당자", ["없음"] + final_m,
                                index=0 if 2 not in st.session_state["m_edu"] else
                                (final_m.index(st.session_state["m_edu"][2])+1))

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
            st.subheader("🔢 최종 Load(가중치)")
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
            st.session_state["a_course"] = course_list  # 오후 코스는 사용 X (룰상 제외)

            st.success("자동 추출 완료!")
            st.write("근무자:", staff_raw)
            st.write("다음교시 교양자:", edu_map)

    # 수정
    if "a_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정")
        df = pd.DataFrame({"근무자": st.session_state["a_staff_raw"]})
        edited = st.data_editor(df, num_rows="dynamic", key="a_edit")
        final_a = edited["근무자"].dropna().tolist()
        st.session_state["a_staff"] = final_a

        # 오후는 코스 제외, 4·5교시 교양만 존재
        st.subheader("🛠 교양 수정")

        edu_sel = {}
        for k in [4,5]:
            sel = st.selectbox(f"{k}교시 교양 담당자", ["없음"]+final_a,
                               key=f"a_edu_{k}")
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
            st.subheader("🔢 최종 Load(가중치)")
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
    if not hist:
        st.info("랜덤 기록 없음")
    else:
        st.table(pd.DataFrame(hist))
