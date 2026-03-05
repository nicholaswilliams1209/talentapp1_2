# modules/feedback_360.py
# V1.2 — 360° Peer Feedback sustav
# Arhitektura: Manager nominira → Evaluator ispunjava → Anonimni prikaz

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sqlite3
import uuid
import random
from datetime import datetime
from modules.database import get_connection, DB_FILE
from modules.utils import render_empty_state, render_status_pill
from modules.constants import MAX_TEXT_LENGTH

# ─────────────────────────────────────────────────────────────
# KONSTANTE
# ─────────────────────────────────────────────────────────────

COMPETENCIES = [
    {
        "id":    "suradnja",
        "label": "Suradnja",
        "desc":  "Dijeljenje informacija, resursa i pomoć timu.",
    },
    {
        "id":    "komunikacija",
        "label": "Komunikacija",
        "desc":  "Jasnoća, konstruktivnost i aktivno slušanje.",
    },
    {
        "id":    "pouzdanost",
        "label": "Pouzdanost",
        "desc":  "Dosljednost u ispunjavanju obveza i rokova.",
    },
    {
        "id":    "rjesavanje",
        "label": "Rješavanje problema",
        "desc":  "Reakcija i učinkovitost pod pritiskom.",
    },
]

RELATIONSHIPS = ["Peer", "Direct Report", "Manager", "Self"]

# Prag za prikaz numeričkih rezultata (N<3 pravilo)
MIN_RESPONSES_FOR_SCORES = 3

# Boje po relaciji za grafove
REL_COLORS = {
    "Self":          "#7986cb",
    "Manager":       "#ef9a9a",
    "Peer":          "#80cbc4",
    "Direct Report": "#ffe082",
    "Suradnici":     "#b0bec5",   # kombinirani Others kad N<3
}


# ─────────────────────────────────────────────────────────────
# DB HELPER FUNKCIJE
# ─────────────────────────────────────────────────────────────

def get_nominations_for_target(conn, target_id: str, period: str, company_id: int) -> pd.DataFrame:
    """Vraća sve nominacije za jednog zaposlenika (target) u periodu."""
    return pd.read_sql_query(
        """SELECT pn.*, em.ime_prezime AS evaluator_name
           FROM peer_nominations pn
           LEFT JOIN employees_master em ON pn.evaluator_id = em.kadrovski_broj
           WHERE pn.target_id=? AND pn.period=? AND pn.company_id=?
           ORDER BY pn.relationship, pn.evaluator_id""",
        conn, params=(target_id, period, company_id)
    )


def get_nominations_for_evaluator(conn, evaluator_id: str, period: str, company_id: int) -> pd.DataFrame:
    """Vraća sve zadatke procjene koje treba ispuniti određeni evaluator."""
    return pd.read_sql_query(
        """SELECT pn.*, em.ime_prezime AS target_name, em.radno_mjesto
           FROM peer_nominations pn
           LEFT JOIN employees_master em ON pn.target_id = em.kadrovski_broj
           WHERE pn.evaluator_id=? AND pn.period=? AND pn.company_id=?
           ORDER BY pn.status DESC, em.ime_prezime""",
        conn, params=(evaluator_id, period, company_id)
    )


def get_feedback_results(conn, nomination_id: int) -> pd.DataFrame:
    """Vraća sve odgovore za jednu nominaciju."""
    return pd.read_sql_query(
        "SELECT * FROM peer_feedback_results WHERE nomination_id=?",
        conn, params=(nomination_id,)
    )


def create_nomination(company_id: int, period: str, target_id: str,
                      evaluator_id: str, relationship: str,
                      mgr_id: str = None) -> tuple[bool, str]:
    """Kreira novu nominaciju s UUID tokenom. Vraća (success, message)."""
    try:
        token = str(uuid.uuid4())
        conn = get_connection()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO peer_nominations
                   (period, target_id, evaluator_id, manager_id, relationship, status,
                    token, company_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (period, target_id, evaluator_id, mgr_id, relationship, "Draft",
                 token, company_id, datetime.now().strftime("%Y-%m-%d"))
            )
            conn.commit()
            return True, "Nominacija kreirana."
        finally:
            conn.close()
    except sqlite3.IntegrityError:
        return False, "Ova nominacija već postoji."
    except Exception as e:
        return False, str(e)


def save_feedback(nomination_id: int, scores: dict, start_text: str, stop_text: str) -> tuple[bool, str]:
    """
    Sprema feedback odgovore i mijenja status nominacije u 'Submitted'.
    scores: {competency_id: score_int}
    """
    try:
        conn = get_connection()
        try:
            # Briši stare (re-submit slučaj)
            conn.execute(
                "DELETE FROM peer_feedback_results WHERE nomination_id=?",
                (nomination_id,)
            )
            for comp_id, score in scores.items():
                conn.execute(
                    """INSERT INTO peer_feedback_results
                       (nomination_id, competency, score, comment)
                       VALUES (?,?,?,?)""",
                    (nomination_id, comp_id, int(score), "")
                )
            # Start/Stop komentari čuvaju se kao posebni retci
            if start_text.strip():
                conn.execute(
                    """INSERT INTO peer_feedback_results
                       (nomination_id, competency, score, comment)
                       VALUES (?,?,?,?)""",
                    (nomination_id, "__start__", None, start_text.strip())
                )
            if stop_text.strip():
                conn.execute(
                    """INSERT INTO peer_feedback_results
                       (nomination_id, competency, score, comment)
                       VALUES (?,?,?,?)""",
                    (nomination_id, "__stop__", None, stop_text.strip())
                )
            conn.execute(
                "UPDATE peer_nominations SET status='Submitted' WHERE id=?",
                (nomination_id,)
            )
            conn.commit()
            return True, "Feedback uspješno poslan."
        finally:
            conn.close()
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# AGREGACIJA I ANONIMNOST (N<3 pravilo)
# ─────────────────────────────────────────────────────────────

