-- One row per (snapshot day, station, fuel): the price in force on that day.
-- price_key exists so that uniqueness can be tested with dbt's built-in test.
select
    concat_ws('|', snapshot_date, station_id, fuel_id) as price_key,
    snapshot_date,
    station_id,
    fuel_id,
    fuel_name,
    price_updated_at,
    price_eur_per_liter
from {{ ref('stg_prices') }}
