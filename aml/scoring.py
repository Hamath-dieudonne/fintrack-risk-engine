# =============================================================
# aml/scoring.py
# Moteur de scoring AML/CFT — FinTrack 
# =============================================================

import os
import sys
import json
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
pd.set_option('future.no_silent_downcasting', True)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_engine():
    db_url = (
        f"postgresql://"
        f"{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@"
        f"{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/"
        f"{os.getenv('DB_NAME')}"
    )
    return create_engine(db_url)


# =============================================================
# RÈGLES ET POIDS
# =============================================================

REGLES = {
    "SEUIL_REGLEMENTAIRE":     {"poids": 30},
    "SMURFING":                {"poids": 30},  # ← 40 → 30 (moins de bruit)
    "MONTANT_ZONE_STRUCTURING":{"poids": 35},  # ← 20 → 35 (meilleur signal)
    "VELOCITE_ANORMALE":       {"poids": 20},
    "SIM_SWAP":                {"poids": 10},  # ← 15 → 10 (signal faible)
    "ACTIVITE_NOCTURNE":       {"poids": 15},  # inchangé — zéro FP
    "PAYS_RISQUE":             {"poids":  0},  # ← désactivé
    "ANOMALIE_STATISTIQUE":    {"poids": 20},
}

SEUILS = {
    "APPROVED": 31,
    "PENDING":  60,
    "BLOCKED":  75,   # ← 60 → 75
}

# =============================================================
# MOTEUR DE SCORING
# =============================================================

def scorer_transaction(row: pd.Series) -> dict:
    score   = 0
    flags   = []
    details = {}

    # Règle 1 — Seuil réglementaire CENTIF
    if row["montant"] > 1_000_000:
        p = REGLES["SEUIL_REGLEMENTAIRE"]["poids"]
        score += p
        flags.append("SEUIL_REGLEMENTAIRE")
        details["SEUIL_REGLEMENTAIRE"] = p

    # Règle 2 — Montant zone structuring (900k-999k)
    # Signal spécifique : le fraudeur opère juste
    # sous le seuil pour éviter la détection automatique
    if row.get("montant_zone_structuring", False):
        p = REGLES["MONTANT_ZONE_STRUCTURING"]["poids"]
        score += p
        flags.append("MONTANT_ZONE_STRUCTURING")
        details["MONTANT_ZONE_STRUCTURING"] = p

    # Règle 3 — Smurfing (3 critères combinés)
    if row.get("smurfing_flag", False):
        p = REGLES["SMURFING"]["poids"]
        score += p
        flags.append("SMURFING")
        details["SMURFING"] = p
        # Détail du critère déclenché pour l'audit
        if row.get("smurfing_900k", False):
            details["SMURFING_TYPE"] = "900k"
        elif row.get("smurfing_total", False):
            details["SMURFING_TYPE"] = "total_cumule"
        elif row.get("smurfing_regularite", False):
            details["SMURFING_TYPE"] = "regularite"

    # Règle 4 — Vélocité anormale
    if row.get("tx_last_hour", 0) > 10:
        p = REGLES["VELOCITE_ANORMALE"]["poids"]
        score += p
        flags.append("VELOCITE_ANORMALE")
        details["VELOCITE_ANORMALE"] = p

    # Règle 5 — SIM Swap récent
    # Ne déclencher SIM Swap QUE si combiné avec pour diminuer le bruit:

    # Montant > 500k

    # OU anomalie statistique

    # OU activité nocturne
    if row.get("sim_swap_recent", False):
        if row["montant"] > 500_000 or row.get("anomalie_statistique", False):
            p = REGLES["SIM_SWAP"]["poids"]
            score += p
            flags.append("SIM_SWAP")
            details["SIM_SWAP"] = p


    # Règle 6 — Activité nocturne
    if row.get("activite_nocturne", False):
        p = REGLES["ACTIVITE_NOCTURNE"]["poids"]
        score += p
        flags.append("ACTIVITE_NOCTURNE")
        details["ACTIVITE_NOCTURNE"] = p

    # Règle 7 — Pays à risque
    if row.get("pays_risque", False):
        p = REGLES["PAYS_RISQUE"]["poids"]
        score += p
        flags.append("PAYS_RISQUE")
        details["PAYS_RISQUE"] = p

    # Règle 8 — Anomalie statistique (z-score)
    if row.get("anomalie_statistique", False):
        p = REGLES["ANOMALIE_STATISTIQUE"]["poids"]
        score += p
        flags.append("ANOMALIE_STATISTIQUE")
        details["ANOMALIE_STATISTIQUE"] = p

    # Bonus combinaison — plusieurs signaux simultanés
    # En AML, la combinaison est exponentiellement
    # plus suspecte que chaque signal isolé
    if len(flags) >= 3:
        score += 15
        flags.append("COMBINAISON_MULTIPLE")
        details["COMBINAISON_MULTIPLE"] = 15
    elif len(flags) == 2:
        score += 5
        flags.append("COMBINAISON_DOUBLE")
        details["COMBINAISON_DOUBLE"] = 5

        # --- bonus combinaison intelligente ---
    combinaisons_fortes = [
        {"MONTANT_ZONE_STRUCTURING", "SMURFING"},
        {"MONTANT_ZONE_STRUCTURING", "SIM_SWAP"},
    ]

    if any(set(combo).issubset(set(flags)) for combo in combinaisons_fortes):
        score += 20


    # Plafond à 100
    score = min(score, 100)

    # Classification
    if score >= 81:
        niveau = "CRITICAL"
    elif score >= 61:
        niveau = "HIGH"
    elif score >= 31:
        niveau = "MEDIUM"
    else:
        niveau = "LOW"

    # Décision
    if score >= SEUILS["BLOCKED"]:
        decision = "BLOCKED"
    elif score >= SEUILS["APPROVED"]:
        decision = "PENDING"
    else:
        decision = "APPROVED"

    return {
        "score":    score,
        "niveau":   niveau,
        "decision": decision,
        "flags":    flags,
        "details":  details,
    }


