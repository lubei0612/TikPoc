create table public.tikpoc_accounts (
    account_id text primary key,
    device_id text not null unique,
    expected_username text unique,
    browser_profile_label text not null default '',
    enabled boolean not null default true,
    browser_followback_enabled boolean not null default false,
    browser_dm_enabled boolean not null default false,
    updated_at timestamptz not null default now()
);

create table public.tikpoc_target_pools (
    pool_id text primary key,
    source_name text not null,
    source_checksum text not null unique,
    source_rows integer not null check (source_rows >= 0),
    unique_targets integer not null check (unique_targets >= 0),
    imported_at timestamptz not null default now()
);

create table public.tikpoc_targets (
    pool_id text not null references public.tikpoc_target_pools(pool_id) on delete cascade,
    identity_key text not null,
    target_id text not null,
    username text not null,
    profile_url text not null default '',
    sec_uid text not null default '',
    source_video_id text not null default '',
    follower_count integer check (follower_count >= 0),
    following_count integer check (following_count >= 0),
    video_count integer check (video_count >= 0),
    private_account boolean,
    source_line_numbers integer[] not null default '{}',
    created_at timestamptz not null default now(),
    primary key (pool_id, identity_key)
);

create index tikpoc_targets_username_idx
    on public.tikpoc_targets (lower(username));

create table public.tikpoc_runs (
    run_id text primary key,
    pool_id text not null references public.tikpoc_target_pools(pool_id),
    state text not null check (state in ('scheduled', 'running', 'paused', 'stopped', 'completed', 'failed')),
    account_count integer not null check (account_count > 0),
    target_count integer not null check (target_count >= 0),
    required_visits integer not null check (required_visits >= 0),
    confirmed_visits integer not null default 0 check (confirmed_visits >= 0),
    started_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz not null default now()
);

create table public.tikpoc_device_health (
    device_id text primary key,
    account_id text references public.tikpoc_accounts(account_id),
    state text not null,
    adb_state text not null default '',
    tiktok_version text not null default '',
    login_state text not null default '',
    proxy_state text not null default '',
    detail jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null default now()
);

create table public.tikpoc_leads (
    account_id text not null references public.tikpoc_accounts(account_id),
    conversation_id text not null,
    participant_username text not null default '',
    stage text not null default 'new',
    meaningful_turns integer not null default 0 check (meaningful_turns >= 0),
    auto_reply_count integer not null default 0 check (auto_reply_count >= 0),
    last_inbound_at timestamptz,
    last_outbound_at timestamptz,
    last_invited_at timestamptz,
    contact_captured_at timestamptz,
    human_required boolean not null default false,
    updated_at timestamptz not null default now(),
    primary key (account_id, conversation_id)
);

create table public.tikpoc_lead_events (
    account_id text not null references public.tikpoc_accounts(account_id),
    source_key text not null,
    stage text not null,
    conversation_id text not null default '',
    participant_username text not null default '',
    occurred_at timestamptz not null,
    created_at timestamptz not null default now(),
    primary key (account_id, stage, source_key)
);

create index tikpoc_lead_events_occurred_idx
    on public.tikpoc_lead_events (occurred_at desc);

create table public.tikpoc_sales (
    sale_id text primary key,
    account_id text not null references public.tikpoc_accounts(account_id),
    conversation_id text not null,
    amount numeric(14, 2) not null check (amount >= 0),
    currency text not null,
    outcome text not null,
    recorded_at timestamptz not null default now()
);

create table public.tikpoc_sync_checkpoints (
    stream_name text primary key,
    cursor_value text not null default '',
    detail jsonb not null default '{}'::jsonb,
    synced_at timestamptz not null default now()
);

alter table public.tikpoc_accounts enable row level security;
alter table public.tikpoc_target_pools enable row level security;
alter table public.tikpoc_targets enable row level security;
alter table public.tikpoc_runs enable row level security;
alter table public.tikpoc_device_health enable row level security;
alter table public.tikpoc_leads enable row level security;
alter table public.tikpoc_lead_events enable row level security;
alter table public.tikpoc_sales enable row level security;
alter table public.tikpoc_sync_checkpoints enable row level security;

revoke all on table
    public.tikpoc_accounts,
    public.tikpoc_target_pools,
    public.tikpoc_targets,
    public.tikpoc_runs,
    public.tikpoc_device_health,
    public.tikpoc_leads,
    public.tikpoc_lead_events,
    public.tikpoc_sales,
    public.tikpoc_sync_checkpoints
from anon, authenticated;

grant select, insert, update, delete on table
    public.tikpoc_accounts,
    public.tikpoc_target_pools,
    public.tikpoc_targets,
    public.tikpoc_runs,
    public.tikpoc_device_health,
    public.tikpoc_leads,
    public.tikpoc_lead_events,
    public.tikpoc_sales,
    public.tikpoc_sync_checkpoints
to service_role;
