-- SafeAscent Database Schema for PostgreSQL + PostGIS
-- Created: 2026-01-25

-- =============================================================================
-- TABLE: mountains
-- =============================================================================

CREATE TABLE mountains (
    mountain_id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    alt_names TEXT,
    elevation_ft REAL,
    prominence_ft REAL,
    type VARCHAR(50),
    range VARCHAR(255),
    state VARCHAR(100),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    location TEXT,
    accident_count INTEGER DEFAULT 0,

    -- PostGIS geography column (better for distance calculations)
    coordinates GEOGRAPHY(POINT, 4326)
);

-- Create spatial index on coordinates
CREATE INDEX idx_mountains_coordinates ON mountains USING GIST(coordinates);

-- Create regular indexes
CREATE INDEX idx_mountains_name ON mountains(name);
CREATE INDEX idx_mountains_state ON mountains(state);

-- =============================================================================
-- TABLE: routes
-- =============================================================================

CREATE TABLE routes (
    route_id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    mountain_id INTEGER REFERENCES mountains(mountain_id),
    mountain_name VARCHAR(255),
    grade VARCHAR(50),
    grade_yds VARCHAR(50),
    length_ft REAL,
    pitches INTEGER,
    type VARCHAR(100),
    first_ascent_year INTEGER,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    accident_count INTEGER DEFAULT 0,
    mp_route_id VARCHAR(50),

    -- PostGIS geography column
    coordinates GEOGRAPHY(POINT, 4326)
);

-- Create spatial index
CREATE INDEX idx_routes_coordinates ON routes USING GIST(coordinates);

-- Create foreign key and regular indexes
CREATE INDEX idx_routes_mountain_id ON routes(mountain_id);
CREATE INDEX idx_routes_name ON routes(name);
CREATE INDEX idx_routes_mp_route_id ON routes(mp_route_id);

-- =============================================================================
-- TABLE: accidents
-- =============================================================================

CREATE TABLE accidents (
    accident_id INTEGER PRIMARY KEY,
    source VARCHAR(50),
    source_id VARCHAR(100),
    date DATE,
    year REAL,
    state VARCHAR(100),
    location TEXT,
    mountain VARCHAR(255),
    route VARCHAR(255),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    accident_type VARCHAR(100),
    activity VARCHAR(100),
    injury_severity VARCHAR(50),
    age_range VARCHAR(50),
    description TEXT,
    tags TEXT,
    mountain_id INTEGER REFERENCES mountains(mountain_id),
    route_id INTEGER REFERENCES routes(route_id),

    -- PostGIS geography column
    coordinates GEOGRAPHY(POINT, 4326)
);

-- Create spatial index
CREATE INDEX idx_accidents_coordinates ON accidents USING GIST(coordinates);

-- Create foreign key and regular indexes
CREATE INDEX idx_accidents_mountain_id ON accidents(mountain_id);
CREATE INDEX idx_accidents_route_id ON accidents(route_id);
CREATE INDEX idx_accidents_date ON accidents(date);
CREATE INDEX idx_accidents_state ON accidents(state);
CREATE INDEX idx_accidents_injury_severity ON accidents(injury_severity);
CREATE INDEX idx_accidents_accident_type ON accidents(accident_type);

-- =============================================================================
-- TABLE: weather
-- =============================================================================

CREATE TABLE weather (
    weather_id INTEGER PRIMARY KEY,
    accident_id INTEGER REFERENCES accidents(accident_id),
    date DATE NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    temperature_avg REAL,
    temperature_min REAL,
    temperature_max REAL,
    wind_speed_avg REAL,
    wind_speed_max REAL,
    precipitation_total REAL,
    visibility_avg REAL,
    cloud_cover_avg REAL,

    -- PostGIS geography column (rounded coordinates from collection)
    coordinates GEOGRAPHY(POINT, 4326)
);

-- Create spatial index
CREATE INDEX idx_weather_coordinates ON weather USING GIST(coordinates);

-- Create foreign key and regular indexes
CREATE INDEX idx_weather_accident_id ON weather(accident_id);
CREATE INDEX idx_weather_date ON weather(date);

-- Composite index for spatial-temporal queries
CREATE INDEX idx_weather_date_coords ON weather(date, latitude, longitude);

-- =============================================================================
-- TABLE: climbers
-- =============================================================================

CREATE TABLE climbers (
    climber_id INTEGER PRIMARY KEY,
    username VARCHAR(255) NOT NULL UNIQUE,
    mp_user_id VARCHAR(50)
);

-- Create index on username for lookups
CREATE INDEX idx_climbers_username ON climbers(username);
CREATE INDEX idx_climbers_mp_user_id ON climbers(mp_user_id);

