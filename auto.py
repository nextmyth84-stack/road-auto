import streamlit as st
import re
import json
import random
import os
from datetime import datetime, timedelta

# ============================================================
# 기본 설정
# ============================================================

MANUAL_STAFF = {
    "권한솔", "김남균", "김성연", "김주현", "이호석", "조정래"
}

HISTORY_FILE = "random_history.json"

st.set_page_config(layout="wide")
st.title("🚗 도로주행 자동 배정 시스템 (Python v15 로직)")

# ============================================================
# 랜덤 히스토리 관리 (조건 8)
# ============================================================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def prune_history(history):
    today = datetime.now().date()
    new_hist = {}
    for d_str, lst in history.items():
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except:
            continue
        if (today - d).days <= 3:
            new_hist[d_str] = lst
    return new_hist

def record_random_pick(name, period, type_code):
    history = load_history()
    today_str = datetime.now().strftime("%Y-%m-%d")
    if today_str not in history:
        history[today_str] = []
    history[today_str].append({
        "name": name,
        "period": period,
        "type": type_code,
    })
    history = prune_history(history)
    save_history(history)

def is_recent_random(name):
    history = load_history()
    today = datetime.now().date()
    for d_str, lst in history.items():
        try:
            d = datetime.strptime(d_str, "%Y-%m-%d").date()
        except:
            continue
        if (today - d).days > 3:
            continue
        for item in lst:
            if item.get("name") == name:
                return True
    return False

# ============================================================
# Staff 구조체 (조건 3·4용 자격 + 조건 1·5·6·7용 상태값)
# ============================================================

class Staff:
    def __init__(self, name, is_manual=False):
        self.name = name
        self.is_manual = is_manual     # 수동 가능자(1M/2M 가능)
        self.is_course = False         # 코스 점검자 여부 (조건 5)
        self.is_edu = {1:False, 2:False, 3:False, 4:False, 5:False}  # 교양 담당 (조건 6)

        self.load = 0.0                # 누적 Load (적을수록 우선)
        self.skipped_prev = False      # 직전 교시 미배정 여부
        self.need_low_next = False     # 코스 혜택 연장 플래그(1→2교시)

    def eligible(self, type_code: str) -> bool:
        # 조건 3·4
        # 1M/2M → 수동 가능자만
        if type_code in ["1M", "2M"]:
            return self.is_manual
        # 1A/2A → 전체 가능
        return True

# ============================================================
# 파서: 텍스트 → (1종수동/2종자동/교양/코스) 추출
# ============================================================

NAME_RE = re.compile(r"[가-힣]{2,4}")

NAME_BLACKLIST = {
    "교시", "코스", "종수동", "종자동",
    "합격", "불합격", "마감", "오전", "오후"
}

def extract_name(line: str):
    # 차량호 뒤에 오는 이름 중, 블랙리스트 제외
    found = NAME_RE.findall(line)
    for nm in found:
        if nm in NAME_BLACKLIST:
            continue
        return nm
    return None

