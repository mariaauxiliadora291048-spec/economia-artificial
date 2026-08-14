-- Persistent operational control-plane state.

CREATE TABLE provider_configs (
    id UUID PRIMARY KEY,
    provider TEXT NOT NULL,
    base_url TEXT,
    model TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    secret_reference TEXT,
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
    quota JSONB NOT NULL DEFAULT '{}'::jsonb,
    pricing JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE agent_runtime (
    agent_id UUID PRIMARY KEY REFERENCES agents(id),
    provider_config_id UUID REFERENCES provider_configs(id),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN (
        'created', 'starting', 'active', 'paused', 'waiting', 'error',
        'suspended', 'bankrupt', 'terminated'
    )),
    next_wake_at TIMESTAMPTZ,
    priority INTEGER NOT NULL DEFAULT 0,
    last_action TEXT,
    last_error TEXT,
    crash_count INTEGER NOT NULL DEFAULT 0,
    cycles_completed BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE agent_resources (
    agent_id UUID PRIMARY KEY REFERENCES agents(id),
    compute_units NUMERIC(18,4) NOT NULL,
    token_budget BIGINT NOT NULL,
    tool_budget BIGINT NOT NULL,
    storage_budget_mb BIGINT NOT NULL,
    network_budget BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE agent_lineage (
    parent_agent_id UUID NOT NULL REFERENCES agents(id),
    child_agent_id UUID NOT NULL UNIQUE REFERENCES agents(id),
    creation_tool_call_id UUID REFERENCES tool_calls(id),
    initial_capital NUMERIC(18,2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (parent_agent_id, child_agent_id)
);

CREATE TABLE organizations (
    id UUID PRIMARY KEY,
    simulation_run_id UUID NOT NULL REFERENCES simulation_runs(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'dissolved')),
    treasury_account TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    dissolved_at TIMESTAMPTZ
);

CREATE TABLE organization_members (
    organization_id UUID NOT NULL REFERENCES organizations(id),
    agent_id UUID NOT NULL REFERENCES agents(id),
    role TEXT NOT NULL,
    ownership_share NUMERIC(7,6) NOT NULL DEFAULT 0,
    joined_at TIMESTAMPTZ NOT NULL,
    left_at TIMESTAMPTZ,
    PRIMARY KEY (organization_id, agent_id)
);

CREATE INDEX ix_agent_runtime_due ON agent_runtime (next_wake_at)
    WHERE lifecycle IN ('starting', 'waiting', 'error');
