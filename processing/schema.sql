-- Nettoyage complet
DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS alerts CASCADE;
DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP FUNCTION IF EXISTS update_timestamp CASCADE;

-- Trigger function
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Table users
CREATE TABLE users (
    user_id         UUID PRIMARY KEY,
    nom             VARCHAR(100),
    prenom          VARCHAR(100),
    telephone       VARCHAR(20),
    pays            VARCHAR(50),
    operateur       VARCHAR(50),
    sim_swap_recent BOOLEAN DEFAULT FALSE,
    sim_swap_at     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE TRIGGER trg_update_users
BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE INDEX idx_users_pays      ON users(pays);
CREATE INDEX idx_users_operateur ON users(operateur);

-- Table transactions
CREATE TABLE transactions (
    transaction_id      UUID PRIMARY KEY,
    user_id             UUID REFERENCES users(user_id) ON DELETE SET NULL,
    receiver_id         UUID REFERENCES users(user_id) ON DELETE SET NULL,
    type_transaction    VARCHAR(30),
    montant             NUMERIC(15, 2),
    devise              VARCHAR(10) DEFAULT 'FCFA',
    operateur           VARCHAR(50),
    pays_emetteur       VARCHAR(50),
    pays_recepteur      VARCHAR(50),
    telephone_emetteur  VARCHAR(20),
    telephone_recepteur VARCHAR(20),
    sim_swap_recent     BOOLEAN DEFAULT FALSE,
    statut              VARCHAR(20) DEFAULT 'PENDING'
                        CONSTRAINT check_statut
                        CHECK (statut IN ('SUCCESS','FAILED','PENDING','REVERSED','BLOCKED')),
    risk_level          VARCHAR(10)
                        CONSTRAINT check_risk_level
                        CHECK (risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL') OR risk_level IS NULL),
    score_aml           INTEGER
                        CONSTRAINT check_score_aml
                        CHECK (score_aml BETWEEN 0 AND 100 OR score_aml IS NULL),
    is_suspect          BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);

CREATE TRIGGER trg_update_transactions
BEFORE UPDATE ON transactions
FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE INDEX idx_transactions_created_at     ON transactions(created_at);
CREATE INDEX idx_transactions_user_id        ON transactions(user_id);
CREATE INDEX idx_transactions_statut         ON transactions(statut);
CREATE INDEX idx_transactions_score_aml      ON transactions(score_aml);
CREATE INDEX idx_transactions_type           ON transactions(type_transaction);
CREATE INDEX idx_transactions_pays_emetteur  ON transactions(pays_emetteur);
CREATE INDEX idx_transactions_user_created   ON transactions(user_id, created_at DESC);

-- Table alerts
CREATE TABLE alerts (
    alert_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id  UUID REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    score           INTEGER NOT NULL CONSTRAINT check_alert_score CHECK (score BETWEEN 0 AND 100),
    risk_level      VARCHAR(10) NOT NULL CONSTRAINT check_alert_risk CHECK (risk_level IN ('MEDIUM','HIGH','CRITICAL')),
    flags           JSONB,
    statut          VARCHAR(20) DEFAULT 'OPEN'
                    CONSTRAINT check_alert_statut
                    CHECK (statut IN ('OPEN','REVIEWED','CLOSED','REPORTED')),
    reviewed_by     VARCHAR(100),
    reviewed_at     TIMESTAMP,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_alerts_transaction_id ON alerts(transaction_id);
CREATE INDEX idx_alerts_statut         ON alerts(statut);
CREATE INDEX idx_alerts_risk_level     ON alerts(risk_level);
CREATE INDEX idx_alerts_created_at     ON alerts(created_at);

-- Table audit_logs
CREATE TABLE audit_logs (
    log_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type  VARCHAR(50) NOT NULL,
    entity_id   UUID,
    details     JSONB,
    level       VARCHAR(10) DEFAULT 'INFO'
                CONSTRAINT check_log_level
                CHECK (level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')),
    created_at  TIMESTAMP DEFAULT NOW()
);

REVOKE UPDATE, DELETE ON audit_logs FROM PUBLIC;

CREATE RULE no_update_audit AS ON UPDATE TO audit_logs DO INSTEAD NOTHING;
CREATE RULE no_delete_audit AS ON DELETE TO audit_logs DO INSTEAD NOTHING;

CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at);
CREATE INDEX idx_audit_logs_event_type ON audit_logs(event_type);
CREATE INDEX idx_audit_logs_entity_id  ON audit_logs(entity_id);

-- Vues
CREATE OR REPLACE VIEW transactions_summary AS
SELECT
    DATE(created_at)    AS jour,
    type_transaction,
    pays_emetteur,
    statut,
    COUNT(*)            AS nb_transactions,
    SUM(montant)        AS montant_total,
    AVG(montant)        AS montant_moyen,
    COUNT(*) FILTER (WHERE is_suspect = TRUE) AS nb_suspects
FROM transactions
GROUP BY DATE(created_at), type_transaction, pays_emetteur, statut;

CREATE OR REPLACE VIEW alerts_open AS
SELECT
    a.alert_id,
    a.score,
    a.risk_level,
    a.flags,
    a.created_at,
    t.transaction_id,
    t.montant,
    t.type_transaction,
    t.pays_emetteur,
    t.telephone_emetteur,
    t.created_at AS transaction_date
FROM alerts a
JOIN transactions t ON a.transaction_id = t.transaction_id
WHERE a.statut = 'OPEN'
ORDER BY a.score DESC;