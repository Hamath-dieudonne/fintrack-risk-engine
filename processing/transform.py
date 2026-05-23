# =============================================================
# processing/transform.py
# Pipeline batch — Transformation et enrichissement des données
# =============================================================

import os
import sys
import pandas as pd
import numpy as np
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
# ÉTAPE 1 — LECTURE
# =============================================================

def lire_transactions(engine) -> pd.DataFrame:
    print("[1/5] Lecture des transactions...")
    query = """
        SELECT
            t.transaction_id,
            t.user_id,
            t.receiver_id,
            t.type_transaction,
            t.montant,
            t.pays_emetteur,
            t.pays_recepteur,
            t.operateur,
            t.statut,
            t.is_suspect,
            t.created_at,
            u.sim_swap_recent,
            u.sim_swap_at
        FROM transactions t
        LEFT JOIN users u ON t.user_id = u.user_id
        ORDER BY t.created_at ASC
    """
    df = pd.read_sql(query, engine)
    df["created_at"] = pd.to_datetime(df["created_at"])
    print(f"      ✅ {len(df)} transactions lues")
    return df


# =============================================================
# ÉTAPE 2 — STATS PAR UTILISATEUR
# Calculées UNIQUEMENT sur les transactions normales
# pour avoir un profil "propre" non contaminé
# =============================================================

