# =============================================================
# processing/load.py
# Charge les données générées dans PostgreSQL
# =============================================================

import os
import sys
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Ajouter le dossier racine au path Python
# pour pouvoir importer generator.py depuis data/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.generator import generer_dataset

# -------------------------------------------------------------
# CHARGER LES VARIABLES D'ENVIRONNEMENT
# Le mot de passe ne doit JAMAIS être écrit dans le code
# Il est lu depuis le fichier .env
# -------------------------------------------------------------
load_dotenv()


def get_engine():
    """
    Crée la connexion à PostgreSQL via SQLAlchemy.

    SQLAlchemy utilise une URL de connexion standard :
    postgresql://user:password@host:port/database

    L'engine gère automatiquement :
    - Le pool de connexions (réutilisation)
    - La reconnexion en cas de coupure
    - La compatibilité SQL
    """
    db_url = (
        f"postgresql://"
        f"{os.getenv('DB_USER')}:"
        f"{os.getenv('DB_PASSWORD')}@"
        f"{os.getenv('DB_HOST')}:"
        f"{os.getenv('DB_PORT')}/"
        f"{os.getenv('DB_NAME')}"
    )
    return create_engine(db_url)


def verifier_schema(engine):
    """
    Vérifie que les tables existent déjà.
    Le schéma doit être créé manuellement via pgAdmin
    en exécutant processing/schema.sql
    """
    print("[1/4] Vérification du schéma...")

    tables_requises = ["users", "transactions", "alerts", "audit_logs"]

    with engine.connect() as conn:
        for table in tables_requises:
            resultat = conn.execute(text(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = '{table}'
                )
            """)).scalar()

            if not resultat:
                print(f"      ❌ Table manquante : {table}")
                print(f"         Exécute d'abord schema.sql dans pgAdmin")
                sys.exit(1)
            else:
                print(f"      ✅ Table OK : {table}")

def vider_tables(engine):
    """
    Vide les tables avant chaque chargement.
    Évite les doublons si on relance le script.
    CASCADE respecte l'ordre des clés étrangères :
    audit_logs → alerts → transactions → users
    """
    print("[1/4] Nettoyage des tables...")
    with engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE TABLE audit_logs, alerts, transactions, users CASCADE"
        ))
        conn.commit()
    print("      ✅ Tables vidées")

def charger_users(users_df: pd.DataFrame, engine):
    print("[2/4] Chargement des utilisateurs...")

    users_df["user_id"]     = users_df["user_id"].astype(str)
    users_df["created_at"]  = pd.to_datetime(users_df["created_at"])
    users_df["sim_swap_at"] = pd.to_datetime(users_df["sim_swap_at"])

    # on utilise begin() pour forcer le commit
    with engine.begin() as conn:
        users_df.to_sql(
            name="users",
            con=conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=100
        )

    print(f"      ✅ {len(users_df)} utilisateurs insérés")


def charger_transactions(transactions_df: pd.DataFrame, engine):
    print("[3/4] Chargement des transactions...")

    colonnes = [
        "transaction_id", "user_id", "receiver_id",
        "type_transaction", "montant", "devise",
        "operateur", "pays_emetteur", "pays_recepteur",
        "telephone_emetteur", "telephone_recepteur",
        "sim_swap_recent", "statut", "is_suspect",
        "created_at"
    ]

    df_a_inserer = transactions_df[colonnes].copy()
    df_a_inserer["created_at"] = pd.to_datetime(df_a_inserer["created_at"])

    # engine.begin() ouvre une transaction et commit automatiquement
    # engine.connect() sans begin() ne commit pas toujours
    with engine.begin() as conn:
        df_a_inserer.to_sql(
            name="transactions",
            con=conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=200
        )

    print(f"      ✅ {len(df_a_inserer)} transactions insérées")


def verifier_chargement(engine):
    """
    Vérifie que tout a bien été inséré.
    Quelques requêtes SQL de contrôle.
    """
    print("[4/4] Vérification...")

    requetes = {
        "Nombre d'users"       : "SELECT COUNT(*) FROM users",
        "Nombre de transactions": "SELECT COUNT(*) FROM transactions",
        "Transactions SUCCESS"  : "SELECT COUNT(*) FROM transactions WHERE statut='SUCCESS'",
        "Transactions suspectes": "SELECT COUNT(*) FROM transactions WHERE is_suspect=TRUE",
        "Montant total (FCFA)"  : "SELECT SUM(montant) FROM transactions",
    }

    with engine.connect() as conn:
        for label, requete in requetes.items():
            resultat = conn.execute(text(requete)).scalar()
            if label == "Montant total (FCFA)":
                print(f"      {label:<30} : {resultat:,.0f}")
            else:
                print(f"      {label:<30} : {resultat}")


# =============================================================
# POINT D'ENTRÉE
# =============================================================

if __name__ == "__main__":

    print("=" * 50)
    print("  FINTRACK — Chargement PostgreSQL")
    print("=" * 50)

    # Étape 1 — Connexion
    print("\nConnexion à PostgreSQL...")
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ Connexion réussie\n")
    except Exception as e:
        print(f"❌ Connexion échouée : {e}")
        print("   Vérifie que PostgreSQL est bien lancé")
        print("   et que le fichier .env est correct")
        sys.exit(1)

    # Étape 2 — Générer les données
    print("Génération des données...")
    users_df, transactions_df = generer_dataset(
        nb_users=200,
        nb_normales=2000,
        nb_suspectes=100
    )

    # Étape 3 — Créer le schéma
    print()
    verifier_schema(engine)
    vider_tables(engine)

    # Étape 4 — Charger les données
    charger_users(users_df, engine)
    charger_transactions(transactions_df, engine)
    verifier_chargement(engine)

    print("\n" + "=" * 50)
    print("  ✅ Chargement terminé")
    print("  → Ouvre pgAdmin pour vérifier visuellement")
    print("=" * 50)