-- Core tables (idempotent)
CREATE TABLE IF NOT EXISTS guild_settings (
  guild_id        BIGINT PRIMARY KEY,
  welcome_channel BIGINT,
  welcome_role    BIGINT,
  leave_channel   BIGINT,
  default_role    BIGINT,
  lang            TEXT,
  templates       JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS vc_overrides (
  guild_id       BIGINT    NOT NULL,
  channel_id     BIGINT    NOT NULL,
  override_roles JSONB     DEFAULT '[]'::jsonb,
  target_roles   JSONB     DEFAULT '[]'::jsonb,
  PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS vc_tracking (
  guild_id    BIGINT NOT NULL,
  channel_id  BIGINT NOT NULL,
  user_id     BIGINT NOT NULL,
  joined_at   TIMESTAMPTZ DEFAULT NOW()
);
