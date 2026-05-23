# =============================================================
# aml/backtesting.py
# Évaluation des performances du moteur AML/CFT
# =============================================================
# Le backtesting répond à une question fondamentale :
# "Notre moteur est-il bon ?"
#
# On compare les prédictions du moteur avec la vérité
# terrain (is_suspect) pour calculer :
# - Precision : parmi les alertes, combien sont vraies ?
# - Recall    : parmi les vraies fraudes, combien détectées ?
# - F1 Score  : équilibre entre les deux
# - ROC/AUC   : performance à tous les seuils
# =============================================================

import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

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
# CHARGEMENT DES DONNÉES
# =============================================================

def charger_donnees(engine) -> pd.DataFrame:
    """
    Charge les transactions avec leurs scores AML
    et la vérité terrain (is_suspect).

    On joint transactions et transactions_enrichies
    pour avoir à la fois le score calculé ET
    tous les flags détaillés.
    """
    print("[1/5] Chargement des données...")

    query = """
        SELECT
            t.transaction_id,
            t.score_aml,
            t.risk_level,
            t.montant,
            t.statut,
            te.is_suspect,
            te.smurfing_flag,
            te.activite_nocturne,
            te.sim_swap_recent,
            te.anomalie_statistique,
            te.pays_risque,
            te.montant_zone_structuring
        FROM transactions t
        JOIN transactions_enrichies te
            ON t.transaction_id::text = te.transaction_id
        WHERE t.score_aml IS NOT NULL
        ORDER BY t.score_aml DESC
    """

    df = pd.read_sql(query, engine)
    print(f"      ✅ {len(df)} transactions chargées")
    print(f"         Suspectes réelles : {df['is_suspect'].sum()}")
    print(f"         Normales réelles  : {(~df['is_suspect']).sum()}")
    return df


# =============================================================
# MATRICE DE CONFUSION
# =============================================================

def calculer_matrice_confusion(
    df: pd.DataFrame,
    seuil: int = 61
) -> dict:
    """
    Calcule la matrice de confusion pour un seuil donné.

    Le seuil définit à partir de quel score on considère
    une transaction comme 'détectée' (alerte déclenchée).

    Matrice de confusion :
    ┌─────────────┬──────────────┬──────────────┐
    │             │ Réel Suspect │ Réel Normal  │
    ├─────────────┼──────────────┼──────────────┤
    │ Prédit ≥ S  │     VP       │     FP       │
    │ Prédit < S  │     FN       │     VN       │
    └─────────────┴──────────────┴──────────────┘

    VP = Vrai Positif  : fraude détectée correctement
    FP = Faux Positif  : transaction légitime bloquée
    FN = Faux Négatif  : fraude non détectée
    VN = Vrai Négatif  : transaction légitime validée
    """
    predit_positif = df["score_aml"] >= seuil
    reel_positif   = df["is_suspect"] == True

    VP = (predit_positif &  reel_positif).sum()
    FP = (predit_positif & ~reel_positif).sum()
    FN = (~predit_positif &  reel_positif).sum()
    VN = (~predit_positif & ~reel_positif).sum()

    return {"VP": int(VP), "FP": int(FP),
            "FN": int(FN), "VN": int(VN)}


# =============================================================
# MÉTRIQUES DE PERFORMANCE
# =============================================================

def calculer_metriques(matrice: dict) -> dict:
    """
    Calcule les métriques à partir de la matrice.

    PRECISION = VP / (VP + FP)
    → Sur toutes les alertes déclenchées,
      quelle proportion était vraiment suspecte ?
    → Faible = beaucoup de faux positifs
      → analyste submergé de fausses alertes

    RECALL = VP / (VP + FN)
    → Sur toutes les vraies fraudes,
      quelle proportion a été détectée ?
    → Faible = beaucoup de fraudes ratées
      → pertes financières, risque réglementaire
    → MÉTRIQUE PRIORITAIRE en AML

    F1 SCORE = 2 × (P × R) / (P + R)
    → Équilibre entre Precision et Recall
    → Utile pour comparer différentes configurations

    FPR = FP / (FP + VN)
    → Taux de faux positifs
    → Combien de transactions légitimes bloquées ?
    """
    VP = matrice["VP"]
    FP = matrice["FP"]
    FN = matrice["FN"]
    VN = matrice["VN"]

    precision = VP / (VP + FP) if (VP + FP) > 0 else 0
    recall    = VP / (VP + FN) if (VP + FN) > 0 else 0
    f1        = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0 else 0
    )
    fpr = FP / (FP + VN) if (FP + VN) > 0 else 0

    return {
        "precision": round(precision, 4),
        "recall":    round(recall, 4),
        "f1":        round(f1, 4),
        "fpr":       round(fpr, 4),
    }


