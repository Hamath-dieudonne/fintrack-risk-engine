# =============================================================
# data/generator.py
# Générateur de transactions Mobile Money fictives
# Simule les flux d'une plateforme de paiement fintech
# =============================================================

import random
import uuid
from datetime import datetime, timedelta
import pandas as pd
from faker import Faker

# -------------------------------------------------------------
# FAKER
# On initialise Faker avec fr_FR pour avoir des noms
# et formats cohérents avec l'Afrique francophone
# -------------------------------------------------------------
fake = Faker("fr_FR")
Faker.seed(42)        # Seed fixe = résultats reproductibles
random.seed(42)       # Même chose pour random

# -------------------------------------------------------------
# CONSTANTES MÉTIER
# Ces valeurs reflètent la réalité du marché Mobile Money
# en Afrique de l'Ouest
# -------------------------------------------------------------

PAYS = ["Sénégal", "Côte d'Ivoire", "RDC", "Cameroun", "Mali"]

PREFIXES_TEL = {
    "Sénégal":       ["77", "78", "76", "70"],
    "Côte d'Ivoire": ["07", "05", "01"],
    "RDC":           ["081", "082", "083"],
    "Cameroun":      ["650", "651", "652"],
    "Mali":          ["70", "76", "79"],
}

OPERATEURS = [
    "Orange Money",
    "Wave",
    "Free Money",
    "MTN Mobile Money",
    "Airtel Money",
]

# Chaque type a ses propres limites de montant
# C'est la logique métier qu'on a vue ensemble
TYPES_CONFIG = {
    "BANK_TO_WALLET":   {"min": 1_000,  "max": 2_000_000},
    "WALLET_TO_BANK":   {"min": 1_000,  "max": 2_000_000},
    "QR_PAYMENT":       {"min": 500,    "max": 500_000},
    "USSD_TRANSFER":    {"min": 500,    "max": 300_000},
    "BILL_PAYMENT":     {"min": 2_000,  "max": 150_000},
    "WALLET_TO_WALLET": {"min": 500,    "max": 1_000_000},
}

# Poids de probabilité des types
# WALLET_TO_WALLET et USSD sont les plus courants
# car utilisés au quotidien
TYPES_POIDS = {
    "BANK_TO_WALLET":   0.10,
    "WALLET_TO_BANK":   0.10,
    "QR_PAYMENT":       0.20,
    "USSD_TRANSFER":    0.30,
    "BILL_PAYMENT":     0.10,
    "WALLET_TO_WALLET": 0.20,
}

# Poids des statuts — reflète la réalité production
# ~85% des transactions réussissent
STATUTS = ["SUCCESS", "FAILED", "PENDING", "REVERSED"]
STATUTS_POIDS = [0.85, 0.10, 0.03, 0.02]


# =============================================================
# FONCTIONS UTILITAIRES
# =============================================================

def generer_user_id() -> str:
    """
    UUID4 = identifiant unique universel.
    Standard fintech pour éviter les collisions
    sans séquence centralisée.
    Ex: 550e8400-e29b-41d4-a716-446655440000
    """
    return str(uuid.uuid4())


def generer_telephone(pays: str) -> str:
    """
    Génère un numéro réaliste selon le pays.
    Important pour les règles AML géographiques.
    """
    prefixe = random.choice(PREFIXES_TEL[pays])
    suffixe = "".join([str(random.randint(0, 9)) for _ in range(7)])
    return f"+{prefixe}{suffixe}"


def generer_montant(type_transaction: str, suspect: bool = False) -> float:
    """
    Génère un montant cohérent avec le type de transaction.

    Mode suspect : montant entre 950k et 999k FCFA
    C'est la technique du STRUCTURING — fractionner
    juste en dessous du seuil de déclaration UEMOA (1M FCFA)
    pour éviter la détection automatique.
    """
    if suspect:
        # Juste en dessous du seuil réglementaire
        # C'est exactement ce que détecte notre règle SMURFING
        return round(random.uniform(950_000, 999_999), 0)
    else:
        config = TYPES_CONFIG[type_transaction]
        return round(random.uniform(config["min"], config["max"]), 0)


