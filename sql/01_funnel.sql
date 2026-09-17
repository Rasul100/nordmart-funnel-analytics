-- Session funnel. A session is counted on a step if the event happened at least once.
WITH session_flags AS (
  SELECT
    s.session_id,
    s.device,
    MAX(e.event_name = 'session_start') AS session_start,
    MAX(e.event_name = 'view_item') AS view_item,
    MAX(e.event_name = 'add_to_cart') AS add_to_cart,
    MAX(e.event_name = 'begin_checkout') AS begin_checkout,
    MAX(e.event_name = 'purchase') AS purchase
  FROM sessions s
  JOIN events e ON e.session_id = s.session_id
  GROUP BY s.session_id, s.device
)
SELECT
  device,
  COUNT(*) AS sessions,
  SUM(view_item) AS view_item,
  SUM(add_to_cart) AS add_to_cart,
  SUM(begin_checkout) AS begin_checkout,
  SUM(purchase) AS purchase,
  ROUND(1.0 * SUM(purchase) / COUNT(*), 4) AS session_cr,
  ROUND(1.0 * SUM(purchase) / NULLIF(SUM(begin_checkout), 0), 4) AS checkout_to_pay
FROM session_flags
GROUP BY device
ORDER BY sessions DESC;
