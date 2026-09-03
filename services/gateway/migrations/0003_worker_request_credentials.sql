BEGIN;

CREATE DOMAIN cadplot_gateway.worker_request_route AS text
    CHECK (
        VALUE IN (
            '/worker/v1/tasks/poll',
            '/worker/v1/tasks/start',
            '/worker/v1/tasks/complete'
        )
    );

CREATE TABLE cadplot_gateway.worker_request_nonces (
    tenant_id cadplot_gateway.tenant_id NOT NULL,
    workstation_id cadplot_gateway.workstation_id NOT NULL,
    key_id cadplot_gateway.worker_key_id NOT NULL,
    nonce cadplot_gateway.worker_nonce NOT NULL,
    route cadplot_gateway.worker_request_route NOT NULL,
    body_sha256 cadplot_gateway.sha256_hex NOT NULL,
    issued_at timestamptz NOT NULL,
    consumed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, workstation_id, key_id, nonce),
    CONSTRAINT worker_request_nonce_verification_key_fk
        FOREIGN KEY (tenant_id, workstation_id, key_id)
        REFERENCES cadplot_gateway.worker_verification_keys (
            tenant_id, workstation_id, key_id
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT worker_request_nonce_timestamps_finite CHECK (
        pg_catalog.isfinite(issued_at) AND pg_catalog.isfinite(consumed_at)
    ),
    -- Keep the durable boundary aligned with WorkerRequestAuthenticator's production defaults.
    CONSTRAINT worker_request_nonce_timestamp_window CHECK (
        issued_at >= consumed_at - interval '1 minute'
        AND issued_at <= consumed_at + interval '30 seconds'
    )
);

CREATE INDEX worker_request_nonces_consumed_at_idx
    ON cadplot_gateway.worker_request_nonces (tenant_id, consumed_at);

