-- Economia Artificial: PostgreSQL source-of-truth schema.
-- Every monetary change is recorded as a balanced, append-only transaction.

CREATE TABLE simulation_runs (
    id UUID PRIMARY KEY,
    seed BIGINT NOT NULL,
    model_id TEXT NOT NULL,
    config JSONB NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ
);

CREATE TABLE agents (
    id UUID PRIMARY KEY,
    simulation_run_id UUID NOT NULL REFERENCES simulation_runs(id),
    name TEXT NOT NULL,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'bankrupt', 'suspended', 'quarantined')),
    objective TEXT NOT NULL DEFAULT 'maximize_net_worth',
    reputation NUMERIC(5,4) NOT NULL DEFAULT 0.5000,
    created_at TIMESTAMPTZ NOT NULL,
    bankrupt_at TIMESTAMPTZ,
    UNIQUE (simulation_run_id, name)
);

CREATE TABLE wallets (
    id UUID PRIMARY KEY,
    agent_id UUID NOT NULL UNIQUE REFERENCES agents(id),
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    cash NUMERIC(18,2) NOT NULL DEFAULT 0,
    reserved_cash NUMERIC(18,2) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE customers (
    id UUID PRIMARY KEY,
    simulation_run_id UUID NOT NULL REFERENCES simulation_runs(id),
    budget NUMERIC(18,2) NOT NULL CHECK (budget >= 0),
    need_intensity NUMERIC(5,4) NOT NULL CHECK (need_intensity BETWEEN 0 AND 1),
    price_sensitivity NUMERIC(5,4) NOT NULL CHECK (price_sensitivity BETWEEN 0 AND 1),
    reputation_sensitivity NUMERIC(5,4) NOT NULL CHECK (reputation_sensitivity BETWEEN 0 AND 1),
    fit_sensitivity NUMERIC(5,4) NOT NULL CHECK (fit_sensitivity BETWEEN 0 AND 1),
    segment TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE products (
    id UUID PRIMARY KEY,
    owner_agent_id UUID NOT NULL REFERENCES agents(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    price NUMERIC(18,2),
    status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'paused')),
    units_sold INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE agent_cycles (
    id UUID PRIMARY KEY,
    agent_id UUID NOT NULL REFERENCES agents(id),
    simulation_run_id UUID NOT NULL REFERENCES simulation_runs(id),
    cycle_number INTEGER NOT NULL,
    state_snapshot JSONB NOT NULL,
    model_input JSONB NOT NULL,
    model_output JSONB,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    UNIQUE (agent_id, cycle_number)
);

CREATE TABLE tool_calls (
    id UUID PRIMARY KEY,
    agent_id UUID NOT NULL REFERENCES agents(id),
    cycle_id UUID REFERENCES agent_cycles(id),
    tool_name TEXT NOT NULL,
    arguments JSONB NOT NULL,
    validation_status TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    cost NUMERIC(18,4) NOT NULL DEFAULT 0,
    result JSONB,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE transactions (
    id UUID PRIMARY KEY,
    simulation_run_id UUID NOT NULL REFERENCES simulation_runs(id),
    agent_id UUID REFERENCES agents(id),
    type TEXT NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'USD',
    reference_type TEXT,
    reference_id UUID,
    description TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE transaction_entries (
    id BIGSERIAL PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES transactions(id),
    account TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    amount NUMERIC(18,2) NOT NULL CHECK (amount > 0)
);

CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    simulation_run_id UUID NOT NULL REFERENCES simulation_runs(id),
    event_type TEXT NOT NULL,
    agent_id UUID REFERENCES agents(id),
    cycle_id UUID REFERENCES agent_cycles(id),
    entity_type TEXT,
    entity_id UUID,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX ix_events_simulation_created_at ON events (simulation_run_id, created_at);
CREATE INDEX ix_tool_calls_agent_created_at ON tool_calls (agent_id, created_at);
CREATE INDEX ix_products_category_published ON products (category) WHERE status = 'published';
