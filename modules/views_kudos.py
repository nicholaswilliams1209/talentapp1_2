# modules/views_kudos.py
# Kudos Wall — v1.3 Kulturološki sloj
# Javne pohvale vidljive svim zaposlenicima + prisjećanje za managera u procjeni

import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from modules.database import get_connection, log_action

# ─────────────────────────────────────────────
# KONSTANTE
# ─────────────────────────────────────────────

KUDOS_CATEGORIES = [
    ("💡", "Inovacija",       "#7B1FA2", "#F3E5F5"),
    ("🤝", "Timski rad",      "#1565C0", "#E3F2FD"),
    ("🚀", "Inicijativa",     "#E65100", "#FFF3E0"),
    ("🎯", "Fokus na cilj",   "#2E7D32", "#E8F5E9"),
    ("🌟", "Kvaliteta",       "#F57F17", "#FFFDE7"),
    ("💪", "Ustrajnost",      "#C62828", "#FFEBEE"),
    ("🧠", "Stručnost",       "#00695C", "#E0F2F1"),
    ("🙌", "Podrška timu",    "#37474F", "#ECEFF1"),
]

CAT_MAP = {cat: (icon, bg, border) for icon, cat, border, bg in KUDOS_CATEGORIES}

def _category_pill(category: str) -> str:
    icon, bg, border = CAT_MAP.get(category, ("👏", "#F5F5F5", "#9E9E9E"))
    return (
        f'<span style="background:{bg};color:{border};padding:3px 10px;'
        f'border-radius:99px;font-size:12px;font-weight:600;">'
        f'{icon} {category}</span>'
    )

def _time_ago(ts_str: str) -> str:
    try:
        ts = datetime.strptime(ts_str[:10], "%Y-%m-%d").date()
        delta = date.today() - ts
        if delta.days == 0:   return "Danas"
        if delta.days == 1:   return "Jučer"
        if delta.days < 7:    return f"Prije {delta.days} dana"
        if delta.days < 30:   return f"Prije {delta.days // 7} tjedna"
        return ts_str[:10]
    except Exception:
        return ts_str[:10] if ts_str else ""

def _kudos_card(sender: str, receiver: str, message: str,
                category: str, timestamp: str, is_mine: bool = False) -> str:
    icon, bg, border = CAT_MAP.get(category, ("👏", "#F5F5F5", "#9E9E9E"))
    highlight = f"border-left:4px solid {border};" if is_mine else f"border-left:3px solid #E0E0E0;"
    return f"""
    <div style="background:#fff;{highlight}border-radius:8px;
                padding:14px 18px;margin-bottom:10px;
                box-shadow:0 1px 3px rgba(0,0,0,.06);">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div>
          <span style="font-weight:700;font-size:14px;color:#1a1a2e;">{receiver}</span>
          <span style="color:#888;font-size:13px;margin-left:6px;">od {sender}</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
          {_category_pill(category)}
          <span style="font-size:11px;color:#aaa;">{_time_ago(timestamp)}</span>
        </div>
      </div>
      <div style="font-size:14px;color:#444;margin-top:8px;line-height:1.5;">
        "{message}"
      </div>
    </div>
    """


# ─────────────────────────────────────────────
# JAVNI FEED (za sve zaposlenike)
# ─────────────────────────────────────────────