def aggregate_360_results(conn, target_id: str, period: str, company_id: int) -> dict:
    """
    Agregira sve submitted feedback rezultate za jednog zaposlenika.

    Vraća dict:
    {
      "by_relationship": {
          "Self":     {"scores": {comp: avg}, "n": int, "tier": 1|2|3},
          "Manager":  {...},
          "Suradnici": {...},   # merged Peer + Direct Report ako N<3
      },
      "start_comments": [str, ...],   # randomizirani
      "stop_comments":  [str, ...],   # randomizirani
      "total_submitted": int
    }

    Tier sustav:
      Tier 1 (n >= 3): prikaži prosjeke + komentare
      Tier 2 (1 <= n < 3): prikaži SAMO komentare
      Tier 3 (n == 0): empty state
    """
    nominations = pd.read_sql_query(
        """SELECT pn.id, pn.relationship, pn.evaluator_id
           FROM peer_nominations pn
           WHERE pn.target_id=? AND pn.period=? AND pn.company_id=?
             AND pn.status='Submitted'""",
        conn, params=(target_id, period, company_id)
    )

    if nominations.empty:
        return {
            "by_relationship": {},
            "start_comments": [],
            "stop_comments": [],
            "total_submitted": 0,
        }

    result = {}
    all_starts = []
    all_stops  = []

    # Grupiraj relacije
    rel_groups = {
        "Self":          nominations[nominations["relationship"] == "Self"],
        "Manager":       nominations[nominations["relationship"] == "Manager"],
        "Peer":          nominations[nominations["relationship"] == "Peer"],
        "Direct Report": nominations[nominations["relationship"] == "Direct Report"],
    }

    # Provjera N<3 za Peer + Direct Report → spoji u "Suradnici"
    n_peer = len(rel_groups["Peer"])
    n_dr   = len(rel_groups["Direct Report"])
    n_others = n_peer + n_dr

    # Koje grupe procesiramo individualno vs spojeno
    individual_rels = ["Self", "Manager"]
    others_rows = pd.concat([rel_groups["Peer"], rel_groups["Direct Report"]])

    for rel in individual_rels:
        rows = rel_groups[rel]
        if rows.empty:
            continue
        scores_by_comp = _collect_scores(conn, rows["id"].tolist())
        starts, stops  = _collect_comments(conn, rows["id"].tolist())
        all_starts.extend(starts)
        all_stops.extend(stops)
        n = len(rows)
        result[rel] = {
            "scores": scores_by_comp if n >= MIN_RESPONSES_FOR_SCORES else {},
            "n":      n,
            "tier":   1 if n >= MIN_RESPONSES_FOR_SCORES else 2,
        }

    # Others (Suradnici)
    if len(others_rows) > 0:
        scores_by_comp = _collect_scores(conn, others_rows["id"].tolist())
        starts, stops  = _collect_comments(conn, others_rows["id"].tolist())
        all_starts.extend(starts)
        all_stops.extend(stops)
        # Primijeni N<3 pravilo
        if n_others < MIN_RESPONSES_FOR_SCORES:
            tier = 2 if n_others >= 1 else 3
            result["Suradnici"] = {"scores": {}, "n": n_others, "tier": tier}
        else:
            # Provjeri i individualne grupe — ako Peer < 3 ali zajedno >= 3, spoji
            result["Suradnici"] = {
                "scores": scores_by_comp,
                "n":      n_others,
                "tier":   1,
            }

    # Randomiziraj komentare (sprječava de-anonimizaciju kroz redosljed)
    random.shuffle(all_starts)
    random.shuffle(all_stops)

    return {
        "by_relationship": result,
        "start_comments":  all_starts,
        "stop_comments":   all_stops,
        "total_submitted": len(nominations),
    }


def _collect_scores(conn, nomination_ids: list) -> dict:
    """Vraća prosjek ocjena po kompetenciji za listu nominacija."""
    if not nomination_ids:
        return {}
    placeholders = ",".join("?" * len(nomination_ids))
    rows = conn.execute(
        f"""SELECT competency, AVG(score) as avg_score
            FROM peer_feedback_results
            WHERE nomination_id IN ({placeholders})
              AND score IS NOT NULL
            GROUP BY competency""",
        nomination_ids
    ).fetchall()
    return {r[0]: round(r[1], 2) for r in rows}


def _collect_comments(conn, nomination_ids: list) -> tuple[list, list]:
    """Vraća start/stop komentare za listu nominacija."""
    if not nomination_ids:
        return [], []
    placeholders = ",".join("?" * len(nomination_ids))
    rows = conn.execute(
        f"""SELECT competency, comment FROM peer_feedback_results
            WHERE nomination_id IN ({placeholders})
              AND competency IN ('__start__', '__stop__')
              AND comment IS NOT NULL AND comment != ''""",
        nomination_ids
    ).fetchall()
    starts = [r[1] for r in rows if r[0] == "__start__"]
    stops  = [r[1] for r in rows if r[0] == "__stop__"]
    return starts, stops


# ─────────────────────────────────────────────────────────────
# UI KOMPONENTE
# ─────────────────────────────────────────────────────────────

def render_feedback_form(nomination_row: pd.Series):
    """
    Forma za ispunjavanje 360° feedbacka.
    Poziva se iz Employee view → tab Moji Zadaci.
    """
    nom_id      = int(nomination_row["id"])
    target_name = nomination_row.get("target_name", "kolegu")
    rel         = nomination_row.get("relationship", "Peer")
    is_self     = (rel == "Self")

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#1a237e,#283593);
                padding:16px 20px;border-radius:10px;margin-bottom:16px;">
      <div style="color:#fff;font-size:17px;font-weight:700;">
        {'📝 Samoprocjena' if is_self else f'📝 Procjena: {target_name}'}
      </div>
      <div style="color:#9fa8da;font-size:12px;margin-top:4px;">
        Vaša uloga: <b>{rel}</b> &nbsp;·&nbsp;
        Pišite u trećem licu kako biste zaštitili svoju anonimnost
        (npr. "Ova osoba bi trebala...").
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Provjeri je li već submittano — jedna konekcija za oba upita
    _form_conn = get_connection()
    try:
        existing        = get_feedback_results(_form_conn, nom_id)
        already_done    = nomination_row.get("status") == "Submitted"
    finally:
        _form_conn.close()

    if already_done:
        st.markdown(
            render_status_pill("Submitted") +
            " &nbsp; Feedback je poslan i zaključan.",
            unsafe_allow_html=True
        )
        score_rows = existing[existing["score"].notna()] if not existing.empty else existing
        if not score_rows.empty:
            cols = st.columns(len(score_rows))
            for ci, (_, sr) in enumerate(score_rows.iterrows()):
                lbl = next(
                    (cp["label"] for cp in COMPETENCIES if cp["id"] == sr["competency"]),
                    sr["competency"]
                )
                cols[ci].metric(lbl, f"{int(sr['score'])}/5")
        return

    with st.form(f"feedback_form_{nom_id}"):
        scores = {}
        st.markdown("#### Kompetencije (ocjena 1–5)")
        col1, col2 = st.columns(2)
        for i, comp in enumerate(COMPETENCIES):
            target_col = col1 if i % 2 == 0 else col2
            with target_col:
                st.markdown(f"**{comp['label']}**")
                st.caption(comp["desc"])
                scores[comp["id"]] = st.slider(
                    comp["label"], 1, 5, 3,
                    key=f"fb_{nom_id}_{comp['id']}",
                    label_visibility="collapsed"
                )

        st.markdown("---")
        st.markdown("#### Otvorena pitanja")
        st.caption("💡 Pišite u trećem licu: *'Ova osoba bi trebala...'*")
        start_text = st.text_area(
            "🟢 Što bi ova osoba trebala **POČETI** raditi?",
            max_chars=MAX_TEXT_LENGTH,
            placeholder="Ova osoba bi trebala početi...",
            key=f"start_{nom_id}"
        )
        stop_text = st.text_area(
            "🔴 Što bi ova osoba trebala **PRESTATI** raditi?",
            max_chars=MAX_TEXT_LENGTH,
            placeholder="Ova osoba bi trebala prestati...",
            key=f"stop_{nom_id}"
        )

        if st.form_submit_button("✅ Pošalji Feedback", use_container_width=True):
            ok, msg = save_feedback(nom_id, scores, start_text, stop_text)
            if ok:
                st.toast("✅ Feedback poslan!", icon="📝")
                st.rerun()
            else:
                st.error(f"Greška: {msg}")


