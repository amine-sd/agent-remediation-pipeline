-- Average, min and max price per department, fuel and day.
select
    concat_ws('|', p.snapshot_date, s.department_code, p.fuel_name) as agg_key,
    p.snapshot_date,
    s.department_code,
    p.fuel_name,
    count(*) as n_stations,
    avg(p.price_eur_per_liter) as avg_price,
    min(p.price_eur_per_liter) as min_price,
    max(p.price_eur_per_liter) as max_price
from {{ ref('fct_prices') }} as p
join {{ ref('dim_stations') }} as s using (station_id)
group by all
