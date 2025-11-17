import streamlit as st
import re
import json
import random
from datetime import datetime

# ============================================================
# 수동가능자 상수 (1M/2M 가능)
# ============================================================

MANUAL_STAFF = {
    "권한솔", "김남균", "김성연", "김주현", "이호석", "조정래"
}

HISTORY_FILE = "random_history.json"

# ============================================================
# 랜덤 히스토리 관리 (조건 8)
# ============================================================

def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = {}
    return data

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def prune_history(history):
    """최근 3일만 유지"""
    today = datetime.now().date()
    new_hist = {}
    for d_str, lst in history.items():
        try:
            dt = datetime.strptime(d_str, "%Y-%m-%d").date()
        except:
            continue
        if (today - dt).days <= 3:
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
            dt = datetime.strptime(d_str, "%Y-%m-%d").date()
        except:
            continue
        if (today - dt).days > 3:
            continue
        for item in lst:
            if item.get("name") == name:
                return True
    return False

# ============================================================
# Staff 구조체 (VBA Type staff 대응)
# ============================================================

class Staff:
    def __init__(self, name, is_manual=False):
        self.name = name
        self.is_manual = is_manual  # 수동 가능 여부
        self.is_course = False      # 코스점검 여부
        # 교양 담당 여부 (1~5교시)
        self.is_edu = {1: False, 2: False, 3: False, 4: False, 5: False}

        self.load = 0.0
        self.skipped_prev = False
        self.need_low_next = False  # 코스 혜택 연장

# ============================================================
# 파서(parser) — 오전/오후 텍스트 → 이름/교양/코스 추출
# ============================================================

NAME_RE = re.compile(r"[가-힣]{2,4}")

def extract_name(line: str):
    found = NAME_RE.findall(line)
    return found[0] if found else None

def parse_text(raw: str):
    """
    텍스트에서:
    - 1종수동 / 2종자동 근무자
    - 교양(1~5교시)
    - 코스점검자
    추출
    """
    lines = [l.strip() for l in raw.split("\n") if l.strip()]

    one_manual = []      # 1종수동 이름들
    two_auto = []        # 2종자동 이름들
    edu = {}             # {교시: 이름}
    course_check = []    # 코스점검자 이름
    # 열쇠/마감/신규·제외 등은 전부 스킵
    in_two_auto = False
    in_course = False
    in_dead_vehicle = False

    for line in lines:
        # 마감 차량 블록 시작/스킵
        if "마감 차량" in line:
            in_dead_vehicle = True
            continue
        if in_dead_vehicle:
            if not (line.startswith("[") or line.startswith("•") or "호 마감" in line):
                in_dead_vehicle = False
            else:
                continue

        # 열쇠: 완전 스킵
        if line.startswith("열쇠:"):
            continue

        # 교양 담당자 (1~5교시)
        if "교시:" in line:
            try:
                period = int(line.split("교시")[0])
            except:
                period = None
            nm = extract_name(line)
            if period and nm:
                edu[period] = nm
            continue

        # 1종수동
        if line.startswith("1종수동:"):
            nm = extract_name(line)
            if nm:
                one_manual.append(nm)
            continue

        # 1종자동: 항상 스킵 (감독관 없음)
        if line.startswith("1종자동:"):
            continue

        # 2종자동 시작
        if line.startswith("2종자동"):
            in_two_auto = True
            continue

        # 2종자동 리스트
        if in_two_auto and line.startswith("•"):
            nm = extract_name(line)
            if nm:
                two_auto.append(nm)
            continue

        # 코스점검 시작
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

        # '오전 대비 비교', '신규 인원' 등의 블록은 별도 키워드지만
        # 지금은 전부 스킵(사용 안 함)

    return {
        "one_manual": list(dict.fromkeys(one_manual)),
        "two_auto": list(dict.fromkeys(two_auto)),
        "edu": edu,
        "course_check": list(dict.fromkeys(course_check)),
    }

