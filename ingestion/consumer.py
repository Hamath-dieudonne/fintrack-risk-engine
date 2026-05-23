# =============================================================
# ingestion/consumer.py
# Kafka Consumer — Traitement temps réel
# =============================================================
# Lit les transactions depuis Kafka, les insère en base,
# déclenche le moteur AML et crée les audit logs.
# Tout ça en moins d'une seconde par transaction.
# =============================================================

import os
import sys
import json
import uuid
from datetime import datetime
from kafka import KafkaConsumer
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aml.scoring import scorer_transaction
from audit.logger import (
    log_transaction_created,
    log_aml_score,
    log_transaction_bloquee,
    log_declaration_centif,
)

import pandas as pd

# =============================================================
# CONFIGURATION
# =============================================================

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
KAFKA_TOPIC  = "transactions"
KAFKA_GROUP  = "fintrack_consumer_group"


def get_engine():
    db_url = (
        f"postgresql://"
        f"{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@"
        f"{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/"
        f"{os.getenv('DB_NAME')}"
    )
    return create_engine(db_url)


def creer_consumer() -> KafkaConsumer:
    """
    Crée le consumer Kafka.

    group_id : identifiant du groupe de consumers.
    Kafka distribue les messages entre tous les consumers
    du même groupe — permet le parallélisme.

    auto_offset_reset='earliest' : si le consumer redémarre,
    il reprend depuis le dernier message non traité.
    Garantit qu'aucun message n'est perdu.

    enable_auto_commit=True : marque automatiquement
    les messages comme traités après lecture.
    """
    return KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=[KAFKA_BROKER],
        group_id=KAFKA_GROUP,
        value_deserializer=lambda v: json.loads(
            v.decode("utf-8")
        ),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )


# =============================================================
# ENRICHISSEMENT POUR LE SCORING
# =============================================================

def enrichir_transaction(transaction: dict, engine) -> pd.Series:
    """
    Récupère les stats utilisateur depuis la base
    pour calculer les signaux AML en temps réel.

    En batch on calcule les stats sur tout le dataset.
    En streaming on les récupère pour UN utilisateur
    au moment de la transaction.

    C'est le coeur du scoring temps réel.
    """
    user_id = transaction.get("user_id")

    # Stats historiques de l'utilisateur
    with engine.connect() as conn:
        stats = conn.execute(text("""
            SELECT
                AVG(montant)   AS montant_moyen,
                STDDEV(montant) AS montant_std,
                COUNT(*)       AS nb_transactions,
                MAX(montant)   AS montant_max
            FROM transactions
            WHERE user_id = :uid
              AND is_suspect = FALSE
        """), {"uid": user_id}).fetchone()

        # Vélocité : transactions dans la dernière heure
        velocite = conn.execute(text("""
            SELECT COUNT(*) AS tx_last_hour
            FROM transactions
            WHERE user_id = :uid
              AND created_at >= NOW() - INTERVAL '1 hour'
        """), {"uid": user_id}).scalar() or 0

        # Smurfing historique — transactions 900k-999k sur 30 jours
        smurfing_count = conn.execute(text("""
            SELECT COUNT(*) 
            FROM transactions
            WHERE user_id = :uid
              AND montant BETWEEN 900000 AND 999999
              AND created_at >= NOW() - INTERVAL '30 days'
        """), {"uid": user_id}).scalar() or 0

    montant      = float(transaction.get("montant", 0))
    montant_moyen = float(stats[0] or 0)
    montant_std   = float(stats[1] or 0)
    nb_tx         = int(stats[2] or 0)

    # Z-score
    z_score = None
    anomalie = False
    if nb_tx >= 10 and montant_std > 0:
        z_score = (montant - montant_moyen) / montant_std
        anomalie = z_score > 3

    # Construire la Series pour scorer_transaction
    return pd.Series({
        "montant":                  montant,
        "sim_swap_recent":          transaction.get("sim_swap_recent", False),
        "activite_nocturne":        _est_nocturne(
                                        transaction.get("created_at", "")
                                    ),
        "montant_zone_structuring": 900_000 <= montant <= 999_999,
        "smurfing_flag":            smurfing_count >= 2,
        "smurfing_900k":            smurfing_count >= 2,
        "smurfing_total":           False,
        "smurfing_regularite":      False,
        "pays_risque":              False,
        "tx_last_hour":             velocite,
        "montant_moyen":            montant_moyen,
        "montant_std":              montant_std,
        "nb_transactions":          nb_tx,
        "z_score":                  z_score,
        "anomalie_statistique":     anomalie,
        "is_suspect":               transaction.get("is_suspect", False),
    })


def _est_nocturne(created_at_str: str) -> bool:
    """Vérifie si la transaction est entre 1h et 4h."""
    try:
        dt = datetime.fromisoformat(created_at_str)
        return 1 <= dt.hour <= 4
    except Exception:
        return False


# =============================================================
# INSERTION EN BASE
# =============================================================

