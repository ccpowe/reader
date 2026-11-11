-- Reader baseline schema (pg_dump of the squashed migration history,
-- 2026-11-10). Change the schema through new Alembic revisions.

--
-- Name: notify_reader_translation_work_v2(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.notify_reader_translation_work_v2() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'public'
    AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                PERFORM pg_notify('reader_translation_work_v2', NEW.id::text);
            ELSIF NEW.status = 'pending' AND (
                OLD.status IS DISTINCT FROM NEW.status
                OR OLD.priority IS DISTINCT FROM NEW.priority
                OR OLD.available_at IS DISTINCT FROM NEW.available_at
            ) THEN
                PERFORM pg_notify('reader_translation_work_v2', NEW.id::text);
            END IF;
            RETURN NEW;
        END;
        $$;

--
-- Name: prevent_translation_quota_audit_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.prevent_translation_quota_audit_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION 'translation quota audit is append-only';
        END;
        $$;

--
-- Name: app_schema_contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_schema_contracts (
    contract character varying(64) NOT NULL,
    installed_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: content_media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_media (
    id uuid NOT NULL,
    content_id uuid NOT NULL,
    media_type character varying(16) NOT NULL,
    original_url text NOT NULL,
    cached_path text,
    cache_status character varying(16) NOT NULL,
    mime_type character varying(200),
    width integer,
    height integer,
    duration_seconds integer,
    poster_url text,
    sha256 character varying(64),
    sort_order integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT ck_content_media_cache_status CHECK (((cache_status)::text = ANY ((ARRAY['external'::character varying, 'queued'::character varying, 'cached'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT ck_content_media_type CHECK (((media_type)::text = ANY ((ARRAY['image'::character varying, 'audio'::character varying, 'video'::character varying, 'embed'::character varying])::text[])))
);

--
-- Name: contents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contents (
    id uuid NOT NULL,
    kind character varying(16) NOT NULL,
    canonical_url text,
    url_hash character varying(64),
    title character varying(1000) NOT NULL,
    author_name character varying(300),
    published_at timestamp with time zone,
    excerpt text,
    body_text text,
    body_html text,
    content_hash character varying(64),
    extraction_status character varying(16) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    authority_source_id uuid NOT NULL,
    CONSTRAINT ck_contents_extraction_status CHECK (((extraction_status)::text = ANY ((ARRAY['not_needed'::character varying, 'pending'::character varying, 'success'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT ck_contents_kind CHECK (((kind)::text = ANY ((ARRAY['article'::character varying, 'post'::character varying, 'video'::character varying, 'podcast'::character varying])::text[])))
);

--
-- Name: feed_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.feed_sources (
    id uuid NOT NULL,
    kind character varying(32) NOT NULL,
    canonical_key character varying(1024) NOT NULL,
    canonical_url text NOT NULL,
    display_name character varying(300),
    config jsonb NOT NULL,
    visibility character varying(16) NOT NULL,
    owner_id uuid,
    status character varying(16) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    avatar_url text,
    latest_update_sequence bigint DEFAULT 0 NOT NULL,
    CONSTRAINT ck_feed_sources_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'active'::character varying, 'paused'::character varying])::text[]))),
    CONSTRAINT ck_feed_sources_visibility CHECK (((visibility)::text = ANY ((ARRAY['shared'::character varying, 'private'::character varying])::text[])))
);

--
-- Name: ingestion_candidates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingestion_candidates (
    id uuid NOT NULL,
    source_id uuid NOT NULL,
    native_id character varying(1024) NOT NULL,
    payload jsonb NOT NULL,
    payload_hash character varying(64) NOT NULL,
    source_updated_at timestamp with time zone,
    observed_at timestamp with time zone NOT NULL,
    suggested_feed_sort_at timestamp with time zone NOT NULL,
    status character varying(16) NOT NULL,
    attempt_count integer NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    completed_at timestamp with time zone,
    content_id uuid,
    last_error_code character varying(120),
    last_error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    counts_as_update boolean DEFAULT false NOT NULL,
    CONSTRAINT ck_ingestion_candidates_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'completed'::character varying, 'failed'::character varying])::text[])))
);

--
-- Name: profile_avatars; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.profile_avatars (
    user_id uuid NOT NULL,
    version uuid NOT NULL,
    content_type character varying(32) NOT NULL,
    data bytea NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_profile_avatars_size CHECK ((octet_length(data) <= 5242880))
);

--
-- Name: profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.profiles (
    id uuid NOT NULL,
    display_name character varying(120),
    avatar_url text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: provider_quota_usage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_quota_usage (
    provider character varying(64) NOT NULL,
    usage_date date NOT NULL,
    units integer NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: ranking_provider_budgets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ranking_provider_budgets (
    provider character varying(32) NOT NULL,
    window_started_at timestamp with time zone NOT NULL,
    refresh_count integer NOT NULL,
    blocked_until timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_ranking_provider_budget_count CHECK ((refresh_count >= 0))
);

--
-- Name: ranking_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ranking_snapshots (
    cache_key character varying(256) NOT NULL,
    kind character varying(32) NOT NULL,
    parameters jsonb NOT NULL,
    payload jsonb NOT NULL,
    fetched_at timestamp with time zone NOT NULL,
    next_refresh_at timestamp with time zone NOT NULL,
    last_error text,
    last_accessed_at timestamp with time zone DEFAULT now() NOT NULL,
    is_ready boolean DEFAULT false NOT NULL,
    refresh_token uuid,
    refresh_expires_at timestamp with time zone,
    manual_refresh_after timestamp with time zone,
    last_metrics jsonb,
    CONSTRAINT ck_ranking_snapshots_refresh_lease CHECK (((refresh_token IS NULL) = (refresh_expires_at IS NULL)))
);

--
-- Name: reader_refresh_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reader_refresh_tokens (
    token_hash character varying(64) NOT NULL,
    session_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    used_at timestamp with time zone
);

--
-- Name: reader_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reader_sessions (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone
);

--
-- Name: reader_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reader_users (
    id uuid NOT NULL,
    email character varying(320) NOT NULL,
    password_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: source_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_entries (
    id uuid NOT NULL,
    source_id uuid NOT NULL,
    content_id uuid NOT NULL,
    native_id character varying(1024) NOT NULL,
    external_url text NOT NULL,
    title character varying(1000) NOT NULL,
    author_name character varying(300),
    published_at timestamp with time zone,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_metadata jsonb NOT NULL,
    feed_sort_at timestamp with time zone NOT NULL,
    update_sequence bigint
);

--
-- Name: source_entry_update_sequence; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.source_entry_update_sequence
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: source_subscriptions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_subscriptions (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    source_id uuid NOT NULL,
    custom_name character varying(300),
    folder_name character varying(120),
    is_enabled boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    include_in_home boolean DEFAULT true NOT NULL,
    last_viewed_update_sequence bigint DEFAULT 0 NOT NULL
);

--
-- Name: source_sync_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_sync_runs (
    id uuid NOT NULL,
    source_id uuid NOT NULL,
    status character varying(16) NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    error_code character varying(120),
    error_message text,
    provider_mode character varying(64),
    metrics jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_source_sync_runs_status CHECK (((status)::text = ANY ((ARRAY['running'::character varying, 'success'::character varying, 'partial'::character varying, 'failed'::character varying])::text[])))
);

--
-- Name: source_sync_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_sync_states (
    source_id uuid NOT NULL,
    phase character varying(24) NOT NULL,
    committed_checkpoint jsonb NOT NULL,
    pending_checkpoint jsonb NOT NULL,
    continuation jsonb,
    provider_mode character varying(64),
    initial_sync_completed boolean NOT NULL,
    last_attempt_at timestamp with time zone,
    last_complete_at timestamp with time zone,
    next_scan_at timestamp with time zone,
    consecutive_failures integer NOT NULL,
    consecutive_limit_runs integer NOT NULL,
    gap_detected boolean NOT NULL,
    gap_details jsonb,
    last_error_code character varying(120),
    last_error_message text,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_source_sync_states_phase CHECK (((phase)::text = ANY ((ARRAY['idle'::character varying, 'catching_up'::character varying, 'degraded'::character varying])::text[])))
);

--
-- Name: translation_artifacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_artifacts (
    id uuid NOT NULL,
    owner_id uuid,
    purpose character varying(32) NOT NULL,
    scope character varying(16) NOT NULL,
    source_hash character varying(64) NOT NULL,
    source_locale character varying(16) DEFAULT 'auto'::character varying NOT NULL,
    target_locale character varying(16) NOT NULL,
    engine_fingerprint character varying(256) NOT NULL,
    provider_name character varying(64) NOT NULL,
    model_name character varying(160),
    prompt_version character varying(64) DEFAULT 'v1'::character varying NOT NULL,
    glossary_version character varying(64),
    translated_text text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    engine_id character varying(64) NOT NULL,
    CONSTRAINT ck_translation_artifacts_engine_id CHECK ((length((engine_id)::text) > 0)),
    CONSTRAINT ck_translation_artifacts_purpose CHECK (((purpose)::text = ANY ((ARRAY['title'::character varying, 'excerpt'::character varying, 'body'::character varying, 'paragraph'::character varying, 'ranking_title'::character varying, 'ranking_description'::character varying, 'web_segment'::character varying, 'caption'::character varying])::text[]))),
    CONSTRAINT ck_translation_artifacts_scope CHECK (((scope)::text = ANY ((ARRAY['shared'::character varying, 'user'::character varying])::text[]))),
    CONSTRAINT ck_translation_artifacts_scope_owner CHECK (((((scope)::text = 'shared'::text) AND (owner_id IS NULL)) OR (((scope)::text = 'user'::text) AND (owner_id IS NOT NULL))))
);

--
-- Name: translation_quota_audits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_quota_audits (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    actor character varying(160) NOT NULL,
    reason character varying(500) NOT NULL,
    action character varying(32) NOT NULL,
    requests integer,
    actual_miss_chars integer,
    is_disabled boolean,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    reference character varying(128),
    before_state jsonb,
    after_state jsonb,
    CONSTRAINT ck_translation_quota_audits_actor_safe CHECK ((((actor)::text ~ '^[A-Za-z0-9][A-Za-z0-9._@:+-]{0,159}$'::text) AND (lower((actor)::text) !~~ '%bearer%'::text) AND (lower((actor)::text) !~~ '%jwt%'::text) AND (lower((actor)::text) !~~ '%sk-%'::text) AND (lower((actor)::text) !~~ '%sb_%'::text) AND ((actor)::text !~~ '%://%'::text))),
    CONSTRAINT ck_translation_quota_audits_after_state CHECK (((after_state IS NULL) OR (after_state = 'null'::jsonb) OR ((jsonb_typeof(after_state) = 'object'::text) AND (after_state ?& ARRAY['requests'::text, 'actual_miss_chars'::text, 'is_disabled'::text]) AND ((((after_state - 'requests'::text) - 'actual_miss_chars'::text) - 'is_disabled'::text) = '{}'::jsonb)))),
    CONSTRAINT ck_translation_quota_audits_before_state CHECK (((before_state IS NULL) OR (before_state = 'null'::jsonb) OR ((jsonb_typeof(before_state) = 'object'::text) AND (before_state ?& ARRAY['requests'::text, 'actual_miss_chars'::text, 'is_disabled'::text]) AND ((((before_state - 'requests'::text) - 'actual_miss_chars'::text) - 'is_disabled'::text) = '{}'::jsonb)))),
    CONSTRAINT ck_translation_quota_audits_reason_code CHECK ((((reason)::text ~ '^[a-z][a-z0-9]*([_-][a-z0-9]+){0,7}$'::text) AND (lower((reason)::text) !~~ '%bearer%'::text) AND (lower((reason)::text) !~~ '%jwt%'::text) AND (lower((reason)::text) !~~ '%sk-%'::text) AND (lower((reason)::text) !~~ '%sb_%'::text) AND ((reason)::text !~~ '%://%'::text))),
    CONSTRAINT ck_translation_quota_audits_reference_safe CHECK (((reference IS NULL) OR (((reference)::text ~ '^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$'::text) AND (lower((reference)::text) !~~ '%bearer%'::text) AND (lower((reference)::text) !~~ '%jwt%'::text) AND (lower((reference)::text) !~~ '%sk-%'::text) AND (lower((reference)::text) !~~ '%sb_%'::text) AND ((reference)::text !~~ '%://%'::text))))
);

--
-- Name: translation_quota_buckets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_quota_buckets (
    bucket_key character varying(128) NOT NULL,
    user_id uuid,
    window_started_at timestamp with time zone NOT NULL,
    requests_used integer DEFAULT 0 NOT NULL,
    actual_miss_chars_used integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_translation_quota_actual_miss_chars_used CHECK ((actual_miss_chars_used >= 0)),
    CONSTRAINT ck_translation_quota_requests_used CHECK ((requests_used >= 0))
);

--
-- Name: translation_quota_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_quota_overrides (
    user_id uuid NOT NULL,
    requests integer,
    actual_miss_chars integer,
    is_disabled boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_translation_override_chars CHECK (((actual_miss_chars IS NULL) OR (actual_miss_chars >= 0))),
    CONSTRAINT ck_translation_override_requests CHECK (((requests IS NULL) OR (requests >= 0)))
);

--
-- Name: translation_work; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.translation_work (
    id uuid NOT NULL,
    owner_id uuid,
    purpose character varying(32) NOT NULL,
    scope character varying(16) NOT NULL,
    source_hash character varying(64) NOT NULL,
    source_text text NOT NULL,
    source_locale character varying(16) DEFAULT 'auto'::character varying NOT NULL,
    target_locale character varying(16) NOT NULL,
    engine_fingerprint character varying(256) NOT NULL,
    provider_name character varying(64) NOT NULL,
    model_name character varying(160),
    prompt_version character varying(64) DEFAULT 'v1'::character varying NOT NULL,
    glossary_version character varying(64),
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    last_attempt_started_at timestamp with time zone,
    last_attempt_finished_at timestamp with time zone,
    provider_latency_ms integer,
    error_code character varying(120),
    error_message text,
    error_retryable boolean,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    engine_id character varying(64) NOT NULL,
    context_before text,
    context_after text,
    CONSTRAINT ck_translation_work_engine_id CHECK ((length((engine_id)::text) > 0)),
    CONSTRAINT ck_translation_work_priority CHECK ((priority >= 0)),
    CONSTRAINT ck_translation_work_purpose CHECK (((purpose)::text = ANY ((ARRAY['title'::character varying, 'excerpt'::character varying, 'body'::character varying, 'paragraph'::character varying, 'ranking_title'::character varying, 'ranking_description'::character varying, 'web_segment'::character varying, 'caption'::character varying])::text[]))),
    CONSTRAINT ck_translation_work_running_lease CHECK (((((status)::text = 'running'::text) AND (lease_token IS NOT NULL) AND (lease_expires_at IS NOT NULL)) OR (((status)::text <> 'running'::text) AND (lease_token IS NULL) AND (lease_expires_at IS NULL)))),
    CONSTRAINT ck_translation_work_scope CHECK (((scope)::text = ANY ((ARRAY['shared'::character varying, 'user'::character varying])::text[]))),
    CONSTRAINT ck_translation_work_scope_owner CHECK (((((scope)::text = 'shared'::text) AND (owner_id IS NULL)) OR (((scope)::text = 'user'::text) AND (owner_id IS NOT NULL)))),
    CONSTRAINT ck_translation_work_source_text CHECK ((length(source_text) > 0)),
    CONSTRAINT ck_translation_work_status CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);

--
-- Name: user_content_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_content_states (
    user_id uuid NOT NULL,
    content_id uuid NOT NULL,
    is_read boolean NOT NULL,
    read_at timestamp with time zone,
    offline_requested boolean NOT NULL,
    last_opened_at timestamp with time zone,
    progress integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: user_saved_contents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_saved_contents (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    content_id uuid NOT NULL,
    collection_name character varying(120),
    note text,
    saved_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: user_translation_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_translation_preferences (
    user_id uuid NOT NULL,
    target_locale character varying(16) DEFAULT 'zh-CN'::character varying NOT NULL,
    provider_mode character varying(16) DEFAULT 'app_default'::character varying NOT NULL,
    provider_name character varying(64),
    model_name character varying(160),
    is_enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    engine_id character varying(64),
    CONSTRAINT ck_user_translation_preferences_provider_mode CHECK (((provider_mode)::text = ANY ((ARRAY['app_default'::character varying, 'app_managed'::character varying, 'byok'::character varying])::text[])))
);

--
-- Name: worker_loop_heartbeats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.worker_loop_heartbeats (
    worker_instance_id uuid NOT NULL,
    loop_name character varying(64) NOT NULL,
    expected_interval_seconds integer NOT NULL,
    last_started_at timestamp with time zone,
    last_succeeded_at timestamp with time zone,
    last_failed_at timestamp with time zone,
    last_error_code character varying(120),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_metrics jsonb,
    CONSTRAINT ck_worker_loop_heartbeats_expected_interval CHECK ((expected_interval_seconds > 0))
);

--
-- Name: app_schema_contracts app_schema_contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_schema_contracts
    ADD CONSTRAINT app_schema_contracts_pkey PRIMARY KEY (contract);

--
-- Name: content_media content_media_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_media
    ADD CONSTRAINT content_media_pkey PRIMARY KEY (id);

--
-- Name: contents contents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contents
    ADD CONSTRAINT contents_pkey PRIMARY KEY (id);

--
-- Name: feed_sources feed_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feed_sources
    ADD CONSTRAINT feed_sources_pkey PRIMARY KEY (id);

--
-- Name: ingestion_candidates ingestion_candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_candidates
    ADD CONSTRAINT ingestion_candidates_pkey PRIMARY KEY (id);

--
-- Name: worker_loop_heartbeats pk_worker_loop_heartbeats; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_loop_heartbeats
    ADD CONSTRAINT pk_worker_loop_heartbeats PRIMARY KEY (worker_instance_id, loop_name);

--
-- Name: profile_avatars profile_avatars_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profile_avatars
    ADD CONSTRAINT profile_avatars_pkey PRIMARY KEY (user_id);

--
-- Name: profiles profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profiles
    ADD CONSTRAINT profiles_pkey PRIMARY KEY (id);

--
-- Name: provider_quota_usage provider_quota_usage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_quota_usage
    ADD CONSTRAINT provider_quota_usage_pkey PRIMARY KEY (provider, usage_date);

--
-- Name: ranking_provider_budgets ranking_provider_budgets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ranking_provider_budgets
    ADD CONSTRAINT ranking_provider_budgets_pkey PRIMARY KEY (provider);

--
-- Name: ranking_snapshots ranking_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ranking_snapshots
    ADD CONSTRAINT ranking_snapshots_pkey PRIMARY KEY (cache_key);

--
-- Name: reader_refresh_tokens reader_refresh_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reader_refresh_tokens
    ADD CONSTRAINT reader_refresh_tokens_pkey PRIMARY KEY (token_hash);

--
-- Name: reader_sessions reader_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reader_sessions
    ADD CONSTRAINT reader_sessions_pkey PRIMARY KEY (id);

--
-- Name: reader_users reader_users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reader_users
    ADD CONSTRAINT reader_users_email_key UNIQUE (email);

--
-- Name: reader_users reader_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reader_users
    ADD CONSTRAINT reader_users_pkey PRIMARY KEY (id);

--
-- Name: source_entries source_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_entries
    ADD CONSTRAINT source_entries_pkey PRIMARY KEY (id);

--
-- Name: source_subscriptions source_subscriptions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_subscriptions
    ADD CONSTRAINT source_subscriptions_pkey PRIMARY KEY (id);

--
-- Name: source_sync_runs source_sync_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_sync_runs
    ADD CONSTRAINT source_sync_runs_pkey PRIMARY KEY (id);

--
-- Name: source_sync_states source_sync_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_sync_states
    ADD CONSTRAINT source_sync_states_pkey PRIMARY KEY (source_id);

--
-- Name: translation_artifacts translation_artifacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_artifacts
    ADD CONSTRAINT translation_artifacts_pkey PRIMARY KEY (id);

--
-- Name: translation_quota_audits translation_quota_audits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_quota_audits
    ADD CONSTRAINT translation_quota_audits_pkey PRIMARY KEY (id);

--
-- Name: translation_quota_buckets translation_quota_buckets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_quota_buckets
    ADD CONSTRAINT translation_quota_buckets_pkey PRIMARY KEY (bucket_key);

--
-- Name: translation_quota_overrides translation_quota_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_quota_overrides
    ADD CONSTRAINT translation_quota_overrides_pkey PRIMARY KEY (user_id);

--
-- Name: translation_work translation_work_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_work
    ADD CONSTRAINT translation_work_pkey PRIMARY KEY (id);

--
-- Name: content_media uq_content_media_content_url; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_media
    ADD CONSTRAINT uq_content_media_content_url UNIQUE (content_id, original_url);

--
-- Name: contents uq_contents_id_authority_source; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contents
    ADD CONSTRAINT uq_contents_id_authority_source UNIQUE (id, authority_source_id);

--
-- Name: feed_sources uq_feed_sources_canonical_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feed_sources
    ADD CONSTRAINT uq_feed_sources_canonical_key UNIQUE (canonical_key);

--
-- Name: ingestion_candidates uq_ingestion_candidates_source_native; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_candidates
    ADD CONSTRAINT uq_ingestion_candidates_source_native UNIQUE (source_id, native_id);

--
-- Name: source_entries uq_source_entries_source_native; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_entries
    ADD CONSTRAINT uq_source_entries_source_native UNIQUE (source_id, native_id);

--
-- Name: source_subscriptions uq_source_subscriptions_user_source; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_subscriptions
    ADD CONSTRAINT uq_source_subscriptions_user_source UNIQUE (user_id, source_id);

--
-- Name: user_saved_contents uq_user_saved_contents_user_content; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_saved_contents
    ADD CONSTRAINT uq_user_saved_contents_user_content UNIQUE (user_id, content_id);

--
-- Name: user_content_states user_content_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_content_states
    ADD CONSTRAINT user_content_states_pkey PRIMARY KEY (user_id, content_id);

--
-- Name: user_saved_contents user_saved_contents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_saved_contents
    ADD CONSTRAINT user_saved_contents_pkey PRIMARY KEY (id);

--
-- Name: user_translation_preferences user_translation_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_translation_preferences
    ADD CONSTRAINT user_translation_preferences_pkey PRIMARY KEY (user_id);

--
-- Name: ix_contents_authority_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_authority_source_id ON public.contents USING btree (authority_source_id);

--
-- Name: ix_contents_canonical_url; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_canonical_url ON public.contents USING btree (canonical_url);

--
-- Name: ix_contents_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_content_hash ON public.contents USING btree (content_hash);

--
-- Name: ix_contents_published_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_contents_published_at ON public.contents USING btree (published_at);

--
-- Name: ix_ingestion_candidates_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ingestion_candidates_claim ON public.ingestion_candidates USING btree (status, available_at, observed_at);

--
-- Name: ix_ingestion_candidates_source_observed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ingestion_candidates_source_observed ON public.ingestion_candidates USING btree (source_id, observed_at);

--
-- Name: ix_ranking_snapshots_inactive; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ranking_snapshots_inactive ON public.ranking_snapshots USING btree (kind, last_accessed_at);

--
-- Name: ix_ranking_snapshots_next_refresh; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ranking_snapshots_next_refresh ON public.ranking_snapshots USING btree (next_refresh_at);

--
-- Name: ix_reader_refresh_tokens_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reader_refresh_tokens_session_id ON public.reader_refresh_tokens USING btree (session_id);

--
-- Name: ix_reader_sessions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reader_sessions_user_id ON public.reader_sessions USING btree (user_id);

--
-- Name: ix_source_entries_content_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_entries_content_id ON public.source_entries USING btree (content_id);

--
-- Name: ix_source_entries_source_feed_sort; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_entries_source_feed_sort ON public.source_entries USING btree (source_id, feed_sort_at, id);

--
-- Name: ix_source_entries_source_update_sequence; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_entries_source_update_sequence ON public.source_entries USING btree (source_id, update_sequence);

--
-- Name: ix_source_subscriptions_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_subscriptions_source_id ON public.source_subscriptions USING btree (source_id);

--
-- Name: ix_source_subscriptions_user_enabled_folder_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_subscriptions_user_enabled_folder_source ON public.source_subscriptions USING btree (user_id, is_enabled, folder_name, source_id);

--
-- Name: ix_source_sync_runs_source_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_sync_runs_source_started ON public.source_sync_runs USING btree (source_id, started_at);

--
-- Name: ix_source_sync_states_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_sync_states_due ON public.source_sync_states USING btree (next_scan_at, lease_expires_at);

--
-- Name: ix_translation_quota_audits_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_quota_audits_user_created ON public.translation_quota_audits USING btree (user_id, created_at);

--
-- Name: ix_translation_quota_buckets_user_window; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_quota_buckets_user_window ON public.translation_quota_buckets USING btree (user_id, window_started_at);

--
-- Name: ix_translation_work_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translation_work_claim ON public.translation_work USING btree (engine_fingerprint, status, available_at, priority, created_at);

--
-- Name: ix_user_saved_contents_user_saved; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_saved_contents_user_saved ON public.user_saved_contents USING btree (user_id, saved_at);

--
-- Name: ix_user_translation_preferences_target_locale; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_translation_preferences_target_locale ON public.user_translation_preferences USING btree (target_locale);

--
-- Name: ix_worker_loop_heartbeats_loop_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_worker_loop_heartbeats_loop_updated ON public.worker_loop_heartbeats USING btree (loop_name, updated_at);

--
-- Name: uq_contents_authority_url_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_contents_authority_url_hash ON public.contents USING btree (authority_source_id, url_hash) WHERE (url_hash IS NOT NULL);

--
-- Name: uq_translation_artifacts_shared_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_translation_artifacts_shared_identity ON public.translation_artifacts USING btree (purpose, source_hash, source_locale, target_locale, engine_fingerprint) WHERE (owner_id IS NULL);

--
-- Name: uq_translation_artifacts_user_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_translation_artifacts_user_identity ON public.translation_artifacts USING btree (owner_id, purpose, source_hash, source_locale, target_locale, engine_fingerprint) WHERE (owner_id IS NOT NULL);

--
-- Name: uq_translation_work_shared_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_translation_work_shared_identity ON public.translation_work USING btree (purpose, source_hash, source_locale, target_locale, engine_fingerprint) WHERE (owner_id IS NULL);

--
-- Name: uq_translation_work_user_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_translation_work_user_identity ON public.translation_work USING btree (owner_id, purpose, source_hash, source_locale, target_locale, engine_fingerprint) WHERE (owner_id IS NOT NULL);

--
-- Name: translation_quota_audits translation_quota_audits_append_only; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER translation_quota_audits_append_only BEFORE DELETE OR UPDATE ON public.translation_quota_audits FOR EACH ROW EXECUTE FUNCTION public.prevent_translation_quota_audit_mutation();

--
-- Name: translation_work trg_notify_reader_translation_work_v2; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_notify_reader_translation_work_v2 AFTER INSERT OR UPDATE OF status, priority, available_at ON public.translation_work FOR EACH ROW EXECUTE FUNCTION public.notify_reader_translation_work_v2();

--
-- Name: content_media content_media_content_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_media
    ADD CONSTRAINT content_media_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.contents(id) ON DELETE CASCADE;

--
-- Name: contents fk_contents_authority_source_id_feed_sources; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contents
    ADD CONSTRAINT fk_contents_authority_source_id_feed_sources FOREIGN KEY (authority_source_id) REFERENCES public.feed_sources(id) ON DELETE CASCADE;

--
-- Name: source_entries fk_source_entries_content_authority; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_entries
    ADD CONSTRAINT fk_source_entries_content_authority FOREIGN KEY (content_id, source_id) REFERENCES public.contents(id, authority_source_id) ON DELETE CASCADE;

--
-- Name: ingestion_candidates ingestion_candidates_content_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_candidates
    ADD CONSTRAINT ingestion_candidates_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.contents(id) ON DELETE SET NULL;

--
-- Name: ingestion_candidates ingestion_candidates_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_candidates
    ADD CONSTRAINT ingestion_candidates_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.feed_sources(id) ON DELETE CASCADE;

--
-- Name: profile_avatars profile_avatars_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profile_avatars
    ADD CONSTRAINT profile_avatars_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: reader_refresh_tokens reader_refresh_tokens_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reader_refresh_tokens
    ADD CONSTRAINT reader_refresh_tokens_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.reader_sessions(id) ON DELETE CASCADE;

--
-- Name: reader_sessions reader_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reader_sessions
    ADD CONSTRAINT reader_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.reader_users(id) ON DELETE CASCADE;

--
-- Name: reader_users reader_users_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reader_users
    ADD CONSTRAINT reader_users_id_fkey FOREIGN KEY (id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: source_entries source_entries_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_entries
    ADD CONSTRAINT source_entries_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.feed_sources(id) ON DELETE CASCADE;

--
-- Name: source_subscriptions source_subscriptions_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_subscriptions
    ADD CONSTRAINT source_subscriptions_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.feed_sources(id) ON DELETE CASCADE;

--
-- Name: source_subscriptions source_subscriptions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_subscriptions
    ADD CONSTRAINT source_subscriptions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: source_sync_runs source_sync_runs_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_sync_runs
    ADD CONSTRAINT source_sync_runs_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.feed_sources(id) ON DELETE CASCADE;

--
-- Name: source_sync_states source_sync_states_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_sync_states
    ADD CONSTRAINT source_sync_states_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.feed_sources(id) ON DELETE CASCADE;

--
-- Name: translation_artifacts translation_artifacts_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_artifacts
    ADD CONSTRAINT translation_artifacts_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: translation_quota_audits translation_quota_audits_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_quota_audits
    ADD CONSTRAINT translation_quota_audits_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: translation_quota_buckets translation_quota_buckets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_quota_buckets
    ADD CONSTRAINT translation_quota_buckets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: translation_quota_overrides translation_quota_overrides_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_quota_overrides
    ADD CONSTRAINT translation_quota_overrides_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: translation_work translation_work_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translation_work
    ADD CONSTRAINT translation_work_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: user_content_states user_content_states_content_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_content_states
    ADD CONSTRAINT user_content_states_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.contents(id) ON DELETE CASCADE;

--
-- Name: user_content_states user_content_states_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_content_states
    ADD CONSTRAINT user_content_states_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: user_saved_contents user_saved_contents_content_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_saved_contents
    ADD CONSTRAINT user_saved_contents_content_id_fkey FOREIGN KEY (content_id) REFERENCES public.contents(id) ON DELETE CASCADE;

--
-- Name: user_saved_contents user_saved_contents_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_saved_contents
    ADD CONSTRAINT user_saved_contents_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: user_translation_preferences user_translation_preferences_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_translation_preferences
    ADD CONSTRAINT user_translation_preferences_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id) ON DELETE CASCADE;

--
-- Name: app_schema_contracts; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: content_media; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: contents; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: feed_sources; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: ingestion_candidates; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: profile_avatars; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: profiles; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: provider_quota_usage; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: ranking_provider_budgets; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: ranking_snapshots; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: reader_refresh_tokens; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: reader_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: reader_users; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: source_entries; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: source_subscriptions; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: source_sync_runs; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: source_sync_states; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: translation_artifacts; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: translation_quota_audits; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: translation_quota_buckets; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: translation_quota_overrides; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: translation_work; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: user_content_states; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: user_saved_contents; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: user_translation_preferences; Type: ROW SECURITY; Schema: public; Owner: -
--

--
-- Name: worker_loop_heartbeats; Type: ROW SECURITY; Schema: public; Owner: -
--