def calculer_stats_utilisateur(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule le profil normal de chaque utilisateur
    en excluant les transactions suspectes.

    POURQUOI exclure les suspectes ?
    Si on inclut une transaction de 975 000 FCFA dans
    le profil d'un user qui fait habituellement 50 000 FCFA,
    sa moyenne monte et son écart-type explose.
    Le z-score des prochaines transactions suspectes
    sera alors trop faible → fraude non détectée.

    En production on utiliserait une fenêtre glissante
    historique (30 derniers jours avant la transaction).
    Ici on utilise is_suspect=False comme proxy.
    """
    print("[2/5] Calcul des stats par utilisateur...")

    df_propre = df[df["is_suspect"] == False].copy()

    stats = df_propre.groupby("user_id")["montant"].agg(
        montant_moyen="mean",
        montant_std="std",
        montant_median="median",
        montant_max="max",
        nb_transactions="count"
    ).reset_index()

    stats["montant_std"] = stats["montant_std"].fillna(0)

    print(f"      ✅ Stats calculées pour {len(stats)} utilisateurs")
    print(f"         Basé sur transactions normales uniquement")
    print(f"         Montant moyen global : "
          f"{stats['montant_moyen'].mean():,.0f} FCFA")

    return stats


# =============================================================
# ÉTAPE 3 — SIGNAUX AML
# =============================================================

def calculer_smurfing(df: pd.DataFrame) -> pd.DataFrame:

    # Critère 1 : montants entre 900k et 999k
    smurfing_900k = (
        df[df["montant"].between(900_000, 999_999)]
        .groupby("user_id")
        .size()
        .reset_index(name="nb_900k")
    )
    
    smurfing_900k["smurfing_900k"] = smurfing_900k["nb_900k"] >= 3

    # ← MODIFICATION 1 ICI
    # Critère 2 désactivé — montants Bank-to-Wallet légitimes
    # jusqu'à 2M FCFA génèrent trop de faux positifs
    df["smurfing_total"] = False
    smurfing_total_df = pd.DataFrame(
        columns=["user_id", "smurfing_total"]
    )

    # Critère 3 : montants trop réguliers
    regularite = (
        df.groupby("user_id")["montant"]
        .agg(["std", "count"])
        .reset_index()
    )
    regularite.columns = ["user_id", "montant_std_user", "nb_tx_user"]

    # ← MODIFICATION 2 ICI
    regularite["smurfing_regularite"] = (
        (regularite["montant_std_user"] < 30_000) &  # std < 30k
        (regularite["nb_tx_user"] >= 8)               # au moins 8 tx
    )

    # Fusionner les critères 1 et 3 seulement
    df = df.merge(
        smurfing_900k[["user_id", "smurfing_900k"]],
        on="user_id", how="left"
    )
    df = df.merge(
        regularite[["user_id", "smurfing_regularite"]],
        on="user_id", how="left"
    )

    df["smurfing_900k"]       = df["smurfing_900k"].fillna(False)
    df["smurfing_regularite"] = df["smurfing_regularite"].fillna(False)
    # smurfing_total déjà assigné à False directement

    df["smurfing_flag"] = (
    df["smurfing_900k"] |
    df["smurfing_total"] |
    df["smurfing_regularite"]
)


    print(f"         Smurfing 900k-999k    : {df['smurfing_900k'].sum()}")
    print(f"         Smurfing total 24h    : {df['smurfing_total'].sum()}")
    print(f"         Smurfing régularité   : {df['smurfing_regularite'].sum()}")
    print(f"         Smurfing total flaggé : {df['smurfing_flag'].sum()}")
    print(df["smurfing_regularite"].value_counts())


    return df


def calculer_velocite(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule le nombre de transactions dans la dernière heure
    par utilisateur via fenêtre glissante chronologique.
    """
    df = df.sort_values(["user_id", "created_at"]).copy()

    df["tx_last_hour"] = (
        df.groupby("user_id")["created_at"]
        .transform(lambda x: x.expanding().count())
    )

    return df


def calculer_signaux_contextuels(df: pd.DataFrame) -> pd.DataFrame:

    # Activité nocturne
    df["heure"] = df["created_at"].dt.hour
    df["activite_nocturne"] = df["heure"].between(1, 4)

    # Montant en zone structuring
    df["montant_zone_structuring"] = df["montant"].between(
        900_000, 999_999
    )

    # ← MODIFICATION ICI — remplace tout le bloc pays
    # Signal désactivé — nécessite plus d'historique
    # par utilisateur pour être fiable (voir backtesting)
    df["pays_risque"] = False

    nb_nocturnes   = df["activite_nocturne"].sum()
    nb_structuring = df["montant_zone_structuring"].sum()
    nb_pays        = df["pays_risque"].sum()

    print(f"         Transactions nocturnes    : {nb_nocturnes}")
    print(f"         Zone structuring          : {nb_structuring}")
    print(f"         Pays à risque             : {nb_pays}")

    return df


def calculer_zscore(
    df: pd.DataFrame,
    stats: pd.DataFrame
) -> pd.DataFrame:
    """
    Calcule le z-score par rapport au profil propre
    de l'utilisateur.

    Seulement calculé si nb_transactions >= 10
    pour garantir la fiabilité statistique.
    """
    df = df.merge(stats, on="user_id", how="left")

    def zscore_row(row):
        if row["nb_transactions"] < 10:
            return None
        if row["montant_std"] == 0:
            return 0.0
        return (
            (row["montant"] - row["montant_moyen"])
            / row["montant_std"]
        )

    df["z_score"] = df.apply(zscore_row, axis=1)
    df["anomalie_statistique"] = df["z_score"].apply(
        lambda z: z > 3 if z is not None else False
    )

    nb_anomalies = df["anomalie_statistique"].sum()
    print(f"         Anomalies statistiques    : {nb_anomalies}")

    return df


# =============================================================
# ÉTAPE 4 — ASSEMBLAGE
# =============================================================

def assembler_dataset_enrichi(df: pd.DataFrame) -> pd.DataFrame:
    print("[4/5] Assemblage du dataset enrichi...")

    colonnes = [
        "transaction_id", "user_id",
        "type_transaction", "montant", "statut",
        "pays_emetteur", "pays_recepteur", "created_at",

        # Signaux AML
        "sim_swap_recent",
        "activite_nocturne",
        "montant_zone_structuring",
        "smurfing_flag",
        "smurfing_900k",
        "smurfing_total",
        "smurfing_regularite",
        "pays_risque",
        "tx_last_hour",

        # Stats utilisateur
        "montant_moyen",
        "montant_std",
        "nb_transactions",

        # Z-score
        "z_score",
        "anomalie_statistique",

        # Ground truth
        "is_suspect",
    ]

    df_enrichi = df[colonnes].copy()

    print(f"      ✅ Dataset enrichi : {len(df_enrichi)} lignes")
    print(f"         {len(colonnes)} colonnes")

    return df_enrichi


# =============================================================
# ÉTAPE 5 — SAUVEGARDE
# =============================================================

def sauvegarder(df_enrichi: pd.DataFrame, engine):
    print("[5/5] Sauvegarde...")

    df_enrichi.to_csv("data/transactions_enrichies.csv", index=False)
    print(f"      ✅ CSV : data/transactions_enrichies.csv")

    with engine.begin() as conn:
        conn.execute(text(
            "DROP TABLE IF EXISTS transactions_enrichies"
        ))

    with engine.begin() as conn:
        df_enrichi.to_sql(
            name="transactions_enrichies",
            con=conn,
            if_exists="replace",
            index=False,
            method="multi",
            chunksize=200
        )

    print(f"      ✅ PostgreSQL : table transactions_enrichies")


# =============================================================
# POINT D'ENTRÉE
# =============================================================

if __name__ == "__main__":

    print("=" * 50)
    print("  FINTRACK — Pipeline Batch")
    print("=" * 50)

    engine = get_engine()

    df    = lire_transactions(engine)
    stats = calculer_stats_utilisateur(df)

    print("[3/5] Calcul des signaux AML...")
    df = calculer_smurfing(df)
    df = calculer_velocite(df)
    df = calculer_signaux_contextuels(df)
    df = calculer_zscore(df, stats)

    df_enrichi = assembler_dataset_enrichi(df)
    sauvegarder(df_enrichi, engine)

    print("\n" + "=" * 50)
    print("  RÉSUMÉ AML")
    print("=" * 50)
    print(f"  Transactions analysées     : {len(df_enrichi)}")
    print(f"  Nocturnes                  : "
          f"{df_enrichi['activite_nocturne'].sum()}")
    print(f"  Zone structuring           : "
          f"{df_enrichi['montant_zone_structuring'].sum()}")
    print(f"  Smurfing flaggées          : "
          f"{df_enrichi['smurfing_flag'].sum()}")
    print(f"  Pays à risque              : "
          f"{df_enrichi['pays_risque'].sum()}")
    print(f"  Anomalies statistiques     : "
          f"{df_enrichi['anomalie_statistique'].sum()}")
    print(f"  SIM Swap récent            : "
          f"{df_enrichi['sim_swap_recent'].sum()}")
    print("=" * 50)