# CLAUDE.md - Talent App Project Instructions

## 1. Project Vision & Context
**Talent App** is an Enterprise-grade Talent Management, OKR, and Performance Review application. It is designed to compete with industry standards like Lattice, 15Five, and Workday, but optimized for simplicity and speed.
- **Core Philosophy:** Keep the UI clean and intuitive. Complex HR processes (9-Box, OKRs, IDPs) should feel lightweight to the end-user.
- **Role-Based Architecture:** The app serves 4 distinct roles, and every feature must respect data privacy between them:
  1. `Employee` (Self-evaluations, goals, feedback).
  2. `Manager` (Team evaluations, cascading goals, IDP creation).
  3. `HR` (Global dashboard, calibration, template building, GDPR tools).
  4. `SuperAdmin` (Panic buttons, account recovery).

## 2. Tech Stack & Architecture
- **Frontend/Backend:** Pure `Streamlit` (Python 3.x).
- **Data Manipulation:** `pandas` for dataframes, `plotly` for interactive charts.
- **Database:** Raw `sqlite3`. 
- **STRICT RULE:** Do NOT introduce ORMs (like SQLAlchemy or SQLModel). We intentionally use raw SQL queries for ultimate control and simplicity.

## 3. Ironclad Technical Rules (Do NOT break these)
- **Database Connections:** We use WAL mode. You MUST wrap all database operations in `try...finally: conn.close()` blocks to prevent "Database is locked" errors.
- **SQL Injection Prevention:** ALWAYS use parameterized queries (e.g., `execute("... WHERE id=?", (id,))`). NEVER use f-strings for SQL variables.
- **JSON Handling:** Always use the custom `safe_load_json` function from `modules.utils` when reading JSON from the DB to prevent app crashes on malformed data.
- **Progress Bars:** Always pass values through `normalize_progress` from `modules.utils` before rendering `st.progress()`.
- **Security:** Passwords must always be hashed using `make_hashes` (which includes the `SECRET_SALT` from `constants.py`).

## 4. Current State & Future Roadmap
When suggesting solutions, keep our future roadmap in mind so the architecture supports it:
- **Current State (V1.1):** MVP is stable. Features include cascading OKRs, 9-Box matrix, 70-20-10 IDPs, Excel/PDF exports, and GDPR anonymization.
- **Next Up (V1.2 - Feedback & Culture):** Implementing 360° Peer Feedback and Continuous Feedback (Kudos Wall) to increase daily app engagement.
- **Future (V1.3 & V1.4):** Modular performance modes (turning off 9-box for certain clients), HR Calibration drag-and-drop sessions, and Advanced Analytics (Attrition Risk).

## 5. Workflow Expectations
- Act as a Senior Tech Lead. When asked for a feature, think about edge cases and Streamlit's specific limitations (e.g., page reruns).
- Provide surgical code edits rather than rewriting entire files. 
- If a user request contradicts the "Ironclad Technical Rules", politely refuse and explain why.