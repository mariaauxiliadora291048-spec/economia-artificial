-- Real-world autonomy foundation: persistent cognitive state and capability governance.

CREATE TABLE agent_memories (
    id UUID PRIMARY KEY,
    agent_id UUID NOT NULL REFERENCES agents(id),
    kind TEXT NOT NULL CHECK (kind IN ('episode', 'strategy', 'economic_outcome', 'operational_limit')),
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    salience NUMERIC(4,3) NOT NULL CHECK (salience BETWEEN 0 AND 1),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX ix_agent_memories_retrieval
    ON agent_memories (agent_id, salience DESC, created_at DESC);

CREATE TABLE capability_grants (
    id UUID PRIMARY KEY,
    agent_id UUID NOT NULL REFERENCES agents(id),
    capability TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    constraints JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX ux_active_capability_grant
    ON capability_grants (agent_id, capability)
    WHERE revoked_at IS NULL;

CREATE TABLE relationships (
    id UUID PRIMARY KEY,
    agent_id UUID NOT NULL REFERENCES agents(id),
    counterparty_type TEXT NOT NULL CHECK (counterparty_type IN ('agent', 'human', 'organization')),
    counterparty_id TEXT NOT NULL,
    trust_score NUMERIC(5,4) NOT NULL DEFAULT 0.5000,
    reputation_score NUMERIC(5,4) NOT NULL DEFAULT 0.5000,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (agent_id, counterparty_type, counterparty_id)
);

CREATE TABLE external_action_approvals (
    id UUID PRIMARY KEY,
    agent_id UUID NOT NULL REFERENCES agents(id),
    capability TEXT NOT NULL,
    action_fingerprint TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);

CREATE INDEX ix_external_action_approvals_active
    ON external_action_approvals (agent_id, capability, expires_at)
    WHERE consumed_at IS NULL;
