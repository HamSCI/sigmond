INSERT INTO pending_uploads(target_db,target_table,payload_json,queued_at,producer) VALUES
 ('wspr','spots','{"mode":"W2"}','2026-10-22T00:00:00Z','wspr-recorder'),
 ('psk','spots','{"mode":"FT8"}','2026-10-22T00:00:00Z',''),
 ('psk','spots','{"mode":"MSK144"}','2026-10-22T00:00:00Z','meteor-scatter'),
 ('timestd','events','{}','2026-10-22T00:00:00Z','');
