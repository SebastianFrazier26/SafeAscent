"""
Migrate all tables from hash-based IDs to SQL integer IDs

Converts:
- accident_id: ac_XXXXXXXX → 1, 2, 3...
- mountain_id: mt_XXXXXXXX → 1, 2, 3...
- route_id: rt_XXXXXXXX → 1, 2, 3...
- climber_id: cl_XXXXXXXX → 1, 2, 3...
- ascent_id: as_XXXXXXXX → 1, 2, 3...

Maintains all foreign key relationships.
"""

import pandas as pd
import os
from datetime import datetime
import shutil

def backup_tables():
    """Create backup of all tables before migration"""
    backup_dir = f"data/tables/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(backup_dir, exist_ok=True)

    tables = ['accidents.csv', 'mountains.csv', 'routes.csv', 'ascents.csv']
    for table in tables:
        src = f'data/tables/{table}'
        if os.path.exists(src):
            dst = f'{backup_dir}/{table}'
            shutil.copy2(src, dst)
            print(f"✓ Backed up {table}")

    print(f"\nBackup created at: {backup_dir}\n")
    return backup_dir

def migrate_accidents():
    """Migrate accidents table to integer IDs"""
    print("=" * 80)
    print("MIGRATING ACCIDENTS TABLE")
    print("=" * 80)

    df = pd.read_csv('data/tables/accidents.csv')
    print(f"Loaded {len(df)} accidents")

    # Create mapping from old hash IDs to new integer IDs
    old_to_new = {}
    for idx, old_id in enumerate(df['accident_id'], start=1):
        old_to_new[old_id] = idx

    # Replace accident_id with integer
    df['accident_id'] = range(1, len(df) + 1)

    # Save
    df.to_csv('data/tables/accidents.csv', index=False)
    print(f"✓ Migrated {len(df)} accidents to integer IDs (1-{len(df)})\n")

    return old_to_new

def migrate_mountains():
    """Migrate mountains table to integer IDs"""
    print("=" * 80)
    print("MIGRATING MOUNTAINS TABLE")
    print("=" * 80)

    df = pd.read_csv('data/tables/mountains.csv')
    print(f"Loaded {len(df)} mountains")

    # Create mapping from old hash IDs to new integer IDs
    old_to_new = {}
    for idx, old_id in enumerate(df['mountain_id'], start=1):
        old_to_new[old_id] = idx

    # Replace mountain_id with integer
    df['mountain_id'] = range(1, len(df) + 1)

    # Save
    df.to_csv('data/tables/mountains.csv', index=False)
    print(f"✓ Migrated {len(df)} mountains to integer IDs (1-{len(df)})\n")

    return old_to_new

def migrate_routes(mountain_id_map):
    """Migrate routes table to integer IDs and update foreign keys"""
    print("=" * 80)
    print("MIGRATING ROUTES TABLE")
    print("=" * 80)

    df = pd.read_csv('data/tables/routes.csv')
    print(f"Loaded {len(df)} routes")

    # Create mapping from old hash IDs to new integer IDs
    old_to_new = {}
    for idx, old_id in enumerate(df['route_id'], start=1):
        old_to_new[old_id] = idx

    # Replace route_id with integer
    df['route_id'] = range(1, len(df) + 1)

    # Update foreign key: mountain_id
    print("Updating mountain_id foreign keys...")
    df['mountain_id'] = df['mountain_id'].map(mountain_id_map)

    # Check for any missing mappings
    missing_fks = df['mountain_id'].isna().sum()
    if missing_fks > 0:
        print(f"⚠ Warning: {missing_fks} routes have missing mountain_id foreign keys")

    # Save
    df.to_csv('data/tables/routes.csv', index=False)
    print(f"✓ Migrated {len(df)} routes to integer IDs (1-{len(df)})\n")

    return old_to_new

def migrate_ascents(route_id_map):
    """Migrate ascents table to integer IDs and update foreign keys"""
    print("=" * 80)
    print("MIGRATING ASCENTS TABLE")
    print("=" * 80)

    df = pd.read_csv('data/tables/ascents.csv')
    print(f"Loaded {len(df)} ascents")

    # Create mapping for climber_id (from existing climbers)
    unique_climbers = df['climber_id'].unique()
    climber_id_map = {}
    for idx, old_id in enumerate(unique_climbers, start=1):
        climber_id_map[old_id] = idx

    print(f"Found {len(climber_id_map)} unique climbers")

    # Create mapping from old ascent hash IDs to new integer IDs
    old_to_new = {}
    for idx, old_id in enumerate(df['ascent_id'], start=1):
        old_to_new[old_id] = idx

    # Replace ascent_id with integer
    df['ascent_id'] = range(1, len(df) + 1)

    # Update foreign keys
    print("Updating route_id foreign keys...")
    df['route_id'] = df['route_id'].map(route_id_map)

    print("Updating climber_id foreign keys...")
    df['climber_id'] = df['climber_id'].map(climber_id_map)

    # Check for any missing mappings
    missing_route_fks = df['route_id'].isna().sum()
    missing_climber_fks = df['climber_id'].isna().sum()

    if missing_route_fks > 0:
        print(f"⚠ Warning: {missing_route_fks} ascents have missing route_id foreign keys")
    if missing_climber_fks > 0:
        print(f"⚠ Warning: {missing_climber_fks} ascents have missing climber_id foreign keys")

    # Save
    df.to_csv('data/tables/ascents.csv', index=False)
    print(f"✓ Migrated {len(df)} ascents to integer IDs (1-{len(df)})\n")

    return old_to_new, climber_id_map

