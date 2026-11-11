-- Reader baseline schema, compiled from reader/models.py on 2026-11-10.
-- Frozen: change the schema with new Alembic revisions, not by editing this file.


CREATE TABLE board_snapshots (
	key VARCHAR(200) NOT NULL,
	board VARCHAR(32) NOT NULL,
	params JSONB DEFAULT '{}'::jsonb NOT NULL,
	items JSONB DEFAULT '[]'::jsonb NOT NULL,
	fetched_at TIMESTAMP WITH TIME ZONE NOT NULL,
	last_error TEXT,
	paused_until TIMESTAMP WITH TIME ZONE,
	manual_refresh_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (key)
);

CREATE INDEX ix_board_snapshots_fetched_at ON board_snapshots (fetched_at);

CREATE TABLE provider_usage (
	provider VARCHAR(64) NOT NULL,
	window_start TIMESTAMP WITH TIME ZONE NOT NULL,
	units INTEGER DEFAULT 0 NOT NULL,
	paused_until TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (provider, window_start)
);

CREATE TABLE sources (
	id UUID NOT NULL,
	kind VARCHAR(16) NOT NULL,
	canonical_key VARCHAR(1024) NOT NULL,
	url TEXT NOT NULL,
	display_name VARCHAR(300),
	icon_url TEXT,
	config JSONB DEFAULT '{}'::jsonb NOT NULL,
	status VARCHAR(16) DEFAULT 'pending' NOT NULL,
	latest_sequence BIGINT DEFAULT 0 NOT NULL,
	initial_scan_done BOOLEAN DEFAULT false NOT NULL,
	next_scan_at TIMESTAMP WITH TIME ZONE,
	lease_token UUID,
	lease_expires_at TIMESTAMP WITH TIME ZONE,
	last_success_at TIMESTAMP WITH TIME ZONE,
	last_new_items INTEGER DEFAULT 0 NOT NULL,
	unchanged_scans INTEGER DEFAULT 0 NOT NULL,
	consecutive_failures INTEGER DEFAULT 0 NOT NULL,
	last_error_code VARCHAR(120),
	last_error_message TEXT,
	may_have_gap BOOLEAN DEFAULT false NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_sources_kind CHECK (kind IN ('rss', 'youtube', 'x', 'reddit')),
	CONSTRAINT ck_sources_status CHECK (status IN ('pending', 'active', 'paused')),
	UNIQUE (canonical_key)
);

CREATE INDEX ix_sources_next_scan_at ON sources (next_scan_at);

CREATE TABLE translation_budget (
	period VARCHAR(8) NOT NULL,
	window_start TIMESTAMP WITH TIME ZONE NOT NULL,
	chars_used BIGINT DEFAULT 0 NOT NULL,
	PRIMARY KEY (period, window_start),
	CONSTRAINT ck_translation_budget_period CHECK (period IN ('minute', 'day'))
);

CREATE TABLE translations (
	id UUID NOT NULL,
	purpose VARCHAR(32) NOT NULL,
	source_hash VARCHAR(64) NOT NULL,
	target_locale VARCHAR(16) NOT NULL,
	engine_fingerprint VARCHAR(128) NOT NULL,
	source_text TEXT NOT NULL,
	translated_text TEXT,
	status VARCHAR(16) NOT NULL,
	attempts INTEGER DEFAULT 0 NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	last_used_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	error_code VARCHAR(120),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_translations_purpose CHECK (purpose IN ('title', 'paragraph', 'board_title', 'board_description', 'web_segment', 'caption')),
	CONSTRAINT ck_translations_status CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
	CONSTRAINT uq_translations_identity UNIQUE (purpose, source_hash, target_locale, engine_fingerprint)
);

CREATE INDEX ix_translations_last_used ON translations (last_used_at);

CREATE INDEX ix_translations_status_created ON translations (status, created_at);

