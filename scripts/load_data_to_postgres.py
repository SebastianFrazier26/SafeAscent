"""
Load CSV Data into PostgreSQL + PostGIS

Loads all SafeAscent CSV tables into the PostgreSQL database.
Handles NULL values, date formatting, and coordinate conversion.
"""

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from tqdm import tqdm

# Database connection parameters
DB_CONFIG = {
    'dbname': 'safeascent',
    'user': 'sebastianfrazier',
    'host': 'localhost',
    'port': 5432
}

def connect_db():
    """Connect to PostgreSQL database"""
    return psycopg2.connect(**DB_CONFIG)

def load_mountains(conn):
    """Load mountains table"""
    print("\n📍 Loading mountains...")

    df = pd.read_csv('data/tables/mountains.csv')

    # Replace NaN with None for SQL NULL
    # Convert numpy types to native Python types (psycopg2 requirement)
    df = df.astype(object).where(pd.notna(df), None)

    cursor = conn.cursor()

    query = """
        INSERT INTO mountains (
            mountain_id, name, alt_names, elevation_ft, prominence_ft,
            type, range, state, latitude, longitude, location, accident_count
        ) VALUES %s
    """

    data = [tuple(row) for row in df.values]
    execute_values(cursor, query, data)

    conn.commit()
    print(f"  ✅ Loaded {len(df):,} mountains")

def load_routes(conn):
    """Load routes table"""
    print("\n🧗 Loading routes...")

    df = pd.read_csv('data/tables/routes.csv')

    # Convert mp_route_id to string (check for NaN explicitly)
    df['mp_route_id'] = df['mp_route_id'].apply(lambda x: str(int(x)) if pd.notna(x) else None)

    # Convert grade_yds to string
    df['grade_yds'] = df['grade_yds'].apply(lambda x: str(x) if pd.notna(x) else None)

    # Convert columns that should be integers
    df['pitches'] = df['pitches'].apply(lambda x: int(x) if pd.notna(x) else None)
    df['first_ascent_year'] = df['first_ascent_year'].apply(lambda x: int(x) if pd.notna(x) else None)

    # Replace remaining NaN with None
    df = df.where(pd.notna(df), None)

    # Convert numpy types to native Python types (psycopg2 requirement)
    df = df.astype(object).where(pd.notna(df), None)

    cursor = conn.cursor()

    query = """
        INSERT INTO routes (
            route_id, name, mountain_id, mountain_name, grade, grade_yds,
            length_ft, pitches, type, first_ascent_year, latitude, longitude,
            accident_count, mp_route_id
        ) VALUES %s
    """

    data = [tuple(row) for row in df.values]
    execute_values(cursor, query, data)

    conn.commit()
    print(f"  ✅ Loaded {len(df):,} routes")

def load_accidents(conn):
    """Load accidents table"""
    print("\n🚨 Loading accidents...")

    df = pd.read_csv('data/tables/accidents.csv')

    # Handle date conversion
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')

    # Convert numpy types to native Python types (psycopg2 requirement)
    df = df.astype(object).where(pd.notna(df), None)

    cursor = conn.cursor()

    query = """
        INSERT INTO accidents (
            accident_id, source, source_id, date, year, state, location,
            mountain, route, latitude, longitude, accident_type, activity,
            injury_severity, age_range, description, tags, mountain_id, route_id
        ) VALUES %s
    """

    # Use tqdm for progress bar on large dataset
    batch_size = 500
    total_loaded = 0

    for i in tqdm(range(0, len(df), batch_size), desc="  Loading batches"):
        batch = df.iloc[i:i+batch_size]
        data = [tuple(row) for row in batch.values]
        execute_values(cursor, query, data)
        conn.commit()
        total_loaded += len(batch)

    print(f"  ✅ Loaded {total_loaded:,} accidents")

def load_weather(conn):
    """Load weather table"""
    print("\n🌤️  Loading weather data...")

    df = pd.read_csv('data/tables/weather.csv')

    # Handle date conversion
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')

    # Convert numpy types to native Python types (psycopg2 requirement)
    df = df.astype(object).where(pd.notna(df), None)

    cursor = conn.cursor()

    query = """
        INSERT INTO weather (
            weather_id, accident_id, date, latitude, longitude,
            temperature_avg, temperature_min, temperature_max,
            wind_speed_avg, wind_speed_max, precipitation_total,
            visibility_avg, cloud_cover_avg
        ) VALUES %s
    """

    # Load in batches (large dataset)
    batch_size = 1000
    total_loaded = 0

    for i in tqdm(range(0, len(df), batch_size), desc="  Loading batches"):
        batch = df.iloc[i:i+batch_size]
        data = [tuple(row) for row in batch.values]
        execute_values(cursor, query, data)
        conn.commit()
        total_loaded += len(batch)

    print(f"  ✅ Loaded {total_loaded:,} weather records")