def render_kudos_feed(username: str, company_id: int, limit: int = 30):
    """
    Javni zid pohvala — svi vide sve (unutar iste tvrtke).
    Komponenta za ugradnju u views_emp.py tab.
    """
    conn = get_connection()
    try:
        feed = pd.read_sql_query("""
            SELECT r.id, r.sender_id, r.receiver_id, r.message,
                   r.category, r.timestamp,
                   s.ime_prezime AS sender_name,
                   rv.ime_prezime AS receiver_name
            FROM recognitions r
            LEFT JOIN employees_master s  ON r.sender_id   = s.kadrovski_broj
            LEFT JOIN employees_master rv ON r.receiver_id = rv.kadrovski_broj
            WHERE r.company_id = ?
            ORDER BY r.timestamp DESC
            LIMIT ?
        """, conn, params=(company_id, limit))
    finally:
        conn.close()

    # ── HEADER STATISTIKE ──────────────────────────────────────
    my_received  = feed[feed["receiver_id"] == username]
    my_given     = feed[feed["sender_id"]   == username]

    c1, c2, c3 = st.columns(3)
    c1.metric("Pohvale u firmi", len(feed))
    c2.metric("Dobio/la sam",    len(my_received))
    c3.metric("Dao/la sam",      len(my_given))

    if feed.empty:
        st.info("🌱 Nema još pohvala. Budi prvi/a koji će pohvaliti kolegu!")
        return

    # ── FILTERI ────────────────────────────────────────────────
    col_f1, col_f2 = st.columns([2, 2])
    cat_opts = ["Sve kategorije"] + [cat for _, cat, _, _ in KUDOS_CATEGORIES]
    sel_cat = col_f1.selectbox("Kategorija:", cat_opts, key="kudos_feed_cat")

    view_opts = ["Svi", "Moje pohvale", "Koje sam dao/la"]
    sel_view  = col_f2.selectbox("Prikaz:", view_opts, key="kudos_feed_view")

    filtered = feed.copy()
    if sel_cat != "Sve kategorije":
        filtered = filtered[filtered["category"] == sel_cat]
    if sel_view == "Moje pohvale":
        filtered = filtered[filtered["receiver_id"] == username]
    elif sel_view == "Koje sam dao/la":
        filtered = filtered[filtered["sender_id"] == username]

    st.markdown(f"**{len(filtered)} pohvala**")
    st.markdown("---")

    for _, row in filtered.iterrows():
        is_mine = row["receiver_id"] == username
        st.markdown(
            _kudos_card(
                sender   = row.get("sender_name")   or row["sender_id"],
                receiver = row.get("receiver_name") or row["receiver_id"],
                message  = row["message"],
                category = row.get("category") or "Općenito",
                timestamp= row["timestamp"],
                is_mine  = is_mine,
            ),
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────
# FORMA ZA SLANJE (za zaposlenika)
# ─────────────────────────────────────────────

def render_send_kudos_form(username: str, sender_name: str, company_id: int):
    """
    Forma za slanje pohvale kolegi.
    """
    conn = get_connection()
    try:
        colleagues = pd.read_sql_query("""
            SELECT kadrovski_broj, ime_prezime FROM employees_master
            WHERE company_id=? AND active=1 AND kadrovski_broj != ?
            ORDER BY ime_prezime
        """, conn, params=(company_id, username))
    finally:
        conn.close()

    if colleagues.empty:
        st.info("Nema kolega za pohvaljivanje.")
        return

    with st.form("send_kudos_form", clear_on_submit=True):
        st.markdown("#### 👏 Pošalji pohvalu kolegi")

        rec_name = st.selectbox(
            "Pohvali:",
            colleagues["ime_prezime"].tolist(),
            key="kudos_receiver_sel"
        )
        cat_labels = [f"{icon} {cat}" for icon, cat, _, _ in KUDOS_CATEGORIES]
        sel_cat_label = st.selectbox("Kategorija:", cat_labels, key="kudos_cat_sel")
        category = sel_cat_label.split(" ", 1)[1]  # Makni emoji

        message = st.text_area(
            "Poruka (konkretna i iskrena):",
            placeholder='npr. "Odlično si prezentirao projekt klijentu, tim je bio ponosan!"',
            max_chars=300,
            key="kudos_message_input"
        )

        submitted = st.form_submit_button("🚀 Pošalji pohvalu", use_container_width=True)
        if submitted:
            if not message.strip():
                st.error("Poruka ne može biti prazna.")
            else:
                rec_id = colleagues.loc[
                    colleagues["ime_prezime"] == rec_name, "kadrovski_broj"
                ].values[0]
                _save_kudos(username, rec_id, message.strip(), category, company_id)
                log_action(username, "KUDOS_SENT", f"{username}→{rec_id} [{category}]", company_id)
                st.toast(f"✅ Pohvala poslana {rec_name}!", icon="🌟")
                st.rerun()


def _save_kudos(sender_id, receiver_id, message, category, company_id):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO recognitions (sender_id, receiver_id, message, category, timestamp, company_id)"
            " VALUES (?,?,?,?,?,?)",
            (sender_id, receiver_id, message, category,
             datetime.now().strftime("%Y-%m-%d"), company_id)
        )
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────
# MANAGER PANEL — Pohvale tima u procjeni
# ─────────────────────────────────────────────

