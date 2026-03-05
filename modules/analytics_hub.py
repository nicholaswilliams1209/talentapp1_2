# modules/analytics_hub.py
# V1.2 — Centralni Analytics Dashboard za HR
# Tri sekcije: Completion Tracker · Interaktivni 9-Box · Goal Alignment

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from modules.database import get_connection
from modules.utils import render_empty_state, calculate_category

# ─────────────────────────────────────────────────────────────
# BOJE I KONSTANTE
# ─────────────────────────────────────────────────────────────

# 9-Box kvadranti: (x_zona, y_zona) → (label, boja)
NINEBOX_QUADRANTS = {
    (0, 2): ("⭐ Top Talent",          "#1b5e20"),
    (1, 2): ("🚀 High Performer",      "#2e7d32"),
    (2, 2): ("💎 Rastući potencijal",  "#388e3c"),
    (0, 1): ("✅ Pouzdan suradnik",    "#1565c0"),
    (1, 1): ("🔄 Ključni igrač",       "#1976d2"),
    (2, 1): ("📈 Razvoj u tijeku",     "#1e88e5"),
    (0, 0): ("⚖️ Poboljšanje potrebno","#b71c1c"),
    (1, 0): ("🌱 Talent u razvoju",    "#c62828"),
    (2, 0): ("⚠️ Pod promatranjem",    "#d32f2f"),
}

DEPT_COLORS = px.colors.qualitative.Set2


def _get_zone(val: float, low: float = 2.5, high: float = 4.0) -> int:
    """Pretvara ocjenu u zonu 0/1/2 za 9-box grid."""
    if val >= high:
        return 2
    if val >= low:
        return 1
    return 0


# ─────────────────────────────────────────────────────────────
# SEKCIJA 1: COMPLETION TRACKER
# ─────────────────────────────────────────────────────────────

def render_completion_tracker(company_id: int, current_period: str):
    """
    Interaktivni completion dashboard:
    - Evaluacije (Submitted) po odjelima
    - Ciljevi s definiranim KPI-evima po odjelima
    - IDP-ovi (Active/Approved) po odjelima
    """
    conn = get_connection()
    try:
        # Dohvat zaposlenika s odjelima
        employees = pd.read_sql_query(
            """SELECT kadrovski_broj, ime_prezime, department
               FROM employees_master
               WHERE company_id=? AND active=1""",
            conn, params=(company_id,)
        )

        if employees.empty:
            render_empty_state("📊", "Nema zaposlenika",
                               "Dodajte zaposlenike u Šifarnik.")
            return

        depts = sorted(employees["department"].dropna().unique().tolist())
        n_total_by_dept = (
            employees.groupby("department")["kadrovski_broj"]
            .count()
            .rename("total")
        )

        # ── Evaluacije ──────────────────────────────────────
        evals_done = pd.read_sql_query(
            """SELECT em.department, COUNT(*) as done
               FROM evaluations ev
               JOIN employees_master em ON ev.kadrovski_broj = em.kadrovski_broj
               WHERE ev.period=? AND ev.company_id=?
                 AND ev.status='Submitted' AND ev.is_self_eval=0
               GROUP BY em.department""",
            conn, params=(current_period, company_id)
        ).set_index("department")["done"]

        # ── Ciljevi s KPI-evima ──────────────────────────────
        goals_with_kpi = pd.read_sql_query(
            """SELECT em.department, COUNT(DISTINCT g.kadrovski_broj) as done
               FROM goals g
               JOIN employees_master em ON g.kadrovski_broj = em.kadrovski_broj
               JOIN goal_kpis gk ON gk.goal_id = g.id
               WHERE g.period=? AND g.company_id=? AND g.level='employee'
               GROUP BY em.department""",
            conn, params=(current_period, company_id)
        ).set_index("department")["done"]

        # ── IDP-ovi ──────────────────────────────────────────
        idp_done = pd.read_sql_query(
            """SELECT em.department, COUNT(*) as done
               FROM development_plans dp
               JOIN employees_master em ON dp.kadrovski_broj = em.kadrovski_broj
               WHERE dp.period=? AND dp.company_id=?
                 AND dp.status IN ('Active', 'Approved')
               GROUP BY em.department""",
            conn, params=(current_period, company_id)
        ).set_index("department")["done"]

        # ── Kompiliraj tablicu ────────────────────────────────
        rows = []
        for dept in depts:
            total = n_total_by_dept.get(dept, 0)
            if total == 0:
                continue
            rows.append({
                "Odjel":       dept,
                "Zaposlenici": total,
                "Evaluacije":  round(evals_done.get(dept, 0) / total * 100),
                "Ciljevi+KPI": round(goals_with_kpi.get(dept, 0) / total * 100),
                "IDP":         round(idp_done.get(dept, 0) / total * 100),
            })

        if not rows:
            render_empty_state("📊", "Nema podataka",
                               "Još nema evaluacija, ciljeva ili IDP-ova za ovaj period.")
            return

        df = pd.DataFrame(rows)

        # ── Summary metrics ────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Ukupno zaposlenika", employees.shape[0])
        c2.metric("Prosj. evaluacije",
                  f"{df['Evaluacije'].mean():.0f}%")
        c3.metric("Prosj. ciljevi+KPI",
                  f"{df['Ciljevi+KPI'].mean():.0f}%")
        c4.metric("Prosj. IDP",
                  f"{df['IDP'].mean():.0f}%")

        st.divider()

        # ── Grouped bar chart ──────────────────────────────────
        df_melt = df.melt(
            id_vars="Odjel",
            value_vars=["Evaluacije", "Ciljevi+KPI", "IDP"],
            var_name="Kategorija",
            value_name="Postotak (%)"
        )

        fig = px.bar(
            df_melt,
            x="Odjel",
            y="Postotak (%)",
            color="Kategorija",
            barmode="group",
            title=f"Kompletnost po odjelima — {current_period}",
            color_discrete_map={
                "Evaluacije":  "#3f51b5",
                "Ciljevi+KPI": "#00897b",
                "IDP":         "#f57c00",
            },
            range_y=[0, 110],
            text="Postotak (%)",
        )
        fig.update_traces(texttemplate="%{text}%", textposition="outside")
        fig.update_layout(
            legend_title_text="",
            xaxis_title="",
            yaxis_title="Kompletnost (%)",
            plot_bgcolor="#fafafa",
            height=420,
        )
        st.plotly_chart(fig, use_container_width=True)

        # ── Detaljna tablica s conditional styling ─────────────
        with st.expander("📋 Detaljna tablica"):
            def color_pct(val):
                if isinstance(val, (int, float)):
                    if val >= 80:
                        return "background-color:#e8f5e9;color:#1b5e20"
                    if val >= 50:
                        return "background-color:#fff3e0;color:#e65100"
                    return "background-color:#ffebee;color:#b71c1c"
                return ""

            styled = df.style.applymap(
                color_pct,
                subset=["Evaluacije", "Ciljevi+KPI", "IDP"]
            ).format({
                "Evaluacije": "{}%",
                "Ciljevi+KPI": "{}%",
                "IDP": "{}%",
            })
            st.dataframe(styled, use_container_width=True, hide_index=True)

    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# SEKCIJA 2: INTERAKTIVNI 9-BOX
