# SoulSync Web UI Development Plan

## Overview
Build a complete web replica of the SoulSync PyQt6 GUI application with identical layout, styling, and visual components. This will be a single-page application (SPA) contained in `index.html` with all pages shown/hidden via JavaScript.

## Current Architecture Analysis

### Main GUI Structure (PyQt6)
- **Sidebar (240px width)**: Navigation, media player, donation widget, version info, service status
- **Main Content Area**: QStackedWidget containing 5 pages
- **Pages**: Dashboard, Sync, Downloads (Search), Artists, Settings
- **Styling**: Dark theme with Spotify green accents (#1ed760), modern gradients, rounded corners

### Key Components Identified

#### Sidebar Components:
1. **Header**: App name "SoulSync" with subtitle "Music Sync & Manager"
2. **Navigation**: 5 buttons with icons
   - 📊 Dashboard
   - 🔄 Sync 
   - 📥 Search (Downloads)
   - 🎵 Artists
   - ⚙️ Settings
3. **Media Player**: Collapsible with track info, play/pause, volume control, loading animation
4. **Crypto Donation**: Collapsible section with Ko-fi and crypto addresses
5. **Version Info**: Clickable version button (v.0.65)
6. **Service Status**: 3 indicators (Spotify, Media Server, Soulseek) with connection dots

#### Page Components:
1. **Dashboard**: Service stats, recent activity, quick actions, uptime tracking
2. **Sync**: Playlist management, sync operations, progress tracking, track analysis
3. **Downloads/Search**: Music search, download queue, media player, search filters
4. **Artists**: Artist browsing, album management, collection overview, completion status
5. **Settings**: Configuration forms, service setup, preferences, detection tools

## Implementation Plan

### Phase 1: Core Framework Setup ✅
**Files to Create:**
- `headless.md` (this document)
- Update `webui/index.html` (single-page structure)
- Update `webui/static/style.css` (complete styling system)
- Update `webui/static/script.js` (page management, API integration)

### Phase 2: Layout Structure
**2.1 HTML Structure (index.html)**
```html
<div id="app">
  <aside id="sidebar">
    <div id="sidebar-header">
      <h1>SoulSync</h1>
      <p>Music Sync & Manager</p>
    </div>
    <nav id="sidebar-nav">
      <!-- Navigation buttons -->
    </nav>
    <div id="media-player">
      <!-- Media player component -->
    </div>
    <div id="crypto-donation">
      <!-- Donation component -->
    </div>
    <div id="version-info">
      <!-- Version button -->
    </div>
    <div id="service-status">
      <!-- Status indicators -->
    </div>
  </aside>
  <main id="content">
    <div id="dashboard-page" class="page active">...</div>
    <div id="sync-page" class="page">...</div>
    <div id="downloads-page" class="page">...</div>
    <div id="artists-page" class="page">...</div>
    <div id="settings-page" class="page">...</div>
  </main>
</div>
```

**2.2 CSS Framework (style.css)**
- Dark theme base colors (#121212, #1e1e1e, #0d1117)
- Spotify green accent system (#1ed760, #1ed760, #1ca851)
- Modern gradient backgrounds (qlineargradient equivalents)
- Rounded corner design language (12px, 16px border-radius)
- Responsive grid systems
- Animation/transition systems (300ms ease curves)

**2.3 JavaScript Page Manager (script.js)**
- Page switching logic (show/hide with fade transitions)
- State persistence (current page, form data)
- API communication layer (fetch-based)
- Component management system
- Real-time status updates (5-second intervals)

### Phase 3: Sidebar Implementation
**3.1 Sidebar Structure**
- Fixed 240px width sidebar
- Gradient background: `linear-gradient(135deg, #0d1117, #121212, #0a0a0a)`
- Border-right: `1px solid rgba(29, 185, 84, 0.1)`
- Border-radius: `12px` (top-right, bottom-right)

**3.2 Navigation System**
- Button styling: 216px width, 52px height
- Active state: Green gradient background + left border
- Hover states with smooth transitions
- Icon + text layout with proper spacing
- Active state management with visual feedback

**3.3 Media Player Component**
- Collapsible design (85px collapsed, 145px expanded)
- Track information display with scrolling long titles
- Control buttons: play/pause (40px green circle), stop (32px)
- Volume slider: horizontal with green accent
- Loading animations: indefinite + determinate progress
- "No track" placeholder state

**3.4 Additional Sidebar Components**
- **Crypto Donation**: Show/Hide toggle, Ko-fi + crypto addresses
- **Version Info**: Clickable button with hover effects
- **Service Status**: 3 indicators with colored dots (green/red)

### Phase 4: Page Content Implementation
**4.1 Dashboard Page**
```html
<!-- Service status cards -->
<!-- Activity feed with real-time updates -->
<!-- Quick action buttons -->
<!-- Statistics displays (tracks, artists, downloads) -->
<!-- Progress indicators for ongoing operations -->
<!-- Database status and update controls -->
```

**4.2 Sync Page**
```html
<!-- Playlist selection dropdown -->
<!-- Sync progress tracking with animated bars -->
<!-- Track listing tables with status columns -->
<!-- Action buttons (sync, analyze, cancel) -->
<!-- Status indicators for sync operations -->
<!-- Missing/found track summaries -->
```

**4.3 Downloads/Search Page**
```html
<!-- Search interface with filters -->
<!-- Results grid/list with album art -->
<!-- Download queue with progress bars -->
<!-- Progress tracking for active downloads -->
<!-- Media player integration -->
<!-- Search history and suggestions -->
```

**4.4 Artists Page**
```html
<!-- Artist grid layout with images -->
<!-- Album cards with completion indicators -->
<!-- Search/filter interface -->
<!-- Collection management tools -->
<!-- Image lazy loading system -->
<!-- Artist details modal/expansion -->
```

**4.5 Settings Page**
```html
<!-- Service configuration forms (Spotify, Plex, Jellyfin, Soulseek) -->
<!-- Input validation and feedback -->
<!-- Save/test functionality -->
<!-- Configuration display and status -->
<!-- Auto-detection tools (Plex discovery) -->
<!-- Path settings and validation -->
```

### Phase 5: Backend Integration
**5.1 API Endpoints (add to main.py)**
```python
# Page-specific data APIs
@app.route('/api/dashboard')          # Dashboard stats and activity
@app.route('/api/sync')               # Sync status and playlists
@app.route('/api/downloads')          # Download queue and history
@app.route('/api/artists')            # Artist collection and albums
@app.route('/api/settings')           # Current configuration

# Action APIs
@app.route('/api/search', methods=['POST'])         # Music search
@app.route('/api/download', methods=['POST'])       # Download tracks
@app.route('/api/sync-playlist', methods=['POST'])  # Sync operations
@app.route('/api/save-settings', methods=['POST'])  # Save configuration
@app.route('/api/test-connection', methods=['POST']) # Test service connections

# Real-time APIs
@app.route('/api/status')             # Service status (existing)
@app.route('/api/progress')           # Operation progress
@app.route('/api/activity')           # Recent activity feed
```

**5.2 Data Integration**
- Connect to existing service clients (SpotifyClient, PlexClient, etc.)
- Reuse existing business logic from GUI pages
- Maintain state consistency with backend services
- Real-time updates via polling (5-second intervals)
- Error handling and user feedback

### Phase 6: Visual Polish & Testing
**6.1 Styling Refinement**
- Match exact colors and gradients from PyQt6
- Perfect spacing and typography (SF Pro fonts)
- Smooth animations and transitions (300ms standard)
- Responsive behavior for different screen sizes

**6.2 Component Testing**
- Page switching functionality and animations
- Form submissions and validation
- Real-time updates and polling
- Error handling and user feedback
- Media player controls and state management

**6.3 Integration Testing**
- Backend API connectivity and data flow
- State management across page switches
- Performance optimization and caching
- Cross-browser compatibility (Chrome, Firefox, Safari)

## Color Palette & Design System

### Primary Colors
- **Background**: `#121212` (main), `#1e1e1e` (cards), `#0d1117` (sidebar)
- **Accent**: `#1ed760` (Spotify green), `#1fdf64` (hover), `#1ca851` (pressed)
- **Text**: `#ffffff` (primary), `rgba(255, 255, 255, 0.8)` (secondary)
- **Borders**: `rgba(255, 255, 255, 0.05)` (subtle), `rgba(29, 185, 84, 0.1)` (accent)

### Typography
- **Headers**: SF Pro Display, 20px bold (app title)
- **Navigation**: SF Pro Text, 12px medium
- **Body**: SF Pro Text, 11px regular
- **Monospace**: Courier New (crypto addresses)

### Spacing System
- **Margins**: 8px, 12px, 16px, 20px, 24px
- **Padding**: 4px, 8px, 12px, 16px, 18px
- **Border Radius**: 8px (small), 12px (medium), 16px (large), 20px (buttons)

## Critical Safety Measures

### 1. Non-Breaking Approach
- ❌ NO modifications to existing PyQt6 code
- ❌ NO changes to core business logic
- ❌ NO alterations to service clients or database code
- ✅ Only add new Flask routes and web assets

### 2. Isolated Web Components
- All web code in `webui/` directory
- Separate CSS/JS files with no shared globals
- Independent state management
- No shared variables with GUI code

### 3. Shared Backend Services
- Reuse existing SpotifyClient, PlexClient, JellyfinClient, SoulseekClient
- Call same methods as GUI application
- Maintain identical configuration system via config_manager
- Preserve all existing functionality and error handling

### 4. State Isolation
- Web UI maintains its own state in JavaScript
- No persistent state shared between GUI and web
- Configuration changes affect both modes equally
- Independent session management

## Success Criteria
1. ✅ Visual replica of PyQt6 interface (95%+ visual accuracy)
2. ✅ All 5 pages functional and navigable with smooth transitions
3. ✅ Sidebar components fully operational (navigation, media player, status)
4. ✅ Backend integration complete with all major functions working
5. ✅ No disruption to existing GUI app functionality
6. ✅ Seamless switching between `python main.py` and `python main.py --headless`
7. ✅ Real-time updates and responsive user interface
8. ✅ Proper error handling and user feedback

## File Structure
```
webui/
├── index.html              # Single-page application
└── static/
    ├── style.css          # Complete styling system
    ├── script.js          # Page management & API calls
    ├── components.css     # Reusable component styles
    └── animations.css     # Transition and animation definitions
```

## Development Phases Timeline

### Phase 1: Foundation (Current) ✅
- [x] Plan document creation
- [x] Basic file structure setup

### Phase 2: Core Structure 🚧
- [ ] HTML single-page structure
- [ ] CSS foundation and color system
- [ ] JavaScript page management

### Phase 3: Sidebar Complete 📋
- [ ] Navigation component
- [ ] Media player component
- [ ] Status indicators
- [ ] Donation and version sections

### Phase 4: Page Implementation 📋
- [ ] Dashboard page
- [ ] Sync page
- [ ] Downloads/Search page
- [ ] Artists page
- [ ] Settings page

### Phase 5: Backend Integration 📋
- [ ] Flask API endpoints
- [ ] Service client integration
- [ ] Real-time data updates

### Phase 6: Polish & Testing 📋
- [ ] Visual accuracy review
- [ ] Functionality testing
- [ ] Performance optimization

This plan ensures we build an exact replica of the GUI while maintaining complete safety and isolation from the existing codebase. The web interface will provide the same functionality as the desktop app but accessible through any web browser.