def build_staff_list_from_text(raw: str):
    """
    텍스트 하나(오전 또는 오후)에서 오늘 감독관 리스트(staffArr) 구성.
    - 1종수동
    - 2종자동
    - 교양 담당자(1~5)
    - 코스점검자
    전부 합쳐서 staff 리스트 생성.
    """
    parsed = parse_text(raw)
    names = set()
    names.update(parsed["one_manual"])
    names.update(parsed["two_auto"])
    names.update(parsed["edu"].values())
    names.update(parsed["course_check"])

    # Staff 객체 생성
    staff_list = []
    for nm in sorted(names):
        st = Staff(name=nm, is_manual=(nm in MANUAL_STAFF))
        # 교양 플래그
        for k, v in parsed["edu"].items():
            if v == nm:
                st.is_edu[k] = True
        # 코스 플래그
        if nm in parsed["course_check"]:
            st.is_course = True

        staff_list.append(st)

    return staff_list

# ============================================================
# 배정 엔진 (VBA v15 로직 그대로)
# ============================================================

def is_eligible(staff: Staff, type_code: str) -> bool:
    # 1M, 2M → 수동가능자만
    if type_code in ["1M", "2M"]:
        return staff.is_manual
    # 1A, 2A → 전체 가능
    return True

def apply_edu_weight(staff_list, period: int):
    EDU_PREV = 0.8
    for st in staff_list:
        for k in range(2, 6):  # 2~5교시 교양 → 전교시(1~4)에 혜택
            if k == 3:
                # 3교시 교양은 혜택 제외 (조건: 1,3교시 제외)
                continue
            if period == k - 1 and st.is_edu.get(k, False):
                st.load += EDU_PREV

def apply_course_weight(staff_list, period: int, is_morning: bool):
    COURSE_W = 1.0
    # 오전 1교시 코스 담당 → 가중치
    if is_morning and period == 1:
        for st in staff_list:
            if st.is_course:
                st.load += COURSE_W
    # 오전 2교시: NeedLowNext=True 인 경우 가중치
    if is_morning and period == 2:
        for st in staff_list:
            if st.need_low_next:
                st.load += COURSE_W

def pick_random_index(staff_list, candidates, period, type_code):
    # 최근 3일 랜덤 당첨자 제외
    filtered = [idx for idx in candidates if not is_recent_random(staff_list[idx].name)]
    if filtered:
        if len(filtered) == 1:
            pick = filtered[0]
        else:
            pick = random.choice(filtered)
        record_random_pick(staff_list[pick].name, period, type_code)
        return pick

    # 모두 최근 당첨자면 전체 후보 중에서 랜덤
    pick = random.choice(candidates)
    record_random_pick(staff_list[pick].name, period, type_code)
    return pick

def assign_one_type(staff_list, assigned_total, arr_type,
                    base_cap, period, type_code, demand):
    if demand <= 0:
        return

    need = demand
    n = len(staff_list)
    guard = 0

    while need > 0 and guard < 2000:
        guard += 1
        # 1) 최소 load 찾기
        min_load = None
        has_candidate = False
        for i, st in enumerate(staff_list):
            if assigned_total[i] < base_cap and is_eligible(st, type_code):
                if not has_candidate:
                    min_load = st.load
                    has_candidate = True
                else:
                    if st.load < min_load:
                        min_load = st.load

        if not has_candidate:
            raise ValueError(f"{period}교시 {type_code} 수요 {demand}명 배정 불가")

        # 2) 동점자 후보
        candidates = []
        for i, st in enumerate(staff_list):
            if assigned_total[i] < base_cap and is_eligible(st, type_code):
                if abs(st.load - min_load) < 1e-9:
                    candidates.append(i)

        if not candidates:
            break

        # 3) 랜덤
        if len(candidates) == 1:
            pick = candidates[0]
        else:
            pick = pick_random_index(staff_list, candidates, period, type_code)

        # 4) 반영
        arr_type[pick] += 1
        assigned_total[pick] += 1
        need -= 1