# ─────────────────────────────────────────────────────────────

def render_interactive_9box(company_id: int, current_period: str):
    """
    Interaktivni 9-box:
    - Plotly scatter s vidljivim kvadrantima
    - Klik na kvadrant (ili selectbox) → lista zaposlenika u tom segmentu
    - Filter po odjelu
    """
    conn = get_connection()
    try:
        dept_list = ["Svi"] + sorted(
            pd.read_sql_query(
                "SELECT DISTINCT department FROM employees_master WHERE company_id=? AND active=1",
                conn, params=(company_id,)
            )["department"].dropna().tolist()
        )

        # Grandparent View — lista managera
        mgr_df = pd.read_sql_query(
            """SELECT kadrovski_broj, ime_prezime FROM employees_master
               WHERE is_manager=1 AND company_id=? AND active=1
               ORDER BY ime_prezime""",
            conn, params=(company_id,)
        )
        mgr_opts = ["Svi manageri"] + mgr_df["ime_prezime"].tolist()

        col_filter, col_mgr, col_info = st.columns([2, 2, 1])
        sel_dept = col_filter.selectbox(
            "Filtriraj po odjelu:", dept_list, key="nbox_dept"
        )
        sel_mgr = col_mgr.selectbox(
            "👁 Grandparent View:", mgr_opts, key="nbox_mgr",
            help="Usporedi distribuciju po manageru — otkrij 'darežljive' i 'škrte' ocjenjivače"
        )

        # Dohvat evaluacija
        query = """
            SELECT ev.kadrovski_broj, ev.ime_prezime,
                   ev.avg_performance, ev.avg_potential,
                   ev.category, em.department
            FROM evaluations ev
            JOIN employees_master em ON ev.kadrovski_broj = em.kadrovski_broj
            WHERE ev.period=? AND ev.company_id=?
              AND ev.status='Submitted' AND ev.is_self_eval=0
        """
        df = pd.read_sql_query(query, conn, params=(current_period, company_id))

        if df.empty:
            render_empty_state(
                "📊", "Nema zaključanih procjena",
                "9-Box matrica prikazuje se kad manageri zaključaju procjene."
            )
            return

        if sel_dept != "Svi":
            df = df[df["department"] == sel_dept]

        # Grandparent filter — tim specificnog managera
        if sel_mgr != "Svi manageri":
            mgr_id_val = mgr_df.loc[mgr_df["ime_prezime"] == sel_mgr, "kadrovski_broj"]
            if not mgr_id_val.empty:
                team_ids = pd.read_sql_query(
                    "SELECT kadrovski_broj FROM employees_master WHERE manager_id=? AND active=1",
                    conn, params=(mgr_id_val.values[0],)
                )["kadrovski_broj"].tolist()
                df = df[df["kadrovski_broj"].isin(team_ids)]
                mgr_avg_p = df["avg_performance"].mean() if not df.empty else 0
                company_avg = pd.read_sql_query(
                    """SELECT AVG(avg_performance) as ca FROM evaluations
                       WHERE period=? AND company_id=? AND status='Submitted' AND is_self_eval=0""",
                    conn, params=(current_period, company_id)
                ).iloc[0]["ca"] or 0
                col_info.metric(
                    "Prosjek tima",
                    f"{mgr_avg_p:.2f}",
                    delta=f"{mgr_avg_p - company_avg:+.2f} vs firma",
                    delta_color="normal"
                )

        if df.empty:
            render_empty_state("📊", "Nema podataka za odabrani filter", "")
            return

        df["avg_performance"] = pd.to_numeric(df["avg_performance"], errors="coerce").fillna(0)
        df["avg_potential"]   = pd.to_numeric(df["avg_potential"],   errors="coerce").fillna(0)

        # Dodaj zonu za svaki zaposlenik
        df["zone_x"] = df["avg_performance"].apply(_get_zone)
        df["zone_y"] = df["avg_potential"].apply(_get_zone)
        df["quadrant_key"] = list(zip(df["zone_x"], df["zone_y"]))
        df["quadrant_label"] = df["quadrant_key"].map(
            lambda k: NINEBOX_QUADRANTS.get(k, ("Ostalo", "#888"))[0]
        )
        df["quadrant_color"] = df["quadrant_key"].map(
            lambda k: NINEBOX_QUADRANTS.get(k, ("Ostalo", "#888"))[1]
        )

        # ── Statistika kvadranata ─────────────────────────────
        col_info.markdown(
            f"**{len(df)} zaposlenika** · "
            f"Prosjek učinka: **{df['avg_performance'].mean():.2f}** · "
            f"Prosjek potencijala: **{df['avg_potential'].mean():.2f}**"
        )

        # ── Scatter plot ──────────────────────────────────────
        fig = go.Figure()

        # Pozadinski kvadranti (colored zones)
        zone_bg = [
            # (x0, x1, y0, y1, fillcolor)
            (0.5, 2.5, 0.5, 2.5, "rgba(211,47,47,0.06)"),   # dolje-lijevo: poboljšanje
            (2.5, 4.0, 0.5, 2.5, "rgba(211,47,47,0.04)"),   # dolje-sredina
            (4.0, 5.5, 0.5, 2.5, "rgba(198,40,40,0.04)"),   # dolje-desno
            (0.5, 2.5, 2.5, 4.0, "rgba(21,101,192,0.05)"),  # sredina-lijevo
            (2.5, 4.0, 2.5, 4.0, "rgba(25,118,210,0.05)"),  # centar
            (4.0, 5.5, 2.5, 4.0, "rgba(30,136,229,0.05)"),  # sredina-desno
            (0.5, 2.5, 4.0, 5.5, "rgba(27,94,32,0.06)"),    # gore-lijevo
            (2.5, 4.0, 4.0, 5.5, "rgba(46,125,50,0.06)"),   # gore-sredina
            (4.0, 5.5, 4.0, 5.5, "rgba(56,142,60,0.08)"),   # gore-desno: top talent
        ]
        for x0, x1, y0, y1, fc in zone_bg:
            fig.add_shape(
                type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                fillcolor=fc, line_width=0, layer="below"
            )

        # Kvadrant labele u pozadini
        quadrant_positions = [
            (1.5,  1.25, "⚖️ Poboljšanje"),
            (3.25, 1.25, "🌱 U razvoju"),
            (4.75, 1.25, "⚠️ Promatranje"),
            (1.5,  3.25, "✅ Pouzdan"),
            (3.25, 3.25, "🔄 Ključni"),
            (4.75, 3.25, "📈 Razvoj"),
            (1.5,  4.75, "💎 Rastući"),
            (3.25, 4.75, "🚀 High Perf."),
            (4.75, 4.75, "⭐ Top Talent"),
        ]
        for qx, qy, qlabel in quadrant_positions:
            fig.add_annotation(
                x=qx, y=qy, text=qlabel,
                showarrow=False,
                font=dict(size=9, color="rgba(0,0,0,0.25)"),
                xanchor="center", yanchor="middle"
            )

        # Gridlinije kvadranata
        for line_x in [2.5, 4.0]:
            fig.add_vline(x=line_x, line_dash="dot",
                          line_color="rgba(0,0,0,0.2)", line_width=1)
        for line_y in [2.5, 4.0]:
            fig.add_hline(y=line_y, line_dash="dot",
                          line_color="rgba(0,0,0,0.2)", line_width=1)

        # Scatter točke — grupiraj po kvadrantu za legendu
        for qkey, (qlabel, qcolor) in NINEBOX_QUADRANTS.items():
            grp = df[df["quadrant_key"] == qkey]
            if grp.empty:
                continue
            fig.add_trace(go.Scatter(
                x=grp["avg_performance"],
                y=grp["avg_potential"],
                mode="markers+text",
                text=grp["ime_prezime"].apply(lambda n: n.split()[0]),  # samo ime
                textposition="top center",
                textfont=dict(size=9, color="#333"),
                marker=dict(
                    size=14, color=qcolor,
                    line=dict(width=1.5, color="#fff"),
                    symbol="circle"
                ),
                name=f"{qlabel} ({len(grp)})",
                customdata=grp[["ime_prezime", "department",
                                "avg_performance", "avg_potential"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Odjel: %{customdata[1]}<br>"
                    "Učinak: %{customdata[2]:.2f}<br>"
                    "Potencijal: %{customdata[3]:.2f}"
                    "<extra></extra>"
                ),
            ))

        fig.update_layout(
            xaxis=dict(title="Učinak (Performance)", range=[0.5, 5.5],
                       showgrid=False, zeroline=False),
            yaxis=dict(title="Potencijal", range=[0.5, 5.5],
                       showgrid=False, zeroline=False),
            plot_bgcolor="#ffffff",
            height=520,
            legend=dict(
                orientation="h", yanchor="bottom",
                y=-0.3, xanchor="center", x=0.5,
                font=dict(size=10)
            ),
            margin=dict(l=50, r=20, t=40, b=120),
            title=f"9-Box Matrica — {sel_dept}{f" / {sel_mgr}" if sel_mgr != "Svi manageri" else ""} ({current_period})",
        )
        st.plotly_chart(fig, use_container_width=True,
                        key="nbox_chart")

        # ── Klik/filter na kvadrant → lista zaposlenika ────────
        st.markdown("#### 🔍 Detalj po kvadrantu")
        st.caption(
            "Odaberi kvadrant za pregled zaposlenika. "
            "Plotly hover prikazuje detalje direktno na grafu."
        )

        # Selectbox za kvadrant (zamjena za click event — Streamlit limitation)
        quadrant_counts = df.groupby("quadrant_label").size().reset_index(name="n")
        quadrant_options = ["-- Svi --"] + [
            f"{row['quadrant_label']} ({row['n']})"
            for _, row in quadrant_counts.sort_values("n", ascending=False).iterrows()
        ]

        sel_quadrant = st.selectbox(
            "Kvadrant:", quadrant_options, key="nbox_quadrant_sel"
        )

        if sel_quadrant == "-- Svi --":
            display_df = df
        else:
            # Izvuci label iz stringa "⭐ Top Talent (3)"
            sel_label = sel_quadrant.rsplit(" (", 1)[0]
            display_df = df[df["quadrant_label"] == sel_label]

        if not display_df.empty:
            # Lijepa tablica
            show_df = display_df[[
                "ime_prezime", "department",
                "avg_performance", "avg_potential", "quadrant_label"
            ]].rename(columns={
                "ime_prezime":      "Ime i Prezime",
                "department":       "Odjel",
                "avg_performance":  "Učinak",
                "avg_potential":    "Potencijal",
                "quadrant_label":   "Segment",
            }).sort_values("Učinak", ascending=False)

            def _score_color(val):
                try:
                    v = float(val)
                    if v >= 4.0: return "background-color:#c8e6c9;color:#1b5e20"
                    if v >= 3.0: return "background-color:#fff9c4;color:#f57f17"
                    return "background-color:#ffcdd2;color:#b71c1c"
                except (ValueError, TypeError):
                    return ""
            st.dataframe(
                show_df.style
                    .applymap(_score_color, subset=["Učinak", "Potencijal"])
                    .format({"Učinak": "{:.2f}", "Potencijal": "{:.2f}"}),
                use_container_width=True,
                hide_index=True
            )

            # Mini distribucija
            with st.expander("📊 Distribucija po odjelima unutar kvadranta"):
                dept_dist = display_df.groupby("department").size().reset_index(name="n")
                fig2 = px.bar(
                    dept_dist.sort_values("n", ascending=True),
                    x="n", y="department", orientation="h",
                    title="Broj zaposlenika po odjelu",
                    labels={"n": "Zaposlenici", "department": ""},
                    color="n",
                    color_continuous_scale="Blues",
                )
                fig2.update_layout(height=200 + len(dept_dist) * 30,
                                   showlegend=False,
                                   coloraxis_showscale=False)
                st.plotly_chart(fig2, use_container_width=True)

    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# SEKCIJA 3: GOAL ALIGNMENT VIZUALIZACIJA
# ─────────────────────────────────────────────────────────────

def render_goal_alignment(company_id: int, current_period: str):
    """
    Vizualizira postotak zaposleničkih ciljeva koji su linked na
    Org/Dept ciljeve vs. standalone (nelinkovani).

    Prikazuje:
    - Sunburst: Org → Dept → Employee ciljevi (hijerarhija)
    - Bar chart: % linked vs. unlinked po odjelima
    - Metrike: ukupni alignment score
    """
    conn = get_connection()
    try:
        # Svi zaposlenički ciljevi
        emp_goals = pd.read_sql_query(
            """SELECT g.id, g.kadrovski_broj, g.title, g.progress,
                      g.parent_goal_id, g.weight,
                      em.department, em.ime_prezime
               FROM goals g
               JOIN employees_master em ON g.kadrovski_broj = em.kadrovski_broj
               WHERE g.period=? AND g.company_id=? AND g.level='employee'""",
            conn, params=(current_period, company_id)
        )

        if emp_goals.empty:
            render_empty_state(
                "🎯", "Nema zaposleničkih ciljeva",
                "Manageri trebaju kreirati ciljeve za zaposlenike."
            )
            return

        # Org i dept ciljevi za linkanje
        org_goals = pd.read_sql_query(
            "SELECT id, title, progress FROM goals WHERE level='org' AND period=? AND company_id=?",
            conn, params=(current_period, company_id)
        )
        dept_goals = pd.read_sql_query(
            """SELECT g.id, g.title, g.progress, g.parent_goal_id, g.manager_id,
                      em.department
               FROM goals g
               LEFT JOIN employees_master em ON g.manager_id = em.kadrovski_broj
               WHERE g.level='dept' AND g.period=? AND g.company_id=?""",
            conn, params=(current_period, company_id)
        )

        # Koji emp goal ima parent_goal_id koji je dept goal?
        dept_ids = set(dept_goals["id"].tolist()) if not dept_goals.empty else set()
        emp_goals["is_linked_dept"] = emp_goals["parent_goal_id"].isin(dept_ids)
        emp_goals["is_linked"] = emp_goals["parent_goal_id"].notna()

        total      = len(emp_goals)
        n_linked   = emp_goals["is_linked"].sum()
        n_unlinked = total - n_linked
        pct_linked = round(n_linked / total * 100) if total > 0 else 0

        # ── Summary metrics ────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Ukupno ciljeva", total)
        c2.metric("Linkovano na org/dept", n_linked,
                  delta=f"{pct_linked}% alignment")
        c3.metric("Standalone (nelinkovano)", n_unlinked)
        c4.metric("Org ciljeva", len(org_goals) if not org_goals.empty else 0)

        align_color = "#27ae60" if pct_linked >= 70 else (
            "#f57c00" if pct_linked >= 40 else "#c62828"
        )
        st.markdown(f"""
        <div style="background:#f8f9fa;border-radius:10px;padding:12px 16px;
                    margin:8px 0 16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-weight:700;font-size:14px;">
              Ukupni Alignment Score
            </span>
            <span style="font-size:28px;font-weight:800;color:{align_color};">
              {pct_linked}%
            </span>
          </div>
          <div style="background:#e0e0e0;border-radius:99px;height:10px;margin-top:8px;">
            <div style="background:{align_color};width:{pct_linked}%;
                        height:10px;border-radius:99px;"></div>
          </div>
          <div style="font-size:11px;color:#888;margin-top:4px;">
            Benchmark: ≥70% se smatra dobrom strateškom usklađenošću
          </div>
        </div>
        """, unsafe_allow_html=True)

        col_sunburst, col_bar = st.columns([3, 2])

        # ── Sunburst: Org → Dept → Employee ───────────────────
        with col_sunburst:
            sunburst_data = _build_sunburst_data(
                emp_goals, dept_goals, org_goals
            )
            if sunburst_data:
                fig_sun = go.Figure(go.Sunburst(
                    ids=sunburst_data["ids"],
                    labels=sunburst_data["labels"],
                    parents=sunburst_data["parents"],
                    values=sunburst_data["values"],
                    branchvalues="total",
                    hovertemplate="<b>%{label}</b><br>Ciljevi: %{value}<extra></extra>",
                    maxdepth=3,
                    insidetextorientation="radial",
                    marker=dict(
                        colors=sunburst_data["colors"],
                        line=dict(width=1, color="#fff")
                    ),
                ))
                fig_sun.update_layout(
                    title="Hijerarhija ciljeva (Org → Dept → Zaposlenik)",
                    height=420,
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig_sun, use_container_width=True)
            else:
                st.info("Nema org ciljeva — sunburst prikazuje se kad HR kreira org. ciljeve.")

        # ── Bar: linked vs unlinked po odjelima ───────────────
        with col_bar:
            dept_align = (
                emp_goals.groupby("department")
                .agg(
                    total=("id", "count"),
                    linked=("is_linked", "sum")
                )
                .reset_index()
            )
            dept_align["unlinked"]    = dept_align["total"] - dept_align["linked"]
            dept_align["pct_linked"]  = (dept_align["linked"] / dept_align["total"] * 100).round()

            fig_bar = go.Figure()
            fig_bar.add_trace(go.Bar(
                name="Linkovano",
                y=dept_align["department"],
                x=dept_align["linked"],
                orientation="h",
                marker_color="#00897b",
                text=dept_align["pct_linked"].astype(str) + "%",
                textposition="inside",
            ))
            fig_bar.add_trace(go.Bar(
                name="Nelinkovano",
                y=dept_align["department"],
                x=dept_align["unlinked"],
                orientation="h",
                marker_color="#ef9a9a",
                text=dept_align["unlinked"],
                textposition="inside",
            ))
            fig_bar.update_layout(
                barmode="stack",
                title="Alignment po odjelima",
                xaxis_title="Broj ciljeva",
                yaxis_title="",
                height=300 + len(dept_align) * 30,
                legend=dict(orientation="h", y=-0.15),
                plot_bgcolor="#fafafa",
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        # ── Detaljna lista nelinkovanih ciljeva ────────────────
        unlinked_goals = emp_goals[~emp_goals["is_linked"]]
        if not unlinked_goals.empty:
            with st.expander(f"⚠️ {len(unlinked_goals)} nelinkovanih ciljeva "
                             "(nemaju vezu s org/dept strategijom)"):
                st.dataframe(
                    unlinked_goals[["ime_prezime", "department", "title", "progress"]]
                    .rename(columns={
                        "ime_prezime": "Zaposlenik",
                        "department":  "Odjel",
                        "title":       "Cilj",
                        "progress":    "Ostvarenje (%)",
                    })
                    .sort_values("Odjel"),
                    hide_index=True,
                    use_container_width=True
                )

    finally:
        conn.close()


def _build_sunburst_data(emp_goals: pd.DataFrame,
                          dept_goals: pd.DataFrame,
                          org_goals: pd.DataFrame) -> dict | None:
    """Gradi input podatke za Plotly Sunburst (ids, labels, parents, values)."""
    if org_goals.empty:
        return None

    ids     = ["root"]
    labels  = ["Svi ciljevi"]
    parents = [""]
    values  = [len(emp_goals)]
    colors  = ["#e8eaf6"]

    for _, og in org_goals.iterrows():
        oid = f"org_{og['id']}"
        # Koliko emp ciljeva kaskadira kroz ovaj org cilj
        if not dept_goals.empty:
            dept_under = dept_goals[dept_goals["parent_goal_id"] == og["id"]]
            dept_under_ids = dept_under["id"].tolist()
            n_emp_under = len(
                emp_goals[emp_goals["parent_goal_id"].isin(dept_under_ids)]
            ) if dept_under_ids else 0
        else:
            dept_under    = pd.DataFrame()
            n_emp_under   = 0

        ids.append(oid)
        labels.append(og["title"][:30] + ("…" if len(og["title"]) > 30 else ""))
        parents.append("root")
        values.append(max(n_emp_under, 1))
        colors.append("#3f51b5")

        # Dept ciljevi pod ovim org ciljem
        if not dept_goals.empty:
            for _, dg in dept_under.iterrows():
                did = f"dept_{dg['id']}"
                n_emp_dept = len(
                    emp_goals[emp_goals["parent_goal_id"] == dg["id"]]
                )
                ids.append(did)
                labels.append(dg["title"][:25] + ("…" if len(dg["title"]) > 25 else ""))
                parents.append(oid)
                values.append(max(n_emp_dept, 1))
                colors.append("#00897b")

                # Emp ciljevi pod ovim dept ciljem
                emp_under_dept = emp_goals[
                    emp_goals["parent_goal_id"] == dg["id"]
                ]
                for _, eg in emp_under_dept.iterrows():
                    eid = f"emp_{eg['id']}"
                    ids.append(eid)
                    labels.append(eg["ime_prezime"].split()[0] if pd.notna(eg.get("ime_prezime")) else "?")
                    parents.append(did)
                    values.append(1)
                    colors.append("#80cbc4")

    # Nelinkovani emp ciljevi → posebna grana
    linked_emp_ids = set(emp_goals[emp_goals["parent_goal_id"].notna()]["id"].tolist())
    unlinked = emp_goals[~emp_goals["id"].isin(linked_emp_ids)]
    if not unlinked.empty:
        ids.append("unlinked_root")
        labels.append(f"⚠️ Nelinkovano ({len(unlinked)})")
        parents.append("root")
        values.append(len(unlinked))
        colors.append("#ef9a9a")

    return {
        "ids":     ids,
        "labels":  labels,
        "parents": parents,
        "values":  values,
        "colors":  colors,
    }




# ═════════════════════════════════════════════════════════════
# MANAGER ANALYTICS — v1.3
# Funkcije optimizirane za Manager Dashboard (filter po timu)
# ═════════════════════════════════════════════════════════════

def _get_team_ids(conn, manager_id: str, company_id: int) -> list:
    """Vraća listu kadrovski_broj za sve aktivne podređene managera."""
    rows = conn.execute(
        "SELECT kadrovski_broj FROM employees_master WHERE manager_id=? AND company_id=? AND active=1",
        (manager_id, company_id)
    ).fetchall()
    return [r[0] for r in rows]


def render_manager_9box(manager_id: str, company_id: int, current_period: str):
    """
    9-Box matrica filtrirana na tim managera.
    Drill-down: selectbox kvadranta → lista zaposlenika s karticama.
    """
    conn = get_connection()
    try:
        team_ids = _get_team_ids(conn, manager_id, company_id)
        if not team_ids:
            render_empty_state("📊", "Nemate dodijeljenih zaposlenika", "")
            return

        placeholders = ",".join("?" * len(team_ids))
        df = pd.read_sql_query(
            f"""SELECT ev.kadrovski_broj, ev.ime_prezime,
                       ev.avg_performance, ev.avg_potential, ev.category,
                       ev.action_plan, em.department
               FROM evaluations ev
               JOIN employees_master em ON ev.kadrovski_broj = em.kadrovski_broj
               WHERE ev.period=? AND ev.is_self_eval=0 AND ev.status='Submitted'
                 AND ev.kadrovski_broj IN ({placeholders})""",
            conn, params=[current_period] + team_ids
        )

        if df.empty:
            render_empty_state(
                "📊", "Nema zaključanih procjena",
                "9-Box prikazuje se kad zaključate procjene za zaposlenike."
            )
            return

        df["avg_performance"] = pd.to_numeric(df["avg_performance"], errors="coerce").fillna(0)
        df["avg_potential"]   = pd.to_numeric(df["avg_potential"],   errors="coerce").fillna(0)
        df["zone_x"]          = df["avg_performance"].apply(_get_zone)
        df["zone_y"]          = df["avg_potential"].apply(_get_zone)
        df["quadrant_key"]    = list(zip(df["zone_x"], df["zone_y"]))
        df["quadrant_label"]  = df["quadrant_key"].map(
            lambda k: NINEBOX_QUADRANTS.get(k, ("Ostalo", "#888"))[0]
        )
        df["quadrant_color"]  = df["quadrant_key"].map(
            lambda k: NINEBOX_QUADRANTS.get(k, ("Ostalo", "#888"))[1]
        )

        # ── MATRICA (Plotly) ───────────────────────────────────────
        fig = go.Figure()

        # Pozadinski kvadranti
        BG_COLORS = {
            (0,0): "rgba(244,67,54,0.08)",  (1,0): "rgba(255,152,0,0.08)",  (2,0): "rgba(255,152,0,0.08)",
            (0,1): "rgba(255,152,0,0.08)",  (1,1): "rgba(255,235,59,0.08)", (2,1): "rgba(33,150,243,0.08)",
            (0,2): "rgba(255,152,0,0.08)",  (1,2): "rgba(76,175,80,0.08)",  (2,2): "rgba(27,94,32,0.12)",
        }
        x_breaks = [0.5, 2.5, 4.0, 5.5]
        y_breaks = [0.5, 2.5, 4.0, 5.5]
        for xi in range(3):
            for yi in range(3):
                fig.add_shape(
                    type="rect",
                    x0=x_breaks[xi], x1=x_breaks[xi+1],
                    y0=y_breaks[yi], y1=y_breaks[yi+1],
                    fillcolor=BG_COLORS.get((xi,yi), "rgba(200,200,200,0.05)"),
                    line=dict(color="rgba(150,150,150,0.3)", width=1)
                )

        # Scatter po kvadrantu
        for qkey, (qlabel, qcolor) in NINEBOX_QUADRANTS.items():
            grp = df[df["quadrant_key"] == qkey]
            if grp.empty:
                continue
            fig.add_trace(go.Scatter(
                x=grp["avg_performance"], y=grp["avg_potential"],
                mode="markers+text",
                name=qlabel,
                text=grp["ime_prezime"],
                textposition="top center",
                textfont=dict(size=10, color="#222"),
                cliponaxis=False,
                marker=dict(
                    size=16, color=qcolor, opacity=0.9,
                    line=dict(color="white", width=2)
                ),
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "Učinak: %{x:.2f}<br>Potencijal: %{y:.2f}<br>"
                    f"Kvadrant: {qlabel}<extra></extra>"
                )
            ))

        # Linije kvadranta
        for xv in [2.5, 4.0]:
            fig.add_vline(x=xv, line=dict(color="rgba(100,100,100,0.4)", width=1, dash="dot"))
        for yv in [2.5, 4.0]:
            fig.add_hline(y=yv, line=dict(color="rgba(100,100,100,0.4)", width=1, dash="dot"))

        # Labele kvadranta
        quadrant_labels_pos = [
            (1.5, 4.75, "⭐ Top Talent"),   (2.5, 4.75, "🚀 High Performer"), (3.25, 4.75, "💎 Raste"),
            (1.5, 3.25, "🌟 Iniciativa"),   (2.5, 3.25, "✅ Pouzdan"),         (3.25, 3.25, "📈 Usmjeriti"),
            (1.5, 1.75, "⚠️ Rizik"),         (2.5, 1.75, "⚡ Razviti"),          (3.25, 1.75, "🔄 Poboljšati"),
        ]
        for lx, ly, ltxt in quadrant_labels_pos:
            fig.add_annotation(
                x=lx, y=ly, text=ltxt, showarrow=False,
                font=dict(size=9, color="rgba(80,80,80,0.6)"),
                xanchor="center"
            )

        fig.update_layout(
            height=500,
            margin=dict(l=50, r=30, t=100, b=50),
            xaxis=dict(
                title="Učinak (Performance)",
                range=[0.5, 5.5], showgrid=False, zeroline=False,
                tickvals=[1, 2, 3, 4, 5]
            ),
            yaxis=dict(
                title="Potencijal",
                range=[0.5, 5.5], showgrid=False, zeroline=False,
                tickvals=[1, 2, 3, 4, 5]
            ),
            plot_bgcolor="white",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="center", x=0.5,
                font=dict(size=10)
            ),
            title=dict(
                text=f"9-Box Matrica — Moj Tim ({current_period})",
                font=dict(size=13), x=0, xanchor="left"
            )
        )
        st.plotly_chart(fig, use_container_width=True)

        # ── DRILL-DOWN — filter po kvadrantu ─────────────────────
        st.markdown("#### 🔍 Drill-Down: Zaposlenici po kvadrantu")
        quadrant_counts = df.groupby("quadrant_label").size().reset_index(name="n")
        q_opts = ["-- Svi zaposlenici --"] + [
            f"{row['quadrant_label']} ({row['n']})"
            for _, row in quadrant_counts.sort_values("n", ascending=False).iterrows()
        ]
        sel_q = st.selectbox("Odaberi kvadrant:", q_opts, key="mgr_9box_quadrant_sel")

        filtered = df if sel_q == "-- Svi zaposlenici --" else             df[df["quadrant_label"].apply(lambda x: x in sel_q)]

        if filtered.empty:
            st.info("Nema zaposlenika u ovom kvadrantu.")
        else:
            for _, row in filtered.iterrows():
                qc = row.get("quadrant_color", "#555")
                st.markdown(f"""
                <div style="border-left:4px solid {qc};background:#fafafa;
                            border-radius:6px;padding:10px 16px;margin-bottom:6px;">
                  <div style="font-weight:700;font-size:14px;">{row['ime_prezime']}</div>
                  <div style="font-size:12px;color:#666;margin-top:3px;">
                    Učinak: <b>{row['avg_performance']:.2f}</b> &nbsp;|&nbsp;
                    Potencijal: <b>{row['avg_potential']:.2f}</b> &nbsp;|&nbsp;
                    {row['quadrant_label']}
                  </div>
                  {f'<div style="font-size:11px;color:#888;margin-top:4px;font-style:italic;">{row["action_plan"][:120]}...</div>' if row.get("action_plan") else ""}
                </div>
                """, unsafe_allow_html=True)
    finally:
        conn.close()


