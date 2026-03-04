import streamlit as st
import pandas as pd
import json
import plotly.express as px
import plotly.graph_objects as go
import sqlite3
import time
from datetime import datetime, date
import streamlit.components.v1 as components

from modules.database import get_connection, get_active_period_info, DB_FILE, save_evaluation_json_method
from modules.utils import (
    calculate_category, render_metric_input, 
    table_to_json_string, get_df_from_json, get_active_survey_questions,
    safe_load_json, normalize_progress, create_9box_grid, render_empty_state
)
# 1. IMPORT KONSTANTI ZA LIMITE
from modules.constants import MAX_TITLE_LENGTH, MAX_TEXT_LENGTH
from modules.goals_cascade import render_team_goals_manager

def _render_readonly_eval(r, emp, survey_data, current_period, username):
    """Read-only prikaz zaključane procjene s gumbom za toggle u Print View."""
    saved = safe_load_json(r['json_answers'])

    col_meta, col_btn = st.columns([4, 1])
    with col_meta:
        c1, c2, c3 = st.columns(3)
        c1.metric("Učinak", f"{r['avg_performance']:.2f}")
        c2.metric("Potencijal", f"{r['avg_potential']:.2f}")
        c3.metric("Kategorija", r['category'])
    col_btn.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)
    if col_btn.button("🖨️ Print View", key=f"toggle_print_{emp['kadrovski_broj']}"):
        key = f"print_mode_{emp['kadrovski_broj']}"
        st.session_state[key] = not st.session_state.get(key, False)
        st.rerun()

    st.markdown("---")
    st.subheader("Detaljni pregled")
    cr1, cr2 = st.columns(2)
    with cr1:
        st.markdown("**Učinak**")
        for m in survey_data['p']:
            val = saved.get(str(m['id']), "-")
            st.write(f"- {m['title']}: **{val}**")
    with cr2:
        st.markdown("**Potencijal**")
        for m in survey_data['pot']:
            val = saved.get(str(m['id']), "-")
            st.write(f"- {m['title']}: **{val}**")

    st.markdown("**Komentar voditelja:**")
    st.info(r['action_plan'] if r['action_plan'] else "*(Nema komentara)*")


def _render_print_view(r, emp, survey_data, current_period, username):
    """Print-optimiziran prikaz procjene. Gumb za izlaz iz print modea."""
    saved = safe_load_json(r['json_answers'])

    col_title, col_exit = st.columns([4, 1])
    col_title.markdown(f"## 📄 IZVJEŠTAJ O UČINKU: {current_period}")
    if col_exit.button("✖️ Zatvori Print View", key=f"close_print_{emp['kadrovski_broj']}"):
        st.session_state[f"print_mode_{emp['kadrovski_broj']}"] = False
        st.rerun()

    st.markdown(f"**Zaposlenik:** {emp['ime_prezime']} &nbsp;|&nbsp; **Voditelj:** {username} &nbsp;|&nbsp; **Datum:** {r['feedback_date']}")
    st.divider()

    c1, c2, c3 = st.columns(3)
    c1.metric("Učinak", f"{r['avg_performance']:.2f}")
    c2.metric("Potencijal", f"{r['avg_potential']:.2f}")
    c3.metric("Kategorija", r['category'])

    st.markdown("---")
    col_p, col_pot = st.columns(2)
    with col_p:
        st.markdown("#### Učinak")
        for m in survey_data['p']:
            val = saved.get(str(m['id']), "-")
            st.markdown(f"**{m['title']}:** {val} / 5")
    with col_pot:
        st.markdown("#### Potencijal")
        for m in survey_data['pot']:
            val = saved.get(str(m['id']), "-")
            st.markdown(f"**{m['title']}:** {val} / 5")

    st.markdown("---")
    st.markdown("### Zaključni komentar i akcijski plan")
    st.info(r['action_plan'] if r['action_plan'] else "*(Nema komentara)*")
    st.caption("💡 Ctrl+P (Win) / Cmd+P (Mac) → 'Spremi kao PDF'")
    st.divider()