CREATE TABLE users (
	id UUID NOT NULL,
	email VARCHAR(320) NOT NULL,
	password_hash TEXT NOT NULL,
	display_name VARCHAR(120),
	avatar_data BYTEA,
	avatar_content_type VARCHAR(32),
	avatar_version INTEGER DEFAULT 0 NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (email)
);

CREATE TABLE worker_heartbeats (
	loop_name VARCHAR(64) NOT NULL,
	expected_interval_seconds INTEGER NOT NULL,
	last_started_at TIMESTAMP WITH TIME ZONE,
	last_succeeded_at TIMESTAMP WITH TIME ZONE,
	last_failed_at TIMESTAMP WITH TIME ZONE,
	last_error_code VARCHAR(120),
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (loop_name)
);

CREATE TABLE articles (
	id UUID NOT NULL,
	source_id UUID,
	native_id VARCHAR(1024) NOT NULL,
	url TEXT NOT NULL,
	url_hash VARCHAR(64) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	title VARCHAR(1000) NOT NULL,
	author VARCHAR(300),
	published_at TIMESTAMP WITH TIME ZONE,
	summary TEXT,
	body_html TEXT,
	media JSONB DEFAULT '[]'::jsonb NOT NULL,
	extra JSONB DEFAULT '{}'::jsonb NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	sequence BIGINT,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_articles_kind CHECK (kind IN ('article', 'post', 'video')),
	CONSTRAINT uq_articles_source_native UNIQUE (source_id, native_id),
	FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE CASCADE
);

CREATE INDEX ix_articles_source_first_seen ON articles (source_id, first_seen_at, id);

CREATE INDEX ix_articles_source_sequence ON articles (source_id, sequence);

CREATE UNIQUE INDEX uq_articles_board_url ON articles (url_hash) WHERE source_id IS NULL;

CREATE TABLE sessions (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_sessions_user_id ON sessions (user_id);

CREATE TABLE source_scan_runs (
	id UUID NOT NULL,
	source_id UUID NOT NULL,
	status VARCHAR(16) NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	items_read INTEGER DEFAULT 0 NOT NULL,
	new_items INTEGER DEFAULT 0 NOT NULL,
	error_code VARCHAR(120),
	error_message TEXT,
	PRIMARY KEY (id),
	CONSTRAINT ck_source_scan_runs_status CHECK (status IN ('running', 'success', 'failed')),
	FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE CASCADE
);

CREATE INDEX ix_source_scan_runs_source_started ON source_scan_runs (source_id, started_at);

CREATE TABLE subscriptions (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	source_id UUID NOT NULL,
	custom_name VARCHAR(300),
	group_name VARCHAR(120),
	is_enabled BOOLEAN DEFAULT true NOT NULL,
	include_in_home BOOLEAN DEFAULT true NOT NULL,
	last_viewed_sequence BIGINT DEFAULT 0 NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_subscriptions_user_source UNIQUE (user_id, source_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE CASCADE
);

CREATE INDEX ix_subscriptions_source_id ON subscriptions (source_id);

CREATE INDEX ix_subscriptions_user_group ON subscriptions (user_id, is_enabled, group_name);

CREATE TABLE translation_preferences (
	user_id UUID NOT NULL,
	target_locale VARCHAR(16) NOT NULL,
	enabled BOOLEAN DEFAULT true NOT NULL,
	engine_id VARCHAR(64),
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (user_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE refresh_tokens (
	token_hash VARCHAR(64) NOT NULL,
	session_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	used_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (token_hash),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
);

CREATE INDEX ix_refresh_tokens_session_id ON refresh_tokens (session_id);

CREATE TABLE saved_articles (
	user_id UUID NOT NULL,
	article_id UUID NOT NULL,
	saved_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (user_id, article_id),
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
	FOREIGN KEY(article_id) REFERENCES articles (id) ON DELETE CASCADE
);

CREATE INDEX ix_saved_articles_user_saved ON saved_articles (user_id, saved_at);