def parse_text(raw: str):
    """
    텍스트에서:
    - 1종수동: 1줄 (차량호 뒤 이름)
    - 2종자동: 리스트(•)
    - 교양: 1~5교시
    - 코스점검:
    만 추출.
    열쇠/1종자동(감독관 없음)/마감/오전대비비교 등은 스킵.
    """
    lines = [l.strip() for l in raw.split("\n") if l.strip()]

    one_manual = []      # 1종수동 감독관 이름들
    two_auto = []        # 2종자동 감독관 이름들
    edu = {}             # {교시: 이름}
    course_check = []    # 코스 점검자 이름들

    in_two_auto = False
    in_course = False
    in_dead_vehicle = False

    for line in lines:

        # 마감 차량 블록
        if "마감 차량" in line:
            in_dead_vehicle = True
            continue
        if in_dead_vehicle:
            if not (line.startswith("[") or line.startswith("•") or "호 마감" in line):
                in_dead_vehicle = False
            else:
                continue

        # 열쇠: 스킵
        if line.startswith("열쇠:"):
            continue

        # 교양 (n교시: 이름)
        if "교시:" in line:
            try:
                period = int(line.split("교시")[0])
            except:
                period = None
            nm = extract_name(line)
            if period and nm:
                edu[period] = nm
            continue

        # 1종수동: 9호 김주현
        if line.startswith("1종수동:"):
            nm = extract_name(line)
            if nm:
                one_manual.append(nm)
            continue

        # 1종자동: 감독관 없음 → 스킵
        if line.startswith("1종자동:"):
            continue

        # 2종자동 시작
        if line.startswith("2종자동"):
            in_two_auto = True
            continue

        # 2종자동 항목 (• 6호 김지은)
        if in_two_auto and line.startswith("•"):
            nm = extract_name(line)
            if nm:
                two_auto.append(nm)
            continue

        # 코스점검 블록 시작
        if "코스점검" in line:
            in_two_auto = False
            in_course = True
            continue

        # 코스점검 항목
        if in_course and line.startswith("•"):
            nm = extract_name(line)
            if nm:
                course_check.append(nm)
            continue

        if in_course and not line.startswith("•"):
            in_course = False

        # '오전 대비 비교', '신규 인원' 등은 스킵

    return {
        "one_manual": list(dict.fromkeys(one_manual)),
        "two_auto": list(dict.fromkeys(two_auto)),
        "edu": edu,
        "course_check": list(dict.fromkeys(course_check)),
    }

def build_staff_list_from_text(raw: str):
    p = parse_text(raw)
    names = set()
    names.update(p["one_manual"])
    names.update(p["two_auto"])
    names.update(p["edu"].values())
    names.update(p["course_check"])

    staff_list = []
    for nm in sorted(names):
        st = Staff(name=nm, is_manual=(nm in MANUAL_STAFF))
        # 교양 플래그
        for k, v in p["edu"].items():
            if v == nm:
                st.is_edu[k] = True
        # 코스 플래그
        if nm in p["course_check"]:
            st.is_course = True
        staff_list.append(st)

    return staff_list

# ============================================================
# 조건 5·6: 코스/교양 가중치 적용
# ============================================================

def apply_edu_weight(staff_list, period: int):
    """
    조건 6:
    1,3교시를 제외한 교양담당자는 전교시에서 배정을 덜 받는다.
    → k교시 교양 담당자는 (k-1)교시에 Load +0.8 적용
      (단, k=1,3은 제외)
    """
    EDU_PREV = 0.8
    for st in staff_list:
        for k in range(2, 6):  # 2~5교시 교양
            if k == 3:
                # 3교시는 혜택 제외
                continue
            if period == k - 1 and st.is_edu.get(k, False):
                st.load += EDU_PREV

def apply_course_weight(staff_list, period: int, is_morning: bool):
    """
    조건 5:
    코스 점검자는 오전에 +1배정된 효과가 있다.
    - 오전 1교시: 코스 담당자 Load +1
    - 오전 1교시 배정 결과 기준으로, 더 많이 한 코스 담당자는 2교시에도 Load +1 (need_low_next)
    """
    COURSE_W = 1.0
    if is_morning and period == 1:
        for st in staff_list:
            if st.is_course:
                st.load += COURSE_W
    if is_morning and period == 2:
        for st in staff_list:
            if st.need_low_next:
                st.load += COURSE_W

# ============================================================
# 조건 8: 동점자 랜덤 + 3일 히스토리
# ============================================================

def pick_random_index(staff_list, cand_idx_list, period, type_code):
    # 최근 3일 히스토리 제외
    filtered = [i for i in cand_idx_list if not is_recent_random(staff_list[i].name)]
    if filtered:
        idx = random.choice(filtered)
        record_random_pick(staff_list[idx].name, period, type_code)
        return idx

    # 전원 최근 당첨자라면 그냥 후보 중 랜덤
    idx = random.choice(cand_idx_list)
    record_random_pick(staff_list[idx].name, period, type_code)
    return idx

