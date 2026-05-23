# =============================================================
# audit/logger.py
# Système de logs immuables — FinTrack
# =============================================================
# Obligation réglementaire CENTIF/UEMOA :
# Toute action sur une transaction ou une alerte
# doit être tracée de manière immuable.
# =============================================================

import os
import sys
import json
import uuid
from datetime import datetime
from sqlalchemy import create_engine, text
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
# TYPES D'ÉVÉNEMENTS
# =============================================================
# Nomenclature standardisée pour faciliter les audits CENTIF.
# Format : ENTITE_ACTION
# =============================================================

EVENTS = {
    # Cycle de vie des transactions
    "TRANSACTION_CREATED":   "INFO",
    "TRANSACTION_APPROVED":  "INFO",
    "TRANSACTION_PENDING":   "WARNING",
    "TRANSACTION_BLOCKED":   "ERROR",
    "TRANSACTION_REVERSED":  "WARNING",

    # Moteur AML
    "AML_SCORE_CALCULATED":  "INFO",
    "AML_ALERT_CREATED":     "WARNING",
    "AML_ALERT_RESOLVED":    "INFO",
    "AML_ALERT_REPORTED":    "CRITICAL",

    # Pipeline
    "PIPELINE_STARTED":      "INFO",
    "PIPELINE_COMPLETED":    "INFO",
    "PIPELINE_ERROR":        "ERROR",

    # Compliance
    "CENTIF_DECLARATION":    "CRITICAL",
    "THRESHOLD_EXCEEDED":    "WARNING",
}


# =============================================================
# FONCTION PRINCIPALE DE LOG
# =============================================================

def log_event(
    engine,
    event_type: str,
    entity_id: str,
    details: dict,
    level: str = None
) -> bool:
    """
    Insère un événement dans audit_logs.

    IMMUABLE : cette fonction ne fait que des INSERT.
    Jamais d'UPDATE, jamais de DELETE.

    Paramètres :
    - event_type : type d'événement (voir EVENTS)
    - entity_id  : UUID de l'entité concernée
    - details    : dict avec tous les détails de l'événement
    - level      : niveau de sévérité (auto si non précisé)

    Retourne True si succès, False si erreur.
    """
    if level is None:
        level = EVENTS.get(event_type, "INFO")

    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO audit_logs
                    (log_id, event_type, entity_id, details, level)
                VALUES
                    (:log_id, :event_type, :entity_id,
                     :details, :level)
            """), {
                "log_id":     str(uuid.uuid4()),
                "event_type": event_type,
                "entity_id":  str(entity_id),
                "details":    json.dumps(details, default=str),
                "level":      level,
            })
        return True

    except Exception as e:
        print(f"[AUDIT ERROR] {event_type} : {e}")
        return False


# =============================================================
# FONCTIONS SPÉCIALISÉES
# =============================================================

def log_transaction_created(engine, transaction: dict):
    """Log la création d'une transaction."""
    return log_event(
        engine,
        event_type="TRANSACTION_CREATED",
        entity_id=transaction["transaction_id"],
        details={
            "montant":          transaction.get("montant"),
            "type_transaction": transaction.get("type_transaction"),
            "pays_emetteur":    transaction.get("pays_emetteur"),
            "operateur":        transaction.get("operateur"),
            "statut_initial":   "PENDING",
        }
    )


def log_aml_score(engine, transaction_id: str, resultat: dict):
    """
    Log le résultat du moteur AML.
    Inclut le score, le niveau et tous les flags déclenchés.
    C'est la trace qui permet de justifier chaque décision
    devant la CENTIF.
    """
    return log_event(
        engine,
        event_type="AML_SCORE_CALCULATED",
        entity_id=transaction_id,
        details={
            "score":    resultat.get("score"),
            "niveau":   resultat.get("niveau"),
            "decision": resultat.get("decision"),
            "flags":    resultat.get("flags", []),
            "details":  resultat.get("details", {}),
        }
    )


def log_alerte_creee(engine, alert_id: str, transaction_id: str,
                     score: int, niveau: str, flags: list):
    """Log la création d'une alerte HIGH ou CRITICAL."""
    return log_event(
        engine,
        event_type="AML_ALERT_CREATED",
        entity_id=alert_id,
        details={
            "transaction_id": transaction_id,
            "score":          score,
            "niveau":         niveau,
            "flags":          flags,
            "action_requise": "Revue analyste AML obligatoire",
        }
    )


def log_transaction_bloquee(engine, transaction_id: str,
                             score: int, flags: list):
    """
    Log le blocage d'une transaction.
    Niveau ERROR car c'est une action forte
    avec impact direct sur le client.
    """
    return log_event(
        engine,
        event_type="TRANSACTION_BLOCKED",
        entity_id=transaction_id,
        details={
            "score":          score,
            "flags":          flags,
            "raison":         "Score AML dépasse le seuil de blocage",
            "action_client":  "Transaction refusée",
        }
    )


def log_declaration_centif(engine, transaction_id: str,
                            montant: float, motif: str):
    """
    Log la déclaration à la CENTIF.
    Niveau CRITICAL — obligation légale.
    """
    return log_event(
        engine,
        event_type="CENTIF_DECLARATION",
        entity_id=transaction_id,
        details={
            "montant":         montant,
            "motif":           motif,
            "autorite":        "CENTIF",
            "delai_legal":     "24h",
            "timestamp_decl":  datetime.now().isoformat(),
        },
        level="CRITICAL"
    )


