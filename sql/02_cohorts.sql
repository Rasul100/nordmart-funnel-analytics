-- Weekly acquisition cohorts, right-censored.
-- Week n of a user counts only if it is fully observed: first_seen + 7*(n+1) days <= end of data.
-- Without this the last, unfinished week of every cohort shows a fake drop to ~0.
WITH RECURSIVE weeks(week_n) AS (
  SELECT 0
  UNION ALL
  SELECT week_n + 1 FROM weeks WHERE week_n < 9
),
bounds AS (
  SELECT julianday(date(MAX(session_ts), '+1 day')) AS data_end FROM sessions
),
eligible AS (
  SELECT
    u.user_id,
    date(u.first_seen, 'weekday 0', '-6 days') AS cohort_week,
    w.week_n
  FROM users u
  CROSS JOIN weeks w
  CROSS JOIN bounds b
  WHERE julianday(u.first_seen) + 7 * (w.week_n + 1) <= b.data_end
),
active AS (
  SELECT DISTINCT
    s.user_id,
    CAST((julianday(s.session_ts) - julianday(u.first_seen)) / 7 AS INT) AS week_n
  FROM sessions s
  JOIN users u ON u.user_id = s.user_id
)
SELECT
  e.cohort_week,
  e.week_n,
  COUNT(*) AS cohort_size,
  COUNT(a.user_id) AS active_users,
  ROUND(1.0 * COUNT(a.user_id) / COUNT(*), 4) AS retention
FROM eligible e
LEFT JOIN active a ON a.user_id = e.user_id AND a.week_n = e.week_n
GROUP BY 1, 2
ORDER BY 1, 2;