# ============================================================
# 조건 3·4·1·7: 종별 배정 + 공평성/혼합효과
# ============================================================

def assign_one_type(staff_list, assigned_total, arr_type,
                    base_cap, period, type_code, demand):
    """
    한 종별(type_code)에 대해 Load 기반 배정.
    조건 3·4 자격체크, 조건 8 랜덤 포함.
    """
    if demand <= 0:
        return

    need = demand
    n = len(staff_list)
    guard = 0

    while need > 0 and guard < 2000:
        guard += 1

        # 1) 최소 Load 찾기
        min_load = None
        has_candidate = False

        for i, st in enumerate(staff_list):
            if assigned_total[i] < base_cap and st.eligible(type_code):
                if not has_candidate:
                    min_load = st.load
                    has_candidate = True
                else:
                    if st.load < min_load:
                        min_load = st.load

        if not has_candidate:
            raise ValueError(f"{period}교시 {type_code} {demand}명 배정 불가(자격 or baseCap 문제)")

        # 2) 동점자 후보들
        cand_idx = []
        for i, st in enumerate(staff_list):
            if assigned_total[i] < base_cap and st.eligible(type_code):
                if abs(st.load - min_load) < 1e-9:
                    cand_idx.append(i)

        if not cand_idx:
            break

        # 3) 랜덤 선택 (조건 8)
        if len(cand_idx) == 1:
            pick = cand_idx[0]
        else:
            pick = pick_random_index(staff_list, cand_idx, period, type_code)

        # 4) 반영
        arr_type[pick] += 1
        assigned_total[pick] += 1
        need -= 1

def balance_fairness(staff_list, arr1M, arr1A, arr2A, arr2M, base_cap):
    """
    조건 1 + 조건 7:
    - 혼합배정(여러 종별)을 한 사람은 +1 효과
    - 공평성 점수(fair) = 실제배정수 + 혼합효과
    - max-min > 1 이면, 많이 받은 사람→적게 받은 사람으로 1개 이동
    """
    n = len(staff_list)
    guard = 0

    while True:
        guard += 1
        if guard > 200:
            break

        assigned = []
        mixed_eff = []

        for i in range(n):
            total = arr1M[i] + arr1A[i] + arr2A[i] + arr2M[i]
            assigned.append(total)

            t_cnt = 0
            if arr1M[i] > 0: t_cnt += 1
            if arr1A[i] > 0: t_cnt += 1
            if arr2A[i] > 0: t_cnt += 1
            if arr2M[i] > 0: t_cnt += 1
            mixed_eff.append(1 if t_cnt >= 2 else 0)

        fair = [assigned[i] + mixed_eff[i] for i in range(n)]
        max_val = max(fair)
        min_val = min(fair)
        if max_val - min_val <= 1:
            break

        idx_max = fair.index(max_val)
        idx_min = fair.index(min_val)

        # 이동 가능한 종별 순서대로 시도
        moved = False
        for t in ["1M", "1A", "2A", "2M"]:
            if t == "1M" and arr1M[idx_max] > 0 and staff_list[idx_min].eligible("1M") and assigned[idx_min] < base_cap:
                arr1M[idx_max] -= 1
                arr1M[idx_min] += 1
                moved = True
                break
            if t == "1A" and arr1A[idx_max] > 0 and staff_list[idx_min].eligible("1A") and assigned[idx_min] < base_cap:
                arr1A[idx_max] -= 1
                arr1A[idx_min] += 1
                moved = True
                break
            if t == "2A" and arr2A[idx_max] > 0 and staff_list[idx_min].eligible("2A") and assigned[idx_min] < base_cap:
                arr2A[idx_max] -= 1
                arr2A[idx_min] += 1
                moved = True
                break
            if t == "2M" and arr2M[idx_max] > 0 and staff_list[idx_min].eligible("2M") and assigned[idx_min] < base_cap:
                arr2M[idx_max] -= 1
                arr2M[idx_min] += 1
                moved = True
                break

        if not moved:
            break

