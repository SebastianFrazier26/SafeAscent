"""
Apply manual corrections extracted from detailed accident report analysis
"""

import pandas as pd

# Manual corrections based on careful reading of accident reports
manual_corrections = [
    {
        'accident_id': 'ac_ec68a8e1',
        'date': '2001-08-01',  # "early August" - need to check publication year
        'mountain': 'Rainier',
        'route': 'Disappointment Cleaver',
        'location': 'Mount Rainier National Park',
        'state': 'Washington',
        'notes': 'Guided climb, died from cardiovascular disease at 12,000 ft'
    },
    {
        'accident_id': 'ac_fecf88a9',
        'date': '1989-04-18',
        'mountain': 'Buller',
        'route': 'WSW Face Snow Gullies',
        'location': 'Rocky Mountains',
        'state': 'Alberta',
        'notes': 'Solo climber killed by rockfall at 2600m headwall'
    },
    {
        'accident_id': 'ac_f0d5ece5',
        'date': '1989-06-02',
        'mountain': 'Nublock',
        'route': 'Descent route above Lake Agnes',
        'location': 'Rocky Mountains',
        'state': 'Alberta',
        'notes': 'Fell through snow bridge into hidden waterfall while descending'
    },
    {
        'accident_id': 'ac_fe0a4194',
        'date': '1989-07-02',  # July 2, not July 1
        'mountain': 'Andromeda',
        'route': 'West Shoulder Direct',
        'location': 'Rocky Mountains',
        'state': 'Alberta',
        'notes': 'Lead climber fell, pulled out belay, both fell entire route. No helmets.'
    },
    {
        'accident_id': 'ac_4e57af8a',
        'date': '1989-08-05',
        'mountain': 'Tantalus',
        'route': 'Gully to Tantalus-Dione Col',
        'location': 'Coast Mountains',
        'state': 'British Columbia',
        'notes': 'Fell 35m into bergschrund, massive head injuries. North Shore Rescue training exercise.'
    },
    {
        'accident_id': 'ac_19abacf9',
        'date': '1989-10-06',
        'mountain': 'Whitney',
        'route': 'Mountaineers Route (descent after East Face)',
        'location': 'Mount Whitney',
        'state': 'California',
        'notes': 'Slid 250m on hard snow without ice axe/crampons, hit rocks'
    },
    {
        'accident_id': 'ac_96c436e1',
        'date': '1990-06-10',
        'mountain': 'McKinley',
        'route': 'West Rib',
        'location': 'Denali National Park',
        'state': 'Alaska',
        'notes': 'Died from pulmonary edema at 5975m. Left behind while team summited.'
    },
    {
        'accident_id': 'ac_2e17b6f6',
        'date': '1990-08-04',
        'mountain': 'Adams',
        'route': 'Mazama Glacier',
        'location': 'Mount Adams',
        'state': 'Washington',
        'notes': 'Climber sat down unexpectedly, slid into crevasse. Partner tried to stop him, both fell.'
    },
    {
        'accident_id': 'ac_c9aacf69',
        'date': '1990-08-27',
        'mountain': 'Katahdin',
        'route': 'Knife Edge near Pamola Peak',
        'location': 'Baxter State Park',
        'state': 'Maine',
        'notes': 'Boy Scout struck by lightning. Brief afternoon storm.'
    },
    {
        'accident_id': 'ac_f76addbc',
        'date': '1990-08-15',  # August 1990, no specific date
        'mountain': 'Assiniboine',
        'route': 'Northeast Ridge',
        'location': 'Rocky Mountains',
        'state': 'British Columbia',
        'notes': 'Slipped on loose rock while traversing unroped during descent, fell 700m'
    }
]

def apply_corrections():
    """Apply manual corrections to accidents database"""
    df = pd.read_csv('data/accidents.csv')

    print("=" * 80)
    print("APPLYING MANUAL CORRECTIONS")
    print("=" * 80)

    for correction in manual_corrections:
        accident_id = correction['accident_id']
        idx = df[df['accident_id'] == accident_id].index

        if len(idx) == 0:
            print(f"\nWARNING: Could not find accident {accident_id}")
            continue

        idx = idx[0]

        print(f"\nUpdating {accident_id}:")
        print(f"  Before: {df.at[idx, 'date']} | {df.at[idx, 'mountain']} | {df.at[idx, 'route']}")

        # Apply updates
        if 'date' in correction:
            df.at[idx, 'date'] = correction['date']
        if 'mountain' in correction:
            df.at[idx, 'mountain'] = correction['mountain']
        if 'route' in correction:
            df.at[idx, 'route'] = correction['route']
        if 'location' in correction:
            df.at[idx, 'location'] = correction['location']
        if 'state' in correction:
            df.at[idx, 'state'] = correction['state']
        if 'notes' in correction:
            # Append notes to existing tags
            current_tags = df.at[idx, 'tags'] if pd.notna(df.at[idx, 'tags']) else ''
            df.at[idx, 'tags'] = f"{current_tags} | MANUAL_NOTES: {correction['notes']}"

        print(f"  After:  {df.at[idx, 'date']} | {df.at[idx, 'mountain']} | {df.at[idx, 'route']}")

    # Save updated database
    df.to_csv('data/accidents.csv', index=False)

    print("\n" + "=" * 80)
    print(f"SUCCESS! Applied {len(manual_corrections)} manual corrections")
    print("=" * 80)

if __name__ == '__main__':
    apply_corrections()