def render_manager_snail_trail(manager_id: str, company_id: int):
    """
    Snail Trail — putanje svih zaposlenika tima kroz periode na jednom grafu.
    Svaki zaposlenik = jedna linija s točkama (obojana po kvadrantu zadnjeg perioda).
    """
    conn = get_connection()
    try:
        team_ids = _get_team_ids(conn, manager_id, company_id)
        if not team_ids:
            render_empty_state("🗺️", "Nema zaposlenika", "")
            return

        placeholders = ",".join("?" * len(team_ids))
        hist = pd.read_sql_query(
            f"""SELECT ev.kadrovski_broj, ev.ime_prezime,
                       ev.avg_performance, ev.avg_potential, ev.period
               FROM evaluations ev
               WHERE ev.is_self_eval=0 AND ev.status='Submitted'
                 AND ev.kadrovski_broj IN ({placeholders})
               ORDER BY ev.kadrovski_broj, ev.period ASC""",
            conn, params=team_ids
        )
    finally:
        conn.close()

    if hist.empty:
        render_empty_state(
            "🗺️", "Nema povijesnih podataka",
            "Snail trail zahtijeva zaključane procjene iz najmanje jednog perioda."
        )
        return

    hist["avg_performance"] = pd.to_numeric(hist["avg_performance"], errors="coerce")
    hist["avg_potential"]   = pd.to_numeric(hist["avg_potential"],   errors="coerce")

    # Filter — možemo prikazati subset zaposlenika
    emp_list = hist["ime_prezime"].unique().tolist()
    col_sel, col_info = st.columns([3, 1])
    sel_emps = col_sel.multiselect(
        "Odaberi zaposlenike:", emp_list,
        default=emp_list[:min(5, len(emp_list))],
        key="mgr_snail_sel"
    )
    if not sel_emps:
        st.info("Odaberi barem jednog zaposlenika.")
        return

    filtered = hist[hist["ime_prezime"].isin(sel_emps)]
    col_info.metric("Perioda", filtered["period"].nunique())

    fig = go.Figure()

    # Pozadinski kvadranti (lagano)
    BG = [
        (0.5,2.5,0.5,2.5,"rgba(244,67,54,0.05)"),   (2.5,4.0,0.5,2.5,"rgba(255,152,0,0.05)"),
        (4.0,5.5,0.5,2.5,"rgba(255,152,0,0.05)"),   (0.5,2.5,2.5,4.0,"rgba(255,152,0,0.05)"),
        (2.5,4.0,2.5,4.0,"rgba(255,235,59,0.06)"),  (4.0,5.5,2.5,4.0,"rgba(76,175,80,0.08)"),
        (0.5,2.5,4.0,5.5,"rgba(255,152,0,0.05)"),   (2.5,4.0,4.0,5.5,"rgba(76,175,80,0.08)"),
        (4.0,5.5,4.0,5.5,"rgba(27,94,32,0.10)"),
    ]
    for x0,x1,y0,y1,color in BG:
        fig.add_shape(type="rect", x0=x0,x1=x1,y0=y0,y1=y1,
                     fillcolor=color, line=dict(color="rgba(150,150,150,0.2)", width=0.5))

    # Boja po zaposleniku
    PALETTE = ["#1976D2","#388E3C","#F57C00","#7B1FA2","#C62828",
               "#00838F","#558B2F","#E91E63","#0288D1","#5D4037"]

    for idx, emp_name in enumerate(sel_emps):
        emp_data = filtered[filtered["ime_prezime"] == emp_name].sort_values("period")
        if emp_data.empty:
            continue
        color = PALETTE[idx % len(PALETTE)]

        # Linija putanje
        fig.add_trace(go.Scatter(
            x=emp_data["avg_performance"],
            y=emp_data["avg_potential"],
            mode="lines",
            name=emp_name,
            line=dict(color=color, width=2, dash="dot"),
            showlegend=False
        ))
        # Točke s periodom
        fig.add_trace(go.Scatter(
            x=emp_data["avg_performance"],
            y=emp_data["avg_potential"],
            mode="markers+text",
            name=emp_name,
            text=emp_data["period"],
            textposition="top center",
            textfont=dict(size=9),
            marker=dict(size=12, color=color, line=dict(color="white", width=1.5)),
            hovertemplate=(
                f"<b>{emp_name}</b><br>"
                "Period: %{text}<br>"
                "Učinak: %{x:.2f}<br>Potencijal: %{y:.2f}<extra></extra>"
            )
        ))
        # Strelica na zadnjoj točki
        if len(emp_data) >= 2:
            last = emp_data.iloc[-1]
            prev = emp_data.iloc[-2]
            fig.add_annotation(
                x=last["avg_performance"], y=last["avg_potential"],
                ax=prev["avg_performance"], ay=prev["avg_potential"],
                xref="x", yref="y", axref="x", ayref="y",
                arrowhead=3, arrowsize=1.5, arrowwidth=2, arrowcolor=color,
                showarrow=True, text=""
            )

    for xv in [2.5, 4.0]:
        fig.add_vline(x=xv, line=dict(color="rgba(100,100,100,0.3)", width=1, dash="dot"))
    for yv in [2.5, 4.0]:
        fig.add_hline(y=yv, line=dict(color="rgba(100,100,100,0.3)", width=1, dash="dot"))

    fig.update_layout(
        height=460,
        margin=dict(l=40, r=20, t=40, b=40),
        xaxis=dict(title="Učinak", range=[0.5, 5.5], showgrid=False),
        yaxis=dict(title="Potencijal", range=[0.5, 5.5], showgrid=False),
        plot_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, font=dict(size=11)),
        title=dict(text="Putanje razvoja tima (Snail Trail)", font=dict(size=14))
    )
    st.plotly_chart(fig, use_container_width=True)

    # Legenda: trend po zaposleniku
    st.markdown("**📈 Sažetak trendova:**")
    trend_cols = st.columns(min(4, len(sel_emps)))
    for idx, emp_name in enumerate(sel_emps):
        emp_data = filtered[filtered["ime_prezime"] == emp_name].sort_values("period")
        if len(emp_data) < 2:
            trend_cols[idx % len(trend_cols)].caption(f"{emp_name}: premalo podataka")
            continue
        first, last = emp_data.iloc[0], emp_data.iloc[-1]
        dp = last["avg_performance"] - first["avg_performance"]
        dpot = last["avg_potential"]  - first["avg_potential"]
        arrow_p   = "↑" if dp > 0.1   else ("↓" if dp < -0.1   else "→")
        arrow_pot = "↑" if dpot > 0.1 else ("↓" if dpot < -0.1 else "→")
        trend_cols[idx % len(trend_cols)].markdown(
            "**" + emp_name + "**  \n"
            f"Učinak {arrow_p} {dp:+.2f} &nbsp;|&nbsp; Potencijal {arrow_pot} {dpot:+.2f}"
        )