def inserer_transaction(transaction: dict, engine) -> bool:
    """
    Insère la transaction dans PostgreSQL.
    Retourne True si succès.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO transactions (
                    transaction_id, user_id, receiver_id,
                    type_transaction, montant, devise,
                    operateur, pays_emetteur, pays_recepteur,
                    telephone_emetteur, telephone_recepteur,
                    sim_swap_recent, statut, is_suspect,
                    created_at
                ) VALUES (
                    :transaction_id, :user_id, :receiver_id,
                    :type_transaction, :montant, :devise,
                    :operateur, :pays_emetteur, :pays_recepteur,
                    :telephone_emetteur, :telephone_recepteur,
                    :sim_swap_recent, 'PENDING', :is_suspect,
                    :created_at
                )
                ON CONFLICT (transaction_id) DO NOTHING
            """), {
                "transaction_id":     transaction["transaction_id"],
                "user_id":            transaction["user_id"],
                "receiver_id":        transaction["receiver_id"],
                "type_transaction":   transaction["type_transaction"],
                "montant":            float(transaction["montant"]),
                "devise":             transaction.get("devise", "FCFA"),
                "operateur":          transaction.get("operateur", ""),
                "pays_emetteur":      transaction.get("pays_emetteur", ""),
                "pays_recepteur":     transaction.get("pays_recepteur", ""),
                "telephone_emetteur": transaction.get("telephone_emetteur", ""),
                "telephone_recepteur":transaction.get("telephone_recepteur", ""),
                "sim_swap_recent":    bool(transaction.get("sim_swap_recent", False)),
                "is_suspect":         bool(transaction.get("is_suspect", False)),
                "created_at":         transaction.get("created_at"),
            })
        return True
    except Exception as e:
        print(f"  ❌ Insertion échouée : {e}")
        return False


def mettre_a_jour_score(
    transaction_id: str,
    resultat: dict,
    engine
):
    """Met à jour le score AML de la transaction."""
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE transactions
            SET score_aml  = :score,
                risk_level = :niveau,
                statut     = CASE
                    WHEN :decision = 'BLOCKED' THEN 'BLOCKED'
                    WHEN :decision = 'PENDING' THEN 'PENDING'
                    ELSE 'SUCCESS'
                END,
                updated_at = NOW()
            WHERE transaction_id = :tid
        """), {
            "score":    int(resultat["score"]),
            "niveau":   resultat["niveau"],
            "decision": resultat["decision"],
            "tid":      transaction_id,
        })


def creer_alerte_streaming(
    transaction_id: str,
    resultat: dict,
    engine
):
    """Crée une alerte pour les transactions HIGH et CRITICAL."""
    if resultat["niveau"] not in ["HIGH", "CRITICAL"]:
        return

    import json
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO alerts
                (transaction_id, score, risk_level, flags, statut)
            VALUES
                (:tid, :score, :niveau, :flags, 'OPEN')
        """), {
            "tid":    transaction_id,
            "score":  int(resultat["score"]),
            "niveau": resultat["niveau"],
            "flags":  json.dumps(resultat["flags"]),
        })


# =============================================================
# TRAITEMENT D'UN MESSAGE
# =============================================================

def traiter_message(transaction: dict, engine):
    """
    Pipeline complet pour une transaction :
    1. Insérer en base
    2. Enrichir avec les stats user
    3. Scorer avec le moteur AML
    4. Mettre à jour le score en base
    5. Créer alerte si nécessaire
    6. Audit log
    """
    tid = transaction["transaction_id"]
    montant = float(transaction.get("montant", 0))

    # Étape 1 — Insertion
    if not inserer_transaction(transaction, engine):
        return

    # Étape 2 — Enrichissement
    row = enrichir_transaction(transaction, engine)

    # Étape 3 — Scoring AML
    resultat = scorer_transaction(row)

    # Étape 4 — Mise à jour score
    mettre_a_jour_score(tid, resultat, engine)

    # Étape 5 — Alerte si HIGH/CRITICAL
    creer_alerte_streaming(tid, resultat, engine)

    # Étape 6 — Audit logs
    log_transaction_created(engine, transaction)
    log_aml_score(engine, tid, resultat)

    if resultat["decision"] == "BLOCKED":
        log_transaction_bloquee(
            engine, tid,
            resultat["score"],
            resultat["flags"]
        )

    if montant > 1_000_000:
        log_declaration_centif(
            engine, tid, montant,
            "Montant dépasse seuil UEMOA"
        )

    # Affichage temps réel
    niveau_icon = {
        "LOW":      "🟢",
        "MEDIUM":   "🟡",
        "HIGH":     "🟠",
        "CRITICAL": "🔴",
    }.get(resultat["niveau"], "⚪")

    print(
        f"  {niveau_icon} [{resultat['niveau']:<8}] "
        f"Score: {resultat['score']:3d}  "
        f"{montant:>12,.0f} FCFA  "
        f"{transaction.get('type_transaction', ''):<20} "
        f"{'🚨 SUSPECT' if transaction.get('is_suspect') else ''}"
    )


# =============================================================
# POINT D'ENTRÉE
# =============================================================

def lancer_consumer():
    print("=" * 50)
    print("  FINTRACK — Kafka Consumer")
    print("=" * 50)
    print(f"\n  Broker : {KAFKA_BROKER}")
    print(f"  Topic  : {KAFKA_TOPIC}")
    print(f"  Group  : {KAFKA_GROUP}")
    print("\n  En attente de messages...\n")
    print("-" * 50)

    engine   = get_engine()
    consumer = creer_consumer()

    traites = 0
    try:
        for message in consumer:
            transaction = message.value
            traiter_message(transaction, engine)
            traites += 1

    except KeyboardInterrupt:
        print(f"\n\n  Consumer arrêté manuellement")
        print(f"  Transactions traitées : {traites}")

    finally:
        consumer.close()
        print("=" * 50)


if __name__ == "__main__":
    lancer_consumer()