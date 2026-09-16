-- Monthly revenue by product category and region.
-- Feeds the "Category Performance" dashboard page.

WITH monthly_orders AS (
    SELECT
        o.order_id,
        o.customer_id,
        o.order_date,
        DATE_TRUNC('month', o.order_date) AS order_month,
        o.status
    FROM sales.orders o
    WHERE o.status <> 'cancelled'
      AND o.order_date >= :start_date
      AND o.order_date < :end_date
),

order_revenue AS (
    SELECT
        oi.order_id,
        SUM(oi.quantity * oi.unit_price)          AS gross_revenue,
        SUM(oi.quantity * oi.unit_price * oi.discount) AS discount_amount
    FROM sales.order_items oi
    GROUP BY oi.order_id
)

SELECT
    mo.order_month,
    p.category_name                               AS category,
    c.region                                      AS region,
    COUNT(DISTINCT mo.order_id)                   AS order_count,
    SUM(orv.gross_revenue)                        AS gross_revenue,
    SUM(orv.gross_revenue - orv.discount_amount)  AS net_revenue,
    CASE
        WHEN SUM(orv.gross_revenue) > 100000 THEN 'High'
        WHEN SUM(orv.gross_revenue) > 25000  THEN 'Medium'
        ELSE 'Low'
    END                                           AS revenue_band
FROM monthly_orders mo
INNER JOIN order_revenue orv ON orv.order_id = mo.order_id
INNER JOIN sales.customers c ON c.customer_id = mo.customer_id
LEFT JOIN sales.products p ON p.product_id = mo.order_id
WHERE c.region IS NOT NULL
GROUP BY mo.order_month, p.category_name, c.region
ORDER BY mo.order_month DESC, net_revenue DESC