# ============================================================
# 전체 세션 배정 (1~2교시 / 3~5교시 한 번에)
# ============================================================

def allocate_session(staff_list, demand_dict, is_morning: bool):
    """
    demand_dict = { 교시: {"1M":n, "1A":n, "2A":n, "2M":n}, ... }
    is_morning=True  → 1,2교시
    is_morning=False → 3,4,5교시
    """
    periods = [1,2] if is_morning else [3,4,5]
    results = {}

    for period in periods:
        if period not in demand_dict:
            continue

        # 직전 교시 미배정 보정
        for st in staff_list:
            if st.skipped_prev:
                st.load -= 0.8
            st.skipped_prev = False

        # 교양 가중치 (조건 6)
        apply_edu_weight(staff_list, period)
        # 코스 가중치 (조건 5)
        apply_course_weight(staff_list, period, is_morning)

        d = demand_dict.get(period, {"1M":0,"1A":0,"2A":0,"2M":0})
        # baseCap: 1·5교시=2, 나머지=3 (v15 기준)
        base_cap = 2 if period in [1,5] else 3

        n = len(staff_list)
        assigned_total = [0]*n
        arr1M = [0]*n
        arr1A = [0]*n
        arr2A = [0]*n
        arr2M = [0]*n

        # 종별 배정
        assign_one_type(staff_list, assigned_total, arr1M, base_cap, period, "1M", d["1M"])
        assign_one_type(staff_list, assigned_total, arr1A, base_cap, period, "1A", d["1A"])
        assign_one_type(staff_list, assigned_total, arr2A, base_cap, period, "2A", d["2A"])
        assign_one_type(staff_list, assigned_total, arr2M, base_cap, period, "2M", d["2M"])

        # 공평성/혼합효과 재조정 (조건 1·7)
        balance_fairness(staff_list, arr1M, arr1A, arr2A, arr2M, base_cap)

        # 1교시 코스 혜택 → 2교시 연장 플래그 세팅
        if is_morning and period == 1:
            totals = [arr1M[i]+arr1A[i]+arr2A[i]+arr2M[i] for i in range(n)]
            if totals:
                min_assign = min(totals)
            else:
                min_assign = 0
            for i, st in enumerate(staff_list):
                st.need_low_next = (st.is_course and totals[i] > min_assign)
        else:
            for st in staff_list:
                st.need_low_next = False

        # 결과 정리 + 다음 교시를 위한 load/skipPrev 업데이트
        period_result = {}
        for i, st in enumerate(staff_list):
            m1 = arr1M[i]
            a1 = arr1A[i]
            a2 = arr2A[i]
            m2 = arr2M[i]
            period_result[st.name] = {
                "1M": m1,
                "1A": a1,
                "2A": a2,
                "2M": m2,
            }
            total = m1+a1+a2+m2
            st.skipped_prev = (total == 0)
            st.load += total

        results[period] = period_result

    return results

# ============================================================
# Streamlit UI
# ============================================================

st.markdown("### 수동 가능자 (1M/2M 가능)")
st.write(", ".join(sorted(MANUAL_STAFF)))
st.markdown("---")

tab_m, tab_a = st.tabs(["🌅 오전 자동배정", "🌇 오후 자동배정"])