def load_climbers(conn):
    """Load climbers table"""
    print("\n🧗‍♀️ Loading climbers...")

    df = pd.read_csv('data/tables/climbers.csv')

    # Select only the columns that exist in the database schema
    df = df[['climber_id', 'username', 'mp_user_id']]

    # Convert mp_user_id to string (external ID)
    if 'mp_user_id' in df.columns:
        df['mp_user_id'] = df['mp_user_id'].apply(lambda x: str(int(x)) if pd.notna(x) else None)

    # De-duplicate usernames: prefer entries with mp_user_id
    # Sort so entries with mp_user_id come first, then drop duplicates keeping first
    df = df.sort_values('mp_user_id', na_position='last')
    df = df.drop_duplicates(subset='username', keep='first')

    # Reset climber_id to ensure uniqueness after deduplication
    df = df.reset_index(drop=True)
    df['climber_id'] = df.index + 1

    # Handle remaining NaN -> None
    # Convert numpy types to native Python types (psycopg2 requirement)
    df = df.astype(object).where(pd.notna(df), None)

    cursor = conn.cursor()

    query = """
        INSERT INTO climbers (
            climber_id, username, mp_user_id
        ) VALUES %s
    """

    data = [tuple(row) for row in df.values]
    execute_values(cursor, query, data)

    conn.commit()
    print(f"  ✅ Loaded {len(df):,} climbers")

def load_ascents(conn):
    """Load ascents table"""
    print("\n⛰️  Loading ascents...")

    df = pd.read_csv('data/tables/ascents.csv')

    # Get the climbers from database to map username to new climber_id
    cursor = conn.cursor()
    cursor.execute("SELECT climber_id, username FROM climbers")
    climbers_mapping = {username: climber_id for climber_id, username in cursor.fetchall()}

    # Map climber_id using climber_username
    df['climber_id'] = df['climber_username'].map(climbers_mapping)

    # Filter out ascents with no matching climber (after deduplication)
    original_count = len(df)
    df = df[df['climber_id'].notna()]
    filtered_count = len(df)
    if filtered_count < original_count:
        print(f"  ⚠️  Filtered out {original_count - filtered_count} ascents with unmapped climbers")

    # Select only columns that exist in the schema
    df = df[['ascent_id', 'route_id', 'climber_id', 'date', 'style', 'pitches', 'notes']]

    # Add missing columns with None
    df['lead_style'] = None
    df['mp_tick_id'] = None

    # Handle date conversion
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')

    # Convert pitches to integer
    df['pitches'] = df['pitches'].apply(lambda x: int(x) if pd.notna(x) else None)

    # Reorder columns to match schema
    df = df[['ascent_id', 'route_id', 'climber_id', 'date', 'style', 'lead_style', 'pitches', 'notes', 'mp_tick_id']]

    # Handle remaining NaN -> None
    # Convert numpy types to native Python types (psycopg2 requirement)
    df = df.astype(object).where(pd.notna(df), None)

    query = """
        INSERT INTO ascents (
            ascent_id, route_id, climber_id, date, style,
            lead_style, pitches, notes, mp_tick_id
        ) VALUES %s
    """

    data = [tuple(row) for row in df.values]
    execute_values(cursor, query, data)

    conn.commit()
    print(f"  ✅ Loaded {len(df):,} ascents")

def verify_data(conn):
    """Verify data was loaded correctly"""
    print("\n" + "=" * 80)
    print("VERIFICATION")
    print("=" * 80)

    cursor = conn.cursor()

    # Get row counts
    cursor.execute("SELECT * FROM database_summary;")
    summary = cursor.fetchone()

    print(f"\n📊 Database Statistics:")
    print(f"  Mountains: {summary[0]:,}")
    print(f"  Routes: {summary[1]:,}")
    print(f"  Accidents: {summary[2]:,}")
    print(f"  Weather Records: {summary[3]:,}")
    print(f"    - Accident weather: {summary[4]:,}")
    print(f"    - Baseline weather: {summary[5]:,}")
    print(f"  Climbers: {summary[6]:,}")
    print(f"  Ascents: {summary[7]:,}")

    # Test spatial queries
    print(f"\n🌍 Testing Spatial Queries:")

    # Test 1: Count accidents with valid coordinates
    cursor.execute("""
        SELECT COUNT(*) FROM accidents
        WHERE coordinates IS NOT NULL
    """)
    spatial_accidents = cursor.fetchone()[0]
    print(f"  Accidents with coordinates: {spatial_accidents:,}")

    # Test 2: Sample distance query (accidents near Mt. Rainier)
    cursor.execute("""
        SELECT COUNT(*) FROM accidents
        WHERE ST_DWithin(
            coordinates,
            ST_SetSRID(ST_MakePoint(-121.76, 46.85), 4326)::geography,
            10000  -- 10km radius
        )
    """)
    nearby_accidents = cursor.fetchone()[0]
    print(f"  Accidents within 10km of Mt. Rainier: {nearby_accidents:,}")

    # Test 3: Weather coverage
    cursor.execute("""
        SELECT COUNT(DISTINCT a.accident_id)
        FROM accidents a
        JOIN weather w ON a.accident_id = w.accident_id
    """)
    accidents_with_weather = cursor.fetchone()[0]
    print(f"  Accidents with weather data: {accidents_with_weather:,}")

    print(f"\n✅ All data loaded successfully!")

def main():
    """Main loading function"""
    print("\n" + "=" * 80)
    print("LOADING SAFEASCENT DATA INTO POSTGRESQL")
    print("=" * 80)

    try:
        conn = connect_db()
        print(f"\n✅ Connected to database: {DB_CONFIG['dbname']}")

        # Load tables in order (respecting foreign keys)
        load_mountains(conn)
        load_routes(conn)
        load_accidents(conn)
        load_weather(conn)
        load_climbers(conn)
        load_ascents(conn)

        # Verify
        verify_data(conn)

        conn.close()

        print("\n" + "=" * 80)
        print("DATA LOADING COMPLETE")
        print("=" * 80)
        print("\nYour PostgreSQL database is ready to use!")
        print("\nConnect with: psql safeascent")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise

if __name__ == '__main__':
    main()
