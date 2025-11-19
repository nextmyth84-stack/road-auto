# ui_afternoon.py
###############################################
# 오후 배정 탭 UI
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

def render_afternoon_tab():
    st.subheader("📥 오후 텍스트 입력")
    text_a = st.text_area("오후 텍스트 입력", height=200, key="txt_a")

    period_a = st.selectbox("교시 선택", [3, 4, 5], index=0, key="pa")

    # 1) 텍스트 -> 근무자/코스/교양 자동 추출
    if st.button("1) 근무자 자동 추출", key="a_extract"):
        if not text_a.strip():
            st.error("텍스트를 입력하세요.")
        else:
            staff_names = extract_staff(text_a)
            edu_map, course_list = extract_extra(text_a)

            st.success("근무자 자동 추출 완료!")
            st.write("자동 추출 근무자:", staff_names)

            st.session_state["a_staff_raw"] = staff_names
            st.session_state["a_edu"] = edu_map
            st.session_state["a_course"] = course_list

    if "a_staff_raw" in st.session_state:
        st.subheader("✏ 근무자 수정 (추가/삭제/변경 가능)")
        df_a = pd.DataFrame({"근무자": st.session_state["a_staff_raw"]})
        edited_a = st.data_editor(df_a, num_rows="dynamic", key="a_edit")
        final_staff_names_a = edited_a["근무자"].dropna().tolist()

        st.session_state["a_staff_final"] = final_staff_names_a
        st.write("최종 근무자:", final_staff_names_a)

        # 🔧 코스 / 교양 수정 UI (오후: 3,4,5교시)
        st.subheader("🛠 코스·교양 담당자 수정")

        staff_options_a = final_staff_names_a
        edu_raw_a = st.session_state.get("a_edu", {})
        course_raw_a = st.session_state.get("a_course", [])

        # 코스 담당자 멀티 선택
        default_courses_a = [nm for nm in course_raw_a if nm in staff_options_a]
        selected_course_a = st.multiselect(
            "코스 담당자 (여러 명 선택 가능)",
            staff_options_a,
            default=default_courses_a,
            key="a_course_sel",
        )

        options_a_with_none = ["없음"] + staff_options_a

        # 3교시 교양
        cur_edu3 = edu_raw_a.get(3)
        default_label_3 = cur_edu3 if cur_edu3 in staff_options_a else "없음"
        selected_edu3_label = st.selectbox(
            "3교시 교양 담당자",
            options_a_with_none,
            index=options_a_with_none.index(default_label_3),
            key="a_edu3_sel",
        )

        # 4교시 교양
        cur_edu4 = edu_raw_a.get(4)
        default_label_4 = cur_edu4 if cur_edu4 in staff_options_a else "없음"
        selected_edu4_label = st.selectbox(
            "4교시 교양 담당자",
            options_a_with_none,
            index=options_a_with_none.index(default_label_4),
            key="a_edu4_sel",
        )

        # 5교시 교양
        cur_edu5 = edu_raw_a.get(5)
        default_label_5 = cur_edu5 if cur_edu5 in staff_options_a else "없음"
        selected_edu5_label = st.selectbox(
            "5교시 교양 담당자",
            options_a_with_none,
            index=options_a_with_none.index(default_label_5),
            key="a_edu5_sel",
        )

        st.session_state["a_course_manual"] = selected_course_a
        edu_manual_a = {}
        if selected_edu3_label != "없음":
            edu_manual_a[3] = selected_edu3_label
        if selected_edu4_label != "없음":
            edu_manual_a[4] = selected_edu4_label
        if selected_edu5_label != "없음":
            edu_manual_a[5] = selected_edu5_label
        st.session_state["a_edu_manual_a"] = edu_manual_a

        # 🔢 수요 입력
        st.subheader("📊 수요 입력")
        c1, c2, c3, c4 = st.columns(4)
        demand_a = {
            "1M": c1.number_input("1종수동", min_value=0, key=f"a1_{period_a}"),
            "1A": c2.number_input("1종자동", min_value=0, key=f"a2_{period_a}"),
            "2A": c3.number_input("2종자동", min_value=0, key=f"a3_{period_a}"),
            "2M": c4.number_input("2종수동", min_value=0, key=f"a4_{period_a}"),
        }

        # 🧮 배정 실행
        if st.button("2) 오후 배정 실행", key="a_run"):
            staff_list_a = [Staff(n) for n in final_staff_names_a]

            course_manual_a = st.session_state.get("a_course_manual", [])
            edu_manual_a = st.session_state.get("a_edu_manual_a", {})

            for s in staff_list_a:
                if s.name in course_manual_a:
                    s.is_course = True

            for gyo, nm in edu_manual_a.items():
                for s in staff_list_a:
                    if s.name == nm:
                        s.is_edu[gyo] = True

            result_a, low_group_a = assign_one_period(
                staff_list_a, period_a, demand_a, is_morning=False
            )

            st.subheader("📌 배정 결과")
            rows = []
            for s in staff_list_a:
                info = result_a[s.name]
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
            pairs = make_pairs(staff_list_a, result_a)
            st.markdown("### 🔗 짝지어진 감독관")
            if not pairs:
                st.write("짝지을 감독관 없음")
            else:
                for p in pairs:
                    st.write("• " + p)

            st.markdown("#### 🔻 이번 교시에서 진짜 적게 배정된 감독관 (랜덤결과 후보)")
            st.write(low_group_a)

            st.markdown("#### 🔢 최종 가중치(Load)")
            st.table({
                "감독관": [s.name for s in staff_list_a],
                "Load": [float(s.load) for s in staff_list_a],
            })
