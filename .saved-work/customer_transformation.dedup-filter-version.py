import duckdb

con = duckdb.connect("data/warehouse.duckdb")

con.execute(
    r"""
    CREATE OR REPLACE TABLE customer_target AS
    WITH transformed AS (
        SELECT
            CAST(customer_id AS VARCHAR) AS customer_id,
            CAST(first_name AS VARCHAR) AS first_name,
            CAST(last_name AS VARCHAR) AS last_name,
            COALESCE(
                TRY_CAST(dob AS DATE),
                TRY_STRPTIME(dob, '%d/%m/%Y')::DATE
            ) AS birth_date,
            LOWER(CAST(email AS VARCHAR)) AS email,
            CAST(country AS VARCHAR) AS country,
            TRY_CAST(signup_date AS DATE) AS signup_date,
            CAST(status AS VARCHAR) AS status
        FROM raw_customers
    ),
    -- Drop rows that fail contract requirements: missing/malformed data.
    valid AS (
        SELECT *
        FROM transformed
        WHERE customer_id IS NOT NULL
          AND birth_date IS NOT NULL
          AND email IS NOT NULL
          AND regexp_matches(email, '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
          AND country IS NOT NULL
          AND regexp_matches(country, '^[A-Za-z]{2}$')
          AND signup_date IS NOT NULL
          AND signup_date <= CURRENT_DATE
          AND status IN ('active', 'inactive', 'pending')
    ),
    -- For duplicate customer_id, latest signup_date wins.
    deduped AS (
        SELECT
            *,
            ROW_NUMBER() OVER (
                PARTITION BY customer_id
                ORDER BY signup_date DESC
            ) AS rn
        FROM valid
    )
    SELECT
        customer_id,
        first_name,
        last_name,
        birth_date,
        email,
        country,
        signup_date,
        status
    FROM deduped
    WHERE rn = 1
    """
)

con.close()
