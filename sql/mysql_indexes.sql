CREATE INDEX idx_candidate_batches_jd_created
ON candidate_batches(jd_title, created_at);

CREATE INDEX idx_candidate_rows_batch_pool
ON candidate_rows(batch_id, candidate_pool);

CREATE INDEX idx_candidate_rows_batch_lock_status
ON candidate_rows(batch_id, lock_status);

CREATE INDEX idx_candidate_rows_batch_lock_owner
ON candidate_rows(batch_id, lock_owner_user_id);

CREATE INDEX idx_candidate_rows_batch_manual
ON candidate_rows(batch_id, manual_decision);

CREATE INDEX idx_reviews_timestamp
ON reviews(timestamp, id);

CREATE INDEX idx_candidate_action_logs_batch_created
ON candidate_action_logs(batch_id, created_at);

CREATE INDEX idx_candidate_action_logs_candidate_created
ON candidate_action_logs(candidate_id, created_at);

CREATE INDEX idx_candidate_action_logs_review_created
ON candidate_action_logs(review_id, created_at);
