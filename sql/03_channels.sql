-- Channel quality: cheap traffic is not the same as buying traffic.
WITH sess AS (
  SELECT
    s.session_id,
    s.channel,
    MAX(e.event_name = 'add_to_cart') AS add_to_cart,
    MAX(e.event_name = 'purchase') AS purchase,
    SUM(CASE WHEN e.event_name = 'purchase' THEN e.revenue ELSE 0 END) AS revenue
  FROM sessions s
  JOIN events e ON e.session_id = s.session_id
  GROUP BY s.session_id, s.channel
)
SELECT
  channel,
  COUNT(*) AS sessions,
  SUM(add_to_cart) AS carts,
  SUM(purchase) AS purchases,
  ROUND(1.0 * SUM(add_to_cart) / COUNT(*), 4) AS cart_rate,
  ROUND(1.0 * SUM(purchase) / COUNT(*), 4) AS session_cr,
  ROUND(SUM(revenue), 2) AS revenue,
  ROUND(SUM(revenue) / COUNT(*), 2) AS revenue_per_session
FROM sess
GROUP BY channel
ORDER BY revenue DESC;