-- =============================================================================
-- TABLE: ascents
-- =============================================================================

CREATE TABLE ascents (
    ascent_id INTEGER PRIMARY KEY,
    route_id INTEGER REFERENCES routes(route_id),
    climber_id INTEGER REFERENCES climbers(climber_id),
    date DATE,
    style VARCHAR(100),
    lead_style VARCHAR(100),
    pitches INTEGER,
    notes TEXT,
    mp_tick_id VARCHAR(50)
);

-- Create foreign key indexes
CREATE INDEX idx_ascents_route_id ON ascents(route_id);
CREATE INDEX idx_ascents_climber_id ON ascents(climber_id);
CREATE INDEX idx_ascents_date ON ascents(date);

-- =============================================================================
-- HELPER FUNCTIONS
-- =============================================================================

-- Function to automatically populate geography columns from lat/lon
CREATE OR REPLACE FUNCTION update_geography_from_coordinates()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.latitude IS NOT NULL AND NEW.longitude IS NOT NULL THEN
        NEW.coordinates = ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)::geography;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create triggers to auto-populate geography columns
CREATE TRIGGER mountains_geography_trigger
    BEFORE INSERT OR UPDATE ON mountains
    FOR EACH ROW
    EXECUTE FUNCTION update_geography_from_coordinates();

CREATE TRIGGER routes_geography_trigger
    BEFORE INSERT OR UPDATE ON routes
    FOR EACH ROW
    EXECUTE FUNCTION update_geography_from_coordinates();

CREATE TRIGGER accidents_geography_trigger
    BEFORE INSERT OR UPDATE ON accidents
    FOR EACH ROW
    EXECUTE FUNCTION update_geography_from_coordinates();

CREATE TRIGGER weather_geography_trigger
    BEFORE INSERT OR UPDATE ON weather
    FOR EACH ROW
    EXECUTE FUNCTION update_geography_from_coordinates();

-- =============================================================================
-- VIEWS FOR COMMON QUERIES
-- =============================================================================

-- View: Accidents with weather data
CREATE VIEW accidents_with_weather AS
SELECT
    a.accident_id,
    a.date AS accident_date,
    a.mountain,
    a.route,
    a.state,
    a.injury_severity,
    w.temperature_avg,
    w.temperature_min,
    w.temperature_max,
    w.wind_speed_avg,
    w.wind_speed_max,
    w.precipitation_total,
    w.cloud_cover_avg,
    a.coordinates
FROM accidents a
JOIN weather w ON a.accident_id = w.accident_id;

-- View: Accidents with full location details
CREATE VIEW accidents_full AS
SELECT
    a.*,
    m.name AS mountain_full_name,
    m.elevation_ft AS mountain_elevation,
    r.name AS route_full_name,
    r.grade AS route_grade
FROM accidents a
LEFT JOIN mountains m ON a.mountain_id = m.mountain_id
LEFT JOIN routes r ON a.route_id = r.route_id;

-- =============================================================================
-- SUMMARY STATS
-- =============================================================================

-- View: Database summary statistics
CREATE VIEW database_summary AS
SELECT
    (SELECT COUNT(*) FROM mountains) AS total_mountains,
    (SELECT COUNT(*) FROM routes) AS total_routes,
    (SELECT COUNT(*) FROM accidents) AS total_accidents,
    (SELECT COUNT(*) FROM weather) AS total_weather_records,
    (SELECT COUNT(*) FROM weather WHERE accident_id IS NOT NULL) AS accident_weather_records,
    (SELECT COUNT(*) FROM weather WHERE accident_id IS NULL) AS baseline_weather_records,
    (SELECT COUNT(*) FROM climbers) AS total_climbers,
    (SELECT COUNT(*) FROM ascents) AS total_ascents;

-- =============================================================================
-- COMMENTS
-- =============================================================================

COMMENT ON TABLE mountains IS 'Mountains and climbing areas with geographic data';
COMMENT ON TABLE routes IS 'Climbing routes linked to mountains';
COMMENT ON TABLE accidents IS 'Climbing accidents with location and details';
COMMENT ON TABLE weather IS 'Weather observations linked to accidents and baseline data';
COMMENT ON TABLE climbers IS 'Climber profiles from Mountain Project';
COMMENT ON TABLE ascents IS 'Successful ascent records from Mountain Project';

COMMENT ON COLUMN accidents.coordinates IS 'PostGIS geography point for spatial queries';
COMMENT ON COLUMN weather.accident_id IS 'NULL indicates baseline weather (not during accident)';
