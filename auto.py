# app.py
###############################################
# Streamlit 메인 앱
###############################################
import streamlit as st

from core import load_history, reset_history
from ui_morning import render_morning_tab
from ui_afternoon import render_afternoon_tab

st.set_page_config(page_title="도로주행 자동 배정", layout="wide")

st.title("🚗 도로주행 자동 배정 (코스/교양 + 랜덤 우선배정 + 짝짓기 표시)")

tab_m, tab_a, tab_r = st.tabs(["🌅 오전 배정", "🌇 오후 배정", "🎲 랜덤결과"])

with tab_m:
    render_morning_tab()

with tab_a:
    render_afternoon_tab()

with tab_r:
    st.subheader("🎲 우선 배정 대상(이전에 진짜 적게 배정된 감독관 리스트)")
    hist = load_history()
    if not hist:
        st.info("우선 배정 대상 없음")
    else:
        st.table({
            "순번": list(range(1, len(hist) + 1)),
            "감독관": hist,
        })

    if st.button("🧽 랜덤결과 초기화", key="r_reset"):
        reset_history()
        st.success("랜덤결과(우선 배정 리스트)를 초기화했습니다.")
