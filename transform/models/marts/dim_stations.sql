-- One row per station, with its most recent known attributes.
select
    * exclude (snapshot_date),
    snapshot_date as last_seen_date
from {{ ref('stg_stations') }}
qualify row_number() over (partition by station_id order by snapshot_date desc) = 1