def balance_fairness(staff_list, arr1M, arr1A, arr2A, arr2M, base_cap):
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
        idx_max = fair.index(max_val)
        idx_min = fair.index(min_val)

        if max_val - min_val <= 1:
            break

        # 불균형 해소 시도
        for t in ["1M", "1A", "2A", "2M"]:
            if t == "1M" and arr1M[idx_max] > 0 and is_eligible(staff_list[idx_min], "1M") and assigned[idx_min] < base_cap:
                arr1M[idx_max] -= 1
                arr1M[idx_min] += 1
                break
            if t == "1A" and arr1A[idx_max] > 0 and is_eligible(staff_list[idx_min], "1A") and assigned[idx_min] < base_cap:
                arr1A[idx_max] -= 1
                arr1A[idx_min] += 1
                break
            if t == "2A" and arr2A[idx_max] > 0 and is_eligible(staff_list[idx_min], "2A") and assigned[idx_min] < base_cap:
                arr2A[idx_max] -= 1
                arr2A[idx_min] += 1
                break
            if t == "2M" and arr2M[idx_max] > 0 and is_eligible(staff_list[idx_min], "2M") and assigned[idx_min] < base_cap:
                arr2M[idx_max] -= 1
                arr2M[idx_min] += 1
                break

def allocate_session(staff_list, demand_dict, is_morning: bool):
    """
    demand_dict = {
      1: {"1M":?, "1A":?, "2A":?, "2M":?},
      ...
    }
    """
    results = {}
    periods = [1, 2] if is_morning else [3, 4, 5]

    for period in periods:
        # 직전 미배정 보정
        for st in staff_list:
            if st.skipped_prev:
                st.load -= 0.8
            st.skipped_prev = False

        # 교양 가중치
        apply_edu_weight(staff_list, period)
        # 코스 가중치
        apply_course_weight(staff_list, period, is_morning)

        d = demand_dict.get(period, {"1M": 0, "1A": 0, "2A": 0, "2M": 0})

        base_cap = 2 if period in [1, 5] else 3

        n = len(staff_list)
        assigned_total = [0] * n
        arr1M = [0] * n
        arr1A = [0] * n
        arr2A = [0] * n
        arr2M = [0] * n

        assign_one_type(staff_list, assigned_total, arr1M, base_cap, period, "1M", d["1M"])
        assign_one_type(staff_list, assigned_total, arr1A, base_cap, period, "1A", d["1A"])
        assign_one_type(staff_list, assigned_total, arr2A, base_cap, period, "2A", d["2A"])
        assign_one_type(staff_list, assigned_total, arr2M, base_cap, period, "2M", d["2M"])

        balance_fairness(staff_list, arr1M, arr1A, arr2A, arr2M, base_cap)

        # 1교시 코스 혜택 연장
        if is_morning and period == 1:
            totals = [arr1M[i] + arr1A[i] + arr2A[i] + arr2M[i] for i in range(n)]
            min_assign = min(totals) if totals else 0
            for i, st in enumerate(staff_list):
                if st.is_course and totals[i] > min_assign:
                    st.need_low_next = True
                else:
                    st.need_low_next = False
        else:
            for st in staff_list:
                st.need_low_next = False

        # 결과 저장
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

            total = m1 + a1 + a2 + m2
            st.skipped_prev = (total == 0)
            st.load += total

        results[period] = period_result

    return results

# ============================================================
# Streamlit UI (A안 — 교시×종별 표형 입력)
# ============================================================

st.set_page_config(layout="wide")

st.title("🚗 도로주행 자동 배정 (Python 버전 – v15 로직)")

with st.expander("수동가능자(1M/2M 가능) 확인", expanded=False):
    st.write(", ".join(sorted(MANUAL_STAFF)))

st.markdown("### 1. 오전/오후 결과 텍스트 입력")

col_m, col_a = st.columns(2)
with col_m:
    morning_text = st.text_area(
        "🌅 오전 결과 텍스트 (교양/차량배정 전체 붙여넣기)",
        height=220,
        key="morning_text",
    )