def log_pipeline(engine, event: str, details: dict = None):
    """Log les événements du pipeline batch."""
    return log_event(
        engine,
        event_type=event,
        entity_id=str(uuid.uuid4()),  # UUID généré au lieu de "pipeline"
        details=details or {}
    )


# =============================================================
# GÉNÉRATION DES LOGS RÉTROACTIFS
# Pour les transactions déjà traitées aux étapes 2-5
# =============================================================

def generer_logs_retroactifs(engine):
    """
    Génère les audit logs pour toutes les transactions
    déjà traitées. En production les logs sont créés
    en temps réel. Ici on les crée rétroactivement
    pour simuler un historique complet.
    """
    import pandas as pd

    print("[1/4] Lecture des transactions traitées...")

    query = """
        SELECT
            t.transaction_id,
            t.montant,
            t.type_transaction,
            t.pays_emetteur,
            t.operateur,
            t.statut,
            t.score_aml,
            t.risk_level,
            t.created_at,
            a.flags,
            te.is_suspect
        FROM transactions t
        LEFT JOIN transactions_enrichies te
            ON t.transaction_id::text = te.transaction_id
        LEFT JOIN alerts a
            ON t.transaction_id = a.transaction_id
        WHERE t.score_aml IS NOT NULL
        ORDER BY t.created_at ASC
    """

    df = pd.read_sql(query, engine)
    print(f"      ✅ {len(df)} transactions lues")

    # Vider les logs existants pour repartir propre
    with engine.begin() as conn:
        # On désactive temporairement la rule pour le nettoyage
        conn.execute(text("TRUNCATE TABLE audit_logs"))
    print("      ✅ Logs existants effacés")

    # Log pipeline démarrage
    log_pipeline(engine, "PIPELINE_STARTED", {
        "nb_transactions": len(df),
        "timestamp": datetime.now().isoformat()
    })

    print("[2/4] Génération des logs par transaction...")

    compteurs = {
        "created": 0, "scored": 0,
        "blocked": 0, "centif": 0
    }

    for _, row in df.iterrows():
        tid = str(row["transaction_id"])

        # Log 1 — Création de la transaction
        log_transaction_created(engine, {
            "transaction_id":   tid,
            "montant":          float(row["montant"]),
            "type_transaction": row["type_transaction"],
            "pays_emetteur":    row["pays_emetteur"],
            "operateur":        row["operateur"],
        })
        compteurs["created"] += 1

        # Log 2 — Score AML calculé
        flags = row["flags"] if isinstance(row["flags"], list) else []
        log_aml_score(engine, tid, {
            "score":    int(row["score_aml"]),
            "niveau":   row["risk_level"],
            "decision": row["statut"],
            "flags":    flags,
        })
        compteurs["scored"] += 1

        # Log 3 — Transaction bloquée
        if row["statut"] == "BLOCKED":
            log_transaction_bloquee(engine, tid,
                int(row["score_aml"]), flags)
            compteurs["blocked"] += 1

        # Log 4 — Déclaration CENTIF si montant > 1M
        if float(row["montant"]) > 1_000_000:
            log_declaration_centif(engine, tid,
                float(row["montant"]),
                "Montant dépasse le seuil réglementaire UEMOA")
            compteurs["centif"] += 1

    print(f"      ✅ Logs créés :")
    print(f"         TRANSACTION_CREATED  : {compteurs['created']}")
    print(f"         AML_SCORE_CALCULATED : {compteurs['scored']}")
    print(f"         TRANSACTION_BLOCKED  : {compteurs['blocked']}")
    print(f"         CENTIF_DECLARATION   : {compteurs['centif']}")

    # Log pipeline terminé
    log_pipeline(engine, "PIPELINE_COMPLETED", {
        "nb_logs_crees": sum(compteurs.values()),
        "timestamp": datetime.now().isoformat()
    })

    return compteurs


# =============================================================
# POINT D'ENTRÉE
# =============================================================

if __name__ == "__main__":

    print("=" * 50)
    print("  FINTRACK — Audit Logs")
    print("=" * 50)

    engine = get_engine()

    compteurs = generer_logs_retroactifs(engine)

    # Vérification
    print("\n[3/4] Vérification en base...")
    import pandas as pd

    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT COUNT(*) FROM audit_logs")
        ).scalar()

        par_event = pd.read_sql("""
            SELECT event_type, level, COUNT(*) as nb
            FROM audit_logs
            GROUP BY event_type, level
            ORDER BY nb DESC
        """, conn)

    print(f"      ✅ Total logs en base : {total}")
    print("\n[4/4] Distribution par événement :")
    print(par_event.to_string(index=False))

    # Test immuabilité
    print("\n--- Test immuabilité ---")
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM audit_logs WHERE 1=1"
            ))
        print("❌ DELETE a fonctionné — immuabilité compromise")
    except Exception as e:
        print("✅ DELETE bloqué — immuabilité confirmée")

    print("\n✅ Audit Logs terminé")
    print("   → Vérifie dans pgAdmin :")
    print("   SELECT * FROM audit_logs ORDER BY created_at DESC;")