def render_kudos_panel_for_manager(employee_id: str, employee_name: str,
                                   company_id: int, period: str):
    """
    Informativni panel za managera vidljiv unutar evaluacije zaposlenika.
    Prikazuje pohvale primljene u periodu procjene — smanjuje recency bias.
    """
    conn = get_connection()
    try:
        # Dohvati period start/end za filtriranje
        period_row = conn.execute(
            "SELECT start_date, deadline FROM periods WHERE period_name=? AND company_id=?",
            (period, company_id)
        ).fetchone()

        if period_row:
            p_start = period_row[0] or "2000-01-01"
            p_end   = period_row[1] or date.today().isoformat()
        else:
            p_start = "2000-01-01"
            p_end   = date.today().isoformat()

        kudos = pd.read_sql_query("""
            SELECT r.message, r.category, r.timestamp,
                   s.ime_prezime AS sender_name
            FROM recognitions r
            LEFT JOIN employees_master s ON r.sender_id = s.kadrovski_broj
            WHERE r.receiver_id = ?
              AND r.company_id  = ?
              AND r.timestamp  >= ?
              AND r.timestamp  <= ?
            ORDER BY r.timestamp DESC
        """, conn, params=(employee_id, company_id, p_start, p_end))
    finally:
        conn.close()

    if kudos.empty:
        st.markdown(
            '<div style="background:#F9F9F9;border-radius:8px;padding:12px 16px;'
            'font-size:13px;color:#888;">Nema zabilježenih pohvala u ovom periodu.</div>',
            unsafe_allow_html=True
        )
        return

    # Grupiraj po kategoriji
    by_cat = kudos.groupby("category").size().reset_index(name="n")

    # Summary pills
    pills = " ".join(
        _category_pill(f"{row['category']} ×{row['n']}")
        for _, row in by_cat.iterrows()
    )
    st.markdown(
        f'<div style="background:linear-gradient(135deg,#FFFDE7,#FFF9C4);'
        f'border-left:4px solid #F9A825;border-radius:8px;padding:14px 18px;margin-bottom:8px;">'
        f'<div style="font-size:13px;font-weight:700;color:#F57F17;margin-bottom:8px;">'
        f'🌟 Pohvale primljene u periodu {period} ({len(kudos)})</div>'
        f'<div style="margin-bottom:10px;">{pills}</div>',
        unsafe_allow_html=True
    )

    with st.expander("Prikaži sve pohvale"):
        for _, row in kudos.iterrows():
            icon, _, _ = CAT_MAP.get(row["category"], ("👏", "", ""))
            st.markdown(
                f'<div style="padding:8px 0;border-bottom:1px solid #F0F0F0;">'
                f'<span style="font-size:12px;font-weight:600;">'
                f'{icon} {row["category"]}</span>'
                f'<span style="font-size:11px;color:#aaa;margin-left:8px;">'
                f'od {row.get("sender_name","?")} · {_time_ago(row["timestamp"])}</span>'
                f'<div style="font-size:13px;color:#555;margin-top:4px;">"{row["message"]}"</div>'
                f'</div>',
                unsafe_allow_html=True
            )
    st.markdown('</div>', unsafe_allow_html=True)
