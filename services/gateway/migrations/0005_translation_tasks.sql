BEGIN;

-- Explicitly opted-in translation. Existing immutable operation and uniqueness guards remain.
ALTER TABLE cadplot_gateway.operations DROP CONSTRAINT operations_task_object;
ALTER TABLE cadplot_gateway.operations ADD CONSTRAINT operations_task_object CHECK (
    jsonb_typeof(task) = 'object' AND task ? 'command'
    AND task->>'command' IN (
        'validate_environment', 'list_projects', 'scan_drawings',
        'inspect_drawing', 'create_publish_plan', 'translation'
    )
);

CREATE OR REPLACE FUNCTION cadplot_gateway.enforce_operation_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
       OR NEW.owner_subject_id IS DISTINCT FROM OLD.owner_subject_id
       OR NEW.client_id IS DISTINCT FROM OLD.client_id
       OR NEW.workstation_id IS DISTINCT FROM OLD.workstation_id
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
       OR NEW.task IS DISTINCT FROM OLD.task
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'operation_identity_immutable';
    END IF;

    IF NEW.state = OLD.state THEN
        IF NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'operation_same_state_mutation_forbidden';
        END IF;
        RETURN NEW;
    END IF;

    IF NOT (
        (OLD.state = 'CREATED' AND NEW.state IN ('LEASED', 'EXPIRED'))
        OR (OLD.state = 'LEASED' AND NEW.state IN ('RUNNING', 'ATTENTION_REQUIRED'))
        OR (OLD.state = 'RUNNING' AND NEW.state IN ('SUCCEEDED', 'FAILED', 'EXPIRED', 'ATTENTION_REQUIRED'))
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'operation_state_transition_forbidden';
    END IF;

    IF OLD.state = 'LEASED' AND NEW.state = 'RUNNING'
       AND (
           OLD.lease_expires_at IS NULL
           OR NEW.started_at IS NULL
           OR NEW.started_at >= OLD.lease_expires_at
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'operation_lease_deadline_violated';
    END IF;

    IF OLD.state = 'LEASED' AND NEW.state = 'ATTENTION_REQUIRED'
       AND (
           OLD.lease_expires_at IS NULL
           OR NEW.completed_at IS NULL
           OR NEW.completed_at < OLD.lease_expires_at
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'operation_lease_deadline_violated';
    END IF;

    IF OLD.state = 'RUNNING' AND NEW.state IN ('SUCCEEDED', 'FAILED')
       AND (NEW.completed_at IS NULL OR NEW.completed_at >= OLD.expires_at) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'operation_completion_deadline_violated';
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE cadplot_gateway.operations DROP CONSTRAINT operations_state_shape;
ALTER TABLE cadplot_gateway.operations ADD CONSTRAINT operations_state_shape CHECK (
        (
            state = 'CREATED'
            AND lease_expires_at IS NULL
            AND started_at IS NULL
            AND completed_at IS NULL
            AND result IS NULL
            AND error_code IS NULL
        )
        OR (
            state = 'LEASED'
            AND lease_expires_at IS NOT NULL
            AND started_at IS NULL
            AND completed_at IS NULL
            AND result IS NULL
            AND error_code IS NULL
        )
        OR (
            state = 'RUNNING'
            AND lease_expires_at IS NULL
            AND started_at IS NOT NULL
            AND completed_at IS NULL
            AND result IS NULL
            AND error_code IS NULL
        )
        OR (
            state = 'SUCCEEDED'
            AND lease_expires_at IS NULL
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND result IS NOT NULL
            AND jsonb_typeof(result) = 'object'
            AND result ? 'command'
            AND result->>'command' = task->>'command'
            AND error_code IS NULL
        )
        OR (
            state = 'FAILED'
            AND lease_expires_at IS NULL
            AND started_at IS NOT NULL
            AND completed_at IS NOT NULL
            AND result IS NULL
            AND error_code IS NOT NULL
        )
        OR (
            state = 'EXPIRED'
            AND lease_expires_at IS NULL
            AND completed_at IS NOT NULL
            AND completed_at >= expires_at
            AND result IS NULL
            AND error_code = 'operation_expired'
        )
        OR (
            state = 'ATTENTION_REQUIRED'
            AND lease_expires_at IS NULL
            AND completed_at IS NOT NULL
            AND result IS NULL
            AND error_code IN ('lease_expired', 'translation_attention_required', 'worker_failure')
        )
    );

COMMIT;
