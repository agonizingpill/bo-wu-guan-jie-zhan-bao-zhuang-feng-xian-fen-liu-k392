-- 月度低风险包装复查抽样：候选池、入选名单与回报均按月锁定，重跑不换样。
ALTER TABLE exhibit_loan ADD COLUMN IF NOT EXISTS dispatched_at timestamptz;

CREATE TABLE IF NOT EXISTS sampling_batch (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period text NOT NULL UNIQUE CHECK (period ~ '^[0-9]{4}-[0-9]{2}$'),
    status text NOT NULL CHECK (status IN ('locked')),
    sample_quota integer NOT NULL CHECK (sample_quota > 0),
    closed_at timestamptz NOT NULL DEFAULT now(),
    closed_by text NOT NULL REFERENCES staff,
    rng_seed bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS sampling_candidate (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES sampling_batch,
    loan_id bigint NOT NULL REFERENCES exhibit_loan,
    material text NOT NULL CHECK (material IN ('porcelain', 'paper', 'bronze')),
    selected boolean NOT NULL DEFAULT false,
    UNIQUE (batch_id, loan_id)
);
CREATE INDEX IF NOT EXISTS sampling_candidate_batch ON sampling_candidate (batch_id, material);

CREATE TABLE IF NOT EXISTS sampling_selection (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES sampling_batch,
    loan_id bigint NOT NULL REFERENCES exhibit_loan,
    material text NOT NULL CHECK (material IN ('porcelain', 'paper', 'bronze')),
    result text CHECK (result IN ('pass', 'fail')),
    finding text,
    reported_by text REFERENCES staff,
    reported_at timestamptz,
    UNIQUE (batch_id, loan_id),
    CHECK ((result IS NULL AND finding IS NULL AND reported_by IS NULL AND reported_at IS NULL)
        OR (result IS NOT NULL AND reported_by IS NOT NULL AND reported_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS sampling_selection_pending ON sampling_selection (batch_id, material) WHERE result IS NULL;