# =============================================================
# APPLICATION SUR TOUT LE DATASET
# =============================================================

def appliquer_moteur(df: pd.DataFrame) -> pd.DataFrame:
    print("[2/4] Application du moteur AML...")

    resultats = df.apply(scorer_transaction, axis=1)

    df["score_aml"]  = resultats.apply(lambda x: x["score"])
    df["risk_level"] = resultats.apply(lambda x: x["niveau"])
    df["decision"]   = resultats.apply(lambda x: x["decision"])
    df["flags"]      = resultats.apply(lambda x: x["flags"])
    df["details"]    = resultats.apply(lambda x: x["details"])

    print(f"      ✅ {len(df)} transactions scorées")
    return df


# =============================================================
# MISE À JOUR EN BASE
# =============================================================

def mettre_a_jour_transactions(df: pd.DataFrame, engine):
    print("[3/4] Mise à jour en base...")

    with engine.begin() as conn:
        for _, row in df.iterrows():
            conn.execute(text("""
                UPDATE transactions
                SET score_aml  = :score,
                    risk_level = :niveau,
                    statut     = CASE
                        WHEN :decision = 'BLOCKED' THEN 'BLOCKED'
                        WHEN statut = 'BLOCKED' THEN 'PENDING'
                        ELSE statut
                    END,
                    updated_at = NOW()
                WHERE transaction_id = :tid
            """), {
                "score":    int(row["score_aml"]),
                "niveau":   row["risk_level"],
                "decision": row["decision"],
                "tid":      str(row["transaction_id"])
            })

    print(f"      ✅ {len(df)} transactions mises à jour")


def creer_alertes(df: pd.DataFrame, engine):
    print("[4/4] Création des alertes...")

    # Vider les alertes existantes avant de recréer
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alerts"))

    a_alerter = df[
        df["risk_level"].isin(["HIGH", "CRITICAL"])
    ].copy()

    if len(a_alerter) == 0:
        print("      ℹ️  Aucune alerte à créer")
        return

    with engine.begin() as conn:
        for _, row in a_alerter.iterrows():
            conn.execute(text("""
                INSERT INTO alerts
                    (transaction_id, score, risk_level, flags, statut)
                VALUES
                    (:tid, :score, :niveau, :flags, 'OPEN')
            """), {
                "tid":    str(row["transaction_id"]),
                "score":  int(row["score_aml"]),
                "niveau": row["risk_level"],
                "flags":  json.dumps(row["flags"])
            })

    print(f"      ✅ {len(a_alerter)} alertes créées")
    print(f"         HIGH     : "
          f"{len(a_alerter[a_alerter['risk_level']=='HIGH'])}")
    print(f"         CRITICAL : "
          f"{len(a_alerter[a_alerter['risk_level']=='CRITICAL'])}")


# =============================================================
# POINT D'ENTRÉE
# =============================================================

if __name__ == "__main__":

    print("=" * 50)
    print("  FINTRACK — Moteur AML/CFT")
    print("=" * 50)

    engine = get_engine()

    print("\n[1/4] Lecture du dataset enrichi...")
    df = pd.read_sql(
        "SELECT * FROM transactions_enrichies",
        engine
    )
    print(f"      ✅ {len(df)} transactions chargées")

    df = appliquer_moteur(df)

    # Résultats scoring
    print("\n" + "=" * 50)
    print("  RÉSULTATS DU SCORING")
    print("=" * 50)
    print(f"  LOW      : {(df['risk_level']=='LOW').sum()}")
    print(f"  MEDIUM   : {(df['risk_level']=='MEDIUM').sum()}")
    print(f"  HIGH     : {(df['risk_level']=='HIGH').sum()}")
    print(f"  CRITICAL : {(df['risk_level']=='CRITICAL').sum()}")
    print(f"  ───────────────────────────")
    print(f"  APPROVED : {(df['decision']=='APPROVED').sum()}")
    print(f"  PENDING  : {(df['decision']=='PENDING').sum()}")
    print(f"  BLOCKED  : {(df['decision']=='BLOCKED').sum()}")

    # Performance sur les suspectes
    suspectes = df[df["is_suspect"] == True]
    detectees_hc = suspectes[
        suspectes["risk_level"].isin(["HIGH", "CRITICAL"])
    ]
    detectees_medium = suspectes[
        suspectes["risk_level"] == "MEDIUM"
    ]

    print(f"\n  ───────────────────────────")
    print(f"  Suspectes totales    : {len(suspectes)}")
    print(f"  Détectées CRITICAL   : "
          f"{len(suspectes[suspectes['risk_level']=='CRITICAL'])}")
    print(f"  Détectées HIGH       : "
          f"{len(suspectes[suspectes['risk_level']=='HIGH'])}")
    print(f"  Détectées MEDIUM     : {len(detectees_medium)}")
    print(f"  Non détectées (LOW)  : "
          f"{len(suspectes[suspectes['risk_level']=='LOW'])}")
    print(f"  Taux détection H/C   : "
          f"{len(detectees_hc)/len(suspectes)*100:.1f}%")
    print(f"  Taux détection M+    : "
          f"{(len(detectees_hc)+len(detectees_medium))/len(suspectes)*100:.1f}%")
    print("=" * 50)

    mettre_a_jour_transactions(df, engine)
    creer_alertes(df, engine)

    print("\n✅ Moteur AML terminé")
    print("   → Vérifie les alertes : SELECT * FROM alerts_open;")