def render_team_health(manager_id: str, company_id: int, current_period: str):
    """
    Team Health Dashboard — vizualni tracker za procjene, ciljeve, IDP, 360.
    Svaki zaposlenik = red s 4 statusna stupca (progress bari i ikone).
    """
    conn = get_connection()
    try:
        team = pd.read_sql_query(
            """SELECT kadrovski_broj, ime_prezime, radno_mjesto
               FROM employees_master
               WHERE manager_id=? AND company_id=? AND active=1
               ORDER BY ime_prezime""",
            conn, params=(manager_id, company_id)
        )
        if team.empty:
            render_empty_state("👥", "Nema zaposlenika", "")
            return

        ids = team["kadrovski_broj"].tolist()
        placeholders = ",".join("?" * len(ids))

        # Procjene
        evals_done = conn.execute(
            f"SELECT kadrovski_broj FROM evaluations WHERE period=? AND is_self_eval=0 AND status='Submitted' AND kadrovski_broj IN ({placeholders})",
            [current_period] + ids
        ).fetchall()
        eval_ids = {r[0] for r in evals_done}

        # Samoprocjene
        self_done = conn.execute(
            f"SELECT kadrovski_broj FROM evaluations WHERE period=? AND is_self_eval=1 AND status='Submitted' AND kadrovski_broj IN ({placeholders})",
            [current_period] + ids
        ).fetchall()
        self_ids = {r[0] for r in self_done}

        # Ciljevi (ima li barem jedan?)
        goals_done = conn.execute(
            f"SELECT DISTINCT kadrovski_broj FROM goals WHERE period=? AND level='employee' AND kadrovski_broj IN ({placeholders})",
            [current_period] + ids
        ).fetchall()
        goal_ids = {r[0] for r in goals_done}

        # IDP
        idp_done = conn.execute(
            f"SELECT kadrovski_broj FROM development_plans WHERE period=? AND status IN ('Active','Approved') AND kadrovski_broj IN ({placeholders})",
            [current_period] + ids
        ).fetchall()
        idp_ids = {r[0] for r in idp_done}

        # 360 (ima li zaključanih feedback-ova kao target)
        feedback_done = conn.execute(
            f"SELECT DISTINCT target_id FROM peer_nominations WHERE period=? AND status='Submitted' AND target_id IN ({placeholders})",
            [current_period] + ids
        ).fetchall()
        feedback_ids = {r[0] for r in feedback_done}

    finally:
        conn.close()

    # ── SUMMARY METRICI ──────────────────────────────────────
    n = len(ids)
    s_eval  = len(eval_ids)
    s_self  = len(self_ids)
    s_goals = len(goal_ids)
    s_idp   = len(idp_ids)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Procjene zaključane",  f"{s_eval}/{n}",  delta=f"{int(s_eval/n*100)}%" if n else "0%")
    c2.metric("Samoprocjene",         f"{s_self}/{n}",  delta=f"{int(s_self/n*100)}%" if n else "0%")
    c3.metric("Ciljevi dodijeljeni",  f"{s_goals}/{n}", delta=f"{int(s_goals/n*100)}%" if n else "0%")
    c4.metric("IDP aktivni",          f"{s_idp}/{n}",   delta=f"{int(s_idp/n*100)}%" if n else "0%")

    # Progress bar ukupnog zdravlja
    total_checks = n * 4
    completed    = s_eval + s_self + s_goals + s_idp
    health_pct   = (completed / total_checks * 100) if total_checks > 0 else 0
    health_color = "#27ae60" if health_pct >= 75 else ("#f39c12" if health_pct >= 40 else "#e74c3c")
    st.markdown(
        f'<div style="background:#e8edf2;border-radius:99px;height:10px;margin:8px 0 16px;">'
        f'<div style="background:{health_color};width:{health_pct:.0f}%;height:10px;border-radius:99px;"></div>'
        f'</div>'
        f'<div style="font-size:12px;color:#666;margin-bottom:12px;">'
        f'Ukupno zdravlje tima: <b style="color:{health_color};">{health_pct:.0f}%</b> '
        f'({completed}/{total_checks} stavki završeno)</div>',
        unsafe_allow_html=True
    )

    # ── TABLICA PO ZAPOSLENIKU ──────────────────────────────
    st.markdown("**Pregled po zaposleniku:**")
    header_cols = st.columns([3, 1, 1, 1, 1, 1])
    for col, lbl in zip(header_cols, ["Zaposlenik", "Procjena", "Samoprocjena", "Ciljevi", "IDP", "360°"]):
        col.markdown(f"<div style='font-size:11px;font-weight:700;color:#888;'>{lbl}</div>", unsafe_allow_html=True)
    st.markdown("<hr style='margin:4px 0 8px;border-color:#f0f0f0;'>", unsafe_allow_html=True)

    for _, emp in team.iterrows():
        kid = emp["kadrovski_broj"]
        row_cols = st.columns([3, 1, 1, 1, 1, 1])
        row_cols[0].markdown(
            f"<div style='font-size:13px;font-weight:600;'>{emp['ime_prezime']}</div>"
            f"<div style='font-size:11px;color:#888;'>{emp.get('radno_mjesto','')}</div>",
            unsafe_allow_html=True
        )
        def _pill(done, label=""):
            icon = "✅" if done else "⬜"
            color = "#e8f5e9" if done else "#fafafa"
            border = "#4caf50" if done else "#e0e0e0"
            text_color = "#2e7d32" if done else "#9e9e9e"
            return (f'<div style="background:{color};border:1px solid {border};'
                    f'border-radius:6px;padding:3px 8px;font-size:12px;'
                    f'color:{text_color};text-align:center;">{icon}</div>')
        row_cols[1].markdown(_pill(kid in eval_ids),     unsafe_allow_html=True)
        row_cols[2].markdown(_pill(kid in self_ids),     unsafe_allow_html=True)
        row_cols[3].markdown(_pill(kid in goal_ids),     unsafe_allow_html=True)
        row_cols[4].markdown(_pill(kid in idp_ids),      unsafe_allow_html=True)
        row_cols[5].markdown(_pill(kid in feedback_ids), unsafe_allow_html=True)