with col_a:
    afternoon_text = st.text_area(
        "🌇 오후 결과 텍스트 (교양/차량배정 전체 붙여넣기)",
        height=220,
        key="afternoon_text",
    )

st.markdown("---")
st.markdown("### 2. 교시별 수요 입력 (인원수)")

st.caption("※ 1·5교시: 감독관당 최대 2명 / 2·3·4교시: 감독관당 최대 3명")

period_labels = ["1교시", "2교시", "3교시", "4교시", "5교시"]
cols = st.columns([1.0, 1.0, 1.0, 1.0, 1.0])

demands = {}
for idx, period in enumerate(range(1, 6)):
    with cols[idx]:
        st.markdown(f"**{period_labels[idx]}**")
        d1M = st.number_input(f"1종수동", min_value=0, step=1, key=f"d_{period}_1M")
        d1A = st.number_input(f"1종자동", min_value=0, step=1, key=f"d_{period}_1A")
        d2A = st.number_input(f"2종자동", min_value=0, step=1, key=f"d_{period}_2A")
        d2M = st.number_input(f"2종수동", min_value=0, step=1, key=f"d_{period}_2M")
        demands[period] = {"1M": d1M, "1A": d1A, "2A": d2A, "2M": d2M}

st.markdown("---")
st.markdown("### 3. 자동 배정 실행")

col_b1, col_b2 = st.columns(2)

if "morning_result" not in st.session_state:
    st.session_state["morning_result"] = None
if "afternoon_result" not in st.session_state:
    st.session_state["afternoon_result"] = None

with col_b1:
    if st.button("🌅 오전 자동 배정 실행", use_container_width=True):
        if not morning_text.strip():
            st.error("오전 텍스트를 먼저 입력해 주세요.")
        else:
            try:
                staff_list_m = build_staff_list_from_text(morning_text)
                res_m = allocate_session(staff_list_m, demands, is_morning=True)
                st.session_state["morning_result"] = res_m
                st.success("오전 배정을 완료했습니다.")
            except Exception as e:
                st.error(f"오전 배정 중 오류: {e}")

with col_b2:
    if st.button("🌇 오후 자동 배정 실행", use_container_width=True):
        if not afternoon_text.strip():
            st.error("오후 텍스트를 먼저 입력해 주세요.")
        else:
            try:
                staff_list_a = build_staff_list_from_text(afternoon_text)
                res_a = allocate_session(staff_list_a, demands, is_morning=False)
                st.session_state["afternoon_result"] = res_a
                st.success("오후 배정을 완료했습니다.")
            except Exception as e:
                st.error(f"오후 배정 중 오류: {e}")

st.markdown("---")
st.markdown("### 4. 배정 결과")

def render_result(title, result):
    st.markdown(f"#### {title}")
    if not result:
        st.info("배정 결과가 없습니다.")
        return

    # 감독관 이름 모음
    all_names = set()
    for period_res in result.values():
        all_names.update(period_res.keys())
    all_names = sorted(all_names)

    # 교시별 표 출력
    for period in sorted(result.keys()):
        st.markdown(f"**{period}교시**")
        period_res = result[period]
        rows = []
        for name in all_names:
            info = period_res.get(name, {"1M":0,"1A":0,"2A":0,"2M":0})
            s = []
            if info["1M"] > 0: s.append(f"1종수동 {info['1M']}명")
            if info["1A"] > 0: s.append(f"1종자동 {info['1A']}명")
            if info["2A"] > 0: s.append(f"2종자동 {info['2A']}명")
            if info["2M"] > 0: s.append(f"2종수동 {info['2M']}명")
            rows.append((name, " / ".join(s) if s else "0"))

        st.table({"감독관": [r[0] for r in rows],
                  "배정": [r[1] for r in rows]})

if st.session_state["morning_result"]:
    render_result("🌅 오전 배정 결과", st.session_state["morning_result"])

if st.session_state["afternoon_result"]:
    render_result("🌇 오후 배정 결과", st.session_state["afternoon_result"])