def generer_date(jours_passes: int = 90, heure_suspecte: bool = False) -> datetime:
    """
    Génère une date réaliste.

    Mode suspect — distribution réaliste :
    - 60% la nuit (1h-4h) : les fraudeurs préfèrent la nuit
    - 25% tôt le matin (5h-8h) : surveillance réduite
    - 15% en journée : pour se fondre dans la masse

    C'est plus difficile à détecter pour le moteur AML
    et plus proche de la réalité terrain.
    """
    date_base = datetime.now() - timedelta(
        days=random.randint(0, jours_passes)
    )

    if heure_suspecte:
        # Distribution réaliste des fraudes
        tirage = random.random()
        if tirage < 0.60:
            heure = random.randint(1, 4)    # Nuit — 60%
        elif tirage < 0.85:
            heure = random.randint(5, 8)    # Tôt matin — 25%
        else:
            heure = random.randint(9, 22)   # Journée — 15%
    else:
        heures_normales = (
            list(range(7, 12)) * 3 +
            list(range(12, 18)) * 2 +
            list(range(18, 22)) * 3
        )
        heure = random.choice(heures_normales)

    return date_base.replace(
        hour=heure,
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
        microsecond=0,
    )


## Ce que ça change pour le moteur AML
# ```
# Avant :
#   100% des fraudes entre 1h-4h
#   → Règle nocturne détecte 100% → trop facile

# Après :
#   60% entre 1h-4h   → détectées par règle nocturne
#   25% entre 5h-8h   → détectées par z-score + smurfing
#   15% en journée    → détectées uniquement par z-score + smurfing
#   → Moteur AML vraiment mis à l'épreuve ✅


# =============================================================
# GÉNÉRATEUR D'UTILISATEURS
# On génère d'abord les users, ensuite les transactions
# C'est important pour avoir des user_id cohérents
# et simuler un historique par utilisateur
# =============================================================

def generer_users(nb_users: int = 100) -> pd.DataFrame:
    """
    Génère un pool d'utilisateurs réalistes.

    Pourquoi un pool fixe et pas un UUID aléatoire
    par transaction ?
    Parce qu'en réalité, les mêmes utilisateurs
    font plusieurs transactions. Sans pool fixe,
    chaque transaction aurait un user différent
    et le moteur AML ne pourrait jamais détecter
    la vélocité ou le smurfing (qui nécessitent
    plusieurs transactions du même user).
    """
    users = []
    for _ in range(nb_users):
        pays = random.choice(PAYS)
        # Certains users ont eu un SIM Swap récent
        # (dans les dernières 24h) — signal AML fort
        sim_swap_recent = random.random() < 0.05  # 5% des users

        users.append({
            "user_id":         generer_user_id(),
            "nom":             fake.last_name(),
            "prenom":          fake.first_name(),
            "telephone":       generer_telephone(pays),
            "pays":            pays,
            "operateur":       random.choice(OPERATEURS),
            "created_at":      generer_date(jours_passes=365),
            "sim_swap_recent": sim_swap_recent,
            "sim_swap_at":     (
                datetime.now() - timedelta(hours=random.randint(1, 23))
                if sim_swap_recent else None
            ),
        })

    return pd.DataFrame(users)


# =============================================================
# GÉNÉRATEUR DE TRANSACTIONS
# =============================================================

def generer_transaction(
    users_df: pd.DataFrame,
    suspect: bool = False
) -> dict:
    """
    Génère une transaction complète.

    On choisit un émetteur et un récepteur
    dans notre pool d'utilisateurs existants.
    Ça garantit que les même users apparaissent
    plusieurs fois — indispensable pour l'AML.
    """
    # Choisir émetteur et récepteur différents
    emetteur  = users_df.sample(1).iloc[0]
    recepteur = users_df[
        users_df["user_id"] != emetteur["user_id"]
    ].sample(1).iloc[0]

    # Choisir le type selon les poids métier
    type_tx = random.choices(
        list(TYPES_POIDS.keys()),
        weights=list(TYPES_POIDS.values()),
        k=1
    )[0]

    return {
        "transaction_id":    generer_user_id(),
        "user_id":           emetteur["user_id"],
        "receiver_id":       recepteur["user_id"],
        "type_transaction":  type_tx,
        "montant":           generer_montant(type_tx, suspect=suspect),
        "devise":            "FCFA",
        "operateur":         emetteur["operateur"],
        "pays_emetteur":     emetteur["pays"],
        "pays_recepteur":    recepteur["pays"],
        "telephone_emetteur":emetteur["telephone"],
        "telephone_recepteur":recepteur["telephone"],
        "sim_swap_recent":   emetteur["sim_swap_recent"],
        "statut":            random.choices(
                                STATUTS,
                                weights=STATUTS_POIDS,
                                k=1
                             )[0],
        "created_at":        generer_date(heure_suspecte=suspect),
        "is_suspect":        suspect,
    }