def render_360_report(conn, target_id: str, target_name: str,
                      period: str, company_id: int):
    """
    Kompletan 360° izvještaj za jednog zaposlenika.
    Koristi se u Manager i HR viewu.
    """
    agg = aggregate_360_results(conn, target_id, period, company_id)
    total = agg["total_submitted"]

    if total == 0:
        render_empty_state(
            "🔄",
            "Nema podataka za 360° izvještaj",
            f"Još nitko nije submittao feedback za {target_name} u ovom periodu.",
        )
        return

    st.markdown(f"**Ukupno submittanih procjena:** {total}")
    st.divider()

    # ── RADAR CHART ──────────────────────────────────────────
    by_rel = agg["by_relationship"]
    comp_labels = [c["label"] for c in COMPETENCIES]
    comp_ids    = [c["id"]    for c in COMPETENCIES]

    fig = go.Figure()
    has_chart_data = False

    for rel, data in by_rel.items():
        if data["tier"] != 1 or not data["scores"]:
            continue
        vals = [data["scores"].get(cid, 0) for cid in comp_ids]
        # Zatvoreni polygon
        vals_closed = vals + [vals[0]]
        labels_closed = comp_labels + [comp_labels[0]]
        fig.add_trace(go.Scatterpolar(
            r=vals_closed,
            theta=labels_closed,
            fill="toself",
            fillcolor=REL_COLORS.get(rel, "#ccc"),
            opacity=0.3,
            line=dict(color=REL_COLORS.get(rel, "#ccc"), width=2),
            name=f"{rel} (n={data['n']})"
        ))
        has_chart_data = True

    if has_chart_data:
        fig.update_layout(
            polar=dict(
                radialaxis=dict(visible=True, range=[0, 5], tickfont_size=10)
            ),
            showlegend=True,
            title=f"360° Radar — {target_name}",
            height=420,
            margin=dict(l=40, r=40, t=60, b=40),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("📊 Radar graf nije dostupan — nedovoljan broj odgovora po skupini (min. 3).")

    # ── GAP ANALIZA ──────────────────────────────────────────
    self_scores     = by_rel.get("Self",      {}).get("scores", {})
    suradnici_scores = by_rel.get("Suradnici", {}).get("scores", {})

    if self_scores and suradnici_scores:
        st.markdown("#### 📊 Gap Analiza: Self vs. Suradnici")
        gap_rows = []
        for comp in COMPETENCIES:
            cid = comp["id"]
            s = self_scores.get(cid, 0)
            o = suradnici_scores.get(cid, 0)
            gap = round(s - o, 2)
            direction = "🟡 Podudaranje" if abs(gap) < 0.5 else (
                "🔴 Precjenjivanje" if gap > 0 else "🟢 Podcjenjivanje"
            )
            gap_rows.append({
                "Kompetencija": comp["label"],
                "Self":         s,
                "Suradnici":    o,
                "Gap (Self−Ost.)": gap,
                "Interpretacija": direction,
            })
        st.dataframe(pd.DataFrame(gap_rows), hide_index=True, use_container_width=True)

    st.divider()

    # ── KOMENTARI (tier-aware) ────────────────────────────────
    _render_comments_section(agg)


def _render_comments_section(agg: dict):
    """Prikazuje start/stop komentare s tier-aware logikom."""
    starts = agg.get("start_comments", [])
    stops  = agg.get("stop_comments",  [])

    if not starts and not stops:
        render_empty_state(
            "💬", "Nema komentara",
            "Evaluatori nisu ostavili tekstualne komentare."
        )
        return

    st.markdown("#### 💬 Otvoreni komentari")
    st.caption("Prikazani su randomizirano kako bi se zaštitila anonimnost.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**🟢 Što bi trebalo POČETI**")
        if starts:
            for txt in starts:
                st.markdown(
                    f'<div style="background:#e8f5e9;border-left:3px solid #4caf50;'
                    f'border-radius:6px;padding:10px 14px;margin-bottom:8px;'
                    f'font-size:13px;color:#1b5e20;">{txt}</div>',
                    unsafe_allow_html=True
                )
        else:
            st.caption("Nema komentara.")
    with c2:
        st.markdown("**🔴 Što bi trebalo PRESTATI**")
        if stops:
            for txt in stops:
                st.markdown(
                    f'<div style="background:#ffebee;border-left:3px solid #ef5350;'
                    f'border-radius:6px;padding:10px 14px;margin-bottom:8px;'
                    f'font-size:13px;color:#b71c1c;">{txt}</div>',
                    unsafe_allow_html=True
                )
        else:
            st.caption("Nema komentara.")

    # Tier 2 upozorenje
    by_rel = agg.get("by_relationship", {})
    t2_rels = [r for r, d in by_rel.items() if d.get("tier") == 2]
    if t2_rels:
        st.warning(
            f"⚠️ Za skupinu(e) **{', '.join(t2_rels)}**: "
            f"manje od {MIN_RESPONSES_FOR_SCORES} odgovora — "
            "numeričke ocjene su skrivene radi zaštite anonimnosti. "
            "Prikazani su samo komentari."
        )


def render_historical_trend(conn, target_id: str, company_id: int):
    """
    Grafikon prosječnih ocjena kroz više perioda (Snail Trail za 360°).
    """
    hist = pd.read_sql_query(
        """SELECT pn.period,
                  pfr.competency,
                  AVG(pfr.score) as avg_score
           FROM peer_nominations pn
           JOIN peer_feedback_results pfr ON pfr.nomination_id = pn.id
           WHERE pn.target_id=? AND pn.company_id=?
             AND pn.status='Submitted'
             AND pfr.score IS NOT NULL
           GROUP BY pn.period, pfr.competency
           ORDER BY pn.period""",
        conn, params=(target_id, company_id)
    )

    if hist.empty:
        render_empty_state(
            "📈", "Nema povijesnih podataka",
            "Trend će biti vidljiv nakon završetka najmanje dva perioda."
        )
        return

    pivot = hist.pivot(index="period", columns="competency", values="avg_score").reset_index()
    comp_cols = [c["id"] for c in COMPETENCIES if c["id"] in pivot.columns]

    if not comp_cols:
        st.info("Nedovoljno podataka za trend.")
        return

    import plotly.express as px
    fig = px.line(
        pivot, x="period", y=comp_cols,
        markers=True, title="360° Trend kroz periode",
        labels={"value": "Prosječna ocjena", "period": "Period", "variable": "Kompetencija"},
        color_discrete_sequence=list(REL_COLORS.values()),
    )
    fig.update_layout(yaxis=dict(range=[0.5, 5.5]))
    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────
# HR ADMIN: UPRAVLJANJE NOMINACIJAMA
# ─────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
# BULK GENERACIJA (1-klik za cijeli tim)
# ─────────────────────────────────────────────────────────────

def bulk_generate_team_nominations(company_id: int, period: str,
                                   team_ids: list, manager_id: str) -> dict:
    """
    1-klik bulk generacija: za svaki par unutar tima kreira obostrane
    Peer nominacije + Self za svakog + Manager→Employee.

    Vraća: {"created": int, "skipped": int, "errors": list}
    """
    created = 0
    skipped = 0
    errors  = []

    pairs_set = set()  # set deduplicira automatski

    for eid in team_ids:
        pairs_set.add((eid, eid,        "Self",          manager_id))
        pairs_set.add((eid, manager_id, "Manager",       manager_id))
        pairs_set.add((manager_id, eid, "Direct Report", manager_id))
        for other_id in team_ids:
            if other_id != eid:
                pairs_set.add((eid, other_id, "Peer", manager_id))

    # Manager Self (samo jednom, neovisno o veličini tima)
    pairs_set.add((manager_id, manager_id, "Self", manager_id))

    for (target_id, evaluator_id, relationship, mgr_id) in pairs_set:
        ok, msg = create_nomination(
            company_id, period, target_id, evaluator_id, relationship, mgr_id
        )
        if ok:
            created += 1
        elif "već postoji" in msg:
            skipped += 1
        else:
            errors.append(f"{evaluator_id}→{target_id}: {msg}")

    return {"created": created, "skipped": skipped, "errors": errors}


def search_employees(conn, query: str, company_id: int,
                     exclude_ids: list = None) -> pd.DataFrame:
    """
    Pretraga zaposlenika po imenu — za cross-functional dodavanje.
    Vraća max 10 rezultata, isključuje exclude_ids.
    """
    if not query or len(query.strip()) < 2:
        return pd.DataFrame()

    like_q = f"%{query.strip()}%"
    exclude_ids = exclude_ids or []

    base_sql = """
        SELECT kadrovski_broj, ime_prezime, radno_mjesto, department
        FROM employees_master
        WHERE company_id=? AND active=1
          AND (ime_prezime LIKE ? OR radno_mjesto LIKE ?)
    """
    params = [company_id, like_q, like_q]

    if exclude_ids:
        placeholders = ",".join("?" * len(exclude_ids))
        base_sql += f" AND kadrovski_broj NOT IN ({placeholders})"
        params.extend(exclude_ids)

    base_sql += " ORDER BY ime_prezime LIMIT 10"
    return pd.read_sql_query(base_sql, conn, params=params)


# ─────────────────────────────────────────────────────────────
# KARTICA KOMPONENTA (Task Card)
# ─────────────────────────────────────────────────────────────

def _task_card_html(target_name: str, relationship: str,
                    status: str, dept: str = "") -> str:
    """Renderira HTML karticu za jedan feedback zadatak."""
    is_done   = (status == "Submitted")
    border    = "#27ae60" if is_done else "#3f51b5"
    bg        = "#f1f8f4" if is_done else "#f5f7ff"
    icon      = "✅" if is_done else "📝"
    label     = "Dovršeno" if is_done else "Čeka vas"
    lbl_bg    = "#27ae60" if is_done else "#e53935"
    rel_color = REL_COLORS.get(relationship, "#888")

    return f"""
    <div style="border:1px solid {border};border-left:5px solid {border};
                border-radius:10px;padding:14px 16px;background:{bg};
                margin-bottom:8px;cursor:pointer;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div>
          <div style="font-size:15px;font-weight:700;color:#1a1a2e;">
            {icon} {target_name}
          </div>
          <div style="font-size:12px;margin-top:4px;">
            <span style="background:{rel_color};color:#fff;padding:2px 8px;
                         border-radius:99px;font-size:11px;">{relationship}</span>
            {'&nbsp;<span style="color:#888;font-size:11px;">' + dept + '</span>' if dept else ''}
          </div>
        </div>
        <span style="background:{lbl_bg};color:#fff;font-size:11px;
                     padding:3px 10px;border-radius:99px;font-weight:600;
                     white-space:nowrap;">{label}</span>
      </div>
    </div>
    """


# ─────────────────────────────────────────────────────────────
# EMPLOYEE VIEW: TASK GRID
# ─────────────────────────────────────────────────────────────

def render_employee_360_tasks(username: str, my_name: str,
                               company_id: int, current_period: str):
    """
    Employee tab — task kartice + inline forma.
    Vizualni prioritet: 'Čeka vas' prve, 'Dovršeno' na dnu.
    """
    conn = get_connection()
    try:
        # ── Dohvat peer_deadline iz baze ──────────────────────────
        period_row = conn.execute(
            "SELECT peer_deadline, deadline FROM periods WHERE period_name=? AND company_id=?",
            (current_period, company_id)
        ).fetchone()
        peer_dl = None
        if period_row:
            peer_dl = period_row[0] or period_row[1]  # peer_deadline, fallback na deadline

        if peer_dl:
            from datetime import date as _date
            try:
                days_left = (_date.fromisoformat(peer_dl) - _date.today()).days
                if days_left < 0:
                    st.error(f"⏰ Rok za 360° feedback je **istekao** ({peer_dl}).")
                elif days_left <= 3:
                    st.warning(f"⚠️ Rok za 360° feedback: **{peer_dl}** — još samo **{days_left} dana**!")
                else:
                    st.info(f"📅 Rok za ispunjavanje 360° feedbacka: **{peer_dl}** ({days_left} dana)")
            except ValueError:
                st.info(f"📅 Rok za 360° feedback: **{peer_dl}**")

        tasks = get_nominations_for_evaluator(conn, username, current_period, company_id)

        if tasks.empty:
            render_empty_state(
                "🔄", "Nema zadataka feedbacka",
                "Manager još nije pokrenuo 360° krug za vaš tim.",
            )
            # Vlastiti izvještaj (ako postoji iz prošlog perioda)
            st.divider()
            st.markdown("#### 📊 Moj 360° izvještaj")
            render_360_report(conn, username, my_name, current_period, company_id)
            return

        pending  = tasks[tasks["status"] != "Submitted"]
        done     = tasks[tasks["status"] == "Submitted"]
        n_total  = len(tasks)
        n_done   = len(done)
        pct      = int(n_done / n_total * 100) if n_total else 0
        bar_col  = "#27ae60" if pct == 100 else "#3f51b5"

        # Progress header
        st.markdown(f"""
        <div style="background:#f8f9fa;border-radius:10px;padding:14px 18px;
                    margin-bottom:16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-weight:700;font-size:15px;color:#1a1a2e;">
              Moji zadaci feedbacka
            </span>
            <span style="font-size:14px;font-weight:700;color:{bar_col};">
              {n_done} / {n_total} dovršeno
            </span>
          </div>
          <div style="background:#e0e0e0;border-radius:99px;height:8px;margin-top:8px;">
            <div style="background:{bar_col};width:{pct}%;height:8px;
                        border-radius:99px;transition:width .4s;"></div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # Odabir zadatka — kartice kao selector
        all_ordered = pd.concat([pending, done], ignore_index=True)

        if not pending.empty:
            st.markdown("**📝 Čeka vas**")
        for idx, row in all_ordered.iterrows():
            dept = row.get("radno_mjesto", "")
            st.markdown(
                _task_card_html(
                    row.get("target_name", "?"),
                    row["relationship"],
                    row["status"],
                    dept
                ),
                unsafe_allow_html=True
            )

        st.divider()

        # Selector za aktivni zadatak
        task_labels = [
            f"{'✅' if r['status']=='Submitted' else '📝'} "
            f"{r.get('target_name','?')} ({r['relationship']})"
            for _, r in all_ordered.iterrows()
        ]
        # Default: prvi pending (enumerate je siguran nakon reset_index)
        default_idx = 0
        for enum_i, (_, row) in enumerate(all_ordered.iterrows()):
            if row["status"] != "Submitted":
                default_idx = enum_i
                break

        sel_label = st.selectbox(
            "Ispuni feedback za:",
            task_labels,
            index=default_idx,
            key="emp_360_active_task"
        )
        sel_task = all_ordered.iloc[task_labels.index(sel_label)]
        render_feedback_form(sel_task)

        # ── Vlastiti 360° izvještaj — Double-blind guard ─────────
        st.divider()
        st.markdown("#### 📊 Moj 360° izvještaj")

        inbound = pd.read_sql_query(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN status='Submitted' THEN 1 ELSE 0 END) as done
               FROM peer_nominations
               WHERE target_id=? AND period=? AND company_id=?
                 AND relationship != 'Self'""",
            conn, params=(username, current_period, company_id)
        ).iloc[0]
        n_in_total = int(inbound["total"] or 0)
        n_in_done  = int(inbound["done"]  or 0)

        if n_in_total == 0:
            render_empty_state(
                "🔄", "Nema pristiglih procjena",
                "Još nitko nije nominiran da vas ocjeni u ovom periodu."
            )
        elif n_in_done < n_in_total:
            remaining = n_in_total - n_in_done
            msg = (
                f"⏳ Vaš izvještaj bit će dostupan kad svi završe. "
                f"{n_in_done}/{n_in_total} završilo — još {remaining} čeka."
            )
            st.info(msg)
        else:
            render_360_report(conn, username, my_name, current_period, company_id)

    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# MANAGER VIEW: REDESIGN
# ─────────────────────────────────────────────────────────────

def render_manager_360(username: str, company_id: int, current_period: str):
    """
    Manager 360° — 3 taba:
      1. Pokretanje  → 1-klik bulk + cross-functional pretraga
      2. Praćenje    → team grid s progress barovima po osobi
      3. Izvještaji  → selector + radar/gap
    """
    conn = get_connection()
    try:
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#004d40,#00695c);
                    padding:14px 20px;border-radius:10px;margin-bottom:16px;">
          <div style="color:#fff;font-size:17px;font-weight:700;">
            🔄 360° Feedback Tima
          </div>
          <div style="color:#80cbc4;font-size:12px;margin-top:3px;">
            Period: <b>{current_period}</b>
          </div>
        </div>
        """, unsafe_allow_html=True)

        my_team = pd.read_sql_query(
            """SELECT kadrovski_broj, ime_prezime, radno_mjesto, department
               FROM employees_master
               WHERE manager_id=? AND company_id=? AND active=1
               ORDER BY ime_prezime""",
            conn, params=(username, company_id)
        )

        if my_team.empty:
            render_empty_state("👥", "Nemate dodijeljenih zaposlenika",
                               "Obratite se HR-u za dodjelu tima.")
            return

        team_ids = my_team["kadrovski_broj"].tolist()

        # Provjeri postoje li već nominacije za ovaj tim/period
        placeholders = ",".join("?" * len(team_ids))
        existing_count = conn.execute(
            f"""SELECT COUNT(*) FROM peer_nominations
                WHERE target_id IN ({placeholders})
                  AND period=? AND company_id=?""",
            team_ids + [current_period, company_id]
        ).fetchone()[0]

        tab_launch, tab_track, tab_report = st.tabs([
            "🚀 Pokretanje", "📊 Praćenje tima", "📋 Izvještaji"
        ])

        # ════════════════════════════════════════════════
        # TAB 1: POKRETANJE
        # ════════════════════════════════════════════════
        with tab_launch:
            # Prikaz peer_deadline manageru
            _pl_conn = get_connection()
            try:
                _pr = _pl_conn.execute(
                    "SELECT peer_deadline, deadline FROM periods WHERE period_name=? AND company_id=?",
                    (current_period, company_id)
                ).fetchone()
            finally:
                _pl_conn.close()
            _pdl = (_pr[0] or _pr[1]) if _pr else None
            if _pdl:
                from datetime import date as _d2
                try:
                    _days = (_d2.fromisoformat(_pdl) - _d2.today()).days
                    _icon = "⏰" if _days < 0 else ("⚠️" if _days <= 3 else "📅")
                    _msg  = f" — još **{_days} dana**" if _days > 0 else (" — **ISTEKAO**" if _days < 0 else " — **Danas istječe!**")
                    st.info(f"{_icon} Rok za 360° feedback zaposlenika: **{_pdl}**{_msg}")
                except ValueError:
                    st.info(f"📅 Rok za 360°: **{_pdl}**")

            # ── BULK GUMB ───────────────────────────────
            team_names = ", ".join(my_team["ime_prezime"].tolist()[:4])
            if len(my_team) > 4:
                team_names += f" i još {len(my_team)-4}..."

            if existing_count > 0:
                st.success(
                    f"✅ 360° krug je **aktivan** — {existing_count} nominacija "
                    f"kreirano za ovaj period."
                )
                bulk_label = "🔄 Dodaj nedostajuće nominacije"
                bulk_help  = ("Kreira samo one nominacije koje nedostaju. "
                              "Postojeće i popunjene ostaju netaknute.")
            else:
                st.info(
                    f"Tim ({len(my_team)} članova): **{team_names}**\n\n"
                    "Jedan klik kreira Self, Manager i međusobne Peer nominacije "
                    "za sve."
                )
                bulk_label = "🚀 Pokreni 360° za cijeli tim"
                bulk_help  = ("Kreira: Self za svakog · Manager ocjena svakog · "
                              "Peer ocjene između svih članova tima")

            col_btn, col_info = st.columns([2, 3])
            with col_btn:
                if st.button(bulk_label, use_container_width=True,
                             help=bulk_help, type="primary"):
                    with st.spinner("Kreiram nominacije..."):
                        result = bulk_generate_team_nominations(
                            company_id, current_period, team_ids, username
                        )
                    if result["errors"]:
                        st.warning(
                            f"Kreirano: {result['created']}, "
                            f"preskočeno: {result['skipped']}, "
                            f"greške: {len(result['errors'])}"
                        )
                    else:
                        st.toast(
                            f"✅ {result['created']} novih nominacija, "
                            f"{result['skipped']} već postojalo.",
                            icon="🚀"
                        )
                    st.rerun()

            with col_info:
                n_members = len(my_team)
                expected  = (n_members * (n_members - 1)) + (n_members * 2)
                st.markdown(f"""
                <div style="background:#e8f5e9;border-radius:8px;
                            padding:12px 16px;font-size:13px;">
                  <b>Što se kreira za {n_members} članova:</b><br>
                  · {n_members} × Self nominacija<br>
                  · {n_members} × Manager ocjena<br>
                  · {n_members} × Upward (ocjena managera)<br>
                  · {n_members*(n_members-1)} × Peer ocjene<br>
                  <b>Ukupno: ~{expected} nominacija</b>
                </div>
                """, unsafe_allow_html=True)

            # ── CROSS-FUNCTIONAL DODAVANJE ───────────────
            st.divider()
            st.markdown("#### ➕ Dodaj osobu izvan tima")
            st.caption(
                "Za suradnike iz drugih odjela. "
                "Pretraži po imenu — bez dugačkih padajućih lista."
            )

            col_s, col_r = st.columns([3, 2])
            with col_s:
                search_q = st.text_input(
                    "🔍 Pretraži zaposlenika",
                    placeholder="Upiši barem 2 slova imena...",
                    key="cross_search"
                )
            search_results = search_employees(
                conn, search_q, company_id, exclude_ids=team_ids + [username]
            ) if search_q and len(search_q) >= 2 else pd.DataFrame()

            if not search_results.empty:
                with st.form("cross_functional_form"):
                    sel_cross_name = st.selectbox(
                        "Odaberi osobu:",
                        search_results["ime_prezime"].tolist(),
                        key="cross_sel"
                    )
                    cross_row = search_results[
                        search_results["ime_prezime"] == sel_cross_name
                    ].iloc[0]
                    st.caption(
                        f"📍 {cross_row.get('radno_mjesto','')} · "
                        f"{cross_row.get('department','')}"
                    )

                    c1, c2, c3 = st.columns(3)
                    target_name_cf = c1.selectbox(
                        "Procjenjuje se:", my_team["ime_prezime"].tolist(),
                        key="cf_target"
                    )
                    rel_cf = c2.selectbox(
                        "Relacija:", ["Peer", "Direct Report"],
                        key="cf_rel"
                    )
                    reciprocal = c3.checkbox("Obostrano", value=True,
                                            key="cf_recip",
                                            help="Kreira feedback u oba smjera")

                    if st.form_submit_button("➕ Dodaj"):
                        cross_id  = cross_row["kadrovski_broj"]
                        target_id = my_team[
                            my_team["ime_prezime"] == target_name_cf
                        ]["kadrovski_broj"].values[0]

                        if cross_id == target_id:
                            st.error("Ista osoba ne može biti istovremeno target i evaluator.")
                            st.stop()

                        ok1, m1 = create_nomination(
                            company_id, current_period,
                            target_id, cross_id, rel_cf, username
                        )
                        msgs = [m1]
                        if reciprocal:
                            ok2, m2 = create_nomination(
                                company_id, current_period,
                                cross_id, target_id, rel_cf, username
                            )
                            msgs.append(m2)

                        if all("već postoji" in m or "Nominacija kreirana" in m
                               for m in msgs):
                            st.toast(
                                f"✅ {sel_cross_name} dodan "
                                f"{'obostrano' if reciprocal else ''}.",
                                icon="➕"
                            )
                            st.rerun()
                        else:
                            st.warning(" | ".join(msgs))
            elif search_q and len(search_q) >= 2:
                st.caption("Nema rezultata za upit.")

        # ════════════════════════════════════════════════
        # TAB 2: PRAĆENJE TIMA
        # ════════════════════════════════════════════════
        with tab_track:
            st.markdown("#### Status 360° po zaposleniku")

            if existing_count == 0:
                render_empty_state(
                    "🚀", "Krug nije pokrenut",
                    "Idi na tab 'Pokretanje' i pokreni 360° za tim.",
                    action_text="Pokretanje →"
                )
            else:
                # Dohvat statusa za cijeli tim odjednom
                status_df = pd.read_sql_query(
                    f"""SELECT pn.target_id,
                               et.ime_prezime AS target_name,
                               pn.relationship,
                               COUNT(*) AS total,
                               SUM(CASE WHEN pn.status='Submitted'
                                   THEN 1 ELSE 0 END) AS done
                        FROM peer_nominations pn
                        LEFT JOIN employees_master et
                               ON pn.target_id = et.kadrovski_broj
                        WHERE pn.target_id IN ({placeholders})
                          AND pn.period=? AND pn.company_id=?
                        GROUP BY pn.target_id, pn.relationship
                        ORDER BY et.ime_prezime""",
                    conn, params=team_ids + [current_period, company_id]
                )

                if status_df.empty:
                    st.info("Nema podataka o statusu.")
                else:
                    # Grupiraj po zaposleniku — 1 kartica po osobi
                    for emp_id, emp_group in status_df.groupby("target_id"):
                        emp_name = emp_group["target_name"].iloc[0]
                        total_all = emp_group["total"].sum()
                        done_all  = emp_group["done"].sum()
                        pct_emp   = int(done_all / total_all * 100) if total_all else 0
                        color_emp = "#27ae60" if pct_emp == 100 else (
                            "#2196f3" if pct_emp > 0 else "#9e9e9e"
                        )

                        with st.container():
                            st.markdown(f"""
                            <div style="background:#fafafa;border:1px solid #e0e0e0;
                                        border-left:4px solid {color_emp};
                                        border-radius:8px;padding:12px 16px;
                                        margin-bottom:4px;">
                              <div style="display:flex;justify-content:space-between;">
                                <b style="font-size:14px;">👤 {emp_name}</b>
                                <span style="font-size:13px;color:{color_emp};
                                             font-weight:700;">
                                  {done_all}/{total_all} &nbsp;({pct_emp}%)
                                </span>
                              </div>
                              <div style="background:#e0e0e0;border-radius:99px;
                                          height:6px;margin-top:8px;">
                                <div style="background:{color_emp};width:{pct_emp}%;
                                            height:6px;border-radius:99px;"></div>
                              </div>
                            </div>
                            """, unsafe_allow_html=True)

                            # Breakdown po relaciji (collapsible)
                            with st.expander("detalji po relaciji", expanded=False):
                                rel_cols = st.columns(len(emp_group))
                                for ci, (_, rel_row) in enumerate(emp_group.iterrows()):
                                    rp  = int(rel_row["done"] / rel_row["total"] * 100) if rel_row["total"] else 0
                                    rc  = REL_COLORS.get(rel_row["relationship"], "#888")
                                    rel_cols[ci].markdown(
                                        f'<div style="text-align:center;'
                                        f'background:{rc}22;border-radius:8px;'
                                        f'padding:8px;">'
                                        f'<div style="font-size:11px;color:{rc};'
                                        f'font-weight:700;">{rel_row["relationship"]}</div>'
                                        f'<div style="font-size:18px;font-weight:800;">'
                                        f'{int(rel_row["done"])}/{int(rel_row["total"])}</div>'
                                        f'</div>',
                                        unsafe_allow_html=True
                                    )

        # ════════════════════════════════════════════════
        # TAB 3: IZVJEŠTAJI
        # ════════════════════════════════════════════════
        with tab_report:
            if existing_count == 0:
                render_empty_state("📋", "Nema podataka",
                                   "Pokrenite 360° krug prvo.")
            else:
                sel_name = st.selectbox(
                    "Zaposlenik:",
                    my_team["ime_prezime"].tolist(),
                    key="mgr_360_rep"
                )
                sel_id = my_team[
                    my_team["ime_prezime"] == sel_name
                ]["kadrovski_broj"].values[0]

                rep_t1, rep_t2 = st.tabs(["📊 Ovaj period", "📈 Trend"])
                with rep_t1:
                    render_360_report(conn, sel_id, sel_name,
                                      current_period, company_id)
                with rep_t2:
                    render_historical_trend(conn, sel_id, company_id)

    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# HR ADMIN VIEW: REDESIGN
# ─────────────────────────────────────────────────────────────

def render_hr_360_admin(company_id: int, current_period: str):
    """
    HR sučelje — pregled svih timova + bulk akcije + izvještaji.
    """
    conn = get_connection()
    try:
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#4a148c,#6a1b9a);
                    padding:16px 20px;border-radius:10px;margin-bottom:20px;">
          <div style="color:#fff;font-size:18px;font-weight:700;">
            🔄 360° Feedback — Admin Pregled
          </div>
          <div style="color:#ce93d8;font-size:13px;margin-top:4px;">
            Period: <b>{current_period}</b>
          </div>
        </div>
        """, unsafe_allow_html=True)

        employees = pd.read_sql_query(
            """SELECT kadrovski_broj, ime_prezime, department,
                      manager_id, is_manager
               FROM employees_master
               WHERE company_id=? AND active=1
               ORDER BY department, ime_prezime""",
            conn, params=(company_id,)
        )

        tab_overview, tab_report = st.tabs([
            "📊 Pregled i akcije", "📋 Izvještaji"
        ])

        # ════════════════════════════════════════════════
        # TAB 1: PREGLED I AKCIJE
        # ════════════════════════════════════════════════
        with tab_overview:

            # Summary metrike
            total_noms = conn.execute(
                "SELECT COUNT(*) FROM peer_nominations WHERE period=? AND company_id=?",
                (current_period, company_id)
            ).fetchone()[0]
            done_noms = conn.execute(
                """SELECT COUNT(*) FROM peer_nominations
                   WHERE period=? AND company_id=? AND status='Submitted'""",
                (current_period, company_id)
            ).fetchone()[0]

            c1, c2, c3 = st.columns(3)
            c1.metric("Ukupno nominacija", total_noms)
            c2.metric("Submittano", done_noms)
            c3.metric("Čeka odgovor", total_noms - done_noms)

            if total_noms > 0:
                overall_pct = int(done_noms / total_noms * 100)
                bar_c = "#27ae60" if overall_pct == 100 else "#7b1fa2"
                st.markdown(f"""
                <div style="background:#e0e0e0;border-radius:99px;
                            height:10px;margin:8px 0 16px;">
                  <div style="background:{bar_c};width:{overall_pct}%;
                              height:10px;border-radius:99px;"></div>
                </div>
                <div style="font-size:12px;color:#666;margin-bottom:16px;">
                  Ukupna kompletnost: {overall_pct}%
                </div>
                """, unsafe_allow_html=True)

            st.divider()

            # Bulk akcija za sve managere
            col_all, col_info2 = st.columns([2, 3])
            managers = employees[employees["is_manager"] == 1]

            with col_all:
                if st.button("🤖 Generiraj za SVE timove",
                             use_container_width=True,
                             help="Pokreće bulk_generate za svakog managera"):
                    total_created = 0
                    total_skipped = 0
                    for _, mgr in managers.iterrows():
                        mgr_team = employees[
                            employees["manager_id"] == mgr["kadrovski_broj"]
                        ]["kadrovski_broj"].tolist()
                        if mgr_team:
                            res = bulk_generate_team_nominations(
                                company_id, current_period,
                                mgr_team, mgr["kadrovski_broj"]
                            )
                            total_created += res["created"]
                            total_skipped += res["skipped"]
                    st.toast(
                        f"✅ {total_created} kreirano, {total_skipped} preskočeno.",
                        icon="🤖"
                    )
                    st.rerun()

            with col_info2:
                st.caption(
                    f"Ukupno aktivnih managera: **{len(managers)}** · "
                    f"Ukupno zaposlenika: **{len(employees)}**"
                )

            st.divider()

            # Pregled po odjelima — compact
            st.markdown("#### Status po odjelima")
            depts = employees["department"].dropna().unique()

            for dept in sorted(depts):
                dept_emps = employees[employees["department"] == dept]
                dept_ids  = dept_emps["kadrovski_broj"].tolist()
                if not dept_ids:
                    continue
                ph2 = ",".join("?" * len(dept_ids))
                dept_stats = conn.execute(
                    f"""SELECT COUNT(*) as total,
                               SUM(CASE WHEN status='Submitted' THEN 1 ELSE 0 END) as done
                        FROM peer_nominations
                        WHERE target_id IN ({ph2}) AND period=? AND company_id=?""",
                    dept_ids + [current_period, company_id]
                ).fetchone()

                d_total = dept_stats[0] or 0
                d_done  = dept_stats[1] or 0
                d_pct   = int(d_done / d_total * 100) if d_total > 0 else 0
                d_color = "#27ae60" if d_pct == 100 else (
                    "#2196f3" if d_pct > 0 else "#9e9e9e"
                )
                st.markdown(f"""
                <div style="display:flex;align-items:center;gap:12px;
                            margin-bottom:8px;">
                  <div style="width:140px;font-size:13px;
                              font-weight:600;color:#333;">{dept}</div>
                  <div style="flex:1;background:#e0e0e0;border-radius:99px;height:8px;">
                    <div style="background:{d_color};width:{d_pct}%;
                                height:8px;border-radius:99px;"></div>
                  </div>
                  <div style="width:60px;text-align:right;font-size:12px;
                              color:{d_color};font-weight:700;">{d_pct}%</div>
                </div>
                """, unsafe_allow_html=True)

            # Brisanje (opasna zona)
            st.divider()
            with st.expander("🗑️ Brisanje nominacija (opasna zona)"):
                emp_map = dict(zip(employees["ime_prezime"],
                                   employees["kadrovski_broj"]))
                del_target = st.selectbox(
                    "Obriši sve nominacije za zaposlenika:",
                    ["-- Odaberi --"] + employees["ime_prezime"].tolist(),
                    key="hr_del_target"
                )
                if del_target != "-- Odaberi --":
                    t_del = emp_map.get(del_target)
                    st.warning(
                        f"Brisanjem se trajno brišu sve nominacije i odgovori "
                        f"za **{del_target}** u periodu {current_period}."
                    )
                    if st.button(f"🗑️ Potvrdi brisanje za {del_target}",
                                 key="hr_confirm_del"):
                        conn.execute(
                            """DELETE FROM peer_feedback_results
                               WHERE nomination_id IN (
                                 SELECT id FROM peer_nominations
                                 WHERE target_id=? AND period=? AND company_id=?
                               )""",
                            (t_del, current_period, company_id)
                        )
                        conn.execute(
                            """DELETE FROM peer_nominations
                               WHERE target_id=? AND period=? AND company_id=?""",
                            (t_del, current_period, company_id)
                        )
                        conn.commit()
                        st.toast(f"✅ Nominacije za {del_target} obrisane.",
                                 icon="🗑️")
                        st.rerun()

        # ════════════════════════════════════════════════
        # TAB 2: IZVJEŠTAJI
        # ════════════════════════════════════════════════
        with tab_report:
            sel_dept = st.selectbox(
                "Odjel:",
                ["Svi"] + sorted(employees["department"].dropna().unique().tolist()),
                key="hr_360_dept"
            )
            filtered = (employees if sel_dept == "Svi"
                        else employees[employees["department"] == sel_dept])

            sel_name = st.selectbox(
                "Zaposlenik:",
                ["-- Odaberi --"] + filtered["ime_prezime"].tolist(),
                key="hr_360_emp"
            )
            if sel_name != "-- Odaberi --":
                sel_id = filtered[
                    filtered["ime_prezime"] == sel_name
                ]["kadrovski_broj"].values[0]
                r_t1, r_t2 = st.tabs(["📊 Ovaj period", "📈 Trend"])
                with r_t1:
                    render_360_report(conn, sel_id, sel_name,
                                      current_period, company_id)
                with r_t2:
                    render_historical_trend(conn, sel_id, company_id)

    finally:
        conn.close()
