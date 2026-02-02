# SafeAscent Frontend

React + Vite frontend for SafeAscent climbing safety predictions.

## Tech Stack

- **React 18** - UI framework
- **Vite** - Fast build tool and dev server
- **Mapbox GL JS** - Interactive 3D terrain maps
- **React-Map-GL** - React wrapper for Mapbox
- **Tailwind CSS** - Utility-first styling
- **Axios** - HTTP client for API calls
- **Date-fns** - Date utilities

## Setup

### 1. Install Dependencies

```bash
npm install
```

### 2. Get Mapbox Access Token

1. Go to https://account.mapbox.com/
2. Sign up for a free account (50,000 map loads/month free)
3. Copy your default public token

### 3. Configure Environment

Edit the `.env` file:

```env
# Your Mapbox token from https://account.mapbox.com/
VITE_MAPBOX_TOKEN=pk.ey...your_token_here

# Backend API URL (default: local development)
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

### 4. Start Backend (Required)

The frontend needs the backend API running:

```bash
cd ../backend
uvicorn app.main:app --reload
```

Backend should be running on http://localhost:8000

### 5. Start Frontend Dev Server

```bash
npm run dev
```

Frontend will be available at http://localhost:5173

## Project Structure

```
frontend/
├── src/
│   ├── components/       # React components
│   │   ├── MapView.jsx          # Interactive Mapbox map
│   │   ├── PredictionForm.jsx   # Route configuration form
│   │   └── PredictionResult.jsx # Risk score display
│   ├── services/         # API and external services
│   │   └── api.js               # Backend API client
│   ├── utils/            # Utility functions
│   │   └── riskUtils.js         # Risk interpretation helpers
│   ├── App.jsx           # Main application
│   ├── main.jsx          # React entry point
│   └── index.css         # Global styles (Tailwind)
├── .env                  # Environment variables
├── package.json          # Dependencies
├── tailwind.config.js    # Tailwind configuration
└── vite.config.js        # Vite configuration
```

## Features

### Interactive Map
- 3D terrain visualization
- Click-to-select route locations
- Risk score visualization
- Smooth animations

### Prediction Form
- Route type selection (alpine, trad, sport, ice, mixed, boulder)
- Date picker (next 14 days)
- Optional elevation input (auto-detected if omitted)
- Real-time validation

### Results Display
- Risk score (0-100) with color coding
- Confidence level
- Top contributing accidents
- Print-friendly report

## Development

### Available Commands

```bash
npm run dev      # Start dev server (with HMR)
npm run build    # Build for production
npm run preview  # Preview production build
npm run lint     # Run ESLint
```

### Hot Module Replacement (HMR)

The dev server supports HMR - changes to components will update instantly without page reload.

### Building for Production

```bash
npm run build
```

Optimized files will be in `dist/` directory.

## API Integration

The frontend communicates with the FastAPI backend at `/api/v1/predict`:

**Request:**
```json
{
  "latitude": 40.255,
  "longitude": -105.615,
  "route_type": "alpine",
  "planned_date": "2026-02-15",
  "elevation_meters": 4346  // optional
}
```

**Response:**
```json
{
  "risk_score": 75.2,
  "confidence": 82.5,
  "num_contributing_accidents": 42,
  "top_contributing_accidents": [...],
  "confidence_breakdown": {...},
  "metadata": {...}
}
```

## Styling

Uses Tailwind CSS with custom theme:

```js
// tailwind.config.js
theme: {
  extend: {
    colors: {
      'risk-low': '#10b981',      // green
      'risk-moderate': '#f59e0b', // yellow
      'risk-high': '#ef4444',     // red
      'risk-extreme': '#7c2d12',  // dark red
    },
  },
}
```

## Browser Support

- Chrome/Edge (latest)
- Firefox (latest)
- Safari (latest)
- Mobile browsers (iOS Safari, Chrome Android)

## Troubleshooting

### Mapbox Not Loading

1. Check your token in `.env` - should start with `pk.`
2. Verify token is public (not secret token)
3. Check browser console for errors

### Backend Connection Failed

1. Ensure backend is running: http://localhost:8000
2. Check CORS is enabled in backend
3. Verify `VITE_API_BASE_URL` in `.env`

### Build Errors

```bash
rm -rf node_modules
rm package-lock.json
npm install
```

## Next Steps

- [ ] Add route history/favorites
- [ ] Multi-day forecast view
- [ ] Share prediction links
- [ ] Mobile-optimized layout
- [ ] Offline mode
- [ ] Add accident markers on map

## License

MIT

## Contact

For issues or questions, contact the SafeAscent team.
