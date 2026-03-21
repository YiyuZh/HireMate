CREATE TABLE IF NOT EXISTS app_meta (
    key_name VARCHAR(255) NOT NULL,
    value_text LONGTEXT NOT NULL,
    PRIMARY KEY (key_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(191) NOT NULL,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    password_hash TEXT NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    is_admin TINYINT(1) NOT NULL DEFAULT 0,
    created_at VARCHAR(19) NOT NULL,
    updated_at VARCHAR(19) NOT NULL,
    last_login_at VARCHAR(19) NOT NULL DEFAULT '',
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS jobs (
    title VARCHAR(255) NOT NULL,
    jd_text LONGTEXT NOT NULL,
    openings INT NOT NULL DEFAULT 0,
    created_by_user_id VARCHAR(191) NOT NULL DEFAULT '',
    created_by_name VARCHAR(255) NOT NULL DEFAULT '',
    created_by_email VARCHAR(255) NOT NULL DEFAULT '',
    updated_by_user_id VARCHAR(191) NOT NULL DEFAULT '',
    updated_by_name VARCHAR(255) NOT NULL DEFAULT '',
    updated_by_email VARCHAR(255) NOT NULL DEFAULT '',
    created_at VARCHAR(19) NOT NULL,
    updated_at VARCHAR(19) NOT NULL,
    PRIMARY KEY (title)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS job_scoring_configs (
    job_title VARCHAR(255) NOT NULL,
    scoring_config_json LONGTEXT NOT NULL,
    updated_at VARCHAR(19) NOT NULL,
    PRIMARY KEY (job_title),
    CONSTRAINT fk_job_scoring_configs_job_title
        FOREIGN KEY (job_title) REFERENCES jobs(title) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS reviews (
    id BIGINT NOT NULL AUTO_INCREMENT,
    review_id VARCHAR(255) NULL DEFAULT NULL,
    timestamp VARCHAR(19) NOT NULL,
    updated_at VARCHAR(19) NOT NULL,
    jd_title VARCHAR(255) NOT NULL DEFAULT '',
    resume_name VARCHAR(255) NOT NULL DEFAULT '',
    resume_file VARCHAR(512) NOT NULL DEFAULT '',
    auto_screening_result VARCHAR(255) NOT NULL DEFAULT '',
    auto_risk_level VARCHAR(64) NOT NULL DEFAULT 'unknown',
    reviewed_by_user_id VARCHAR(191) NOT NULL DEFAULT '',
    reviewed_by_name VARCHAR(255) NOT NULL DEFAULT '',
    reviewed_by_email VARCHAR(255) NOT NULL DEFAULT '',
    manual_decision VARCHAR(255) NOT NULL DEFAULT '',
    manual_note LONGTEXT NOT NULL,
    manual_priority VARCHAR(64) NOT NULL DEFAULT '',
    scores_json LONGTEXT NOT NULL,
    screening_reasons_json LONGTEXT NOT NULL,
    risk_points_json LONGTEXT NOT NULL,
    interview_summary LONGTEXT NOT NULL,
    evidence_snippets_json LONGTEXT NOT NULL,
    record_json LONGTEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_reviews_review_id (review_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS candidate_batches (
    batch_id VARCHAR(191) NOT NULL,
    jd_title VARCHAR(255) NOT NULL,
    created_by_user_id VARCHAR(191) NOT NULL DEFAULT '',
    created_by_name VARCHAR(255) NOT NULL DEFAULT '',
    created_by_email VARCHAR(255) NOT NULL DEFAULT '',
    created_at VARCHAR(19) NOT NULL,
    total_resumes INT NOT NULL DEFAULT 0,
    candidate_count INT NOT NULL DEFAULT 0,
    pass_count INT NOT NULL DEFAULT 0,
    review_count INT NOT NULL DEFAULT 0,
    reject_count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS candidate_rows (
    id BIGINT NOT NULL AUTO_INCREMENT,
    batch_id VARCHAR(191) NOT NULL,
    candidate_id VARCHAR(191) NOT NULL,
    candidate_name VARCHAR(255) NOT NULL DEFAULT '',
    source_name VARCHAR(255) NOT NULL DEFAULT '',
    parse_status VARCHAR(128) NOT NULL DEFAULT '',
    screening_result VARCHAR(255) NOT NULL DEFAULT '',
    risk_level VARCHAR(64) NOT NULL DEFAULT 'unknown',
    candidate_pool VARCHAR(255) NOT NULL DEFAULT '',
    manual_decision VARCHAR(255) NOT NULL DEFAULT '',
    manual_note LONGTEXT NOT NULL,
    manual_priority VARCHAR(64) NOT NULL DEFAULT '',
    review_summary LONGTEXT NOT NULL,
    scores_json LONGTEXT NOT NULL,
    extract_info_json LONGTEXT NOT NULL,
    row_json LONGTEXT NOT NULL,
    detail_json LONGTEXT NOT NULL,
    created_by_user_id VARCHAR(191) NOT NULL DEFAULT '',
    created_by_name VARCHAR(255) NOT NULL DEFAULT '',
    created_by_email VARCHAR(255) NOT NULL DEFAULT '',
    last_operated_by_user_id VARCHAR(191) NOT NULL DEFAULT '',
    last_operated_by_name VARCHAR(255) NOT NULL DEFAULT '',
    last_operated_by_email VARCHAR(255) NOT NULL DEFAULT '',
    last_operated_at VARCHAR(19) NOT NULL DEFAULT '',
    lock_status VARCHAR(32) NOT NULL DEFAULT 'unlocked',
    lock_owner_user_id VARCHAR(191) NOT NULL DEFAULT '',
    lock_owner_name VARCHAR(255) NOT NULL DEFAULT '',
    lock_owner_email VARCHAR(255) NOT NULL DEFAULT '',
    lock_acquired_at VARCHAR(19) NOT NULL DEFAULT '',
    lock_expires_at VARCHAR(19) NOT NULL DEFAULT '',
    lock_last_heartbeat_at VARCHAR(19) NOT NULL DEFAULT '',
    lock_reason VARCHAR(255) NOT NULL DEFAULT '',
    created_at VARCHAR(19) NOT NULL,
    updated_at VARCHAR(19) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_candidate_rows_batch_candidate (batch_id, candidate_id),
    CONSTRAINT fk_candidate_rows_batch_id
        FOREIGN KEY (batch_id) REFERENCES candidate_batches(batch_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS candidate_action_logs (
    id BIGINT NOT NULL AUTO_INCREMENT,
    action_id VARCHAR(191) NOT NULL,
    batch_id VARCHAR(191) NOT NULL DEFAULT '',
    candidate_id VARCHAR(191) NOT NULL DEFAULT '',
    review_id VARCHAR(255) NOT NULL DEFAULT '',
    jd_title VARCHAR(255) NOT NULL DEFAULT '',
    action_type VARCHAR(128) NOT NULL DEFAULT '',
    operator_user_id VARCHAR(191) NOT NULL DEFAULT '',
    operator_name VARCHAR(255) NOT NULL DEFAULT '',
    operator_email VARCHAR(255) NOT NULL DEFAULT '',
    before_json LONGTEXT NOT NULL,
    after_json LONGTEXT NOT NULL,
    extra_json LONGTEXT NOT NULL,
    created_at VARCHAR(19) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_candidate_action_logs_action_id (action_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
