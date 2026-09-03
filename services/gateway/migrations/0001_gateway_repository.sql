BEGIN;

-- This migration creates only the shared NOLOGIN capability role.
-- Actual LOGIN provisioning and membership are intentionally external. Deployment must run:
--     GRANT cadplot_gateway_runtime TO <actual_login_role>;
-- The actual LOGIN must be NOSUPERUSER, NOBYPASSRLS, own no gateway objects, and receive no
-- direct gateway grants. This migration never creates a LOGIN or embeds a password.
DO $cadplot_runtime_role$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'cadplot_gateway_runtime'
    ) THEN
        CREATE ROLE cadplot_gateway_runtime
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS;
    END IF;
END;
$cadplot_runtime_role$;

ALTER ROLE cadplot_gateway_runtime WITH
    NOLOGIN
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    NOREPLICATION
    NOBYPASSRLS;

COMMENT ON ROLE cadplot_gateway_runtime IS
    'CadPlot NOLOGIN capability role; explicitly grant membership only to a NOSUPERUSER NOBYPASSRLS LOGIN.';

CREATE SCHEMA IF NOT EXISTS cadplot_gateway;
REVOKE ALL ON SCHEMA cadplot_gateway FROM PUBLIC;

CREATE DOMAIN cadplot_gateway.tenant_id AS text
    CHECK (VALUE ~ '^tnt_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.subject_id AS text
    CHECK (VALUE ~ '^usr_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.client_id AS text
    CHECK (VALUE ~ '^cli_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.workstation_id AS text
    CHECK (VALUE ~ '^ws_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.project_id AS text
    CHECK (VALUE ~ '^prj_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.drawing_id AS text
    CHECK (VALUE ~ '^drw_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.operation_id AS text
    CHECK (VALUE ~ '^op_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.idempotency_key AS text
    CHECK (VALUE ~ '^idem_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');
CREATE DOMAIN cadplot_gateway.sha256_hex AS text
    CHECK (VALUE ~ '^[0-9a-f]{64}$');
CREATE DOMAIN cadplot_gateway.safe_code AS text
    CHECK (VALUE ~ '^[a-z][a-z0-9_]{0,63}$');
CREATE DOMAIN cadplot_gateway.safe_display_name AS text
    CHECK (
        char_length(VALUE) BETWEEN 1 AND 128
        AND VALUE !~ '[\\/:[:cntrl:]]'
    );

CREATE TABLE cadplot_gateway.workstations (
    workstation_id cadplot_gateway.workstation_id NOT NULL,
    tenant_id cadplot_gateway.tenant_id NOT NULL,
    owner_subject_id cadplot_gateway.subject_id NOT NULL,
    display_name cadplot_gateway.safe_display_name NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    online boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, workstation_id),
    UNIQUE (tenant_id, workstation_id, owner_subject_id)
);

CREATE TABLE cadplot_gateway.projects (
    project_id cadplot_gateway.project_id NOT NULL,
    tenant_id cadplot_gateway.tenant_id NOT NULL,
    owner_subject_id cadplot_gateway.subject_id NOT NULL,
    workstation_id cadplot_gateway.workstation_id NOT NULL,
    display_name cadplot_gateway.safe_display_name NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, project_id),
    UNIQUE (tenant_id, project_id, owner_subject_id, workstation_id),
    CONSTRAINT projects_owned_workstation_fk
        FOREIGN KEY (tenant_id, workstation_id, owner_subject_id)
        REFERENCES cadplot_gateway.workstations (
            tenant_id, workstation_id, owner_subject_id
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
);

CREATE TABLE cadplot_gateway.drawings (
    drawing_id cadplot_gateway.drawing_id NOT NULL,
    tenant_id cadplot_gateway.tenant_id NOT NULL,
    owner_subject_id cadplot_gateway.subject_id NOT NULL,
    workstation_id cadplot_gateway.workstation_id NOT NULL,
    project_id cadplot_gateway.project_id NOT NULL,
    display_name cadplot_gateway.safe_display_name NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, drawing_id),
    CONSTRAINT drawings_owned_project_fk
        FOREIGN KEY (tenant_id, project_id, owner_subject_id, workstation_id)
        REFERENCES cadplot_gateway.projects (
            tenant_id, project_id, owner_subject_id, workstation_id
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT
);

CREATE TABLE cadplot_gateway.operations (
    operation_id cadplot_gateway.operation_id NOT NULL,
    tenant_id cadplot_gateway.tenant_id NOT NULL,
    owner_subject_id cadplot_gateway.subject_id NOT NULL,
    client_id cadplot_gateway.client_id NOT NULL,
    workstation_id cadplot_gateway.workstation_id NOT NULL,
    idempotency_key cadplot_gateway.idempotency_key NOT NULL,
    request_fingerprint cadplot_gateway.sha256_hex NOT NULL,
    task jsonb NOT NULL,
    state text NOT NULL DEFAULT 'CREATED',
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    lease_expires_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    result jsonb,
    error_code cadplot_gateway.safe_code,
    PRIMARY KEY (tenant_id, operation_id),
    CONSTRAINT operations_idempotency_unique
        UNIQUE (tenant_id, owner_subject_id, client_id, idempotency_key),
    CONSTRAINT operations_owned_workstation_fk
        FOREIGN KEY (tenant_id, workstation_id, owner_subject_id)
        REFERENCES cadplot_gateway.workstations (
            tenant_id, workstation_id, owner_subject_id
        )
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT operations_state_known CHECK (
        state IN (
            'CREATED', 'LEASED', 'RUNNING', 'SUCCEEDED', 'FAILED',
            'EXPIRED', 'ATTENTION_REQUIRED'
        )
    ),
    CONSTRAINT operations_task_object CHECK (
        jsonb_typeof(task) = 'object'
        AND task ? 'command'
        AND task->>'command' IN (
            'validate_environment', 'list_projects', 'scan_drawings',
            'inspect_drawing', 'create_publish_plan'
        )
    ),
    CONSTRAINT operations_deadline_order CHECK (expires_at > created_at),
    CONSTRAINT operations_lease_deadline CHECK (
        lease_expires_at IS NULL
        OR (lease_expires_at > created_at AND lease_expires_at <= expires_at)
    ),
    CONSTRAINT operations_start_deadline CHECK (
        started_at IS NULL OR (started_at >= created_at AND started_at < expires_at)
    ),
    CONSTRAINT operations_completion_order CHECK (
        completed_at IS NULL
        OR (
            completed_at >= created_at
            AND (started_at IS NULL OR completed_at >= started_at)
        )
    ),
    CONSTRAINT operations_state_shape CHECK (
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
            AND error_code = 'lease_expired'
        )
    )
);

CREATE INDEX operations_created_queue_idx
    ON cadplot_gateway.operations (
        tenant_id, workstation_id, created_at, operation_id
    )
    WHERE state = 'CREATED';
CREATE INDEX operations_active_deadline_idx
    ON cadplot_gateway.operations (tenant_id, workstation_id, expires_at)
    WHERE state IN ('CREATED', 'RUNNING');
CREATE INDEX operations_lease_deadline_idx
    ON cadplot_gateway.operations (tenant_id, workstation_id, lease_expires_at)
    WHERE state = 'LEASED';
CREATE INDEX operations_active_client_workstation_idx
    ON cadplot_gateway.operations (
        tenant_id, owner_subject_id, client_id, workstation_id, state
    )
    WHERE state IN ('CREATED', 'LEASED', 'RUNNING');

CREATE TABLE cadplot_gateway.operation_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY,
    tenant_id cadplot_gateway.tenant_id NOT NULL,
    operation_id cadplot_gateway.operation_id NOT NULL,
    from_state text,
    to_state text NOT NULL,
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, event_id),
    CONSTRAINT operation_events_operation_fk
        FOREIGN KEY (tenant_id, operation_id)
        REFERENCES cadplot_gateway.operations (tenant_id, operation_id)
        ON UPDATE RESTRICT
        ON DELETE RESTRICT,
    CONSTRAINT operation_events_state_known CHECK (
        to_state IN (
            'CREATED', 'LEASED', 'RUNNING', 'SUCCEEDED', 'FAILED',
            'EXPIRED', 'ATTENTION_REQUIRED'
        )
        AND (
            from_state IS NULL
            OR from_state IN (
                'CREATED', 'LEASED', 'RUNNING', 'SUCCEEDED', 'FAILED',
                'EXPIRED', 'ATTENTION_REQUIRED'
            )
        )
    )
);

CREATE INDEX operation_events_operation_idx
    ON cadplot_gateway.operation_events (tenant_id, operation_id, event_id);

-- Runtime callers have SELECT-only catalog access. This narrow definer function performs the
-- row locks required to close catalog authorization races without granting catalog UPDATE.
CREATE FUNCTION cadplot_gateway.lock_catalog_rows(
    p_kind text,
    p_tenant text,
    p_owner text,
    p_workstation text,
    p_identifiers text[],
    p_project text,
    p_require_online boolean
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $$
DECLARE
    locked_count integer := 0;
BEGIN
    IF p_tenant IS NULL
       OR p_tenant IS DISTINCT FROM NULLIF(current_setting('cadplot.tenant_id', true), '')
       OR p_workstation IS NULL
       OR p_identifiers IS NULL
       OR cardinality(p_identifiers) = 0
       OR cardinality(p_identifiers) > 500
       OR array_position(p_identifiers, NULL) IS NOT NULL THEN
        RETURN 0;
    END IF;

    IF p_kind = 'owned_workstation' THEN
        IF p_owner IS NULL OR cardinality(p_identifiers) <> 1
           OR p_identifiers[1] IS DISTINCT FROM p_workstation THEN
            RETURN 0;
        END IF;
        PERFORM workstation_id
        FROM cadplot_gateway.workstations
        WHERE tenant_id = p_tenant::cadplot_gateway.tenant_id
          AND workstation_id = p_workstation::cadplot_gateway.workstation_id
          AND owner_subject_id = p_owner::cadplot_gateway.subject_id
          AND enabled = true
          AND (NOT p_require_online OR online = true)
        FOR SHARE;
    ELSIF p_kind = 'worker_workstation' THEN
        IF cardinality(p_identifiers) <> 1
           OR p_identifiers[1] IS DISTINCT FROM p_workstation THEN
            RETURN 0;
        END IF;
        PERFORM workstation_id
        FROM cadplot_gateway.workstations
        WHERE tenant_id = p_tenant::cadplot_gateway.tenant_id
          AND workstation_id = p_workstation::cadplot_gateway.workstation_id
          AND enabled = true
          AND (NOT p_require_online OR online = true)
        FOR SHARE;
    ELSIF p_kind = 'projects' THEN
        IF p_owner IS NULL THEN
            RETURN 0;
        END IF;
        PERFORM project_id
        FROM cadplot_gateway.projects
        WHERE tenant_id = p_tenant::cadplot_gateway.tenant_id
          AND owner_subject_id = p_owner::cadplot_gateway.subject_id
          AND workstation_id = p_workstation::cadplot_gateway.workstation_id
          AND enabled = true
          AND project_id::text = ANY(p_identifiers)
        FOR SHARE;
    ELSIF p_kind = 'drawings' THEN
        IF p_owner IS NULL THEN
            RETURN 0;
        END IF;
        PERFORM drawing_id
        FROM cadplot_gateway.drawings
        WHERE tenant_id = p_tenant::cadplot_gateway.tenant_id
          AND owner_subject_id = p_owner::cadplot_gateway.subject_id
          AND workstation_id = p_workstation::cadplot_gateway.workstation_id
          AND enabled = true
          AND drawing_id::text = ANY(p_identifiers)
          AND (
              p_project IS NULL
              OR project_id = p_project::cadplot_gateway.project_id
          )
        FOR SHARE;
    ELSIF p_kind = 'drawing_chains' THEN
        IF p_owner IS NULL THEN
            RETURN 0;
        END IF;
        PERFORM drawing.drawing_id
        FROM cadplot_gateway.drawings AS drawing
        JOIN cadplot_gateway.projects AS project
          ON project.tenant_id = drawing.tenant_id
         AND project.project_id = drawing.project_id
         AND project.owner_subject_id = drawing.owner_subject_id
         AND project.workstation_id = drawing.workstation_id
        WHERE drawing.tenant_id = p_tenant::cadplot_gateway.tenant_id
          AND project.tenant_id = p_tenant::cadplot_gateway.tenant_id
          AND drawing.owner_subject_id = p_owner::cadplot_gateway.subject_id
          AND drawing.workstation_id = p_workstation::cadplot_gateway.workstation_id
          AND drawing.enabled = true
          AND project.enabled = true
          AND drawing.drawing_id::text = ANY(p_identifiers)
        FOR SHARE OF drawing, project;
    ELSE
        RETURN 0;
    END IF;

    GET DIAGNOSTICS locked_count = ROW_COUNT;
    RETURN locked_count;
END;
$$;

CREATE FUNCTION cadplot_gateway.enforce_operation_transition()
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
        OR (OLD.state = 'RUNNING' AND NEW.state IN ('SUCCEEDED', 'FAILED', 'EXPIRED'))
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

CREATE TRIGGER operations_transition_guard
BEFORE UPDATE ON cadplot_gateway.operations
FOR EACH ROW EXECUTE FUNCTION cadplot_gateway.enforce_operation_transition();

CREATE FUNCTION cadplot_gateway.reject_operation_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'operation_event_is_append_only';
END;
$$;

CREATE TRIGGER operation_events_append_only
BEFORE UPDATE OR DELETE ON cadplot_gateway.operation_events
FOR EACH ROW EXECUTE FUNCTION cadplot_gateway.reject_operation_event_mutation();

ALTER TABLE cadplot_gateway.workstations ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.workstations FORCE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.projects FORCE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.drawings ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.drawings FORCE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.operations FORCE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.operation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE cadplot_gateway.operation_events FORCE ROW LEVEL SECURITY;

CREATE POLICY workstations_tenant_isolation ON cadplot_gateway.workstations
    USING (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    );
CREATE POLICY projects_tenant_isolation ON cadplot_gateway.projects
    USING (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    );
CREATE POLICY drawings_tenant_isolation ON cadplot_gateway.drawings
    USING (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    );
CREATE POLICY operations_tenant_isolation ON cadplot_gateway.operations
    USING (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    );
CREATE POLICY operation_events_tenant_select ON cadplot_gateway.operation_events
    FOR SELECT
    USING (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    );
CREATE POLICY operation_events_tenant_insert ON cadplot_gateway.operation_events
    FOR INSERT
    WITH CHECK (
        tenant_id = NULLIF(current_setting('cadplot.tenant_id', true), '')
    );

REVOKE ALL ON ALL TABLES IN SCHEMA cadplot_gateway FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA cadplot_gateway FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cadplot_gateway FROM PUBLIC;

-- Reset the shared role to a closed baseline before granting the exact data-plane surface.
REVOKE ALL PRIVILEGES ON SCHEMA cadplot_gateway FROM cadplot_gateway_runtime;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA cadplot_gateway
    FROM cadplot_gateway_runtime;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA cadplot_gateway
    FROM cadplot_gateway_runtime;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA cadplot_gateway
    FROM cadplot_gateway_runtime;

GRANT USAGE ON SCHEMA cadplot_gateway TO cadplot_gateway_runtime;
GRANT SELECT ON TABLE
    cadplot_gateway.workstations,
    cadplot_gateway.projects,
    cadplot_gateway.drawings
TO cadplot_gateway_runtime;
GRANT SELECT, INSERT ON TABLE cadplot_gateway.operations TO cadplot_gateway_runtime;
GRANT UPDATE (
    state,
    lease_expires_at,
    started_at,
    completed_at,
    result,
    error_code
) ON cadplot_gateway.operations TO cadplot_gateway_runtime;
GRANT INSERT ON TABLE cadplot_gateway.operation_events TO cadplot_gateway_runtime;
GRANT USAGE ON SEQUENCE cadplot_gateway.operation_events_event_id_seq
    TO cadplot_gateway_runtime;
GRANT EXECUTE ON FUNCTION cadplot_gateway.lock_catalog_rows(
    text, text, text, text, text[], text, boolean
) TO cadplot_gateway_runtime;

COMMIT;
