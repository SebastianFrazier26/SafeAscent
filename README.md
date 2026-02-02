# SafeAscent

## Project Overview

Web application displaying climbing safety information based on past accident reports and current weather patterns. Based on previous data, makes predictive reports about the safety of particular routes/areas depending on the up-to-date (current day) weather reporting.

Given limited amount of available data. Algorithm utilizes exponential weighting based on both proximity to the route (using lon/lat) calculations, similarity to weather patterns, and timeframe. Taking in all available information (which is somewhat limited) try to make the best predictions possible.

Further, project should include analytics relating to accident types, experience levels, weather conditions, proximity, etc. to improve understanding of climbing accidents across the US.

Additionally, we want to take into account weather reporting in the area of route on not only the day of an accident but in a substantial time period preceeding it (which will require additional data) as freeze-thaw cycles, slickness, rockfall, and other conditions are created by weather conditions extending far before the day a climber gets on the rock.

## Frontend Components

For most frontend design, we'll utilize [Material Design 3](https://m3.material.io) for clean appearances.

For display, we utilize a map-based system with climbing zones /broader areas highlighted based on danger/risk on a green (safe) --> red (dangerous). The map output will comprise our "homepage"

Further, we display a general analytics dashboard showcasing key statistics on accidents, successful ascents, etc. for all routes as well as for individual routes/climbing areas (as is discussed later on)

## Backend Components

We utilize postgreSQL with postgis for coordinate mapping and search to advance our ability to link nearby routes/climbing areas and their weather reports.

Further, we want quick search functionality to look at analytics for any particular route or area in our database. Lookup will be available by route name or ID.

See earlier explanation of algorithm/strategy.

For more detailed descriptions of tables see modeling.md in ./data. Tables consist of:

* Ascents
* Routes
* Mountains
* Climbers
* Accidents
* Weather Reports

## Third-party Services

As mentioned we utilized PostgreSQL for the database.

We also will make use of a web-hosting service like Vercel or something similar for our frontend.