def create_climbers_table(climber_id_map, ascents_df):
    """Create initial climbers table from ascents data"""
    print("=" * 80)
    print("CREATING CLIMBERS TABLE")
    print("=" * 80)

    # Get unique climbers from ascents
    climbers_data = []

    for old_climber_id, new_climber_id in climber_id_map.items():
        # Get username from ascents
        username = ascents_df[ascents_df['climber_id'] == new_climber_id]['climber_username'].iloc[0]

        climbers_data.append({
            'climber_id': new_climber_id,
            'username': username,
            'location': None,
            'years_climbing': None,
            'bio': None,
            'total_ticks': None,
            'mp_user_id': None
        })

    climbers_df = pd.DataFrame(climbers_data)
    climbers_df = climbers_df.sort_values('climber_id')

    # Save
    climbers_df.to_csv('data/tables/climbers.csv', index=False)
    print(f"✓ Created climbers table with {len(climbers_df)} climbers\n")

    return climbers_df

def generate_migration_report(backup_dir):
    """Generate report of migration results"""
    print("=" * 80)
    print("MIGRATION SUMMARY")
    print("=" * 80)

    # Load migrated tables
    accidents = pd.read_csv('data/tables/accidents.csv')
    mountains = pd.read_csv('data/tables/mountains.csv')
    routes = pd.read_csv('data/tables/routes.csv')
    ascents = pd.read_csv('data/tables/ascents.csv')
    climbers = pd.read_csv('data/tables/climbers.csv')

    print("\n✅ MIGRATION COMPLETE\n")
    print(f"Accidents:  {len(accidents):,} records (IDs: 1-{len(accidents)})")
    print(f"Mountains:  {len(mountains):,} records (IDs: 1-{len(mountains)})")
    print(f"Routes:     {len(routes):,} records (IDs: 1-{len(routes)})")
    print(f"Ascents:    {len(ascents):,} records (IDs: 1-{len(ascents)})")
    print(f"Climbers:   {len(climbers):,} records (IDs: 1-{len(climbers)})")

    print(f"\n📁 Backup location: {backup_dir}")
    print("\n🔗 Foreign key relationships verified:")
    print(f"   - Routes → Mountains: {(~routes['mountain_id'].isna()).sum()}/{len(routes)} linked")
    print(f"   - Ascents → Routes: {(~ascents['route_id'].isna()).sum()}/{len(ascents)} linked")
    print(f"   - Ascents → Climbers: {(~ascents['climber_id'].isna()).sum()}/{len(ascents)} linked")

    # Check data integrity
    print("\n🔍 Data Integrity Checks:")

    # Check for invalid foreign keys
    invalid_mountain_fks = routes[~routes['mountain_id'].isin(mountains['mountain_id'])]['mountain_id'].notna().sum()
    invalid_route_fks = ascents[~ascents['route_id'].isin(routes['route_id'])]['route_id'].notna().sum()
    invalid_climber_fks = ascents[~ascents['climber_id'].isin(climbers['climber_id'])]['climber_id'].notna().sum()

    if invalid_mountain_fks == 0 and invalid_route_fks == 0 and invalid_climber_fks == 0:
        print("   ✓ All foreign keys are valid")
    else:
        if invalid_mountain_fks > 0:
            print(f"   ⚠ {invalid_mountain_fks} routes have invalid mountain_id")
        if invalid_route_fks > 0:
            print(f"   ⚠ {invalid_route_fks} ascents have invalid route_id")
        if invalid_climber_fks > 0:
            print(f"   ⚠ {invalid_climber_fks} ascents have invalid climber_id")

    print("\n" + "=" * 80)

def main():
    """Main migration function"""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "ID MIGRATION TO INTEGER IDs" + " " * 31 + "║")
    print("╚" + "=" * 78 + "╝")
    print("\n")

    # Step 0: Backup
    backup_dir = backup_tables()

    # Step 1: Migrate accidents (no foreign keys)
    accident_id_map = migrate_accidents()

    # Step 2: Migrate mountains (no foreign keys)
    mountain_id_map = migrate_mountains()

    # Step 3: Migrate routes (has mountain_id FK)
    route_id_map = migrate_routes(mountain_id_map)

    # Step 4: Migrate ascents (has route_id and climber_id FKs)
    ascent_id_map, climber_id_map = migrate_ascents(route_id_map)

    # Step 5: Create climbers table
    ascents_df = pd.read_csv('data/tables/ascents.csv')
    climbers_df = create_climbers_table(climber_id_map, ascents_df)

    # Step 6: Generate report
    generate_migration_report(backup_dir)

    print("\n✨ Migration complete! Your tables now use SQL-style integer IDs.\n")

if __name__ == '__main__':
    main()