# --------------------- 오전 탭 ------------------------------
with tab_m:
    st.subheader("📥 오전 텍스트 붙여넣기")
    morning_text = st.text_area("오전 교양/차량배정 텍스트", height=220)

    st.subheader("⏱ 오전 배정 교시 선택")
    morning_periods = st.multiselect(
        "배정할 교시 선택",
        [1,2],
        default=[1,2],
    )

    st.subheader("👥 오전 각 교시 수요 입력")
    demand_m = {}
    for p in morning_periods:
        st.markdown(f"**{p}교시 수요**")
        c1, c2, c3, c4 = st.columns(4)
        demand_m[p] = {
            "1M": c1.number_input(f"{p}교시 1종수동", min_value=0, step=1, key=f"m_{p}_1M"),
            "1A": c2.number_input(f"{p}교시 1종자동", min_value=0, step=1, key=f"m_{p}_1A"),
            "2A": c3.number_input(f"{p}교시 2종자동", min_value=0, step=1, key=f"m_{p}_2A"),
            "2M": c4.number_input(f"{p}교시 2종수동", min_value=0, step=1, key=f"m_{p}_2M"),
        }

    if st.button("🚀 오전 자동배정 실행"):
        if not morning_text.strip():
            st.error("오전 텍스트를 먼저 입력하세요.")
        else:
            staff_list_m = build_staff_list_from_text(morning_text)
            st.markdown("#### 추출된 감독관 목록")
            st.write([s.name for s in staff_list_m])

            # 선택한 교시만 demand_dict에 넣기
            demand_dict_m = {p: demand_m[p] for p in morning_periods}
            result_m = allocate_session(staff_list_m, demand_dict_m, is_morning=True)

            st.markdown("#### 오전 배정 결과")
            for p in sorted(result_m.keys()):
                st.markdown(f"**{p}교시**")
                rows = []
                for name, info in result_m[p].items():
                    parts = []
                    if info["1M"] > 0: parts.append(f"1종수동 {info['1M']}명")
                    if info["1A"] > 0: parts.append(f"1종자동 {info['1A']}명")
                    if info["2A"] > 0: parts.append(f"2종자동 {info['2A']}명")
                    if info["2M"] > 0: parts.append(f"2종수동 {info['2M']}명")
                    rows.append((name, " / ".join(parts) if parts else "0"))
                st.table({"감독관": [r[0] for r in rows],
                          "배정": [r[1] for r in rows]})

# --------------------- 오후 탭 ------------------------------
with tab_a:
    st.subheader("📥 오후 텍스트 붙여넣기")
    afternoon_text = st.text_area("오후 교양/차량배정 텍스트", height=220)

    st.subheader("⏱ 오후 배정 교시 선택")
    afternoon_periods = st.multiselect(
        "배정할 교시 선택",
        [3,4,5],
        default=[3,4,5],
    )

    st.subheader("👥 오후 각 교시 수요 입력")
    demand_a = {}
    for p in afternoon_periods:
        st.markdown(f"**{p}교시 수요**")
        c1, c2, c3, c4 = st.columns(4)
        demand_a[p] = {
            "1M": c1.number_input(f"{p}교시 1종수동", min_value=0, step=1, key=f"a_{p}_1M"),
            "1A": c2.number_input(f"{p}교시 1종자동", min_value=0, step=1, key=f"a_{p}_1A"),
            "2A": c3.number_input(f"{p}교시 2종자동", min_value=0, step=1, key=f"a_{p}_2A"),
            "2M": c4.number_input(f"{p}교시 2종수동", min_value=0, step=1, key=f"a_{p}_2M"),
        }

    if st.button("🚀 오후 자동배정 실행"):
        if not afternoon_text.strip():
            st.error("오후 텍스트를 먼저 입력하세요.")
        else:
            staff_list_a = build_staff_list_from_text(afternoon_text)
            st.markdown("#### 추출된 감독관 목록")
            st.write([s.name for s in staff_list_a])

            demand_dict_a = {p: demand_a[p] for p in afternoon_periods}
            result_a = allocate_session(staff_list_a, demand_dict_a, is_morning=False)

            st.markdown("#### 오후 배정 결과")
            for p in sorted(result_a.keys()):
                st.markdown(f"**{p}교시**")
                rows = []
                for name, info in result_a[p].items():
                    parts = []
                    if info["1M"] > 0: parts.append(f"1종수동 {info['1M']}명")
                    if info["1A"] > 0: parts.append(f"1종자동 {info['1A']}명")
                    if info["2A"] > 0: parts.append(f"2종자동 {info['2A']}명")
                    if info["2M"] > 0: parts.append(f"2종수동 {info['2M']}명")
                    rows.append((name, " / ".join(parts) if parts else "0"))
                st.table({"감독관": [r[0] for r in rows],
                          "배정": [r[1] for r in rows]})
