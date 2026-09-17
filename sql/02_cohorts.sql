-- Weekly acquisition cohorts: share of users with a session in week_n after first_seen.
WITH user_cohort AS (
  SELECT
    user_id,
    date(first_seen, 'weekday 0', '-6 days') AS cohort_week
  FROM users
),
activity AS (
  SELECT
    c.cohort_week,
    CAST((julianday(s.session_ts) - julianday(c.first_seen)) / 7 AS INT) AS week_n,
    s.user_id
  FROM (
    SELECT
      user_id,
      first_seen,
      date(first_seen, 'weekday 0', '-6 days') AS cohort_week
    FROM users
  ) c
  JOIN sessions s ON s.user_id = c.user_id
)
SELECT
  a.cohort_week,
  a.week_n,
  COUNT(DISTINCT a.user_id) AS active_users,
  cs.cohort_size,
  ROUND(1.0 * COUNT(DISTINCT a.user_id) / cs.cohort_size, 4) AS retention
FROM activity a
JOIN (
  SELECT cohort_week, COUNT(*) AS cohort_size
  FROM user_cohort
  GROUP BY 1
) cs ON cs.cohort_week = a.cohort_week
WHERE a.week_n >= 0
GROUP BY 1, 2, 4
ORDER BY 1, 2;
