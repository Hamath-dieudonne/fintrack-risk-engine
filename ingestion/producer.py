# =============================================================
# ingestion/producer.py
# Kafka Producer — Simule le flux temps réel
# =============================================================
# Génère des transactions une par une et les envoie
# dans le topic Kafka "transactions".
# Simule ce que ferait une vraie application mobile
# (Wave, Orange Money) en production.
#
# USAGE :
#   python ingestion/producer.py               → 50 transactions (une fois)
#   python ingestion/producer.py --continu     → boucle infinie
#   python ingestion/producer.py --continu --batch 100 --pause 2
# =============================================================

import os
import sys
import json
import time
import random
import argparse
from datetime import datetime
from kafka import KafkaProducer
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.generator import generer_users, generer_transaction

import pandas as pd
from sqlalchemy import create_engine

# =============================================================
# CONFIGURATION
# =============================================================

KAFKA_BROKER   = os.getenv("KAFKA_BROKER", "localhost:9092")
KAFKA_TOPIC    = "transactions"
INTERVALLE_SEC = 0.5   # Une transaction toutes les 500ms


def get_engine():
    db_url = (
        f"postgresql://"
        f"{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@"
        f"{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/"
        f"{os.getenv('DB_NAME')}"
    )
    return create_engine(db_url)


def creer_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=[KAFKA_BROKER],
        value_serializer=lambda v: json.dumps(
            v, default=str
        ).encode("utf-8"),
        acks="all",
        retries=3,
    )


def preparer_transaction(transaction: dict) -> dict:
    tx = transaction.copy()
    if isinstance(tx.get("created_at"), datetime):
        tx["created_at"] = tx["created_at"].isoformat()
    for key in ["sim_swap_recent", "is_suspect"]:
        if key in tx:
            tx[key] = bool(tx[key])
    return tx


def envoyer_batch(producer, users_df, nb: int, tour: int) -> tuple[int, int, int]:
    """Envoie un batch de nb transactions. Retourne (envoyés, suspects, erreurs)."""
    envoyes = erreurs = suspects = 0

    for i in range(nb):
        is_suspect = random.random() < 0.05
        try:
            transaction = generer_transaction(users_df, suspect=is_suspect)
            transaction = preparer_transaction(transaction)

            future = producer.send(KAFKA_TOPIC, value=transaction)
            future.get(timeout=5)

            envoyes += 1
            if is_suspect:
                suspects += 1

            statut_icon = "🚨" if is_suspect else "✅"
            num_global = (tour - 1) * nb + i + 1
            print(
                f"  [T{tour:02d} | {i+1:03d}/{nb}] {statut_icon} "
                f"{transaction['type_transaction']:<20} "
                f"{transaction['montant']:>12,.0f} FCFA  "
                f"{'SUSPECT' if is_suspect else ''}"
            )

        except Exception as e:
            erreurs += 1
            print(f"  ❌ Erreur : {e}")

        time.sleep(INTERVALLE_SEC)

    return envoyes, suspects, erreurs


def lancer_producer(nb_transactions: int = 50, continu: bool = False, pause: float = 3.0):
    print("=" * 50)
    print("  FINTRACK — Kafka Producer")
    print("=" * 50)
    print(f"\n  Broker  : {KAFKA_BROKER}")
    print(f"  Topic   : {KAFKA_TOPIC}")
    print(f"  Rythme  : 1 transaction / {INTERVALLE_SEC}s")
    print(f"  Volume  : {nb_transactions} transactions / batch")
    if continu:
        print(f"  Mode    : 🔄 CONTINU (Ctrl+C pour arrêter)")
        print(f"  Pause   : {pause}s entre chaque batch")
    else:
        print(f"  Mode    : Une seule passe")
    print("\n  Démarrage dans 3 secondes...")
    time.sleep(3)

    print("\n  Chargement des utilisateurs depuis la base...")
    engine = get_engine()
    users_df = pd.read_sql("SELECT * FROM users", engine)
    print(f"  ✅ {len(users_df)} utilisateurs chargés\n")

    producer = creer_producer()
    print(f"  ✅ Connexion Kafka établie\n")

    total_envoyes = total_suspects = total_erreurs = 0
    tour = 1

    try:
        while True:
            print("-" * 50)
            if continu:
                print(f"  🔄 Batch #{tour} en cours...")
            print("-" * 50)

            e, s, err = envoyer_batch(producer, users_df, nb_transactions, tour)
            total_envoyes  += e
            total_suspects += s
            total_erreurs  += err
            tour += 1

            producer.flush()

            if continu:
                print(f"\n  ✅ Batch terminé — Total envoyées : {total_envoyes} | Suspectes : {total_suspects}")
                print(f"  ⏳ Pause de {pause}s avant le prochain batch...\n")
                time.sleep(pause)
            else:
                break  # Mode normal : une seule passe

    except KeyboardInterrupt:
        print("\n\n  ⛔ Arrêt demandé par l'utilisateur.")

    finally:
        producer.flush()
        producer.close()
        print("-" * 50)
        print(f"\n  ✅ Producer arrêté")
        print(f"     Batches   : {tour - 1}")
        print(f"     Envoyées  : {total_envoyes}")
        print(f"     Suspectes : {total_suspects}")
        print(f"     Erreurs   : {total_erreurs}")
        print("=" * 50)


# =============================================================
# POINT D'ENTRÉE
# =============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FinTrack Kafka Producer")
    parser.add_argument(
        "--continu",
        action="store_true",
        help="Mode continu : envoie des batches en boucle infinie"
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=50,
        help="Nombre de transactions par batch (défaut: 50)"
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=3.0,
        help="Secondes de pause entre chaque batch en mode continu (défaut: 3)"
    )
    args = parser.parse_args()

    lancer_producer(
        nb_transactions=args.batch,
        continu=args.continu,
        pause=args.pause,
    )