def render_at_risk_items(manager_id: str, company_id: int, current_period: str):
    """
    At-Risk Items — ciljevi i IDP-ovi blizu roka s niskim progresom.
    Threshold: deadline unutar 14 dana AND progress < 50%.
    """
    from datetime import date, datetime

    conn = get_connection()
    try:
        team_ids = _get_team_ids(conn, manager_id, company_id)
        if not team_ids:
            render_empty_state("⚠️", "Nema zaposlenika", "")
            return

        placeholders = ",".join("?" * len(team_ids))

        # At-risk ciljevi
        goals_df = pd.read_sql_query(
            f"""SELECT g.id, g.title, g.progress, g.deadline, g.status,
                       g.kadrovski_broj, e.ime_prezime
               FROM goals g
               JOIN employees_master e ON g.kadrovski_broj = e.kadrovski_broj
               WHERE g.period=? AND g.level='employee' AND g.company_id=?
                 AND g.kadrovski_broj IN ({placeholders})
               ORDER BY g.deadline ASC""",
            conn, params=[current_period, company_id] + team_ids
        )

        # At-risk IDP aktivnosti — iz json_70, json_20, json_10
        idp_df = pd.read_sql_query(
            f"""SELECT dp.kadrovski_broj, e.ime_prezime,
                       dp.career_goal, dp.status
               FROM development_plans dp
               JOIN employees_master e ON dp.kadrovski_broj = e.kadrovski_broj
               WHERE dp.period=? AND dp.company_id=?
                 AND dp.kadrovski_broj IN ({placeholders})""",
            conn, params=[current_period, company_id] + team_ids
        )
    finally:
        conn.close()

    today = date.today()
    at_risk_goals = []

    if not goals_df.empty:
        goals_df["progress"] = pd.to_numeric(goals_df["progress"], errors="coerce").fillna(0)
        for _, g in goals_df.iterrows():
            try:
                dl = datetime.strptime(str(g["deadline"])[:10], "%Y-%m-%d").date()
                days_left = (dl - today).days
            except (ValueError, TypeError):
                days_left = 999
            if days_left <= 14 and g["progress"] < 50:
                at_risk_goals.append({
                    "ime": g["ime_prezime"],
                    "naziv": g["title"],
                    "progress": g["progress"],
                    "days_left": days_left,
                    "tip": "🎯 Cilj",
                    "urgency": "🔴 Kritično" if days_left <= 7 else "🟡 Upozorenje"
                })

    if not at_risk_goals:
        st.success("✅ Nema at-risk stavki unutar 14 dana! Svim ciljevima je napredak zadovoljavajući.")
        return

    # Sortiraj po urgentnosti
    at_risk_goals.sort(key=lambda x: x["days_left"])

    critical = [r for r in at_risk_goals if r["days_left"] <= 7]
    warning  = [r for r in at_risk_goals if r["days_left"] > 7]

    if critical:
        st.error(f"🔴 **{len(critical)} kritičnih stavki** — rok unutar 7 dana, progres < 50%")
    if warning:
        st.warning(f"🟡 **{len(warning)} upozorenja** — rok unutar 14 dana, progres < 50%")

    for item in at_risk_goals:
        days_txt = f"**{item['days_left']} dana**" if item['days_left'] >= 0 else "**PROŠAO ROK**"
        bar_color = "#e74c3c" if item['days_left'] <= 7 else "#f39c12"
        prog_pct = item['progress']
        st.markdown(f"""
        <div style="border-left:4px solid {bar_color};background:#fff;
                    border-radius:6px;padding:12px 16px;margin-bottom:8px;
                    box-shadow:0 1px 3px rgba(0,0,0,.06);">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <span style="font-size:13px;font-weight:700;">{item['tip']} — {item['naziv']}</span><br>
              <span style="font-size:12px;color:#666;">👤 {item['ime']}</span>
            </div>
            <div style="text-align:right;">
              <span style="font-size:12px;color:{bar_color};font-weight:700;">{item['urgency']}</span><br>
              <span style="font-size:11px;color:#888;">Rok za {days_txt}</span>
            </div>
          </div>
          <div style="background:#f0f0f0;border-radius:99px;height:6px;margin-top:8px;">
            <div style="background:{bar_color};width:{prog_pct:.0f}%;height:6px;border-radius:99px;min-width:4px;"></div>
          </div>
          <div style="font-size:11px;color:#888;margin-top:3px;">{prog_pct:.0f}% ostvareno</div>
        </div>
        """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# GLAVNI ENTRY POINT
# ─────────────────────────────────────────────────────────────

def render_analytics_hub(company_id: int, current_period: str):
    """
    Glavni analytics dashboard — poziva se iz views_hr.py.
    Tri taba: Completion · 9-Box · Goal Alignment
    """
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#1a237e,#283593);
                padding:16px 20px;border-radius:10px;margin-bottom:20px;">
      <div style="color:#fff;font-size:19px;font-weight:700;">
        📊 Analytics Hub
      </div>
      <div style="color:#9fa8da;font-size:13px;margin-top:4px;">
        Period: <b>{current_period}</b> &nbsp;·&nbsp;
        Sve metrike za zaključane podatke (status = Submitted)
      </div>
    </div>
    """, unsafe_allow_html=True)

    tab_completion, tab_9box, tab_alignment = st.tabs([
        "✅ Completion Tracker",
        "📊 9-Box Matrica",
        "🎯 Goal Alignment",
    ])

    with tab_completion:
        render_completion_tracker(company_id, current_period)

    with tab_9box:
        render_interactive_9box(company_id, current_period)

    with tab_alignment:
        render_goal_alignment(company_id, current_period)
