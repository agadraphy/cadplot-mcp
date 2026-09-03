BEGIN;

CREATE DOMAIN cadplot_gateway.task_id AS text
    CHECK (VALUE ~ '^tsk_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.command_id AS text
    CHECK (VALUE ~ '^cmd_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.worker_nonce AS text
    CHECK (VALUE ~ '^[A-Za-z0-9_-]{22,86}$');
CREATE DOMAIN cadplot_gateway.worker_key_id AS text
    CHECK (VALUE ~ '^wkey_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');

ALTER TABLE cadplot_gateway.operations
    ADD CONSTRAINT operations_worker_dispatch_identity_unique
    UNIQUE (tenant_id, operation_id, workstation_id, owner_subject_id, idempotency_key);

CREATE TABLE cadplot_gateway.worker_dispatches (
    tenant_id cadplot_gateway.tenant_id NOT NULL,
    workstation_id cadplot_gateway.workstation_id NOT NULL,
    task_id cadplot_gateway.task_id NOT NULL,
    operation_id cadplot_gateway.operation_id NOT NULL,
    user_id cadplot_gateway.subject_id NOT NULL,
    command_id cadplot_gateway.command_id NOT NULL,
    idempotency_key cadplot_gateway.idempotency_key NOT NULL,
    nonce cadplot_gateway.worker_nonce NOT NULL,
    policy_version integer NOT NULL CHECK (policy_version > 0),
    dispatch_sha256 cadplot_gateway.sha256_hex NOT NULL,
    issued_at timestamptz NOT NULL,
    dispatch_expires_at timestamptz NOT NULL,
    operation_expires_at timestamptz NOT NULL,
    envelope jsonb NOT NULL,
    correlation jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, workstation_id, task_id, operation_id),
    CONSTRAINT worker_dispatch_replay_identity_unique
        UNIQUE (tenant_id, workstation_id, task_id, command_id, nonce),
    CONSTRAINT worker_dispatch_operation_fk
        FOREIGN KEY (
            tenant_id, operation_id, workstation_id, user_id, idempotency_key
        )
        REFERENCES cadplot_gateway.operations (
            tenant_id, operation_id, workstation_id, owner_subject_id, idempotency_key
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT worker_dispatch_deadline_order CHECK (
        issued_at < dispatch_expires_at
        AND dispatch_expires_at <= operation_expires_at
    ),
    CONSTRAINT worker_dispatch_json_objects CHECK (
        jsonb_typeof(envelope) = 'object'
        AND jsonb_typeof(correlation) = 'object'
    ),
    CONSTRAINT worker_dispatch_json_identity CHECK (
        envelope->>'tenant_id' = tenant_id::text
        AND envelope->>'user_id' = user_id::text
        AND envelope->>'device_id' = workstation_id::text
        AND envelope->>'task_id' = task_id::text
        AND envelope->>'operation_id' = operation_id::text
        AND envelope->>'command_id' = command_id::text
        AND envelope->>'idempotency_key' = idempotency_key::text
        AND envelope->>'nonce' = nonce::text
        AND (envelope->>'policy_version')::integer = policy_version
        AND envelope->>'signature_algorithm' = 'ed25519'
        AND (envelope->>'issued_at')::timestamptz = issued_at
        AND (envelope->>'expires_at')::timestamptz = dispatch_expires_at
        AND correlation->>'tenant_id' = tenant_id::text
        AND correlation->>'user_id' = user_id::text
        AND correlation->>'device_id' = workstation_id::text
        AND correlation->>'task_id' = task_id::text
        AND correlation->>'operation_id' = operation_id::text
        AND correlation->>'command_id' = command_id::text
        AND correlation->>'idempotency_key' = idempotency_key::text
        AND correlation->>'nonce' = nonce::text
        AND (correlation->>'policy_version')::integer = policy_version
        AND correlation->>'dispatch_sha256' = dispatch_sha256::text
        AND (correlation->>'issued_at')::timestamptz = issued_at
        AND (correlation->>'dispatch_expires_at')::timestamptz = dispatch_expires_at
        AND (correlation->>'operation_expires_at')::timestamptz = operation_expires_at
        AND envelope->'command' = correlation->'command'
    )
);

CREATE TABLE cadplot_gateway.worker_result_replays (
    tenant_id cadplot_gateway.tenant_id NOT NULL,
    workstation_id cadplot_gateway.workstation_id NOT NULL,
    nonce cadplot_gateway.worker_nonce NOT NULL,
    task_id cadplot_gateway.task_id NOT NULL,
    command_id cadplot_gateway.command_id NOT NULL,
    result_sha256 cadplot_gateway.sha256_hex NOT NULL,
    completed_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    application_id uuid,
    application_lease_expires_at timestamptz,
    applied_at timestamptz,
    PRIMARY KEY (tenant_id, workstation_id, nonce),
    CONSTRAINT worker_result_replay_timestamps_finite CHECK (
        pg_catalog.isfinite(completed_at)
        AND pg_catalog.isfinite(recorded_at)
        AND (
            application_lease_expires_at IS NULL
            OR pg_catalog.isfinite(application_lease_expires_at)
        )
        AND (applied_at IS NULL OR pg_catalog.isfinite(applied_at))
    ),
    CONSTRAINT worker_result_replay_application_shape CHECK (
        (application_id IS NULL) = (application_lease_expires_at IS NULL)
        AND (applied_at IS NULL OR application_id IS NOT NULL)
    ),
    CONSTRAINT worker_result_replay_dispatch_fk
        FOREIGN KEY (tenant_id, workstation_id, task_id, command_id, nonce)
        REFERENCES cadplot_gateway.worker_dispatches (
            tenant_id, workstation_id, task_id, command_id, nonce
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
);

CREATE TABLE cadplot_gateway.worker_verification_keys (
    tenant_id cadplot_gateway.tenant_id NOT NULL,
    workstation_id cadplot_gateway.workstation_id NOT NULL,
    key_id cadplot_gateway.worker_key_id NOT NULL,
    algorithm text NOT NULL DEFAULT 'ed25519' CHECK (algorithm = 'ed25519'),
    public_key bytea NOT NULL CHECK (octet_length(public_key) = 32),
    enabled boolean NOT NULL DEFAULT true,
    not_before timestamptz NOT NULL,
    expires_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, workstation_id, key_id),
    CONSTRAINT worker_verification_key_workstation_fk
        FOREIGN KEY (tenant_id, workstation_id)
        REFERENCES cadplot_gateway.workstations (tenant_id, workstation_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT worker_verification_key_lifetime CHECK (
        (expires_at IS NULL OR expires_at > not_before)
        AND (revoked_at IS NULL OR revoked_at >= not_before)
    )
);

CREATE FUNCTION cadplot_gateway.reject_worker_dispatch_identity_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $worker_dispatch_immutable$
BEGIN
    IF ROW(
        NEW.tenant_id, NEW.workstation_id, NEW.task_id, NEW.operation_id,
        NEW.user_id, NEW.command_id, NEW.idempotency_key, NEW.nonce,
        NEW.policy_version, NEW.dispatch_sha256, NEW.issued_at,
        NEW.dispatch_expires_at, NEW.operation_expires_at,
        NEW.envelope, NEW.correlation
    ) IS DISTINCT FROM ROW(
        OLD.tenant_id, OLD.workstation_id, OLD.task_id, OLD.operation_id,
        OLD.user_id, OLD.command_id, OLD.idempotency_key, OLD.nonce,
        OLD.policy_version, OLD.dispatch_sha256, OLD.issued_at,
        OLD.dispatch_expires_at, OLD.operation_expires_at,
        OLD.envelope, OLD.correlation
    ) THEN
        RAISE EXCEPTION 'worker dispatch correlation identity is immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$worker_dispatch_immutable$;

CREATE TRIGGER worker_dispatch_identity_immutable
BEFORE UPDATE ON cadplot_gateway.worker_dispatches
FOR EACH ROW
EXECUTE FUNCTION cadplot_gateway.reject_worker_dispatch_identity_mutation();

CREATE FUNCTION cadplot_gateway.reject_worker_replay_identity_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $worker_replay_immutable$
BEGIN
    IF ROW(
        NEW.tenant_id, NEW.workstation_id, NEW.nonce, NEW.task_id,
        NEW.command_id, NEW.result_sha256, NEW.completed_at
    ) IS DISTINCT FROM ROW(
        OLD.tenant_id, OLD.workstation_id, OLD.nonce, OLD.task_id,
        OLD.command_id, OLD.result_sha256, OLD.completed_at
    ) THEN
        RAISE EXCEPTION 'worker signed-result identity and hash are immutable'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.applied_at IS NOT NULL
       AND ROW(
           NEW.application_id,
           NEW.application_lease_expires_at,
           NEW.applied_at
       ) IS DISTINCT FROM ROW(
           OLD.application_id,
           OLD.application_lease_expires_at,
           OLD.applied_at
       ) THEN
        RAISE EXCEPTION 'worker result application marker is immutable once set'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$worker_replay_immutable$;

CREATE TRIGGER worker_replay_identity_immutable
BEFORE UPDATE ON cadplot_gateway.worker_result_replays
FOR EACH ROW
EXECUTE FUNCTION cadplot_gateway.reject_worker_replay_identity_mutation();

CREATE FUNCTION cadplot_gateway.classify_worker_result_replay(
    selected_tenant_id cadplot_gateway.tenant_id,
    selected_workstation_id cadplot_gateway.workstation_id,
    selected_nonce cadplot_gateway.worker_nonce,
    selected_task_id cadplot_gateway.task_id,
    selected_command_id cadplot_gateway.command_id,
    selected_result_sha256 cadplot_gateway.sha256_hex,
    selected_completed_at timestamptz
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $classify_worker_result_replay$
DECLARE
    existing_task_id cadplot_gateway.task_id;
    existing_command_id cadplot_gateway.command_id;
    existing_result_sha256 cadplot_gateway.sha256_hex;
    existing_completed_at timestamptz;
BEGIN
    IF selected_tenant_id IS NULL
       OR selected_workstation_id IS NULL
       OR selected_nonce IS NULL
       OR selected_task_id IS NULL
       OR selected_command_id IS NULL
       OR selected_result_sha256 IS NULL
       OR selected_completed_at IS NULL
       OR NOT pg_catalog.isfinite(selected_completed_at)
       OR selected_tenant_id::text IS DISTINCT FROM
          NULLIF(current_setting('cadplot.tenant_id', true), '') THEN
        RETURN 'conflict';
    END IF;

    SELECT replay.task_id, replay.command_id, replay.result_sha256, replay.completed_at
    INTO
        existing_task_id,
        existing_command_id,
        existing_result_sha256,
        existing_completed_at
    FROM cadplot_gateway.worker_result_replays AS replay
    WHERE replay.tenant_id = selected_tenant_id
      AND replay.workstation_id = selected_workstation_id
      AND replay.nonce = selected_nonce;

    IF NOT FOUND THEN
        RETURN 'unseen';
    END IF;
    IF ROW(
        existing_task_id,
        existing_command_id,
        existing_result_sha256,
        existing_completed_at
    ) IS NOT DISTINCT FROM ROW(
        selected_task_id,
        selected_command_id,
        selected_result_sha256,
        selected_completed_at
    ) THEN
        RETURN 'exact_match';
    END IF;
    RETURN 'conflict';
END;
$classify_worker_result_replay$;

CREATE FUNCTION cadplot_gateway.claim_worker_result_replay(
    selected_tenant_id cadplot_gateway.tenant_id,
    selected_workstation_id cadplot_gateway.workstation_id,
    selected_nonce cadplot_gateway.worker_nonce,
    selected_task_id cadplot_gateway.task_id,
    selected_command_id cadplot_gateway.command_id,
    selected_result_sha256 cadplot_gateway.sha256_hex,
    selected_completed_at timestamptz,
    selected_claim_expires_at timestamptz
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $claim_worker_result_replay$
DECLARE
    inserted boolean := false;
    existing_task_id cadplot_gateway.task_id;
    existing_command_id cadplot_gateway.command_id;
    existing_result_sha256 cadplot_gateway.sha256_hex;
    existing_completed_at timestamptz;
    dispatch_issued_at timestamptz;
    dispatch_operation_expires_at timestamptz;
    database_now timestamptz;
BEGIN
    IF selected_tenant_id IS NULL
       OR selected_workstation_id IS NULL
       OR selected_nonce IS NULL
       OR selected_task_id IS NULL
       OR selected_command_id IS NULL
       OR selected_result_sha256 IS NULL
       OR selected_completed_at IS NULL
       OR selected_claim_expires_at IS NULL
       OR NOT pg_catalog.isfinite(selected_completed_at)
       OR NOT pg_catalog.isfinite(selected_claim_expires_at)
       OR selected_claim_expires_at < selected_completed_at
       OR selected_claim_expires_at >
          selected_completed_at + pg_catalog.make_interval(secs => 300)
       OR selected_tenant_id::text IS DISTINCT FROM
          NULLIF(current_setting('cadplot.tenant_id', true), '') THEN
        RETURN 'conflict';
    END IF;

    SELECT dispatch.issued_at, dispatch.operation_expires_at
    INTO dispatch_issued_at, dispatch_operation_expires_at
    FROM cadplot_gateway.worker_dispatches AS dispatch
    WHERE dispatch.tenant_id = selected_tenant_id
      AND dispatch.workstation_id = selected_workstation_id
      AND dispatch.task_id = selected_task_id
      AND dispatch.command_id = selected_command_id
      AND dispatch.nonce = selected_nonce;

    IF NOT FOUND
       OR selected_completed_at <
          dispatch_issued_at - pg_catalog.make_interval(secs => 30)
       OR selected_completed_at > dispatch_operation_expires_at
       OR selected_claim_expires_at > dispatch_operation_expires_at THEN
        RETURN 'conflict';
    END IF;

    -- Serialize the logical replay identity before observing or inserting it. Hash collisions
    -- can only reduce concurrency; the primary key remains the source of uniqueness truth.
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            selected_tenant_id::text || ':' ||
            selected_workstation_id::text || ':' ||
            selected_nonce::text,
            0
        )
    );

    SELECT replay.task_id, replay.command_id, replay.result_sha256, replay.completed_at
    INTO
        existing_task_id,
        existing_command_id,
        existing_result_sha256,
        existing_completed_at
    FROM cadplot_gateway.worker_result_replays AS replay
    WHERE replay.tenant_id = selected_tenant_id
      AND replay.workstation_id = selected_workstation_id
      AND replay.nonce = selected_nonce;

    IF FOUND THEN
        IF ROW(
            existing_task_id,
            existing_command_id,
            existing_result_sha256,
            existing_completed_at
        ) IS NOT DISTINCT FROM ROW(
            selected_task_id,
            selected_command_id,
            selected_result_sha256,
            selected_completed_at
        ) THEN
            RETURN 'exact_match';
        END IF;
        RETURN 'conflict';
    END IF;

    -- clock_timestamp() is intentionally evaluated after any advisory-lock wait.
    -- transaction_timestamp()/statement_timestamp() would preserve a stale pre-wait time.
    database_now := pg_catalog.clock_timestamp();
    IF database_now >= selected_claim_expires_at
       OR database_now >= dispatch_operation_expires_at
       OR selected_completed_at >
          database_now + pg_catalog.make_interval(secs => 30) THEN
        RETURN 'unseen';
    END IF;

    INSERT INTO cadplot_gateway.worker_result_replays (
        tenant_id, workstation_id, nonce, task_id, command_id,
        result_sha256, completed_at
    )
    SELECT
        selected_tenant_id, selected_workstation_id, selected_nonce,
        selected_task_id, selected_command_id, selected_result_sha256,
        selected_completed_at
    WHERE pg_catalog.clock_timestamp() < selected_claim_expires_at
      AND pg_catalog.clock_timestamp() < dispatch_operation_expires_at
    ON CONFLICT (tenant_id, workstation_id, nonce) DO NOTHING
    RETURNING true INTO inserted;

    IF inserted THEN
        RETURN 'first_seen';
    END IF;

    -- A privileged writer that does not take the advisory lock may have raced this function.
    SELECT replay.task_id, replay.command_id, replay.result_sha256, replay.completed_at
    INTO
        existing_task_id,
        existing_command_id,
        existing_result_sha256,
        existing_completed_at
    FROM cadplot_gateway.worker_result_replays AS replay
    WHERE replay.tenant_id = selected_tenant_id
      AND replay.workstation_id = selected_workstation_id
      AND replay.nonce = selected_nonce;

    IF NOT FOUND THEN
        RETURN 'unseen';
    END IF;

    IF ROW(
        existing_task_id,
        existing_command_id,
        existing_result_sha256,
        existing_completed_at
    ) IS NOT DISTINCT FROM ROW(
        selected_task_id,
        selected_command_id,
        selected_result_sha256,
        selected_completed_at
    ) THEN
        RETURN 'exact_match';
    END IF;

    RETURN 'conflict';
END;
$claim_worker_result_replay$;

CREATE FUNCTION cadplot_gateway.worker_result_application_applied(
    selected_tenant_id cadplot_gateway.tenant_id,
    selected_workstation_id cadplot_gateway.workstation_id,
    selected_nonce cadplot_gateway.worker_nonce,
    selected_task_id cadplot_gateway.task_id,
    selected_command_id cadplot_gateway.command_id,
    selected_result_sha256 cadplot_gateway.sha256_hex,
    selected_completed_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $worker_result_application_applied$
DECLARE
    application_applied boolean := false;
BEGIN
    IF selected_tenant_id IS NULL
       OR selected_workstation_id IS NULL
       OR selected_nonce IS NULL
       OR selected_task_id IS NULL
       OR selected_command_id IS NULL
       OR selected_result_sha256 IS NULL
       OR selected_completed_at IS NULL
       OR NOT pg_catalog.isfinite(selected_completed_at)
       OR selected_tenant_id::text IS DISTINCT FROM
          NULLIF(current_setting('cadplot.tenant_id', true), '') THEN
        RETURN false;
    END IF;

    SELECT replay.applied_at IS NOT NULL
    INTO application_applied
    FROM cadplot_gateway.worker_result_replays AS replay
    WHERE replay.tenant_id = selected_tenant_id
      AND replay.workstation_id = selected_workstation_id
      AND replay.nonce = selected_nonce
      AND replay.task_id = selected_task_id
      AND replay.command_id = selected_command_id
      AND replay.result_sha256 = selected_result_sha256
      AND replay.completed_at = selected_completed_at;

    RETURN COALESCE(application_applied, false);
END;
$worker_result_application_applied$;

CREATE FUNCTION cadplot_gateway.acquire_worker_result_application(
    selected_tenant_id cadplot_gateway.tenant_id,
    selected_workstation_id cadplot_gateway.workstation_id,
    selected_nonce cadplot_gateway.worker_nonce,
    selected_task_id cadplot_gateway.task_id,
    selected_command_id cadplot_gateway.command_id,
    selected_result_sha256 cadplot_gateway.sha256_hex,
    selected_completed_at timestamptz,
    selected_application_id uuid,
    selected_lease_seconds integer
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $acquire_worker_result_application$
DECLARE
    existing_task_id cadplot_gateway.task_id;
    existing_command_id cadplot_gateway.command_id;
    existing_result_sha256 cadplot_gateway.sha256_hex;
    existing_completed_at timestamptz;
    existing_application_id uuid;
    existing_application_lease_expires_at timestamptz;
    existing_applied_at timestamptz;
    database_now timestamptz;
BEGIN
    IF selected_tenant_id IS NULL
       OR selected_workstation_id IS NULL
       OR selected_nonce IS NULL
       OR selected_task_id IS NULL
       OR selected_command_id IS NULL
       OR selected_result_sha256 IS NULL
       OR selected_completed_at IS NULL
       OR selected_application_id IS NULL
       OR selected_lease_seconds IS NULL
       OR selected_lease_seconds < 1
       OR selected_lease_seconds > 60
       OR NOT pg_catalog.isfinite(selected_completed_at)
       OR selected_tenant_id::text IS DISTINCT FROM
          NULLIF(current_setting('cadplot.tenant_id', true), '') THEN
        RETURN 'conflict';
    END IF;

    SELECT
        replay.task_id,
        replay.command_id,
        replay.result_sha256,
        replay.completed_at,
        replay.application_id,
        replay.application_lease_expires_at,
        replay.applied_at
    INTO
        existing_task_id,
        existing_command_id,
        existing_result_sha256,
        existing_completed_at,
        existing_application_id,
        existing_application_lease_expires_at,
        existing_applied_at
    FROM cadplot_gateway.worker_result_replays AS replay
    WHERE replay.tenant_id = selected_tenant_id
      AND replay.workstation_id = selected_workstation_id
      AND replay.nonce = selected_nonce
    FOR UPDATE;

    IF NOT FOUND
       OR ROW(
           existing_task_id,
           existing_command_id,
           existing_result_sha256,
           existing_completed_at
       ) IS DISTINCT FROM ROW(
           selected_task_id,
           selected_command_id,
           selected_result_sha256,
           selected_completed_at
       ) THEN
        RETURN 'conflict';
    END IF;
    IF existing_applied_at IS NOT NULL THEN
        RETURN 'applied';
    END IF;

    database_now := pg_catalog.clock_timestamp();
    IF existing_application_lease_expires_at > database_now
       AND existing_application_id IS DISTINCT FROM selected_application_id THEN
        RETURN 'busy';
    END IF;

    UPDATE cadplot_gateway.worker_result_replays AS replay
    SET application_id = selected_application_id,
        application_lease_expires_at =
            database_now + pg_catalog.make_interval(secs => selected_lease_seconds)
    WHERE replay.tenant_id = selected_tenant_id
      AND replay.workstation_id = selected_workstation_id
      AND replay.nonce = selected_nonce
      AND replay.task_id = selected_task_id
      AND replay.command_id = selected_command_id
      AND replay.result_sha256 = selected_result_sha256
      AND replay.completed_at = selected_completed_at
      AND replay.applied_at IS NULL;
    IF NOT FOUND THEN
        RETURN 'conflict';
    END IF;
    RETURN 'acquired';
END;
$acquire_worker_result_application$;

CREATE FUNCTION cadplot_gateway.mark_worker_result_applied(
    selected_tenant_id cadplot_gateway.tenant_id,
    selected_workstation_id cadplot_gateway.workstation_id,
    selected_nonce cadplot_gateway.worker_nonce,
    selected_task_id cadplot_gateway.task_id,
    selected_command_id cadplot_gateway.command_id,
    selected_result_sha256 cadplot_gateway.sha256_hex,
    selected_completed_at timestamptz,
    selected_application_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $mark_worker_result_applied$
DECLARE
    application_marked boolean := false;
BEGIN
    IF selected_tenant_id IS NULL
       OR selected_workstation_id IS NULL
       OR selected_nonce IS NULL
       OR selected_task_id IS NULL
       OR selected_command_id IS NULL
       OR selected_result_sha256 IS NULL
       OR selected_completed_at IS NULL
       OR selected_application_id IS NULL
       OR NOT pg_catalog.isfinite(selected_completed_at)
       OR selected_tenant_id::text IS DISTINCT FROM
          NULLIF(current_setting('cadplot.tenant_id', true), '') THEN
        RETURN false;
    END IF;

    UPDATE cadplot_gateway.worker_result_replays AS replay
    SET applied_at = COALESCE(replay.applied_at, pg_catalog.clock_timestamp())
    WHERE replay.tenant_id = selected_tenant_id
      AND replay.workstation_id = selected_workstation_id
      AND replay.nonce = selected_nonce
      AND replay.task_id = selected_task_id
      AND replay.command_id = selected_command_id
      AND replay.result_sha256 = selected_result_sha256
      AND replay.completed_at = selected_completed_at
      AND (
          replay.applied_at IS NOT NULL
          OR (
              replay.application_id = selected_application_id
              AND replay.application_lease_expires_at > pg_catalog.clock_timestamp()
          )
      )
    RETURNING true INTO application_marked;

    RETURN COALESCE(application_marked, false);
END;
$mark_worker_result_applied$;

ALTER TABLE cadplot_gateway.worker_dispatches ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.worker_dispatches FORCE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.worker_result_replays ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.worker_result_replays FORCE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.worker_verification_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.worker_verification_keys FORCE ROW LEVEL SECURITY;

CREATE POLICY worker_dispatches_tenant_isolation
ON cadplot_gateway.worker_dispatches
FOR ALL
USING (tenant_id::text = current_setting('cadplot.tenant_id', true))
WITH CHECK (tenant_id::text = current_setting('cadplot.tenant_id', true));

CREATE POLICY worker_result_replays_tenant_isolation
ON cadplot_gateway.worker_result_replays
FOR ALL
USING (tenant_id::text = current_setting('cadplot.tenant_id', true))
WITH CHECK (tenant_id::text = current_setting('cadplot.tenant_id', true));

CREATE POLICY worker_verification_keys_tenant_isolation
ON cadplot_gateway.worker_verification_keys
FOR ALL
USING (tenant_id::text = current_setting('cadplot.tenant_id', true))
WITH CHECK (tenant_id::text = current_setting('cadplot.tenant_id', true));

REVOKE ALL PRIVILEGES ON TABLE
    cadplot_gateway.worker_dispatches,
    cadplot_gateway.worker_result_replays,
    cadplot_gateway.worker_verification_keys
FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE
    cadplot_gateway.worker_dispatches,
    cadplot_gateway.worker_result_replays,
    cadplot_gateway.worker_verification_keys
FROM cadplot_gateway_runtime;

GRANT SELECT, INSERT ON TABLE cadplot_gateway.worker_dispatches
TO cadplot_gateway_runtime;
GRANT SELECT ON TABLE cadplot_gateway.worker_verification_keys
TO cadplot_gateway_runtime;

-- The authenticated worker result path may update only display metadata inside the
-- tenant/owner/workstation chains revalidated by the gateway transaction and RLS/FKs.
GRANT INSERT (
    project_id,
    tenant_id,
    owner_subject_id,
    workstation_id,
    display_name,
    enabled
) ON TABLE cadplot_gateway.projects TO cadplot_gateway_runtime;
GRANT UPDATE (display_name, enabled)
ON TABLE cadplot_gateway.projects TO cadplot_gateway_runtime;
GRANT INSERT (
    drawing_id,
    tenant_id,
    owner_subject_id,
    workstation_id,
    project_id,
    display_name,
    enabled
) ON TABLE cadplot_gateway.drawings TO cadplot_gateway_runtime;
GRANT UPDATE (display_name, enabled)
ON TABLE cadplot_gateway.drawings TO cadplot_gateway_runtime;

REVOKE ALL ON FUNCTION cadplot_gateway.reject_worker_dispatch_identity_mutation()
FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.reject_worker_replay_identity_mutation()
FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.classify_worker_result_replay(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz
) FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.claim_worker_result_replay(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz,
    timestamptz
) FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.worker_result_application_applied(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz
) FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.acquire_worker_result_application(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz,
    uuid,
    integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.mark_worker_result_applied(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz,
    uuid
) FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.classify_worker_result_replay(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz
) FROM cadplot_gateway_runtime;
REVOKE ALL ON FUNCTION cadplot_gateway.claim_worker_result_replay(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz,
    timestamptz
) FROM cadplot_gateway_runtime;
REVOKE ALL ON FUNCTION cadplot_gateway.worker_result_application_applied(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz
) FROM cadplot_gateway_runtime;
REVOKE ALL ON FUNCTION cadplot_gateway.acquire_worker_result_application(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz,
    uuid,
    integer
) FROM cadplot_gateway_runtime;
REVOKE ALL ON FUNCTION cadplot_gateway.mark_worker_result_applied(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz,
    uuid
) FROM cadplot_gateway_runtime;
GRANT EXECUTE ON FUNCTION cadplot_gateway.classify_worker_result_replay(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz
) TO cadplot_gateway_runtime;
GRANT EXECUTE ON FUNCTION cadplot_gateway.claim_worker_result_replay(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz,
    timestamptz
) TO cadplot_gateway_runtime;
GRANT EXECUTE ON FUNCTION cadplot_gateway.worker_result_application_applied(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz
) TO cadplot_gateway_runtime;
GRANT EXECUTE ON FUNCTION cadplot_gateway.acquire_worker_result_application(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz,
    uuid,
    integer
) TO cadplot_gateway_runtime;
GRANT EXECUTE ON FUNCTION cadplot_gateway.mark_worker_result_applied(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_nonce,
    cadplot_gateway.task_id,
    cadplot_gateway.command_id,
    cadplot_gateway.sha256_hex,
    timestamptz,
    uuid
) TO cadplot_gateway_runtime;

-- Key enrollment, rotation, revocation, and deletion are control-plane-only operations.
-- cadplot_gateway_runtime intentionally receives SELECT and no key-table write privilege.

COMMIT;
