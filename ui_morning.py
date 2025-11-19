# ui_morning.py
###############################################
# 오전 배정 탭 UI
###############################################
import streamlit as st
import pandas as pd

from core import (
    Staff,
    extract_staff,
    extract_extra,
    assign_one_period,
    make_pairs,
    LABEL_MAP,
)

def render_morning_tab():
    st.subheader("📥 오전 텍스트 입력")
    text_m = st.text_area("오전 텍스트 입력", height=200, key="txt_m")

    period_m = st.selectbox("교시 선택", [1, 2], index=0, key="pm")

    # 1) 텍스트 -> 근무자/코스/교양 자동 추출
    if st.button("1) 근무자 자동 추출", key="m_extract"):
        if not text_m.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text_m)
            edu_map, course_list = extract_extra(text_m)

            st.success("근무자 자동 추출 완료!")
            st.write("자동 추출 근무자:", staff_names)

            st.session_state["m_staff_raw"] = staff_names
            st.session_state["m_edu"] = edu_map        # {교시:이름}
            st.session_state["m_course"] = course_list # [이름,이름...]

    # 2) 근무자 수정 + 코스/교양 수정 + 수요 입력 + 배정 실행
    if "m_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정 (추가/삭제/변경 가능)")
        df_m = pd.DataFrame({"근무자": st.session_state["m_staff_raw"]})
        edited_m = st.data_editor(df_m, num_rows="dynamic", key="m_edit")
        final_staff_names_m = edited_m["근무자"].dropna().tolist()

        st.session_state["m_staff_final"] = final_staff_names_m
        st.write("최종 근무자:", final_staff_names_m)

        # 🔧 코스 / 교양 수정 UI (오전: 1,2교시)
        st.subheader("🛠 코스·교양 담당자 수정")

        staff_options_m = final_staff_names_m
        edu_raw_m = st.session_state.get("m_edu", {})
        course_raw_m = st.session_state.get("m_course", [])

        # 코스 담당자 멀티 선택
        default_courses_m = [nm for nm in course_raw_m if nm in staff_options_m]
        selected_course_m = st.multiselect(
            "코스 담당자 (여러 명 선택 가능)",
            staff_options_m,
            default=default_courses_m,
            key="m_course_sel",
        )

        options_m_with_none = ["없음"] + staff_options_m

        # 1교시 교양
        cur_edu1 = edu_raw_m.get(1)
        default_label_1 = cur_edu1 if cur_edu1 in staff_options_m else "없음"
        selected_edu1_label = st.selectbox(
            "1교시 교양 담당자",
            options_m_with_none,
            index=options_m_with_none.index(default_label_1),
            key="m_edu1_sel",
        )

        # 2교시 교양
        cur_edu2 = edu_raw_m.get(2)
        default_label_2 = cur_edu2 if cur_edu2 in staff_options_m else "없음"
        selected_edu2_label = st.selectbox(
            "2교시 교양 담당자",
            options_m_with_none,
            index=options_m_with_none.index(default_label_2),
            key="m_edu2_sel",
        )

        # 세션 저장
        st.session_state["m_course_manual"] = selected_course_m  # list
        edu_manual_m = {}
        if selected_edu1_label != "없음":
            edu_manual_m[1] = selected_edu1_label
        if selected_edu2_label != "없음":
            edu_manual_m[2] = selected_edu2_label
        st.session_state["m_edu_manual_m"] = edu_manual_m

        # 🔢 수요 입력
        st.subheader("📊 수요 입력")
        c1, c2, c3, c4 = st.columns(4)
        demand_m = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"m1_{period_m}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"m2_{period_m}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"m3_{period_m}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"m4_{period_m}"),
        }

        # 🧮 배정 실행
        if st.button("2) 오전 배정 실행", key="m_run"):
            staff_list_m = [Staff(n) for n in final_staff_names_m]

            # 코스/교양 수동 반영
            course_manual = st.session_state.get("m_course_manual", [])
            edu_manual_m = st.session_state.get("m_edu_manual_m", {})

            for s in staff_list_m:
                if s.name in course_manual:
                    s.is_course = True

            for gyo, nm in edu_manual_m.items():
                for s in staff_list_m:
                    if s.name == nm:
                        s.is_edu[gyo] = True

            result_m, low_group_m = assign_one_period(
                staff_list_m, period_m, demand_m, is_morning=True
            )

            # 결과 출력
            st.subheader("📌 배정 결과")
            rows = []
            for s in staff_list_m:
                info = result_m[s.name]
                desc = []
                for t in ("1M", "1A", "2A", "2M"):
                    if info[t] > 0:
                        desc.append(f"{LABEL_MAP[t]} {info[t]}명")
                rows.append((s.name, " / ".join(desc) if desc else "0"))

            st.table({
                "감독관": [x[0] for x in rows],
                "배정": [x[1] for x in rows],
            })

            # 🔗 짝지어진 감독관 표시
            pairs = make_pairs(staff_list_m, result_m)
            st.markdown("### 🔗 짝지어진 감독관")
            if not pairs:
                st.write("짝지을 감독관 없음")
            else:
                for p in pairs:
                    st.write("• " + p)

            st.markdown("#### 🔻 이번 교시에서 진짜 적게 배정된 감독관 (랜덤결과 후보)")
            st.write(low_group_m)

            # 가중치(Load) 확인용
            st.markdown("#### 🔢 최종 가중치(Load)")
            st.table({
                "감독관": [s.name for s in staff_list_m],
                "Load": [float(s.load) for s in staff_list_m],
            })
