-- Apply once to a hosted Supabase PostgreSQL database as a database owner.
-- No existing generator tables or files are changed by this migration.
BEGIN;
CREATE SCHEMA IF NOT EXISTS video_editor;
CREATE TABLE video_editor.workspaces (
    id text PRIMARY KEY,
    name text NOT NULL
);
CREATE TABLE video_editor.members (
    workspace_id text NOT NULL REFERENCES video_editor.workspaces,
    user_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('owner', 'editor', 'reviewer')),
    PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE video_editor.edit_plans (
    id uuid PRIMARY KEY,
    workspace_id text NOT NULL REFERENCES video_editor.workspaces,
    project_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    plan jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, project_id, version)
);
CREATE TABLE video_editor.render_jobs (
    id uuid PRIMARY KEY,
    plan_id uuid NOT NULL REFERENCES video_editor.edit_plans,
    workspace_id text NOT NULL REFERENCES video_editor.workspaces,
    format text NOT NULL CHECK (format IN ('9:16', '4:5', '1:1', '16:9')),
    quality text NOT NULL CHECK (quality IN ('preview', 'master')),
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'ready', 'failed')),
    approved_by uuid,
    approved_at timestamptz,
    lease_token uuid,
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    output jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (quality <> 'master' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);
CREATE FUNCTION video_editor.render_plan_workspace() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM video_editor.edit_plans p WHERE p.id=NEW.plan_id AND p.workspace_id=NEW.workspace_id) THEN
        RAISE EXCEPTION 'Render plan workspace mismatch';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER render_plan_workspace BEFORE INSERT OR UPDATE ON video_editor.render_jobs
FOR EACH ROW EXECUTE FUNCTION video_editor.render_plan_workspace();
CREATE INDEX render_jobs_queue ON video_editor.render_jobs (created_at) WHERE state IN ('queued', 'running');
CREATE TABLE video_editor.usage_ledger (
    job_id uuid NOT NULL REFERENCES video_editor.render_jobs,
    attempt integer NOT NULL,
    workspace_id text NOT NULL REFERENCES video_editor.workspaces,
    render_minutes numeric NOT NULL CHECK (render_minutes >= 0),
    estimated_usd numeric NOT NULL CHECK (estimated_usd >= 0),
    status text NOT NULL CHECK (status IN ('running', 'ready', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, attempt)
);
CREATE FUNCTION video_editor.can_read_workspace(workspace text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT EXISTS (SELECT 1 FROM video_editor.members m
                   WHERE m.workspace_id = workspace AND m.user_id = auth.uid())
$$;
REVOKE ALL ON FUNCTION video_editor.can_read_workspace(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION video_editor.can_read_workspace(text) TO authenticated;
ALTER TABLE video_editor.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_editor.members ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_editor.edit_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_editor.render_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_editor.usage_ledger ENABLE ROW LEVEL SECURITY;
CREATE POLICY workspaces_read ON video_editor.workspaces FOR SELECT TO authenticated USING (video_editor.can_read_workspace(id));
CREATE POLICY members_read ON video_editor.members FOR SELECT TO authenticated USING (user_id = auth.uid());
CREATE POLICY plans_read ON video_editor.edit_plans FOR SELECT TO authenticated USING (video_editor.can_read_workspace(workspace_id));
CREATE POLICY jobs_read ON video_editor.render_jobs FOR SELECT TO authenticated USING (video_editor.can_read_workspace(workspace_id));
CREATE POLICY usage_read ON video_editor.usage_ledger FOR SELECT TO authenticated USING (video_editor.can_read_workspace(workspace_id));
-- Worker/admin writes use a server-only connection. Browser users receive read access only.
GRANT USAGE ON SCHEMA video_editor TO authenticated;
GRANT SELECT ON ALL TABLES IN SCHEMA video_editor TO authenticated;
CREATE FUNCTION video_editor.immutable_plan() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Edit plans are immutable. Save a new version.';
END $$;
CREATE TRIGGER edit_plan_immutable BEFORE UPDATE ON video_editor.edit_plans
FOR EACH ROW EXECUTE FUNCTION video_editor.immutable_plan();
COMMIT;