# =============================================================
# GÉNÉRATEUR PRINCIPAL — DATASET COMPLET
# =============================================================

def generer_dataset(
    nb_users: int    = 100,
    nb_normales: int = 1000,
    nb_suspectes: int = 50
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Génère le dataset complet : users + transactions.

    Retourne un tuple (users_df, transactions_df)
    car on a besoin des deux séparément pour
    l'insertion en base (deux tables différentes).

    Ratio fraude : 50/1050 = ~4.8%
    Réaliste pour le marché Mobile Money africain.
    """

    print("=" * 50)
    print("  FINTRACK — Génération des données")
    print("=" * 50)

    # Étape 1 — Générer les utilisateurs
    print(f"\n[1/3] Génération de {nb_users} utilisateurs...")
    users_df = generer_users(nb_users)
    print(f"      ✅ {len(users_df)} utilisateurs créés")
    print(f"      → Avec SIM Swap récent : "
          f"{users_df['sim_swap_recent'].sum()}")

    # Étape 2 — Générer les transactions normales
    print(f"\n[2/3] Génération de {nb_normales} transactions normales...")
    transactions_normales = [
        generer_transaction(users_df, suspect=False)
        for _ in range(nb_normales)
    ]
    print(f"      ✅ {nb_normales} transactions normales créées")

    # Étape 3 — Générer les transactions suspectes
    print(f"\n[3/3] Génération de {nb_suspectes} transactions suspectes...")
    transactions_suspectes = [
        generer_transaction(users_df, suspect=True)
        for _ in range(nb_suspectes)
    ]
    print(f"      ✅ {nb_suspectes} transactions suspectes créées")

    # Fusion et tri chronologique
    toutes = transactions_normales + transactions_suspectes
    random.shuffle(toutes)
    transactions_df = pd.DataFrame(toutes)
    transactions_df = transactions_df.sort_values("created_at").reset_index(drop=True)

    # Résumé
    print("\n" + "=" * 50)
    print("  RÉSUMÉ")
    print("=" * 50)
    print(f"  Transactions totales  : {len(transactions_df)}")
    print(f"  → Normales            : "
          f"{len(transactions_df[transactions_df['is_suspect']==False])}")
    print(f"  → Suspectes           : "
          f"{len(transactions_df[transactions_df['is_suspect']==True])}")
    print(f"  Période               : "
          f"{transactions_df['created_at'].min().date()} "
          f"→ {transactions_df['created_at'].max().date()}")
    print(f"  Montant total         : "
          f"{transactions_df['montant'].sum():,.0f} FCFA")
    print("=" * 50)

    return users_df, transactions_df


# =============================================================
# POINT D'ENTRÉE — pour tester le fichier directement
# =============================================================

if __name__ == "__main__":

    users_df, transactions_df = generer_dataset(
        nb_users=100,
        nb_normales=1000,
        nb_suspectes=50
    )

    # Aperçu
    print("\n--- 5 premières transactions ---")
    print(transactions_df[[
        "transaction_id", "type_transaction",
        "montant", "statut", "is_suspect"
    ]].head())

    print("\n--- Répartition par type ---")
    print(transactions_df["type_transaction"].value_counts())

    print("\n--- Répartition par statut ---")
    print(transactions_df["statut"].value_counts())

    print("\n--- Transactions suspectes ---")
    suspects = transactions_df[transactions_df["is_suspect"] == True]
    print(f"Montant moyen suspect  : {suspects['montant'].mean():,.0f} FCFA")
    print(f"Montant moyen normal   : "
          f"{transactions_df[transactions_df['is_suspect']==False]['montant'].mean():,.0f} FCFA")
    print(f"Heures suspectes (1-4h): "
          f"{(suspects['created_at'].dt.hour.between(1,4)).sum()} transactions")

    # Sauvegarde CSV
    users_df.to_csv("data/users.csv", index=False)
    transactions_df.to_csv("data/transactions.csv", index=False)
    print("\n✅ Fichiers sauvegardés :")
    print("   → data/users.csv")
    print("   → data/transactions.csv")