CREATE FUNCTION cadplot_gateway.consume_worker_request_nonce(
    selected_tenant_id cadplot_gateway.tenant_id,
    selected_workstation_id cadplot_gateway.workstation_id,
    selected_key_id cadplot_gateway.worker_key_id,
    selected_nonce cadplot_gateway.worker_nonce,
    selected_route cadplot_gateway.worker_request_route,
    selected_body_sha256 cadplot_gateway.sha256_hex,
    selected_issued_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $consume_worker_request_nonce$
DECLARE
    accepted boolean := false;
    database_time timestamptz;
    selected_workstation_enabled boolean;
    selected_key_enabled boolean;
    selected_key_algorithm text;
    selected_key_length integer;
    selected_key_not_before timestamptz;
    selected_key_expires_at timestamptz;
    selected_key_revoked_at timestamptz;
BEGIN
    IF selected_tenant_id IS NULL
       OR selected_workstation_id IS NULL
       OR selected_key_id IS NULL
       OR selected_nonce IS NULL
       OR selected_route IS NULL
       OR selected_body_sha256 IS NULL
       OR selected_issued_at IS NULL
       OR NOT pg_catalog.isfinite(selected_issued_at)
       OR selected_tenant_id::text IS DISTINCT FROM
          NULLIF(current_setting('cadplot.tenant_id', true), '') THEN
        RETURN false;
    END IF;

    -- Lock the exact credential and workstation before taking the decision timestamp. This
    -- closes replacement, revocation, expiry, and disable races after signature verification.
    SELECT
        workstation.enabled,
        verification_key.enabled,
        verification_key.algorithm,
        pg_catalog.octet_length(verification_key.public_key),
        verification_key.not_before,
        verification_key.expires_at,
        verification_key.revoked_at
    INTO
        selected_workstation_enabled,
        selected_key_enabled,
        selected_key_algorithm,
        selected_key_length,
        selected_key_not_before,
        selected_key_expires_at,
        selected_key_revoked_at
    FROM cadplot_gateway.worker_verification_keys AS verification_key
    JOIN cadplot_gateway.workstations AS workstation
      ON workstation.tenant_id = verification_key.tenant_id
     AND workstation.workstation_id = verification_key.workstation_id
    WHERE verification_key.tenant_id = selected_tenant_id
      AND verification_key.workstation_id = selected_workstation_id
      AND verification_key.key_id = selected_key_id
      AND workstation.tenant_id = selected_tenant_id
      AND workstation.workstation_id = selected_workstation_id
    FOR SHARE OF verification_key, workstation;

    IF NOT FOUND THEN
        RETURN false;
    END IF;

    database_time := clock_timestamp();
    IF selected_workstation_enabled IS DISTINCT FROM true
       OR selected_key_enabled IS DISTINCT FROM true
       OR selected_key_algorithm IS DISTINCT FROM 'ed25519'
       OR selected_key_length IS DISTINCT FROM 32
       OR selected_key_revoked_at IS NOT NULL
       OR selected_key_not_before > selected_issued_at
       OR selected_key_not_before > database_time
       OR (
           selected_key_expires_at IS NOT NULL
           AND (
               selected_key_expires_at <= selected_issued_at
               OR selected_key_expires_at <= database_time
           )
       )
       OR selected_issued_at < database_time - interval '1 minute'
       OR selected_issued_at > database_time + interval '30 seconds' THEN
        RETURN false;
    END IF;

    INSERT INTO cadplot_gateway.worker_request_nonces (
        tenant_id,
        workstation_id,
        key_id,
        nonce,
        route,
        body_sha256,
        issued_at,
        consumed_at
    )
    VALUES (
        selected_tenant_id,
        selected_workstation_id,
        selected_key_id,
        selected_nonce,
        selected_route,
        selected_body_sha256,
        selected_issued_at,
        database_time
    )
    ON CONFLICT (tenant_id, workstation_id, key_id, nonce) DO NOTHING
    RETURNING true INTO accepted;

    RETURN COALESCE(accepted, false);
END;
$consume_worker_request_nonce$;

CREATE FUNCTION cadplot_gateway.reject_worker_verification_key_unsafe_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $worker_verification_key_immutable$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.workstation_id IS DISTINCT FROM OLD.workstation_id
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.algorithm IS DISTINCT FROM OLD.algorithm
       OR NEW.public_key IS DISTINCT FROM OLD.public_key
       OR NEW.not_before IS DISTINCT FROM OLD.not_before
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR (
           OLD.revoked_at IS NOT NULL
           AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'worker_verification_key_unsafe_mutation';
    END IF;
    RETURN NEW;
END;
$worker_verification_key_immutable$;

CREATE TRIGGER worker_verification_key_unsafe_mutation
BEFORE UPDATE ON cadplot_gateway.worker_verification_keys
FOR EACH ROW
EXECUTE FUNCTION cadplot_gateway.reject_worker_verification_key_unsafe_mutation();

ALTER TABLE cadplot_gateway.worker_request_nonces ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.worker_request_nonces FORCE ROW LEVEL SECURITY;

CREATE POLICY worker_request_nonces_tenant_isolation
ON cadplot_gateway.worker_request_nonces
FOR ALL
USING (
    tenant_id::text = NULLIF(current_setting('cadplot.tenant_id', true), '')
)
WITH CHECK (
    tenant_id::text = NULLIF(current_setting('cadplot.tenant_id', true), '')
);

REVOKE ALL PRIVILEGES ON TABLE cadplot_gateway.worker_request_nonces
FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE cadplot_gateway.worker_request_nonces
FROM cadplot_gateway_runtime;
REVOKE ALL ON FUNCTION cadplot_gateway.consume_worker_request_nonce(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_key_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.worker_request_route,
    cadplot_gateway.sha256_hex,
    timestamptz
) FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.consume_worker_request_nonce(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_key_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.worker_request_route,
    cadplot_gateway.sha256_hex,
    timestamptz
) FROM cadplot_gateway_runtime;
REVOKE ALL ON FUNCTION cadplot_gateway.reject_worker_verification_key_unsafe_mutation()
FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.reject_worker_verification_key_unsafe_mutation()
FROM cadplot_gateway_runtime;
GRANT EXECUTE ON FUNCTION cadplot_gateway.consume_worker_request_nonce(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_key_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.worker_request_route,
    cadplot_gateway.sha256_hex,
    timestamptz
) TO cadplot_gateway_runtime;

-- cadplot_gateway_runtime intentionally receives no direct nonce-table privilege.
-- Retention and credential enrollment/revocation remain control-plane-only operations.

COMMIT;