def render_manager_view():
    conn = get_connection()
    try:
        current_period, deadline = get_active_period_info()
        username = st.session_state.get('username')
        company_id = st.session_state.get('company_id', 1)

        # INFO BAR
        st.info(f"📅 **AKTIVNO RAZDOBLJE:** {current_period}  |  ⏳ **ROK:** {deadline}")

        mode, survey_data = get_active_survey_questions(current_period, company_id)

        # IZBORNIK
        menu = st.sidebar.radio("Voditeljski Izbornik", [
            "📊 Dashboard", 
            "👤 Moji Rezultati",
            "🎯 Ciljevi Tima", 
            "📝 Unos Procjena", 
            "🚀 Razvojni Planovi (IDP)", 
            "🤝 Upravljanje Ljudima",
            "📥 Export Podataka"
        ])

        # ----------------------------------------------------------------
        # 1. DASHBOARD
        # ----------------------------------------------------------------
        if menu == "📊 Dashboard":
            st.header(f"📊 Moj Dashboard")
            my_team = pd.read_sql_query("SELECT * FROM employees_master WHERE manager_id=? AND company_id=?", conn, params=(username, company_id))

            # Statistika
            evals = pd.read_sql_query("SELECT * FROM evaluations WHERE period=? AND manager_id=? AND is_self_eval=0", conn, params=(current_period, username))

            c1, c2, c3 = st.columns(3)
            c1.metric("Moj Tim", len(my_team))
            finished = len(evals[evals['status']=='Submitted'])
            c2.metric("Završeno", f"{finished} / {len(my_team)}")
            avg_score = evals['avg_performance'].mean() if not evals.empty else 0
            c3.metric("Prosjek Tima", f"{avg_score:.2f}")

            t1, t2 = st.tabs(["9-Box Matrica", "Povijest (Snail Trail)"])
            with t1:
                if not evals.empty:
                    fig = create_9box_grid(evals, title="9-Box Matrica Tima")
                    if fig: st.plotly_chart(fig, use_container_width=True)
                else:
                    render_empty_state(
                        "📊",
                        "Nema zaključanih procjena",
                        "9-Box matrica prikazuje se kad voditelj zaključa (Submitted) "
                        "procjene za zaposlenike. Idite na 'Unos Procjena' za početak.",
                        action_text="Idite na Unos Procjena"
                    )

            with t2:
                if not my_team.empty:
                    sel = st.selectbox("Odaberi zaposlenika:", my_team['ime_prezime'].tolist())
                    kid = my_team[my_team['ime_prezime']==sel]['kadrovski_broj'].values[0]
                    hist = pd.read_sql_query("SELECT period, avg_performance, avg_potential FROM evaluations WHERE kadrovski_broj=? AND is_self_eval=0 AND status='Submitted' ORDER BY period", conn, params=(kid,))

                    if not hist.empty:
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=hist['avg_performance'], 
                            y=hist['avg_potential'],
                            mode='lines+markers+text',
                            text=hist['period'], 
                            textposition="top center",
                            marker=dict(size=12, color='blue'),
                            line=dict(color='rgba(0,0,255,0.3)', width=2, dash='dot'),
                            name='Razvojni put'
                        ))

                        fig.update_layout(
                            title=f"Razvojni put: {sel}",
                            xaxis=dict(title="Učinak (Performance)", range=[0.5, 5.5], showgrid=False),
                            yaxis=dict(title="Potencijal (Potential)", range=[0.5, 5.5], showgrid=False),
                            shapes=[
                                dict(type="line", x0=2.5, x1=2.5, y0=0, y1=6, line=dict(color="gray", width=1, dash="dot")),
                                dict(type="line", x0=4.0, x1=4.0, y0=0, y1=6, line=dict(color="gray", width=1, dash="dot")),
                                dict(type="line", x0=0, x1=6, y0=2.5, y1=2.5, line=dict(color="gray", width=1, dash="dot")),
                                dict(type="line", x0=0, x1=6, y0=4.0, y1=4.0, line=dict(color="gray", width=1, dash="dot")),
                            ]
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        render_empty_state(
                            "🗺️",
                            "Nema razvojnih podataka",
                            "Snail trail prikazuje se nakon što zaposlenik ima zaključane "
                            "procjene iz više ciklusa. Podaci se nadograđuju svakim periodom."
                        )

        # ----------------------------------------------------------------
        # 2. MOJI REZULTATI
        # ----------------------------------------------------------------
        elif menu == "👤 Moji Rezultati":
            st.header("👤 Moji Rezultati")
            me_eval = pd.read_sql_query("SELECT * FROM evaluations WHERE kadrovski_broj=? AND period=? AND is_self_eval=0", conn, params=(username, current_period))
            if not me_eval.empty:
                r = me_eval.iloc[0]
                st.info(f"Status: {r['status']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Učinak", f"{r['avg_performance']:.2f}")
                c2.metric("Potencijal", f"{r['avg_potential']:.2f}")
                c3.metric("Kategorija", r['category'])
                st.write("**Komentar nadređenog:**")
                st.write(r['action_plan'])
            else:
                render_empty_state(
                    "👤",
                    "Vaša procjena još nije unesena",
                    "Vaš nadređeni još nije kreirao vašu procjenu za ovaj period. "
                    "Ako smatrate da je to pogreška, kontaktirajte HR ili svog nadređenog."
                )

        # ----------------------------------------------------------------
        # 3. CILJEVI TIMA
        # ----------------------------------------------------------------
        elif menu == "🎯 Ciljevi Tima":
            render_team_goals_manager(username, company_id, current_period)

        # ----------------------------------------------------------------
        # 4. UNOS PROCJENA
        # ----------------------------------------------------------------
        elif menu == "📝 Unos Procjena":
            st.header("📝 Procjena Zaposlenika")
            my_team = pd.read_sql_query("SELECT * FROM employees_master WHERE manager_id=? AND company_id=?", conn, params=(username, company_id))

            if my_team.empty:
                st.info("📭 Nemate dodijeljenih članova tima.")
            else:
                # --- DOHVAT STATUSA ZA CIJELI TIM (jedan SQL upit) ---
                team_ids = my_team['kadrovski_broj'].tolist()
                placeholders = ",".join(["?"] * len(team_ids))
                eval_statuses = pd.read_sql_query(
                    f"SELECT kadrovski_broj, status FROM evaluations "
                    f"WHERE period=? AND is_self_eval=0 AND kadrovski_broj IN ({placeholders})",
                    conn, params=[current_period] + team_ids
                )
                status_map = dict(zip(eval_statuses['kadrovski_broj'], eval_statuses['status']))

                def _eval_label(kid):
                    s = status_map.get(kid)
                    if s == 'Submitted': return '✅'
                    if s == 'Draft':     return '✏️'
                    return '⚪'

                # --- TEAM SUMMARY PANEL ---
                n_done  = sum(1 for s in status_map.values() if s == 'Submitted')
                n_draft = sum(1 for s in status_map.values() if s == 'Draft')
                n_total = len(my_team)
                progress_val = n_done / n_total if n_total else 0

                st.markdown("**Pregled tima**")
                prog_col, cnt_col = st.columns([3, 1])
                prog_col.progress(progress_val)
                cnt_col.caption(f"✅ {n_done} / {n_total} završeno")

                # Mini grid — ikonice za sve zaposlenike, 4 po redu
                grid_cols = st.columns(4)
                for i, (_, row) in enumerate(my_team.iterrows()):
                    icon = _eval_label(row['kadrovski_broj'])
                    s = status_map.get(row['kadrovski_broj'])
                    label = "Završeno" if s == 'Submitted' else ("U tijeku" if s == 'Draft' else "Nije započeto")
                    grid_cols[i % 4].caption(f"{icon} {row['ime_prezime']}")

                st.divider()

                # --- SMART SELECTBOX ---
                emp_ids = my_team['kadrovski_broj'].tolist()
                emp_id_to_row = {row['kadrovski_broj']: row for _, row in my_team.iterrows()}

                sel_kid = st.selectbox(
                    "👤 Odaberi zaposlenika:",
                    emp_ids,
                    format_func=lambda kid: f"{_eval_label(kid)} {emp_id_to_row[kid]['ime_prezime']}",
                    key="eval_emp_switcher"
                )
                emp = emp_id_to_row[sel_kid]
                kid = sel_kid

                r_df = pd.read_sql_query("SELECT * FROM evaluations WHERE kadrovski_broj=? AND period=? AND is_self_eval=0", conn, params=(kid, current_period))
                r = r_df.iloc[0] if not r_df.empty else None

                is_locked = (r is not None and str(r['status']).strip() == 'Submitted')
                status_text = "🔒 Zaključano" if is_locked else ("📝 U tijeku" if r is not None else "⬜ Nije započeto")
                st.info(f"Status: **{status_text}**  |  **{emp['ime_prezime']}** — {emp['radno_mjesto']}")

                tab_input, tab_gap = st.tabs(["🖊️ Unos Ocjena", "🔍 Gap Analiza"])

                # --- TAB 1: UNOS OCJENA (ILI PREGLED ZAKLJUČANOG) ---
                with tab_input:
                    if is_locked:
                        st.success("✅ Procjena je zaključana i poslana.")
                        # Session state toggle — eksplicitni print mode po zaposleniku
                        if st.session_state.get(f"print_mode_{kid}", False):
                            _render_print_view(r, emp, survey_data, current_period, username)
                        else:
                            _render_readonly_eval(r, emp, survey_data, current_period, username)

                    else:
                        # FORMA ZA UNOS
                        with st.form(f"eval_form_{kid}"):
                            saved = safe_load_json(r['json_answers'] if r is not None else None)
                            scores_p = []
                            scores_pot = []

                            c1, c2 = st.columns(2)
                            with c1:
                                st.subheader("Učinak")
                                for m in survey_data['p']:
                                    val = int(saved.get(str(m['id']), 3))
                                    s = render_metric_input(m['title'], m['def'], m['crit'], f"p_{kid}_{m['id']}", val, "perf")
                                    scores_p.append((str(m['id']), s))
                            with c2:
                                st.subheader("Potencijal")
                                for m in survey_data['pot']:
                                    val = int(saved.get(str(m['id']), 3))
                                    s = render_metric_input(m['title'], m['def'], m['crit'], f"pot_{kid}_{m['id']}", val, "pot")
                                    scores_pot.append((str(m['id']), s))

                            plan = st.text_area("Komentar / Akcijski plan", r['action_plan'] if r is not None else "", max_chars=MAX_TEXT_LENGTH)

                            col_d, col_f = st.columns(2)
                            is_draft = col_d.form_submit_button("💾 Spremi kao Nacrt")
                            is_final = col_f.form_submit_button("✅ Pošalji i Zaključaj")

                            if is_draft or is_final:
                                vals_p = [x[1] for x in scores_p]
                                vals_pot = [x[1] for x in scores_pot]
                                avg_p = sum(vals_p) / len(vals_p) if vals_p else 0
                                avg_pot = sum(vals_pot) / len(vals_pot) if vals_pot else 0
                                cat = calculate_category(avg_p, avg_pot)

                                all_ans = {**dict(scores_p), **dict(scores_pot)}
                                user_data = {'ime': emp['ime_prezime'], 'radno_mjesto': emp['radno_mjesto'], 'odjel': emp['department']}
                                status = "Submitted" if is_final else "Draft"

                                success, msg = save_evaluation_json_method(company_id, current_period, kid, username, user_data, vals_p, vals_pot, avg_p, avg_pot, cat, plan, all_ans, False, status)
                                if success:
                                    if is_final:
                                        st.balloons()
                                        st.toast(f"✅ Procjena za {emp['ime_prezime']} zaključana!", icon="🔒")
                                    else:
                                        st.toast(f"💾 Nacrt spremljen za {emp['ime_prezime']}", icon="💾")
                                    st.rerun()
                                else: st.error(msg)

                # --- TAB 2: GAP ANALIZA (PUNI KOD) ---
                with tab_gap:
                    se_df = pd.read_sql_query("SELECT * FROM evaluations WHERE kadrovski_broj=? AND period=? AND is_self_eval=1", conn, params=(kid, current_period))
                    if not se_df.empty:
                        se_row = se_df.iloc[0]

                        # Učitaj JSON odgovore
                        mgr_json = safe_load_json(r['json_answers'] if r is not None else None)
                        se_json = safe_load_json(se_row['json_answers'])

                        gap_data = []
                        all_questions = survey_data['p'] + survey_data['pot']

                        for q in all_questions:
                            qid = str(q['id'])
                            try:
                                s_mgr = int(mgr_json.get(qid, 0))
                                s_emp = int(se_json.get(qid, 0))
                            except:
                                s_mgr, s_emp = 0, 0

                            diff = s_mgr - s_emp

                            gap_data.append({
                                "Kategorija": "Učinak" if q in survey_data['p'] else "Potencijal",
                                "Pitanje": q['title'],
                                "Radnik": s_emp,
                                "Manager": s_mgr,
                                "Razlika": diff
                            })

                        gap_df = pd.DataFrame(gap_data)

                        # Prikaz metrike
                        c1, c2 = st.columns(2)
                        c1.metric("Samoprocjena (Prosjek)", f"{se_row['avg_performance']:.2f}")
                        if r is not None:
                            c2.metric("Vaša ocjena (Prosjek)", f"{r['avg_performance']:.2f}")

                        st.write("#### Detaljna usporedba")
                        # Jednostavan color highlight za dataframe
                        st.dataframe(gap_df.style.applymap(lambda x: 'color: red' if x < 0 else ('color: green' if x > 0 else 'color: gray'), subset=['Razlika']), use_container_width=True)

                    else:
                        st.warning("Radnik još nije ispunio samoprocjenu, pa usporedba nije moguća.")

    # ----------------------------------------------------------------
        # 5. IDP (RAZVOJNI PLANOVI)
        # ----------------------------------------------------------------
        elif menu == "🚀 Razvojni Planovi (IDP)":
            st.header("🚀 Razvojni Planovi (IDP)")
            team = pd.read_sql_query("SELECT * FROM employees_master WHERE manager_id=?", conn, params=(username,))

            if team.empty:
                st.info("📭 Nemate dodijeljenih članova tima.")
            else:
                # --- DOHVAT IDP STATUSA ZA CIJELI TIM (jedan SQL upit) ---
                team_ids_idp = team['kadrovski_broj'].tolist()
                placeholders_idp = ",".join(["?"] * len(team_ids_idp))
                idp_statuses = pd.read_sql_query(
                    f"SELECT kadrovski_broj, status FROM development_plans "
                    f"WHERE period=? AND kadrovski_broj IN ({placeholders_idp})",
                    conn, params=[current_period] + team_ids_idp
                )
                idp_status_map = dict(zip(idp_statuses['kadrovski_broj'], idp_statuses['status']))

                def _idp_label(kid):
                    s = idp_status_map.get(kid)
                    if s == 'Active':   return '🟢'
                    if s == 'Approved': return '✅'
                    return '⚪'

                # --- TEAM SUMMARY PANEL ---
                n_idp_done  = sum(1 for s in idp_status_map.values() if s in ('Active', 'Approved'))
                n_idp_total = len(team)
                idp_progress_val = n_idp_done / n_idp_total if n_idp_total else 0

                st.markdown("**Pregled tima**")
                idp_prog_col, idp_cnt_col = st.columns([3, 1])
                idp_prog_col.progress(idp_progress_val)
                idp_cnt_col.caption(f"🟢 {n_idp_done} / {n_idp_total} kreirano")

                # Mini grid — 4 po redu
                idp_grid_cols = st.columns(4)
                for i, (_, row) in enumerate(team.iterrows()):
                    icon = _idp_label(row['kadrovski_broj'])
                    idp_grid_cols[i % 4].caption(f"{icon} {row['ime_prezime']}")

                st.divider()

                # --- SMART SELECTBOX ---
                idp_ids = team['kadrovski_broj'].tolist()
                idp_id_to_row = {row['kadrovski_broj']: row for _, row in team.iterrows()}

                sel_eid = st.selectbox(
                    "👤 Odaberi zaposlenika:",
                    idp_ids,
                    format_func=lambda kid: f"{_idp_label(kid)} {idp_id_to_row[kid]['ime_prezime']}",
                    key="idp_emp_switcher"
                )
                emp = idp_id_to_row[sel_eid]
                eid = sel_eid

                res = conn.execute("SELECT * FROM development_plans WHERE kadrovski_broj=? AND period=?", (eid, current_period)).fetchone()
                d = dict(zip([col[1] for col in conn.execute("PRAGMA table_info(development_plans)").fetchall()], res)) if res else {}

                status_icon = _idp_label(eid)
                st.info(f"{status_icon} **{emp['ime_prezime']}** — {emp['radno_mjesto']}  |  IDP Status: **{d.get('status', 'Nije kreiran')}**")

                with st.form(f"idp_form_{eid}"):
                            st.subheader("1. Dijagnoza i Smjer")
                            c1, c2 = st.columns(2)
                            with c1: val_s = st.text_area("💪 Ključne Snage", value=d.get('strengths',''), height=100, max_chars=MAX_TEXT_LENGTH, help="U čemu je zaposlenik izniman?")
                            with c2: val_w = st.text_area("🚧 Područja za razvoj", value=d.get('areas_improve',''), height=100, max_chars=MAX_TEXT_LENGTH, help="Što koči zaposlenika?")
                            val_g = st.text_input("🎯 Karijerni cilj", value=d.get('career_goal',''), max_chars=MAX_TITLE_LENGTH, help="Kratkoročni ili dugoročni cilj?")

                            st.markdown("---")
                            st.subheader("2. Akcijski plan (Max 15 redaka po tablici)")

                            # --- 70% ISKUSTVO ---
                            st.info("📌 **70% - Učenje kroz rad (Iskustvo)**\n\nNovi zadaci, projekti, rotacije, povećanje odgovornosti.")
                            d70 = st.data_editor(get_df_from_json(d.get('json_70',''), ["Što razviti?", "Aktivnost", "Rok", "Dokaz"]), key=f"d70_{eid}", num_rows="dynamic", use_container_width=True)

                            # --- 20% MENTORING ---
                            st.info("👥 **20% - Učenje od drugih (Izloženost)**\n\nMentoring, coaching, feedback, shadowing, networking.")
                            d20 = st.data_editor(get_df_from_json(d.get('json_20',''), ["Što razviti?", "Aktivnost", "Rok"]), key=f"d20_{eid}", num_rows="dynamic", use_container_width=True)

                            # --- 10% EDUKACIJA ---
                            st.info("📚 **10% - Formalna edukacija**\n\nTečajevi, certifikati, knjige, konferencije.")
                            d10 = st.data_editor(get_df_from_json(d.get('json_10',''), ["Edukacija", "Trošak", "Rok"]), key=f"d10_{eid}", num_rows="dynamic", use_container_width=True)

                            st.markdown("---")
                            st.subheader("3. Podrška")
                            curr_supp = d.get('support_needed', '---')
                            supp_opts = ["---", "Mentoring", "Coaching", "Budžet", "Slobodni dani", "Rotacija posla", "Tehnička oprema"]
                            if curr_supp not in supp_opts: curr_supp = "---"
                            new_supp = st.selectbox("Vrsta podrške:", supp_opts, index=supp_opts.index(curr_supp), key=f"supp_{eid}")
                            new_notes = st.text_area("Napomene:", value=d.get('support_notes',''), max_chars=MAX_TEXT_LENGTH, key=f"notes_{eid}")

                            if st.form_submit_button("💾 Spremi Razvojni Plan"):
                                # VALIDACIJA LIMITA REDAKA
                                if len(d70) > 15 or len(d20) > 15 or len(d10) > 15:
                                    st.error("❌ Previše redaka! Maksimalno 15 aktivnosti po tablici.")
                                else:
                                    with sqlite3.connect(DB_FILE) as db:
                                        db.execute("DELETE FROM development_plans WHERE kadrovski_broj=? AND period=?", (eid, current_period))
                                        db.execute("""INSERT INTO development_plans (period, kadrovski_broj, manager_id, strengths, areas_improve, career_goal, json_70, json_20, json_10, support_needed, support_notes, status, company_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                                   (current_period, eid, username, val_s, val_w, val_g, table_to_json_string(d70), table_to_json_string(d20), table_to_json_string(d10), new_supp, new_notes, 'Active', company_id))
                                    st.toast(f"✅ IDP spremljen za {emp['ime_prezime']}", icon="🚀")
                                    st.rerun()

        # ----------------------------------------------------------------
        # 6. UPRAVLJANJE LJUDIMA
        # ----------------------------------------------------------------
        elif menu == "🤝 Upravljanje Ljudima":
            st.header("🤝 Upravljanje Ljudima")
            my_team = pd.read_sql_query("SELECT * FROM employees_master WHERE manager_id=?", conn, params=(username,))
            t1, t2 = st.tabs(["Pohvale", "Delegiranje"])
            with t1:
                with st.form("mgr_kudos"):
                    rec = st.selectbox("Zaposlenik:", my_team['ime_prezime'].tolist())
                    msg = st.text_area("Poruka:", max_chars=MAX_TEXT_LENGTH)
                    if st.form_submit_button("Pošalji"):
                        rid = my_team[my_team['ime_prezime']==rec]['kadrovski_broj'].values[0]
                        conn.execute("INSERT INTO recognitions (sender_id, receiver_id, message, timestamp, company_id) VALUES (?,?,?,?,?)", (username, rid, msg, str(date.today()), company_id))
                        conn.commit(); st.success("Poslano!")
            with t2:
                render_empty_state(
                    "🔜",
                    "Delegiranje zadataka — uskoro",
                    "Ova funkcionalnost je u razvoju i bit će dostupna u sljedećoj verziji. "
                    "Pratite changelog za novosti."
                )

        # ----------------------------------------------------------------
        # 7. EXPORT PODATAKA
        # ----------------------------------------------------------------
        elif menu == "📥 Export Podataka":
            st.header("📥 Export Mojih Podataka")
            st.caption("Preuzmite podatke vašeg tima u Excel formatu.")

            if st.button("Preuzmi Excel (Moj Tim)"):
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    pd.read_sql_query("SELECT * FROM employees_master WHERE manager_id=?", conn, params=(username,)).to_excel(writer, sheet_name="Moj Tim")
                    pd.read_sql_query("SELECT * FROM evaluations WHERE manager_id=? AND period=? AND status='Submitted'", conn, params=(username, current_period)).to_excel(writer, sheet_name="Procjene")
                    pd.read_sql_query("SELECT * FROM goals WHERE manager_id=? AND period=?", conn, params=(username, current_period)).to_excel(writer, sheet_name="Ciljevi")
                st.download_button("Download Excel", buffer.getvalue(), f"export_team_{username}_{date.today()}.xlsx")

    finally:
        conn.close()
