# =============================================================
# dashboard/app.py
# Dashboard temps réel AML/CFT — FinTrack
# Structure originale + couleurs Stitch design
# =============================================================

import os
import sys
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# =============================================================
# CONFIGURATION PAGE
# =============================================================

st.set_page_config(
    page_title="FinTrack — AML Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Couleurs Stitch :
# Fond principal   : #0a0f1e
# Card/sidebar     : #161b2c
# Border           : #2d3748
# Vert brand       : #3cf91a
# Texte secondaire : #94a3b8

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body,
[data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"],
[data-testid="stApp"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
.main, .stApp, .appview-container {
    background-color: #0a0f1e !important;
    font-family: 'Inter', sans-serif;
    color: #e2e8f0;
}
.block-container,
[data-testid="stVerticalBlock"] {
    background-color: #0a0f1e !important;
}

#MainMenu, footer { visibility: hidden; }
/* Ne PAS cacher header — il contient le bouton sidebar */

/* Sidebar — Streamlit 1.54.0 */
[data-testid="stSidebar"],
[data-testid="stSidebar"] > div,
[data-testid="stSidebar"] > div > div,
[data-testid="stSidebar"] > div > div > div,
section[data-testid="stSidebar"],
section[data-testid="stSidebar"] > div {
    background-color: #0a0f1e !important;
    border-right: 1px solid #2d3748 !important;
}

/* st.metric cards */
[data-testid="stMetric"] {
    background: #161b2c !important;
    border: 1px solid #2d3748 !important;
    border-radius: 8px !important;
    padding: 16px 20px !important;
}
[data-testid="stMetricLabel"] p {
    color: #94a3b8 !important;
    font-size: 0.75rem !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
[data-testid="stMetricValue"] {
    color: #e2e8f0 !important;
    font-size: 1.8rem !important;
    font-weight: 700 !important;
}
[data-testid="stMetricDelta"] {
    font-size: 0.72rem !important;
}

/* Divider */
hr { border-color: #2d3748 !important; }

/* Buttons */
.stButton > button {
    background: #161b2c !important;
    border: 1px solid #2d3748 !important;
    color: #e2e8f0 !important;
    border-radius: 8px !important;
    font-size: 0.8rem !important;
}
.stButton > button:hover {
    background: #1e293b !important;
    border-color: #3cf91a !important;
    color: #3cf91a !important;
}

/* Sidebar title/text */
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #e2e8f0 !important;
}
[data-testid="stSidebar"] p {
    color: #94a3b8 !important;
}

/* Expanders */
[data-testid="stExpander"] {
    background: #161b2c !important;
    border: 1px solid #2d3748 !important;
    border-radius: 8px !important;
}
details summary { color: #e2e8f0 !important; }

/* Dataframe */
[data-testid="stDataFrame"] {
    background: #161b2c !important;
    border: 1px solid #2d3748 !important;
    border-radius: 8px !important;
}

/* Multiselect / select */
[data-testid="stMultiSelect"] > div,
[data-testid="stSelectbox"] > div {
    background: #161b2c !important;
    border-color: #2d3748 !important;
    color: #e2e8f0 !important;
}

/* Page title */
h1, h2, h3 { color: #e2e8f0 !important; }
</style>
""", unsafe_allow_html=True)

# Couleurs Plotly communes
PLOT_BG   = "#161b2c"
PAPER_BG  = "#0a0f1e"
FONT_COL  = "#94a3b8"
GRID_COL  = "#2d3748"

# Palette risque Stitch
RISK_COLORS = {
    "LOW":      "#3cf91a",
    "MEDIUM":   "#eab308",
    "HIGH":     "#f97316",
    "CRITICAL": "#ef4444",
}


# =============================================================
# CONNEXION BASE
# =============================================================

@st.cache_resource
def get_engine():
    db_url = (
        f"postgresql://"
        f"{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@"
        f"{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/"
        f"{os.getenv('DB_NAME')}"
    )
    return create_engine(db_url)


# =============================================================
# CHARGEMENT DES DONNÉES
# =============================================================

@st.cache_data(ttl=30)
def charger_kpis(_engine) -> dict:
    with _engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar()
        alertes_open = conn.execute(text("SELECT COUNT(*) FROM alerts WHERE statut = 'OPEN'")).scalar()
        bloquees = conn.execute(text("SELECT COUNT(*) FROM transactions WHERE statut = 'BLOCKED'")).scalar()
        montant_total = conn.execute(text("SELECT SUM(montant) FROM transactions")).scalar() or 0
        taux_fraude = conn.execute(text("""
            SELECT ROUND(COUNT(*) FILTER (WHERE is_suspect) * 100.0 / COUNT(*), 2)
            FROM transactions
        """)).scalar() or 0
        score_moyen = conn.execute(text("SELECT ROUND(AVG(score_aml), 1) FROM transactions")).scalar() or 0
    return {
        "total":         int(total),
        "alertes_open":  int(alertes_open),
        "bloquees":      int(bloquees),
        "montant_total": float(montant_total),
        "taux_fraude":   float(taux_fraude),
        "score_moyen":   float(score_moyen),
    }


@st.cache_data(ttl=30)
def charger_distribution_scores(_engine) -> pd.DataFrame:
    return pd.read_sql("""
        SELECT score_aml, risk_level, is_suspect, COUNT(*) as nb
        FROM transactions WHERE score_aml IS NOT NULL
        GROUP BY score_aml, risk_level, is_suspect ORDER BY score_aml
    """, _engine)


@st.cache_data(ttl=30)
def charger_alertes(_engine) -> pd.DataFrame:
    return pd.read_sql("""
        SELECT a.alert_id, a.transaction_id, a.score, a.risk_level,
               a.flags, a.statut, a.created_at,
               t.montant, t.type_transaction, t.pays_emetteur, t.pays_recepteur, t.operateur
        FROM alerts a JOIN transactions t ON a.transaction_id = t.transaction_id
        ORDER BY a.score DESC, a.created_at DESC LIMIT 50
    """, _engine)


@st.cache_data(ttl=30)
def charger_transactions_recentes(_engine) -> pd.DataFrame:
    return pd.read_sql("""
        SELECT transaction_id, type_transaction, montant, pays_emetteur, pays_recepteur,
               operateur, score_aml, risk_level, statut, is_suspect, created_at
        FROM transactions ORDER BY created_at DESC LIMIT 100
    """, _engine)


@st.cache_data(ttl=30)
def charger_volume_temporel(_engine) -> pd.DataFrame:
    return pd.read_sql("""
        SELECT DATE(created_at) AS jour, COUNT(*) AS nb_transactions,
               SUM(montant) AS volume_fcfa,
               COUNT(*) FILTER (WHERE risk_level IN ('HIGH','CRITICAL')) AS nb_alertes,
               COUNT(*) FILTER (WHERE is_suspect) AS nb_suspects
        FROM transactions GROUP BY DATE(created_at) ORDER BY jour
    """, _engine)


@st.cache_data(ttl=30)
def charger_stats_pays(_engine) -> pd.DataFrame:
    return pd.read_sql("""
        SELECT pays_emetteur, COUNT(*) AS nb_transactions, SUM(montant) AS volume,
               COUNT(*) FILTER (WHERE risk_level IN ('HIGH','CRITICAL')) AS nb_alertes,
               ROUND(AVG(score_aml), 1) AS score_moyen
        FROM transactions GROUP BY pays_emetteur ORDER BY nb_alertes DESC
    """, _engine)


@st.cache_data(ttl=30)
def charger_audit_recents(_engine) -> pd.DataFrame:
    return pd.read_sql("""
        SELECT event_type, level, created_at, details
        FROM audit_logs ORDER BY created_at DESC LIMIT 20
    """, _engine)


# =============================================================
# SIDEBAR
# =============================================================

def render_sidebar():
    st.sidebar.title("🛡️ FinTrack AML")
    st.sidebar.markdown("**Moteur AML/CFT — Afrique de l'Ouest**")
    st.sidebar.divider()

    st.sidebar.markdown("### Navigation")
    page = st.sidebar.radio(
        "",
        ["📊 Vue d'ensemble",
         "🚨 Alertes",
         "📈 Analyse temporelle",
         "🌍 Analyse géographique",
         "📋 Transactions récentes",
         "🔍 Audit Logs"],
        label_visibility="collapsed"
    )

    st.sidebar.divider()
    st.sidebar.markdown("### Paramètres moteur")
    st.sidebar.metric("Seuil alerte", "31")
    st.sidebar.metric("Seuil blocage", "75")
    st.sidebar.metric("AUC moteur", "0.973")

    st.sidebar.divider()
    st.sidebar.markdown("🔄 Données rafraîchies toutes les 30s")
    if st.sidebar.button("🔄 Rafraîchir maintenant"):
        st.cache_data.clear()
        st.rerun()

    return page


# =============================================================
# PAGE — VUE D'ENSEMBLE
# =============================================================

def page_vue_ensemble(kpis: dict, engine):
    st.title("📊 Vue d'ensemble — FinTrack AML/CFT")
    st.markdown(f"*Dernière mise à jour : {datetime.now().strftime('%H:%M:%S')}*")
    st.divider()

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Transactions",     f"{kpis['total']:,}")
    col2.metric("Alertes ouvertes", kpis['alertes_open'])
    col3.metric("Bloquées",         kpis['bloquees'])
    col4.metric("Volume total",     f"{kpis['montant_total']/1_000_000:.1f}M FCFA")
    col5.metric("Taux fraude",      f"{kpis['taux_fraude']}%")
    col6.metric("Score moyen",      kpis['score_moyen'])

    st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Distribution des scores AML")
        df_scores = charger_distribution_scores(engine)
        fig = px.histogram(
            df_scores, x="score_aml", color="is_suspect", nbins=20,
            color_discrete_map={True: "#ef4444", False: "#3b82f6"},
            category_orders={"is_suspect": [False, True]},
            labels={"score_aml": "Score AML", "is_suspect": ""},
        )
        fig.for_each_trace(lambda t: t.update(
            name="Suspectes" if t.name == "True" else "Normales"
        ))
        fig.add_vline(x=31, line_dash="dot", line_color="#f97316", line_width=1.5,
                      annotation_text="Seuil alerte (31)",
                      annotation_font_color="#f97316", annotation_font_size=11)
        fig.add_vline(x=75, line_dash="dot", line_color="#ef4444", line_width=1.5,
                      annotation_text="Seuil blocage (75)",
                      annotation_font_color="#ef4444", annotation_font_size=11)
        fig.update_layout(
            plot_bgcolor=PLOT_BG, paper_bgcolor=PAPER_BG,
            font=dict(color=FONT_COL, family="Inter"),
            xaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
            yaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
            legend=dict(orientation="h", y=-0.2, bgcolor="rgba(0,0,0,0)", borderwidth=0,
                        font=dict(color="#e2e8f0")),
            margin=dict(t=30, b=10, l=5, r=5),
            bargap=0.1,
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        st.subheader("Répartition par niveau de risque")
        with engine.connect() as conn:
            df_risk = pd.read_sql("""
                SELECT risk_level, COUNT(*) as nb FROM transactions
                WHERE risk_level IS NOT NULL GROUP BY risk_level ORDER BY nb DESC
            """, conn)
        total_tx = df_risk["nb"].sum()
        df_risk["pct"] = (df_risk["nb"] / total_tx * 100).round(1)
        df_risk["label"] = df_risk.apply(lambda r: f"{r['risk_level']} ({r['pct']}%)", axis=1)

        fig2 = go.Figure(go.Pie(
            labels=df_risk["label"],
            values=df_risk["nb"],
            hole=0.58,
            marker_colors=[RISK_COLORS.get(r, "#888") for r in df_risk["risk_level"]],
            textinfo="none",
            hovertemplate="%{label}<extra></extra>",
        ))
        fig2.update_layout(
            plot_bgcolor=PLOT_BG, paper_bgcolor=PAPER_BG,
            font=dict(color=FONT_COL, family="Inter"),
            legend=dict(orientation="v", x=0.65, y=0.5,
                        bgcolor="rgba(0,0,0,0)", borderwidth=0,
                        font=dict(color="#e2e8f0", size=12)),
            annotations=[dict(text="<b>100%</b>", x=0.32, y=0.5,
                              font=dict(size=16, color="#e2e8f0"), showarrow=False)],
            margin=dict(t=30, b=10, l=5, r=5),
        )
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Performance du moteur AML")
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("AUC",       "0.973", "Excellent ✓")
    col_b.metric("Recall",    "100%",  "Seuil 31 ✓")
    col_c.metric("Precision", "42.7%", " ")
    col_d.metric("F1 Score",  "59.9%", " ")


# =============================================================
# PAGE — ALERTES
# =============================================================

def page_alertes(engine):
    st.title("🚨 Alertes AML — Transactions à risque")
    st.divider()

    df_alertes = charger_alertes(engine)
    if df_alertes.empty:
        st.info("Aucune alerte ouverte.")
        return

    col1, col2 = st.columns(2)
    with col1:
        niveau_filtre = st.multiselect("Filtrer par niveau",
            ["CRITICAL", "HIGH"], default=["CRITICAL", "HIGH"])
    with col2:
        statut_filtre = st.multiselect("Filtrer par statut",
            ["OPEN", "REVIEWED", "CLOSED", "REPORTED"], default=["OPEN"])

    df_filtered = df_alertes[
        df_alertes["risk_level"].isin(niveau_filtre) &
        df_alertes["statut"].isin(statut_filtre)
    ]
    st.markdown(f"**{len(df_filtered)} alertes affichées**")

    for _, row in df_filtered.iterrows():
        couleur = "🔴" if row["risk_level"] == "CRITICAL" else "🟠"
        with st.expander(
            f"{couleur} [{row['risk_level']}] Score {row['score']} — "
            f"{row['montant']:,.0f} FCFA — {row['type_transaction']} — "
            f"{row['pays_emetteur']} → {row['pays_recepteur']}"
        ):
            col1, col2, col3 = st.columns(3)
            col1.markdown(f"**Transaction ID**\n`{row['transaction_id']}`")
            col2.markdown(f"**Opérateur**\n{row['operateur']}")
            col3.markdown(f"**Statut**\n{row['statut']}")
            st.markdown(f"**Flags déclenchés :** `{row['flags']}`")
            st.markdown(f"**Créée le :** {pd.to_datetime(row['created_at']).strftime('%d/%m/%Y %H:%M')}")


# =============================================================
# PAGE — ANALYSE TEMPORELLE
# =============================================================

def page_temporelle(engine):
    st.title("📈 Analyse temporelle des transactions")
    st.divider()

    df = charger_volume_temporel(engine)
    df["jour"] = pd.to_datetime(df["jour"])

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["jour"], y=df["nb_transactions"],
        name="Transactions", marker_color="#3b82f6", marker_line_width=0
    ))
    fig.add_trace(go.Scatter(
        x=df["jour"], y=df["nb_alertes"],
        name="Alertes H/C", line=dict(color="#ef4444", width=2),
        yaxis="y2"
    ))
    fig.update_layout(
        title=dict(text="Volume de transactions et alertes par jour",
                   font=dict(color="#e2e8f0")),
        plot_bgcolor=PLOT_BG, paper_bgcolor=PAPER_BG,
        font=dict(color=FONT_COL, family="Inter"),
        xaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
        yaxis=dict(title="Transactions", gridcolor=GRID_COL, color=FONT_COL),
        yaxis2=dict(title="Alertes H/C", overlaying="y", side="right",
                    color=FONT_COL, gridcolor=GRID_COL),
        legend=dict(orientation="h", bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#e2e8f0")),
        bargap=0.3,
    )
    st.plotly_chart(fig, use_container_width=True)

    fig2 = px.area(df, x="jour", y="volume_fcfa",
                   title="Volume financier quotidien (FCFA)",
                   color_discrete_sequence=["#3cf91a"])
    fig2.update_traces(fillcolor="rgba(60,249,26,0.1)")
    fig2.update_layout(
        plot_bgcolor=PLOT_BG, paper_bgcolor=PAPER_BG,
        font=dict(color=FONT_COL, family="Inter"),
        title=dict(font=dict(color="#e2e8f0")),
        xaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
        yaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
    )
    st.plotly_chart(fig2, use_container_width=True)


# =============================================================
# PAGE — ANALYSE GÉOGRAPHIQUE
# =============================================================

def page_geographique(engine):
    st.title("🌍 Analyse géographique")
    st.divider()

    df = charger_stats_pays(engine)
    col1, col2 = st.columns(2)

    with col1:
        fig = px.bar(df, x="pays_emetteur", y="nb_alertes", color="score_moyen",
            color_continuous_scale="Reds",
            title="Alertes HIGH/CRITICAL par pays émetteur",
            labels={"pays_emetteur": "Pays", "nb_alertes": "Alertes", "score_moyen": "Score moyen"})
        fig.update_layout(
            plot_bgcolor=PLOT_BG, paper_bgcolor=PAPER_BG,
            font=dict(color=FONT_COL, family="Inter"),
            title=dict(font=dict(color="#e2e8f0")),
            xaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
            yaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
            bargap=0.3,
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        fig2 = px.bar(df, x="pays_emetteur", y="volume",
            title="Volume financier par pays (FCFA)",
            color_discrete_sequence=["#3b82f6"],
            labels={"pays_emetteur": "Pays", "volume": "Volume FCFA"})
        fig2.update_layout(
            plot_bgcolor=PLOT_BG, paper_bgcolor=PAPER_BG,
            font=dict(color=FONT_COL, family="Inter"),
            title=dict(font=dict(color="#e2e8f0")),
            xaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
            yaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
            bargap=0.3,
        )
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Tableau détaillé par pays")
    df_display = df.copy()
    df_display["volume"] = df_display["volume"].apply(lambda x: f"{x/1_000_000:.1f}M FCFA")
    st.dataframe(df_display, use_container_width=True)


# =============================================================
# PAGE — TRANSACTIONS RÉCENTES
# =============================================================

def page_transactions(engine):
    st.title("📋 Transactions récentes")
    st.divider()

    df = charger_transactions_recentes(engine)

    col1, col2, col3 = st.columns(3)
    with col1:
        risk_filter = st.multiselect("Niveau de risque",
            ["LOW", "MEDIUM", "HIGH", "CRITICAL"], default=["HIGH", "CRITICAL"])
    with col2:
        type_filter = st.multiselect("Type",
            df["type_transaction"].unique().tolist(), default=[])
    with col3:
        suspect_only = st.checkbox("Suspectes seulement", False)

    df_f = df.copy()
    if risk_filter:   df_f = df_f[df_f["risk_level"].isin(risk_filter)]
    if type_filter:   df_f = df_f[df_f["type_transaction"].isin(type_filter)]
    if suspect_only:  df_f = df_f[df_f["is_suspect"] == True]

    def colorier_niveau(val):
        return {
            "CRITICAL": "background-color: rgba(239,68,68,0.15); color: #ef4444",
            "HIGH":     "background-color: rgba(249,115,22,0.15); color: #f97316",
            "MEDIUM":   "background-color: rgba(234,179,8,0.15);  color: #eab308",
            "LOW":      ""
        }.get(val, "")

    cols_affichage = [
        "created_at", "type_transaction", "montant", "pays_emetteur",
        "pays_recepteur", "operateur", "score_aml", "risk_level", "statut", "is_suspect"
    ]
    st.markdown(f"**{len(df_f)} transactions affichées**")
    st.dataframe(
        df_f[cols_affichage].style.map(colorier_niveau, subset=["risk_level"]),
        use_container_width=True
    )


# =============================================================
# PAGE — AUDIT LOGS
# =============================================================

def page_audit(engine):
    st.title("🔍 Audit Logs — Traçabilité CENTIF")
    st.divider()

    with engine.connect() as conn:
        stats = pd.read_sql("""
            SELECT event_type, level, COUNT(*) as nb
            FROM audit_logs GROUP BY event_type, level ORDER BY nb DESC
        """, conn)

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Distribution des événements")
        fig = px.bar(stats, x="event_type", y="nb", color="level",
            color_discrete_map={
                "INFO":     "#3b82f6",
                "WARNING":  "#eab308",
                "ERROR":    "#f97316",
                "CRITICAL": "#ef4444",
            },
            title="Logs par type d'événement")
        fig.update_layout(
            xaxis_tickangle=-45,
            plot_bgcolor=PLOT_BG, paper_bgcolor=PAPER_BG,
            font=dict(color=FONT_COL, family="Inter"),
            title=dict(font=dict(color="#e2e8f0")),
            xaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
            yaxis=dict(gridcolor=GRID_COL, color=FONT_COL),
            legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color="#e2e8f0")),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Statistiques")
        total_logs = stats["nb"].sum()
        st.metric("Total logs", f"{total_logs:,}")

        centif = stats[stats["event_type"] == "CENTIF_DECLARATION"]["nb"]
        st.metric("Déclarations CENTIF",
                  int(centif.sum()) if not centif.empty else 0)

        bloquees = stats[stats["event_type"] == "TRANSACTION_BLOCKED"]["nb"]
        st.metric("Transactions bloquées loggées",
                  int(bloquees.sum()) if not bloquees.empty else 0)

    st.subheader("Logs récents")
    df_audit = charger_audit_recents(engine)
    st.dataframe(df_audit, use_container_width=True)


# =============================================================
# POINT D'ENTRÉE
# =============================================================

def main():
    engine = get_engine()
    kpis   = charger_kpis(engine)
    page   = render_sidebar()

    if page == "📊 Vue d'ensemble":
        page_vue_ensemble(kpis, engine)
    elif page == "🚨 Alertes":
        page_alertes(engine)
    elif page == "📈 Analyse temporelle":
        page_temporelle(engine)
    elif page == "🌍 Analyse géographique":
        page_geographique(engine)
    elif page == "📋 Transactions récentes":
        page_transactions(engine)
    elif page == "🔍 Audit Logs":
        page_audit(engine)


if __name__ == "__main__":
    main()
