BEGIN;

ALTER TABLE cadplot_gateway.workstations
    ADD COLUMN last_seen_at timestamptz,
    ADD COLUMN presence_expires_at timestamptz;

-- A pre-migration boolean was control-plane state, not proof of a live authenticated worker.
-- Require every registered workstation to establish fresh presence after this migration.
UPDATE cadplot_gateway.workstations
SET online = false,
    last_seen_at = NULL,
    presence_expires_at = NULL;

ALTER TABLE cadplot_gateway.workstations
    ADD CONSTRAINT workstations_presence_timestamps CHECK (
        (
            last_seen_at IS NULL
            AND presence_expires_at IS NULL
        )
        OR (
            last_seen_at IS NOT NULL
            AND presence_expires_at IS NOT NULL
            AND pg_catalog.isfinite(last_seen_at)
            AND pg_catalog.isfinite(presence_expires_at)
            AND presence_expires_at > last_seen_at
            AND presence_expires_at <= last_seen_at + interval '5 minutes'
        )
    );

CREATE FUNCTION cadplot_gateway.record_worker_presence(
    selected_tenant_id cadplot_gateway.tenant_id,
    selected_workstation_id cadplot_gateway.workstation_id,
    selected_key_id cadplot_gateway.worker_key_id,
    selected_presence_seconds integer
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET row_security = on
AS $record_worker_presence$
DECLARE
    recorded boolean := false;
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
       OR selected_presence_seconds IS NULL
       OR selected_presence_seconds < 30
       OR selected_presence_seconds > 300
       OR selected_tenant_id::text IS DISTINCT FROM
          NULLIF(current_setting('cadplot.tenant_id', true), '') THEN
        RETURN false;
    END IF;

    -- Recheck and lock the exact credential at the presence decision boundary. The workstation
    -- lock serializes concurrent heartbeats and closes disable/revocation races.
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
    FOR SHARE OF verification_key
    FOR NO KEY UPDATE OF workstation;

    IF NOT FOUND THEN
        RETURN false;
    END IF;

    database_time := clock_timestamp();
    IF selected_workstation_enabled IS DISTINCT FROM true
       OR selected_key_enabled IS DISTINCT FROM true
       OR selected_key_algorithm IS DISTINCT FROM 'ed25519'
       OR selected_key_length IS DISTINCT FROM 32
       OR selected_key_revoked_at IS NOT NULL
       OR selected_key_not_before > database_time
       OR (
           selected_key_expires_at IS NOT NULL
           AND selected_key_expires_at <= database_time
       ) THEN
        RETURN false;
    END IF;

    UPDATE cadplot_gateway.workstations
    SET online = true,
        last_seen_at = database_time,
        presence_expires_at = database_time
            + pg_catalog.make_interval(secs => selected_presence_seconds)
    WHERE tenant_id = selected_tenant_id
      AND workstation_id = selected_workstation_id
      AND enabled = true
    RETURNING true INTO recorded;

    RETURN COALESCE(recorded, false);
END;
$record_worker_presence$;

-- Preserve the existing narrow catalog-lock API while making every online authorization check
-- depend on an unexpired authenticated-worker presence window.
CREATE OR REPLACE FUNCTION cadplot_gateway.lock_catalog_rows(
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
AS $lock_catalog_rows$
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
          AND (
              NOT p_require_online
              OR (
                  online = true
                  AND last_seen_at IS NOT NULL
                  AND presence_expires_at > clock_timestamp()
              )
          )
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
          AND (
              NOT p_require_online
              OR (
                  online = true
                  AND last_seen_at IS NOT NULL
                  AND presence_expires_at > clock_timestamp()
              )
          )
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
$lock_catalog_rows$;

REVOKE ALL ON FUNCTION cadplot_gateway.record_worker_presence(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_key_id,
    integer
) FROM PUBLIC;
REVOKE ALL ON FUNCTION cadplot_gateway.record_worker_presence(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_key_id,
    integer
) FROM cadplot_gateway_runtime;
GRANT EXECUTE ON FUNCTION cadplot_gateway.record_worker_presence(
    cadplot_gateway.tenant_id,
    cadplot_gateway.workstation_id,
    cadplot_gateway.worker_key_id,
    integer
) TO cadplot_gateway_runtime;

-- Direct workstation mutation remains unavailable to the runtime role; presence is the only new
-- write capability and is constrained by tenant RLS plus an active device verification key.

COMMIT;