# =============================================================
# COURBE ROC ET SEUIL OPTIMAL
# =============================================================

def calculer_roc(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule la courbe ROC sur tous les seuils possibles.

    La courbe ROC montre le compromis entre :
    - TPR (Recall) : taux de vraies fraudes détectées
    - FPR          : taux de faux positifs

    Un moteur parfait aurait TPR=1 et FPR=0.
    Un moteur aléatoire suivrait la diagonale.

    L'AUC (Area Under Curve) résume la courbe
    en un seul chiffre entre 0 et 1 :
    - AUC = 1.0 : moteur parfait
    - AUC = 0.5 : moteur aléatoire (inutile)
    - AUC > 0.8 : bon moteur
    """
    seuils = range(0, 101, 1)
    points = []

    for seuil in seuils:
        matrice  = calculer_matrice_confusion(df, seuil)
        metriques = calculer_metriques(matrice)
        points.append({
            "seuil":     seuil,
            "tpr":       metriques["recall"],
            "fpr":       metriques["fpr"],
            "precision": metriques["precision"],
            "f1":        metriques["f1"],
        })

    return pd.DataFrame(points)


def trouver_seuil_optimal(roc_df: pd.DataFrame) -> dict:
    """
    Trouve le seuil qui maximise le F1 Score.

    En AML on pourrait aussi maximiser le Recall
    en acceptant plus de faux positifs.
    Le F1 est un bon compromis pour commencer.

    On peut aussi utiliser la distance au point (0,1)
    sur la courbe ROC — le point parfait.
    """
    # Méthode 1 : maximiser le F1
    idx_f1   = roc_df["f1"].idxmax()
    seuil_f1 = roc_df.loc[idx_f1, "seuil"]

    # Méthode 2 : distance minimale au point parfait (0,1)
    roc_df["distance_parfait"] = np.sqrt(
        roc_df["fpr"]**2 + (1 - roc_df["tpr"])**2
    )
    idx_dist   = roc_df["distance_parfait"].idxmin()
    seuil_dist = roc_df.loc[idx_dist, "seuil"]

    return {
        "seuil_f1":        int(seuil_f1),
        "f1_max":          round(roc_df.loc[idx_f1, "f1"], 4),
        "seuil_distance":  int(seuil_dist),
        "tpr_optimal":     round(roc_df.loc[idx_dist, "tpr"], 4),
        "fpr_optimal":     round(roc_df.loc[idx_dist, "fpr"], 4),
    }


def calculer_auc(roc_df: pd.DataFrame) -> float:
    """
    Calcule l'AUC par la méthode des trapèzes.
    np.trapz est déprécié → on utilise np.trapezoid
    ou le calcul manuel selon la version numpy.
    """
    roc_sorted = roc_df.sort_values("fpr")
    
    # Compatible toutes versions numpy
    x = roc_sorted["fpr"].values
    y = roc_sorted["tpr"].values
    auc = float(np.sum((x[1:] - x[:-1]) * (y[1:] + y[:-1]) / 2))
    
    return round(abs(auc), 4)


# =============================================================
# ANALYSE PAR RÈGLE
# =============================================================

def analyser_par_regle(df: pd.DataFrame) -> pd.DataFrame:
    """
    Analyse la contribution de chaque règle AML.

    Pour chaque règle on calcule :
    - Combien de vraies fraudes elle détecte seule
    - Combien de faux positifs elle génère
    - Sa précision individuelle

    Ça permet d'identifier les règles efficaces
    et celles qui génèrent trop de bruit.
    """
    regles = {
        "smurfing_flag":             "Smurfing",
        "activite_nocturne":         "Activité nocturne",
        "sim_swap_recent":           "SIM Swap",
        "anomalie_statistique":      "Anomalie statistique",
        "pays_risque":               "Pays à risque",
        "montant_zone_structuring":  "Zone structuring",
    }

    resultats = []
    for col, nom in regles.items():
        if col not in df.columns:
            continue

        flag = df[col] == True

        vp = (flag &  df["is_suspect"]).sum()
        fp = (flag & ~df["is_suspect"]).sum()
        fn = (~flag & df["is_suspect"]).sum()

        precision = vp / (vp + fp) if (vp + fp) > 0 else 0
        recall    = vp / (vp + fn) if (vp + fn) > 0 else 0

        resultats.append({
            "Règle":      nom,
            "VP":         int(vp),
            "FP":         int(fp),
            "FN":         int(fn),
            "Precision":  f"{precision:.1%}",
            "Recall":     f"{recall:.1%}",
            "Total flags": int(flag.sum()),
        })

    return pd.DataFrame(resultats).sort_values(
        "VP", ascending=False
    )


# =============================================================
# VISUALISATIONS
# =============================================================

def generer_graphiques(
    df: pd.DataFrame,
    roc_df: pd.DataFrame,
    seuil_optimal: dict
):
    """
    Génère 4 graphiques de diagnostic sauvegardés
    dans le dossier data/.
    """
    print("[4/5] Génération des graphiques...")

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(
        "FinTrack  — Rapport Backtesting AML/CFT",
        fontsize=14, fontweight="bold"
    )

    # ── Graphique 1 : Courbe ROC ──────────────────────────
    ax1 = axes[0, 0]
    ax1.plot(
        roc_df["fpr"], roc_df["tpr"],
        color="steelblue", linewidth=2,
        label=f"Moteur AML"
    )
    ax1.plot([0, 1], [0, 1], "k--",
             linewidth=1, label="Aléatoire")
    ax1.scatter(
        seuil_optimal["fpr_optimal"],
        seuil_optimal["tpr_optimal"],
        color="red", s=100, zorder=5,
        label=f"Seuil optimal = {seuil_optimal['seuil_distance']}"
    )
    ax1.set_xlabel("Taux Faux Positifs (FPR)")
    ax1.set_ylabel("Taux Vrais Positifs (Recall)")
    ax1.set_title("Courbe ROC")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # ── Graphique 2 : F1 par seuil ────────────────────────
    ax2 = axes[0, 1]
    ax2.plot(
        roc_df["seuil"], roc_df["f1"],
        color="green", linewidth=2
    )
    ax2.axvline(
        x=seuil_optimal["seuil_f1"],
        color="red", linestyle="--",
        label=f"Seuil optimal F1 = {seuil_optimal['seuil_f1']}"
    )
    ax2.set_xlabel("Seuil de score")
    ax2.set_ylabel("F1 Score")
    ax2.set_title("F1 Score par seuil")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    # ── Graphique 3 : Distribution des scores ────────────
    ax3 = axes[1, 0]
    scores_normaux   = df[df["is_suspect"] == False]["score_aml"]
    scores_suspects  = df[df["is_suspect"] == True]["score_aml"]

    ax3.hist(scores_normaux, bins=20, alpha=0.6,
             color="steelblue", label="Normales")
    ax3.hist(scores_suspects, bins=20, alpha=0.6,
             color="red", label="Suspectes")
    ax3.axvline(
        x=seuil_optimal["seuil_f1"],
        color="black", linestyle="--",
        label=f"Seuil = {seuil_optimal['seuil_f1']}"
    )
    ax3.set_xlabel("Score AML")
    ax3.set_ylabel("Nombre de transactions")
    ax3.set_title("Distribution des scores")
    ax3.legend()
    ax3.grid(True, alpha=0.3)

    # ── Graphique 4 : Precision et Recall par seuil ──────
    ax4 = axes[1, 1]
    ax4.plot(
        roc_df["seuil"], roc_df["precision"],
        color="orange", linewidth=2, label="Precision"
    )
    ax4.plot(
        roc_df["seuil"], roc_df["tpr"],
        color="purple", linewidth=2, label="Recall"
    )
    ax4.axvline(
        x=seuil_optimal["seuil_f1"],
        color="red", linestyle="--",
        label=f"Seuil optimal = {seuil_optimal['seuil_f1']}"
    )
    ax4.set_xlabel("Seuil de score")
    ax4.set_ylabel("Score")
    ax4.set_title("Precision vs Recall par seuil")
    ax4.legend()
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig("data/backtesting_rapport.png",
                dpi=150, bbox_inches="tight")
    plt.close()

    print("      ✅ Graphiques sauvegardés : "
          "data/backtesting_rapport.png")


# =============================================================
# RAPPORT FINAL
# =============================================================

def afficher_rapport(
    df: pd.DataFrame,
    roc_df: pd.DataFrame,
    seuil_optimal: dict,
    auc: float,
    analyse_regles: pd.DataFrame
):
    seuil = seuil_optimal["seuil_f1"]
    matrice  = calculer_matrice_confusion(df, seuil)
    metriques = calculer_metriques(matrice)

    print("\n" + "=" * 55)
    print("  RAPPORT BACKTESTING AML/CFT — FinTrack")
    print("=" * 55)
    print(f"  Transactions analysées  : {len(df)}")
    print(f"  Vraies fraudes          : {df['is_suspect'].sum()}")
    print(f"  Seuil optimal (F1)      : {seuil}")
    print(f"  ─────────────────────────────────────────")
    print(f"  MATRICE DE CONFUSION (seuil = {seuil})")
    print(f"    Vrai Positif  (VP) : {matrice['VP']:4d}  "
          f"← fraudes détectées")
    print(f"    Faux Positif  (FP) : {matrice['FP']:4d}  "
          f"← fausses alertes")
    print(f"    Faux Négatif  (FN) : {matrice['FN']:4d}  "
          f"← fraudes ratées")
    print(f"    Vrai Négatif  (VN) : {matrice['VN']:4d}  "
          f"← transactions OK")
    print(f"  ─────────────────────────────────────────")
    print(f"  MÉTRIQUES DE PERFORMANCE")
    print(f"    Precision  : {metriques['precision']:.1%}  "
          f"← alertes pertinentes")
    print(f"    Recall     : {metriques['recall']:.1%}  "
          f"← fraudes détectées  ← PRIORITAIRE")
    print(f"    F1 Score   : {metriques['f1']:.1%}")
    print(f"    FPR        : {metriques['fpr']:.1%}  "
          f"← faux positifs")
    print(f"  ─────────────────────────────────────────")
    print(f"  AUC (courbe ROC)   : {auc:.4f}")

    if auc >= 0.9:
        qualite = "Excellent"
    elif auc >= 0.8:
        qualite = "Bon"
    elif auc >= 0.7:
        qualite = "Acceptable"
    else:
        qualite = "À améliorer"

    print(f"  Qualité moteur     : {qualite}")
    print(f"  ─────────────────────────────────────────")
    print(f"  ANALYSE PAR RÈGLE")
    print(analyse_regles.to_string(index=False))
    print("=" * 55)


# =============================================================
# POINT D'ENTRÉE
# =============================================================

if __name__ == "__main__":

    print("=" * 55)
    print("  FINTRACK — Backtesting AML/CFT")
    print("=" * 55)

    engine = get_engine()

    # Charger les données
    df = charger_donnees(engine)

    # Calculer la courbe ROC
    print("[2/5] Calcul de la courbe ROC...")
    roc_df = calculer_roc(df)
    auc    = calculer_auc(roc_df)
    print(f"      ✅ AUC calculée : {auc:.4f}")

    # Trouver le seuil optimal
    print("[3/5] Recherche du seuil optimal...")
    seuil_optimal = trouver_seuil_optimal(roc_df)
    print(f"      ✅ Seuil optimal F1       : "
          f"{seuil_optimal['seuil_f1']}")
    print(f"      ✅ F1 max                 : "
          f"{seuil_optimal['f1_max']:.1%}")
    print(f"      ✅ Seuil optimal distance : "
          f"{seuil_optimal['seuil_distance']}")

    # Générer les graphiques
    generer_graphiques(df, roc_df, seuil_optimal)

    # Analyse par règle
    print("[5/5] Analyse par règle...")
    analyse_regles = analyser_par_regle(df)
    print("      ✅ Analyse terminée")

    # Rapport final
    afficher_rapport(
        df, roc_df, seuil_optimal,
        auc, analyse_regles
    )

    # Sauvegarder le rapport CSV
    roc_df.to_csv("data/roc_curve.csv", index=False)
    print("\n✅ Fichiers générés :")
    print("   → data/backtesting_rapport.png")
    print("   → data/roc_curve.csv")