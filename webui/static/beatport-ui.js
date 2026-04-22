// BEATPORT REBUILD SLIDER FUNCTIONALITY
// =================================

let beatportRebuildSliderState = {
    currentSlide: 0,
    totalSlides: 4,
    autoPlayInterval: null,
    autoPlayDelay: 5000
};

/**
 * Initialize the beatport rebuild slider functionality
 */
function initializeBeatportRebuildSlider() {
    console.log('🔄 Initializing beatport rebuild slider...');

    const slider = document.getElementById('beatport-rebuild-slider');
    if (!slider) {
        console.warn('Beatport rebuild slider not found');
        return;
    }

    // Check if already initialized to prevent duplicate event listeners
    if (slider.dataset.initialized === 'true') {
        console.log('Beatport rebuild slider already initialized, skipping...');
        startBeatportRebuildSliderAutoPlay(); // Just restart autoplay
        return;
    }

    // Mark as initialized
    slider.dataset.initialized = 'true';

    // Load real Beatport data first
    loadBeatportHeroTracks();

    console.log('✅ Beatport rebuild slider initialized successfully');
}

/**
 * Load real Beatport hero tracks and populate the slider
 */
async function loadBeatportHeroTracks() {
    console.log('🎯 Loading real Beatport hero tracks...');

    try {
        const signal = getBeatportContentSignal();
        const response = await fetch('/api/beatport/hero-tracks', signal ? { signal } : undefined);
        const data = await response.json();

        if (data.success && data.tracks && data.tracks.length > 0) {
            console.log(`✅ Loaded ${data.tracks.length} Beatport tracks`);
            populateBeatportSlider(data.tracks);
        } else {
            console.warn('❌ No tracks received from Beatport API, using placeholder data');
            setupBeatportSliderWithPlaceholders();
        }
    } catch (error) {
        if (error && error.name === 'AbortError') return;
        console.error('❌ Error loading Beatport tracks:', error);
        setupBeatportSliderWithPlaceholders();
    }
}

/**
 * Populate the slider with real Beatport track data
 */
function populateBeatportSlider(tracks) {
    const sliderTrack = document.getElementById('beatport-rebuild-slider-track');
    const indicatorsContainer = document.querySelector('.beatport-rebuild-slider-indicators');

    if (!sliderTrack || !indicatorsContainer) {
        console.warn('Slider elements not found');
        return;
    }

    // Clear existing content
    sliderTrack.innerHTML = '';
    indicatorsContainer.innerHTML = '';

    // Update state
    beatportRebuildSliderState.totalSlides = tracks.length;
    beatportRebuildSliderState.currentSlide = 0;

    // Generate slides HTML
    tracks.forEach((track, index) => {
        const slideHtml = `
            <div class="beatport-rebuild-slide ${index === 0 ? 'active' : ''}"
                 data-slide="${index}"
                 data-url="${track.url}"
                 data-image="${track.image_url}"
                 style="--slide-bg-image: url('${track.image_url}')">
                <div class="beatport-rebuild-slide-background">
                    <div class="beatport-rebuild-slide-gradient"></div>
                </div>
                <div class="beatport-rebuild-slide-content">
                    <div class="beatport-rebuild-track-info">
                        <h2 class="beatport-rebuild-track-title">${track.title}</h2>
                        <p class="beatport-rebuild-artist-name">${track.artist}</p>
                        <p class="beatport-rebuild-album-name">New on Beatport</p>
                    </div>
                </div>
            </div>
        `;
        sliderTrack.insertAdjacentHTML('beforeend', slideHtml);

        // Add indicator
        const indicatorHtml = `<button class="beatport-rebuild-indicator ${index === 0 ? 'active' : ''}" data-slide="${index}"></button>`;
        indicatorsContainer.insertAdjacentHTML('beforeend', indicatorHtml);
    });

    // Now set up all the functionality
    setupBeatportSliderFunctionality();

    // Add individual click handlers for each slide (like top 10 releases pattern)
    setupHeroSliderIndividualClickHandlers(tracks);

    console.log(`✅ Populated slider with ${tracks.length} real Beatport tracks`);
}

/**
 * Set up individual click handlers for hero slider slides (like top 10 releases)
 */
function setupHeroSliderIndividualClickHandlers(tracks) {
    const slides = document.querySelectorAll('.beatport-rebuild-slide[data-url]');

    slides.forEach((slide, index) => {
        const releaseUrl = slide.getAttribute('data-url');
        if (releaseUrl && releaseUrl !== '#' && releaseUrl !== '') {
            // Create release data object from the track data (similar to top 10 releases)
            const track = tracks[index];
            if (track) {
                const releaseData = {
                    url: releaseUrl,
                    title: track.title || 'Unknown Title',
                    artist: track.artist || 'Unknown Artist',
                    label: track.label || 'Unknown Label',
                    image_url: track.image_url || ''
                };

                // Add click handler that mimics the top 10 releases behavior
                slide.addEventListener('click', (event) => {
                    // Prevent navigation button clicks from triggering this
                    if (event.target.closest('.beatport-rebuild-nav-btn') ||
                        event.target.closest('.beatport-rebuild-indicator')) {
                        return;
                    }

                    console.log(`🎯 Hero slider slide clicked: ${releaseData.title} by ${releaseData.artist}`);
                    handleBeatportReleaseCardClick(slide, releaseData);
                });

                slide.style.cursor = 'pointer';
            }
        }
    });

    console.log(`✅ Set up individual click handlers for ${slides.length} hero slider slides`);
}

/**
 * Set up placeholder data if API fails
 */
function setupBeatportSliderWithPlaceholders() {
    console.log('🔄 Setting up slider with placeholder data...');

    // The HTML already has placeholder slides, just set up functionality
    setupBeatportSliderFunctionality();
}

/**
 * Set up all slider functionality after content is loaded
 */
function setupBeatportSliderFunctionality() {
    // Set up navigation buttons
    setupBeatportRebuildSliderNavigation();

    // Set up indicators
    setupBeatportRebuildSliderIndicators();


    // Start auto-play
    startBeatportRebuildSliderAutoPlay();

    // Set up pause on hover
    setupBeatportRebuildSliderHoverPause();
}

/**
 * Set up navigation button functionality
 */
function setupBeatportRebuildSliderNavigation() {
    const prevBtn = document.getElementById('beatport-rebuild-prev-btn');
    const nextBtn = document.getElementById('beatport-rebuild-next-btn');

    if (prevBtn) {
        prevBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Previous button clicked, current slide:', beatportRebuildSliderState.currentSlide);
            goToBeatportRebuildSlide(beatportRebuildSliderState.currentSlide - 1);
            resetBeatportRebuildSliderAutoPlay();
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Next button clicked, current slide:', beatportRebuildSliderState.currentSlide);
            goToBeatportRebuildSlide(beatportRebuildSliderState.currentSlide + 1);
            resetBeatportRebuildSliderAutoPlay();
        });
    }
}

/**
 * Set up indicator functionality
 */
function setupBeatportRebuildSliderIndicators() {
    const indicators = document.querySelectorAll('.beatport-rebuild-indicator');

    indicators.forEach((indicator, index) => {
        indicator.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            goToBeatportRebuildSlide(index);
            resetBeatportRebuildSliderAutoPlay();
        });
    });
}

/**
 * Navigate to a specific slide
 */
function goToBeatportRebuildSlide(slideIndex) {
    console.log('goToBeatportRebuildSlide called with:', slideIndex, 'current:', beatportRebuildSliderState.currentSlide);

    // Wrap around if out of bounds
    if (slideIndex < 0) {
        slideIndex = beatportRebuildSliderState.totalSlides - 1;
    } else if (slideIndex >= beatportRebuildSliderState.totalSlides) {
        slideIndex = 0;
    }

    console.log('After wrapping, slideIndex:', slideIndex);

    // Update current slide
    beatportRebuildSliderState.currentSlide = slideIndex;

    // Update slide visibility
    const slides = document.querySelectorAll('.beatport-rebuild-slide');
    slides.forEach((slide, index) => {
        slide.classList.remove('active', 'prev', 'next');

        if (index === slideIndex) {
            slide.classList.add('active');
        } else if (index < slideIndex) {
            slide.classList.add('prev');
        } else {
            slide.classList.add('next');
        }
    });

    // Update indicators
    const indicators = document.querySelectorAll('.beatport-rebuild-indicator');
    indicators.forEach((indicator, index) => {
        indicator.classList.toggle('active', index === slideIndex);
    });

    console.log('Slide updated to:', beatportRebuildSliderState.currentSlide);
}

/**
 * Start auto-play functionality
 */
function startBeatportRebuildSliderAutoPlay() {
    if (beatportRebuildSliderState.autoPlayInterval) {
        clearInterval(beatportRebuildSliderState.autoPlayInterval);
    }

    beatportRebuildSliderState.autoPlayInterval = setInterval(() => {
        goToBeatportRebuildSlide(beatportRebuildSliderState.currentSlide + 1);
    }, beatportRebuildSliderState.autoPlayDelay);
}

/**
 * Reset auto-play timer
 */
function resetBeatportRebuildSliderAutoPlay() {
    startBeatportRebuildSliderAutoPlay();
}

/**
 * Set up hover pause functionality
 */
function setupBeatportRebuildSliderHoverPause() {
    const sliderContainer = document.querySelector('.beatport-rebuild-slider-container');

    if (sliderContainer) {
        sliderContainer.addEventListener('mouseenter', () => {
            if (beatportRebuildSliderState.autoPlayInterval) {
                clearInterval(beatportRebuildSliderState.autoPlayInterval);
            }
        });

        sliderContainer.addEventListener('mouseleave', () => {
            startBeatportRebuildSliderAutoPlay();
        });
    }
}


/**
 * Clean up beatport rebuild slider when switching away
 */
function cleanupBeatportRebuildSlider() {
    if (beatportRebuildSliderState.autoPlayInterval) {
        clearInterval(beatportRebuildSliderState.autoPlayInterval);
        beatportRebuildSliderState.autoPlayInterval = null;
    }
}

// ===================================
// BEATPORT NEW RELEASES SLIDER
// ===================================

// State management for new releases slider (copied from hero slider)
let beatportReleasesSliderState = {
    currentSlide: 0,
    totalSlides: 0,
    autoPlayInterval: null,
    autoPlayDelay: 8000,
    isInitialized: false
};

/**
 * Initialize the beatport new releases slider functionality (based on hero slider)
 */
function initializeBeatportReleasesSlider() {
    console.log('🆕 Initializing beatport new releases slider...');

    const slider = document.getElementById('beatport-releases-slider');
    if (!slider) {
        console.warn('Beatport releases slider not found');
        return;
    }

    // Prevent double initialization
    if (slider.dataset.initialized === 'true') {
        console.log('Releases slider already initialized');
        return;
    }

    const sliderTrack = document.getElementById('beatport-releases-slider-track');
    const indicatorsContainer = document.getElementById('beatport-releases-slider-indicators');

    if (!sliderTrack || !indicatorsContainer) {
        console.warn('Releases slider elements not found');
        return;
    }

    // Load data and initialize
    loadBeatportNewReleases().then(success => {
        if (success) {
            setupBeatportReleasesSliderNavigation();
            setupBeatportReleasesSliderIndicators();
            setupBeatportReleasesSliderHoverPause();
            startBeatportReleasesSliderAutoPlay();
            slider.dataset.initialized = 'true';
            beatportReleasesSliderState.isInitialized = true;
            console.log('✅ New releases slider initialized successfully');
        }
    });
}

/**
 * Load new releases data from API
 */
async function loadBeatportNewReleases() {
    try {
        console.log('📡 Fetching new releases data...');

        const signal = getBeatportContentSignal();
        const response = await fetch('/api/beatport/new-releases', signal ? { signal } : undefined);
        const data = await response.json();

        if (data.success && data.releases && data.releases.length > 0) {
            console.log(`📀 Loaded ${data.releases.length} releases`);
            populateBeatportReleasesSlider(data.releases);
            return true;
        } else {
            console.error('Failed to load releases:', data.error || 'No releases found');
            showBeatportReleasesError(data.error || 'No releases available');
            return false;
        }
    } catch (error) {
        if (error && error.name === 'AbortError') return false;
        console.error('Error loading new releases:', error);
        showBeatportReleasesError('Failed to load releases');
        return false;
    }
}

/**
 * Populate the releases slider with data (based on hero slider)
 */
function populateBeatportReleasesSlider(releases) {
    const sliderTrack = document.getElementById('beatport-releases-slider-track');
    const indicatorsContainer = document.getElementById('beatport-releases-slider-indicators');

    if (!sliderTrack || !indicatorsContainer) return;

    // Calculate slides needed (10 cards per slide)
    const cardsPerSlide = 10;
    const totalSlides = Math.ceil(releases.length / cardsPerSlide);

    // Clear existing content
    sliderTrack.innerHTML = '';
    indicatorsContainer.innerHTML = '';

    // Update state
    beatportReleasesSliderState.totalSlides = totalSlides;
    beatportReleasesSliderState.currentSlide = 0;

    console.log(`🎯 Creating ${totalSlides} slides with ${cardsPerSlide} cards each`);

    // Generate slides HTML (similar to hero slider)
    for (let slideIndex = 0; slideIndex < totalSlides; slideIndex++) {
        const startIndex = slideIndex * cardsPerSlide;
        const endIndex = Math.min(startIndex + cardsPerSlide, releases.length);
        const slideReleases = releases.slice(startIndex, endIndex);

        // Create grid HTML for this slide
        let gridHtml = '';
        for (let i = 0; i < cardsPerSlide; i++) {
            if (i < slideReleases.length) {
                const release = slideReleases[i];
                gridHtml += `
                    <div class="beatport-release-card" data-url="${release.url}" style="--card-bg-image: url('${release.image_url}')">
                        <div class="beatport-release-card-content">
                            <div class="beatport-release-artwork">
                                ${release.image_url ? `<img src="${release.image_url}" alt="${release.title}" loading="lazy">` : ''}
                            </div>
                            <div class="beatport-release-info">
                                <div class="beatport-release-title" title="${release.title}">${release.title}</div>
                                <div class="beatport-release-artist" title="${release.artist}">${release.artist}</div>
                                <div class="beatport-release-label" title="${release.label}">${release.label}</div>
                            </div>
                        </div>
                    </div>
                `;
            } else {
                // Placeholder card
                gridHtml += `
                    <div class="beatport-release-card beatport-release-placeholder">
                        <div class="beatport-release-card-content">
                            <div class="beatport-release-artwork">
                                <div class="placeholder-icon">📀</div>
                            </div>
                            <div class="beatport-release-info">
                                <div class="beatport-release-title">More Releases</div>
                                <div class="beatport-release-artist">Coming Soon</div>
                                <div class="beatport-release-label">Beatport</div>
                            </div>
                        </div>
                    </div>
                `;
            }
        }

        const slideHtml = `
            <div class="beatport-releases-slide ${slideIndex === 0 ? 'active' : ''}"
                 data-slide="${slideIndex}">
                <div class="beatport-releases-grid">
                    ${gridHtml}
                </div>
            </div>
        `;

        sliderTrack.innerHTML += slideHtml;

        // Create indicator
        const indicatorHtml = `<button class="beatport-releases-indicator ${slideIndex === 0 ? 'active' : ''}" data-slide="${slideIndex}"></button>`;
        indicatorsContainer.innerHTML += indicatorHtml;
    }

    console.log(`✅ Created ${totalSlides} slides for releases slider`);

    // Add click handlers for individual release discovery (matching Top 10 Releases pattern)
    const releaseCards = sliderTrack.querySelectorAll('.beatport-release-card[data-url]:not(.beatport-release-placeholder)');
    releaseCards.forEach((card) => {
        const releaseUrl = card.getAttribute('data-url');
        if (releaseUrl && releaseUrl !== '#') {
            // Find the corresponding release data
            const releaseData = releases.find(release => release.url === releaseUrl);
            if (releaseData) {
                card.addEventListener('click', () => handleBeatportReleaseCardClick(card, releaseData));
                card.style.cursor = 'pointer';
            }
        }
    });
}

/**
 * Set up navigation functionality (copied from hero slider)
 */
function setupBeatportReleasesSliderNavigation() {
    const prevBtn = document.getElementById('beatport-releases-prev-btn');
    const nextBtn = document.getElementById('beatport-releases-next-btn');

    if (prevBtn) {
        // Clone button to remove all existing event listeners
        const newPrevBtn = prevBtn.cloneNode(true);
        prevBtn.parentNode.replaceChild(newPrevBtn, prevBtn);

        newPrevBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Previous releases button clicked, current slide:', beatportReleasesSliderState.currentSlide);
            goToBeatportReleasesSlide(beatportReleasesSliderState.currentSlide - 1);
            resetBeatportReleasesSliderAutoPlay();
        });
    }

    if (nextBtn) {
        // Clone button to remove all existing event listeners
        const newNextBtn = nextBtn.cloneNode(true);
        nextBtn.parentNode.replaceChild(newNextBtn, nextBtn);

        newNextBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Next releases button clicked, current slide:', beatportReleasesSliderState.currentSlide);
            goToBeatportReleasesSlide(beatportReleasesSliderState.currentSlide + 1);
            resetBeatportReleasesSliderAutoPlay();
        });
    }
}

/**
 * Set up indicator functionality (copied from hero slider)
 */
function setupBeatportReleasesSliderIndicators() {
    const indicators = document.querySelectorAll('.beatport-releases-indicator');

    indicators.forEach((indicator, index) => {
        indicator.addEventListener('click', () => {
            goToBeatportReleasesSlide(index);
            resetBeatportReleasesSliderAutoPlay();
        });
    });
}

/**
 * Navigate to a specific slide (copied from hero slider)
 */
function goToBeatportReleasesSlide(slideIndex) {
    console.log('goToBeatportReleasesSlide called with:', slideIndex, 'current:', beatportReleasesSliderState.currentSlide);

    // Wrap around if out of bounds
    if (slideIndex < 0) {
        slideIndex = beatportReleasesSliderState.totalSlides - 1;
    } else if (slideIndex >= beatportReleasesSliderState.totalSlides) {
        slideIndex = 0;
    }

    console.log('After wrapping, slideIndex:', slideIndex);

    // Update current slide
    beatportReleasesSliderState.currentSlide = slideIndex;

    // Update slide visibility
    const slides = document.querySelectorAll('.beatport-releases-slide');
    slides.forEach((slide, index) => {
        slide.classList.remove('active', 'prev', 'next');

        if (index === slideIndex) {
            slide.classList.add('active');
        } else if (index < slideIndex) {
            slide.classList.add('prev');
        } else {
            slide.classList.add('next');
        }
    });

    // Update indicators
    const indicators = document.querySelectorAll('.beatport-releases-indicator');
    indicators.forEach((indicator, index) => {
        indicator.classList.toggle('active', index === slideIndex);
    });

    console.log('Releases slide updated to:', beatportReleasesSliderState.currentSlide);
}

/**
 * Start auto-play functionality (copied from hero slider)
 */
function startBeatportReleasesSliderAutoPlay() {
    if (beatportReleasesSliderState.autoPlayInterval) {
        clearInterval(beatportReleasesSliderState.autoPlayInterval);
    }

    beatportReleasesSliderState.autoPlayInterval = setInterval(() => {
        goToBeatportReleasesSlide(beatportReleasesSliderState.currentSlide + 1);
    }, beatportReleasesSliderState.autoPlayDelay);
}

/**
 * Reset auto-play timer (copied from hero slider)
 */
function resetBeatportReleasesSliderAutoPlay() {
    startBeatportReleasesSliderAutoPlay();
}

/**
 * Set up hover pause functionality (copied from hero slider)
 */
function setupBeatportReleasesSliderHoverPause() {
    const sliderContainer = document.querySelector('.beatport-releases-slider-container');

    if (sliderContainer) {
        sliderContainer.addEventListener('mouseenter', () => {
            if (beatportReleasesSliderState.autoPlayInterval) {
                clearInterval(beatportReleasesSliderState.autoPlayInterval);
                beatportReleasesSliderState.autoPlayInterval = null;
            }
        });

        sliderContainer.addEventListener('mouseleave', () => {
            startBeatportReleasesSliderAutoPlay();
        });
    }
}

/**
 * Show error state
 */
function showBeatportReleasesError(errorMessage) {
    const sliderTrack = document.getElementById('beatport-releases-slider-track');
    if (!sliderTrack) return;

    sliderTrack.innerHTML = `
        <div class="beatport-releases-loading">
            <div class="beatport-releases-loading-content">
                <h3>❌ Error Loading Releases</h3>
                <p>${errorMessage}</p>
            </div>
        </div>
    `;
}

/**
 * Clean up releases slider when switching away (copied from hero slider)
 */
function cleanupBeatportReleasesSlider() {
    if (beatportReleasesSliderState.autoPlayInterval) {
        clearInterval(beatportReleasesSliderState.autoPlayInterval);
        beatportReleasesSliderState.autoPlayInterval = null;
    }
}

// ===================================
// BEATPORT HYPE PICKS SLIDER
// ===================================

// Hype Picks Slider State
let beatportHypePicksSliderState = {
    currentSlide: 0,
    totalSlides: 0,
    autoPlayInterval: null,
    autoPlayDelay: 4000,
    isInitialized: false
};

/**
 * Initialize the beatport hype picks slider functionality (based on releases slider)
 */
function initializeBeatportHypePicksSlider() {
    console.log('🔥 Initializing beatport hype picks slider...');

    const slider = document.getElementById('beatport-hype-picks-slider');
    if (!slider) {
        console.warn('Beatport hype picks slider not found');
        return;
    }

    // Check if already initialized
    if (beatportHypePicksSliderState.isInitialized) {
        console.log('Beatport hype picks slider already initialized, skipping...');
        startBeatportHypePicksSliderAutoPlay(); // Just restart autoplay
        return;
    }

    // Mark as initialized
    beatportHypePicksSliderState.isInitialized = true;

    // Reset state
    beatportHypePicksSliderState.currentSlide = 0;
    beatportHypePicksSliderState.totalSlides = 0;

    // Load data and initialize
    loadBeatportHypePicks().then(success => {
        if (success) {
            setupBeatportHypePicksSliderNavigation();
            setupBeatportHypePicksSliderIndicators();
            setupBeatportHypePicksSliderHoverPause();
            startBeatportHypePicksSliderAutoPlay();
        }
    });

    console.log('✅ Beatport hype picks slider initialized successfully');
}

/**
 * Load hype picks data from API
 */
async function loadBeatportHypePicks() {
    try {
        console.log('🔥 Fetching hype picks data...');

        const signal = getBeatportContentSignal();
        const response = await fetch('/api/beatport/hype-picks', signal ? { signal } : undefined);
        const data = await response.json();

        if (data.success && data.releases && data.releases.length > 0) {
            console.log(`🔥 Loaded ${data.releases.length} hype picks releases`);
            populateBeatportHypePicksSlider(data.releases);
            return true;
        } else {
            console.error('Failed to load hype picks:', data.error || 'No hype picks found');
            showBeatportHypePicksError(data.error || 'No hype picks available');
            return false;
        }
    } catch (error) {
        if (error && error.name === 'AbortError') return false;
        console.error('Error loading hype picks:', error);
        showBeatportHypePicksError('Failed to load hype picks');
        return false;
    }
}

/**
 * Populate the hype picks slider with data (based on releases slider)
 */
function populateBeatportHypePicksSlider(releases) {
    const sliderTrack = document.getElementById('beatport-hype-picks-slider-track');
    const indicatorsContainer = document.getElementById('beatport-hype-picks-slider-indicators');

    if (!sliderTrack || !indicatorsContainer) return;

    // Clear existing content
    sliderTrack.innerHTML = '';
    indicatorsContainer.innerHTML = '';

    // Group releases into slides (10 releases per slide in 5x2 grid)
    const releasesPerSlide = 10;
    const slides = [];
    for (let i = 0; i < releases.length; i += releasesPerSlide) {
        slides.push(releases.slice(i, i + releasesPerSlide));
    }

    console.log(`🔥 Hype Picks: Got ${releases.length} releases, creating ${slides.length} slides`);
    beatportHypePicksSliderState.totalSlides = slides.length;
    beatportHypePicksSliderState.currentSlide = 0;

    // Create slides
    slides.forEach((slideReleases, slideIndex) => {
        const slideHtml = `
            <div class="beatport-hype-picks-slide ${slideIndex === 0 ? 'active' : ''}"
                 data-slide="${slideIndex}">
                <div class="beatport-hype-picks-grid">
                    ${slideReleases.map(release => createBeatportHypePickCard(release)).join('')}
                    ${slideReleases.length < releasesPerSlide ?
                Array(releasesPerSlide - slideReleases.length).fill(0).map(() =>
                    `<div class="beatport-hype-pick-card beatport-hype-pick-placeholder">
                                <div class="placeholder-icon">🔥</div>
                            </div>`
                ).join('') : ''
            }
                </div>
            </div>
        `;
        sliderTrack.insertAdjacentHTML('beforeend', slideHtml);
        console.log(`🔥 Created slide ${slideIndex + 1}/${slides.length} with ${slideReleases.length} releases`);

        // Create indicator
        const indicatorHtml = `<button class="beatport-hype-picks-indicator ${slideIndex === 0 ? 'active' : ''}" data-slide="${slideIndex}"></button>`;
        indicatorsContainer.insertAdjacentHTML('beforeend', indicatorHtml);
    });

    // Add click handlers to track cards
    setupBeatportHypePickCardHandlers();
}

/**
 * Create a hype pick card HTML (for release cards, same as new releases)
 */
function createBeatportHypePickCard(release) {
    const artworkUrl = release.image_url || '';
    const bgStyle = artworkUrl ? `style="--card-bg-image: url('${artworkUrl}')"` : '';

    return `
        <div class="beatport-hype-pick-card" data-url="${release.url || ''}" ${bgStyle}>
            <div class="beatport-hype-pick-card-content">
                <div class="beatport-hype-pick-artwork">
                    ${artworkUrl ? `<img src="${artworkUrl}" alt="${release.title || 'Release'}" loading="lazy">` : ''}
                </div>
                <div class="beatport-hype-pick-info">
                    <div class="beatport-hype-pick-title">${release.title || 'Unknown Title'}</div>
                    <div class="beatport-hype-pick-artist">${release.artist || 'Unknown Artist'}</div>
                    <div class="beatport-hype-pick-label">${release.label || 'Hype Pick'}</div>
                </div>
            </div>
        </div>
    `;
}

/**
 * Setup navigation for hype picks slider (same pattern as releases)
 */
function setupBeatportHypePicksSliderNavigation() {
    const prevBtn = document.getElementById('beatport-hype-picks-prev-btn');
    const nextBtn = document.getElementById('beatport-hype-picks-next-btn');

    if (prevBtn) {
        // Clone button to remove all existing event listeners
        const newPrevBtn = prevBtn.cloneNode(true);
        prevBtn.parentNode.replaceChild(newPrevBtn, prevBtn);

        newPrevBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Previous hype picks button clicked, current slide:', beatportHypePicksSliderState.currentSlide);
            goToBeatportHypePicksSlide(beatportHypePicksSliderState.currentSlide - 1);
            resetBeatportHypePicksSliderAutoPlay();
        });
    }

    if (nextBtn) {
        // Clone button to remove all existing event listeners
        const newNextBtn = nextBtn.cloneNode(true);
        nextBtn.parentNode.replaceChild(newNextBtn, nextBtn);

        newNextBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Next hype picks button clicked, current slide:', beatportHypePicksSliderState.currentSlide);
            goToBeatportHypePicksSlide(beatportHypePicksSliderState.currentSlide + 1);
            resetBeatportHypePicksSliderAutoPlay();
        });
    }
}

/**
 * Setup indicators for hype picks slider
 */
function setupBeatportHypePicksSliderIndicators() {
    const indicators = document.querySelectorAll('.beatport-hype-picks-indicator');

    indicators.forEach((indicator, index) => {
        indicator.addEventListener('click', () => {
            goToBeatportHypePicksSlide(index);
            resetBeatportHypePicksSliderAutoPlay();
        });
    });
}

/**
 * Navigate to specific slide
 */
function goToBeatportHypePicksSlide(slideIndex) {
    console.log('goToBeatportHypePicksSlide called with:', slideIndex, 'current:', beatportHypePicksSliderState.currentSlide);

    // Handle wrap around
    if (slideIndex < 0) {
        slideIndex = beatportHypePicksSliderState.totalSlides - 1;
    } else if (slideIndex >= beatportHypePicksSliderState.totalSlides) {
        slideIndex = 0;
    }

    // Update current slide
    beatportHypePicksSliderState.currentSlide = slideIndex;

    // Update slides
    const slides = document.querySelectorAll('.beatport-hype-picks-slide');
    slides.forEach((slide, index) => {
        slide.classList.remove('active', 'prev', 'next');
        if (index === slideIndex) {
            slide.classList.add('active');
        } else if (index < slideIndex) {
            slide.classList.add('prev');
        } else {
            slide.classList.add('next');
        }
    });

    // Update indicators
    const indicators = document.querySelectorAll('.beatport-hype-picks-indicator');
    indicators.forEach((indicator, index) => {
        indicator.classList.toggle('active', index === slideIndex);
    });

    console.log('Slide updated to:', beatportHypePicksSliderState.currentSlide);
}

/**
 * Start auto-play for hype picks slider
 */
function startBeatportHypePicksSliderAutoPlay() {
    if (beatportHypePicksSliderState.autoPlayInterval) {
        clearInterval(beatportHypePicksSliderState.autoPlayInterval);
    }

    beatportHypePicksSliderState.autoPlayInterval = setInterval(() => {
        goToBeatportHypePicksSlide(beatportHypePicksSliderState.currentSlide + 1);
    }, beatportHypePicksSliderState.autoPlayDelay);

    console.log('🔥 Hype picks slider autoplay started');
}

/**
 * Reset auto-play for hype picks slider
 */
function resetBeatportHypePicksSliderAutoPlay() {
    startBeatportHypePicksSliderAutoPlay();
}

/**
 * Setup hover pause for hype picks slider
 */
function setupBeatportHypePicksSliderHoverPause() {
    const sliderContainer = document.querySelector('.beatport-hype-picks-slider-container');
    if (sliderContainer) {
        sliderContainer.addEventListener('mouseenter', () => {
            if (beatportHypePicksSliderState.autoPlayInterval) {
                clearInterval(beatportHypePicksSliderState.autoPlayInterval);
            }
        });

        sliderContainer.addEventListener('mouseleave', () => {
            startBeatportHypePicksSliderAutoPlay();
        });
    }
}

/**
 * Setup click handlers for hype pick cards
 */
function setupBeatportHypePickCardHandlers() {
    const cards = document.querySelectorAll('.beatport-hype-pick-card:not(.beatport-hype-pick-placeholder)');

    cards.forEach(card => {
        const releaseUrl = card.getAttribute('data-url');
        if (releaseUrl && releaseUrl !== '#' && releaseUrl !== '') {
            // Extract release data from the card elements
            const titleElement = card.querySelector('.beatport-hype-pick-title');
            const artistElement = card.querySelector('.beatport-hype-pick-artist');
            const labelElement = card.querySelector('.beatport-hype-pick-label');
            const imageElement = card.querySelector('.beatport-hype-pick-artwork img');

            const releaseData = {
                url: releaseUrl,
                title: titleElement ? titleElement.textContent.trim() : 'Unknown Title',
                artist: artistElement ? artistElement.textContent.trim() : 'Unknown Artist',
                label: labelElement ? labelElement.textContent.trim() : 'Unknown Label',
                image_url: imageElement ? imageElement.src : ''
            };

            card.addEventListener('click', () => handleBeatportReleaseCardClick(card, releaseData));
            card.style.cursor = 'pointer';
        }
    });
}

/**
 * Show error state for hype picks slider
 */
function showBeatportHypePicksError(errorMessage) {
    const sliderTrack = document.getElementById('beatport-hype-picks-slider-track');
    if (sliderTrack) {
        sliderTrack.innerHTML = `
        <div class="beatport-hype-picks-loading">
            <div class="beatport-hype-picks-loading-content">
                <h3>❌ Error Loading Hype Picks</h3>
                <p>${errorMessage}</p>
            </div>
        </div>
        `;
    }
}

/**
 * Clean up hype picks slider when switching away
 */
function cleanupBeatportHypePicksSlider() {
    if (beatportHypePicksSliderState.autoPlayInterval) {
        clearInterval(beatportHypePicksSliderState.autoPlayInterval);
        beatportHypePicksSliderState.autoPlayInterval = null;
    }
}

// ===================================
// BEATPORT FEATURED CHARTS SLIDER
// ===================================

// State management for featured charts slider (copied from releases slider)
let beatportChartsSliderState = {
    currentSlide: 0,
    totalSlides: 0,
    autoPlayInterval: null,
    autoPlayDelay: 10000,  // Slightly longer auto-play for charts
    isInitialized: false
};

/**
 * Initialize the beatport featured charts slider functionality (based on releases slider)
 */
function initializeBeatportChartsSlider() {
    console.log('🔥 Initializing beatport featured charts slider...');

    const slider = document.getElementById('beatport-charts-slider');
    if (!slider) {
        console.warn('Beatport charts slider not found');
        return;
    }

    // Prevent double initialization
    if (slider.dataset.initialized === 'true') {
        console.log('Charts slider already initialized');
        return;
    }

    const sliderTrack = document.getElementById('beatport-charts-slider-track');
    const indicatorsContainer = document.getElementById('beatport-charts-slider-indicators');

    if (!sliderTrack || !indicatorsContainer) {
        console.warn('Charts slider elements not found');
        return;
    }

    // Load data and initialize
    loadBeatportFeaturedCharts().then(success => {
        if (success) {
            setupBeatportChartsSliderNavigation();
            setupBeatportChartsSliderIndicators();
            setupBeatportChartsSliderHoverPause();
            startBeatportChartsSliderAutoPlay();
            slider.dataset.initialized = 'true';
            beatportChartsSliderState.isInitialized = true;
            console.log('✅ Featured charts slider initialized successfully');
        }
    });
}

/**
 * Load featured charts data from API
 */
async function loadBeatportFeaturedCharts() {
    try {
        console.log('📊 Loading featured charts data...');
        const signal = getBeatportContentSignal();
        const response = await fetch('/api/beatport/featured-charts', signal ? { signal } : undefined);
        const data = await response.json();

        if (data.success && data.charts && data.charts.length > 0) {
            console.log(`📈 Loaded ${data.charts.length} featured charts`);
            createBeatportChartsSlides(data.charts);
            return true;
        } else {
            console.warn('No featured charts data available');
            return false;
        }
    } catch (error) {
        if (error && error.name === 'AbortError') return false;
        console.error('❌ Error loading featured charts:', error);
        return false;
    }
}

/**
 * Create chart slides with grid layout (copied from releases slider)
 */
function createBeatportChartsSlides(charts) {
    const sliderTrack = document.getElementById('beatport-charts-slider-track');
    const indicatorsContainer = document.getElementById('beatport-charts-slider-indicators');

    if (!sliderTrack || !indicatorsContainer) {
        console.error('Charts slider elements not found');
        return;
    }

    const cardsPerSlide = 10; // 5x2 grid
    const totalSlides = Math.ceil(charts.length / cardsPerSlide);

    // Clear existing content
    sliderTrack.innerHTML = '';
    indicatorsContainer.innerHTML = '';

    // Update state
    beatportChartsSliderState.totalSlides = totalSlides;
    beatportChartsSliderState.currentSlide = 0;

    console.log(`🎯 Creating ${totalSlides} chart slides with ${cardsPerSlide} cards each`);

    // Generate slides HTML
    for (let slideIndex = 0; slideIndex < totalSlides; slideIndex++) {
        const startIndex = slideIndex * cardsPerSlide;
        const endIndex = Math.min(startIndex + cardsPerSlide, charts.length);
        const slideCharts = charts.slice(startIndex, endIndex);

        // Create grid HTML for this slide
        const gridHtml = slideCharts.map(chart => {
            const bgImageStyle = chart.image ? `--chart-bg-image: url('${chart.image}')` : '';
            return `
                <div class="beatport-chart-card" style="${bgImageStyle}" data-url="${chart.url || ''}">
                    <div class="beatport-chart-card-content">
                        <div class="beatport-chart-name">${chart.name || 'Unknown Chart'}</div>
                        <div class="beatport-chart-creator">${chart.creator || 'Unknown Creator'}</div>
                    </div>
                </div>
            `;
        }).join('');

        // Create slide HTML
        const slideHtml = `
            <div class="beatport-charts-slide ${slideIndex === 0 ? 'active' : ''}">
                <div class="beatport-charts-grid">
                    ${gridHtml}
                </div>
            </div>
        `;

        sliderTrack.innerHTML += slideHtml;

        // Create indicator
        const indicatorHtml = `<button class="beatport-charts-indicator ${slideIndex === 0 ? 'active' : ''}" data-slide="${slideIndex}"></button>`;
        indicatorsContainer.innerHTML += indicatorHtml;
    }

    console.log(`✅ Created ${totalSlides} chart slides`);

    // Add click handlers for individual chart discovery (matching chart pattern)
    const chartCards = sliderTrack.querySelectorAll('.beatport-chart-card[data-url]');
    chartCards.forEach((card) => {
        const chartUrl = card.getAttribute('data-url');
        if (chartUrl && chartUrl !== '') {
            // Find the corresponding chart data
            const chartData = charts.find(chart => chart.url === chartUrl);
            if (chartData) {
                card.addEventListener('click', () => handleBeatportChartCardClick(card, chartData));
                card.style.cursor = 'pointer';
            }
        }
    });
}

/**
 * Set up navigation functionality (copied from releases slider with button cloning)
 */
function setupBeatportChartsSliderNavigation() {
    const prevBtn = document.getElementById('beatport-charts-prev-btn');
    const nextBtn = document.getElementById('beatport-charts-next-btn');

    if (prevBtn) {
        // Clone button to remove all existing event listeners
        const newPrevBtn = prevBtn.cloneNode(true);
        prevBtn.parentNode.replaceChild(newPrevBtn, prevBtn);

        newPrevBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Previous charts button clicked, current slide:', beatportChartsSliderState.currentSlide);
            goToBeatportChartsSlide(beatportChartsSliderState.currentSlide - 1);
            resetBeatportChartsSliderAutoPlay();
        });
    }

    if (nextBtn) {
        // Clone button to remove all existing event listeners
        const newNextBtn = nextBtn.cloneNode(true);
        nextBtn.parentNode.replaceChild(newNextBtn, nextBtn);

        newNextBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Next charts button clicked, current slide:', beatportChartsSliderState.currentSlide);
            goToBeatportChartsSlide(beatportChartsSliderState.currentSlide + 1);
            resetBeatportChartsSliderAutoPlay();
        });
    }
}

/**
 * Set up indicator functionality (copied from releases slider)
 */
function setupBeatportChartsSliderIndicators() {
    const indicators = document.querySelectorAll('.beatport-charts-indicator');

    indicators.forEach((indicator, index) => {
        indicator.addEventListener('click', () => {
            goToBeatportChartsSlide(index);
            resetBeatportChartsSliderAutoPlay();
        });
    });
}

/**
 * Navigate to a specific slide (copied from releases slider)
 */
function goToBeatportChartsSlide(slideIndex) {
    console.log('goToBeatportChartsSlide called with:', slideIndex, 'current:', beatportChartsSliderState.currentSlide);

    // Wrap around if out of bounds
    if (slideIndex < 0) {
        slideIndex = beatportChartsSliderState.totalSlides - 1;
    } else if (slideIndex >= beatportChartsSliderState.totalSlides) {
        slideIndex = 0;
    }

    console.log('After wrapping, slideIndex:', slideIndex);

    // Update current slide
    beatportChartsSliderState.currentSlide = slideIndex;

    // Update slide visibility
    const slides = document.querySelectorAll('.beatport-charts-slide');
    slides.forEach((slide, index) => {
        slide.classList.remove('active', 'prev', 'next');

        if (index === slideIndex) {
            slide.classList.add('active');
        } else if (index < slideIndex) {
            slide.classList.add('prev');
        } else {
            slide.classList.add('next');
        }
    });

    // Update indicators
    const indicators = document.querySelectorAll('.beatport-charts-indicator');
    indicators.forEach((indicator, index) => {
        indicator.classList.toggle('active', index === slideIndex);
    });

    console.log('Charts slide updated to:', beatportChartsSliderState.currentSlide);
}

/**
 * Start auto-play functionality (copied from releases slider)
 */
function startBeatportChartsSliderAutoPlay() {
    if (beatportChartsSliderState.autoPlayInterval) {
        clearInterval(beatportChartsSliderState.autoPlayInterval);
    }

    beatportChartsSliderState.autoPlayInterval = setInterval(() => {
        goToBeatportChartsSlide(beatportChartsSliderState.currentSlide + 1);
    }, beatportChartsSliderState.autoPlayDelay);
}

/**
 * Reset auto-play timer (copied from releases slider)
 */
function resetBeatportChartsSliderAutoPlay() {
    startBeatportChartsSliderAutoPlay();
}

/**
 * Set up hover pause functionality (copied from releases slider)
 */
function setupBeatportChartsSliderHoverPause() {
    const sliderContainer = document.querySelector('.beatport-charts-slider-container');

    if (sliderContainer) {
        sliderContainer.addEventListener('mouseenter', () => {
            if (beatportChartsSliderState.autoPlayInterval) {
                clearInterval(beatportChartsSliderState.autoPlayInterval);
                beatportChartsSliderState.autoPlayInterval = null;
            }
        });

        sliderContainer.addEventListener('mouseleave', () => {
            startBeatportChartsSliderAutoPlay();
        });
    }
}

/**
 * Clean up charts slider when switching away (copied from releases slider)
 */
function cleanupBeatportChartsSlider() {
    if (beatportChartsSliderState.autoPlayInterval) {
        clearInterval(beatportChartsSliderState.autoPlayInterval);
        beatportChartsSliderState.autoPlayInterval = null;
    }
}

// ===================================
// BEATPORT DJ CHARTS SLIDER
// ===================================

// State management for DJ charts slider (3 cards per slide)
let beatportDJSliderState = {
    currentSlide: 0,
    totalSlides: 0,
    autoPlayInterval: null,
    autoPlayDelay: 12000,  // Longer auto-play for DJ charts
    isInitialized: false
};

/**
 * Initialize the beatport DJ charts slider functionality (based on charts slider)
 */
function initializeBeatportDJSlider() {
    console.log('🎧 Initializing beatport DJ charts slider...');

    const slider = document.getElementById('beatport-dj-slider');
    if (!slider) {
        console.warn('Beatport DJ slider not found');
        return;
    }

    // Prevent double initialization
    if (slider.dataset.initialized === 'true') {
        console.log('DJ slider already initialized');
        return;
    }

    const sliderTrack = document.getElementById('beatport-dj-slider-track');
    const indicatorsContainer = document.getElementById('beatport-dj-slider-indicators');

    if (!sliderTrack || !indicatorsContainer) {
        console.warn('DJ slider elements not found');
        return;
    }

    // Load data and initialize
    loadBeatportDJCharts().then(success => {
        if (success) {
            setupBeatportDJSliderNavigation();
            setupBeatportDJSliderIndicators();
            setupBeatportDJSliderHoverPause();
            startBeatportDJSliderAutoPlay();
            slider.dataset.initialized = 'true';
            beatportDJSliderState.isInitialized = true;
            console.log('✅ DJ charts slider initialized successfully');
        }
    });
}

/**
 * Load DJ charts data from API
 */
async function loadBeatportDJCharts() {
    try {
        console.log('🎧 Loading DJ charts data...');
        const signal = getBeatportContentSignal();
        const response = await fetch('/api/beatport/dj-charts', signal ? { signal } : undefined);
        const data = await response.json();

        if (data.success && data.charts && data.charts.length > 0) {
            console.log(`📈 Loaded ${data.charts.length} DJ charts`);
            createBeatportDJSlides(data.charts);
            return true;
        } else {
            console.warn('No DJ charts data available');
            return false;
        }
    } catch (error) {
        if (error && error.name === 'AbortError') return false;
        console.error('❌ Error loading DJ charts:', error);
        return false;
    }
}

/**
 * Create DJ chart slides with 3 cards per slide layout
 */
function createBeatportDJSlides(charts) {
    const sliderTrack = document.getElementById('beatport-dj-slider-track');
    const indicatorsContainer = document.getElementById('beatport-dj-slider-indicators');

    if (!sliderTrack || !indicatorsContainer) {
        console.error('DJ slider elements not found');
        return;
    }

    const cardsPerSlide = 3; // 3 cards per slide for DJ charts
    const totalSlides = Math.ceil(charts.length / cardsPerSlide);

    // Clear existing content
    sliderTrack.innerHTML = '';
    indicatorsContainer.innerHTML = '';

    // Update state
    beatportDJSliderState.totalSlides = totalSlides;
    beatportDJSliderState.currentSlide = 0;

    console.log(`🎯 Creating ${totalSlides} DJ chart slides with ${cardsPerSlide} cards each`);

    // Generate slides HTML
    for (let slideIndex = 0; slideIndex < totalSlides; slideIndex++) {
        const startIndex = slideIndex * cardsPerSlide;
        const endIndex = Math.min(startIndex + cardsPerSlide, charts.length);
        const slideCharts = charts.slice(startIndex, endIndex);

        // Create grid HTML for this slide
        const gridHtml = slideCharts.map(chart => {
            const bgImageStyle = chart.image ? `--dj-bg-image: url('${chart.image}')` : '';
            return `
                <div class="beatport-dj-card" style="${bgImageStyle}" data-url="${chart.url || ''}">
                    <div class="beatport-dj-card-content">
                        <div class="beatport-dj-name">${chart.name || 'Unknown Chart'}</div>
                        <div class="beatport-dj-creator">${chart.creator || 'Unknown Creator'}</div>
                    </div>
                </div>
            `;
        }).join('');

        // Create slide HTML
        const slideHtml = `
            <div class="beatport-dj-slide ${slideIndex === 0 ? 'active' : ''}">
                <div class="beatport-dj-grid">
                    ${gridHtml}
                </div>
            </div>
        `;

        sliderTrack.innerHTML += slideHtml;

        // Create indicator
        const indicatorHtml = `<button class="beatport-dj-indicator ${slideIndex === 0 ? 'active' : ''}" data-slide="${slideIndex}"></button>`;
        indicatorsContainer.innerHTML += indicatorHtml;
    }

    console.log(`✅ Created ${totalSlides} DJ chart slides`);

    // Add click handlers for individual DJ chart discovery (matching chart pattern)
    const djChartCards = sliderTrack.querySelectorAll('.beatport-dj-card[data-url]');
    djChartCards.forEach((card) => {
        const chartUrl = card.getAttribute('data-url');
        if (chartUrl && chartUrl !== '') {
            // Find the corresponding chart data
            const chartData = charts.find(chart => chart.url === chartUrl);
            if (chartData) {
                card.addEventListener('click', () => handleBeatportDJChartCardClick(card, chartData));
                card.style.cursor = 'pointer';
            }
        }
    });
}

/**
 * Set up navigation functionality (copied from charts slider with button cloning)
 */
function setupBeatportDJSliderNavigation() {
    const prevBtn = document.getElementById('beatport-dj-prev-btn');
    const nextBtn = document.getElementById('beatport-dj-next-btn');

    if (prevBtn) {
        // Clone button to remove all existing event listeners
        const newPrevBtn = prevBtn.cloneNode(true);
        prevBtn.parentNode.replaceChild(newPrevBtn, prevBtn);

        newPrevBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Previous DJ button clicked, current slide:', beatportDJSliderState.currentSlide);
            goToBeatportDJSlide(beatportDJSliderState.currentSlide - 1);
            resetBeatportDJSliderAutoPlay();
        });
    }

    if (nextBtn) {
        // Clone button to remove all existing event listeners
        const newNextBtn = nextBtn.cloneNode(true);
        nextBtn.parentNode.replaceChild(newNextBtn, nextBtn);

        newNextBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('Next DJ button clicked, current slide:', beatportDJSliderState.currentSlide);
            goToBeatportDJSlide(beatportDJSliderState.currentSlide + 1);
            resetBeatportDJSliderAutoPlay();
        });
    }
}

/**
 * Set up indicator functionality (copied from charts slider)
 */
function setupBeatportDJSliderIndicators() {
    const indicators = document.querySelectorAll('.beatport-dj-indicator');

    indicators.forEach((indicator, index) => {
        indicator.addEventListener('click', () => {
            goToBeatportDJSlide(index);
            resetBeatportDJSliderAutoPlay();
        });
    });
}

/**
 * Navigate to a specific slide (copied from charts slider)
 */
function goToBeatportDJSlide(slideIndex) {
    console.log('goToBeatportDJSlide called with:', slideIndex, 'current:', beatportDJSliderState.currentSlide);

    // Wrap around if out of bounds
    if (slideIndex < 0) {
        slideIndex = beatportDJSliderState.totalSlides - 1;
    } else if (slideIndex >= beatportDJSliderState.totalSlides) {
        slideIndex = 0;
    }

    console.log('After wrapping, slideIndex:', slideIndex);

    // Update current slide
    beatportDJSliderState.currentSlide = slideIndex;

    // Update slide visibility
    const slides = document.querySelectorAll('.beatport-dj-slide');
    slides.forEach((slide, index) => {
        slide.classList.remove('active', 'prev', 'next');

        if (index === slideIndex) {
            slide.classList.add('active');
        } else if (index < slideIndex) {
            slide.classList.add('prev');
        } else {
            slide.classList.add('next');
        }
    });

    // Update indicators
    const indicators = document.querySelectorAll('.beatport-dj-indicator');
    indicators.forEach((indicator, index) => {
        indicator.classList.toggle('active', index === slideIndex);
    });

    console.log('DJ slide updated to:', beatportDJSliderState.currentSlide);
}

/**
 * Start auto-play functionality (copied from charts slider)
 */
function startBeatportDJSliderAutoPlay() {
    if (beatportDJSliderState.autoPlayInterval) {
        clearInterval(beatportDJSliderState.autoPlayInterval);
    }

    beatportDJSliderState.autoPlayInterval = setInterval(() => {
        goToBeatportDJSlide(beatportDJSliderState.currentSlide + 1);
    }, beatportDJSliderState.autoPlayDelay);
}

/**
 * Reset auto-play timer (copied from charts slider)
 */
function resetBeatportDJSliderAutoPlay() {
    startBeatportDJSliderAutoPlay();
}

/**
 * Set up hover pause functionality (copied from charts slider)
 */
function setupBeatportDJSliderHoverPause() {
    const sliderContainer = document.querySelector('.beatport-dj-slider-container');

    if (sliderContainer) {
        sliderContainer.addEventListener('mouseenter', () => {
            if (beatportDJSliderState.autoPlayInterval) {
                clearInterval(beatportDJSliderState.autoPlayInterval);
                beatportDJSliderState.autoPlayInterval = null;
            }
        });

        sliderContainer.addEventListener('mouseleave', () => {
            startBeatportDJSliderAutoPlay();
        });
    }
}

/**
 * Clean up DJ slider when switching away (copied from charts slider)
 */
function cleanupBeatportDJSlider() {
    if (beatportDJSliderState.autoPlayInterval) {
        clearInterval(beatportDJSliderState.autoPlayInterval);
        beatportDJSliderState.autoPlayInterval = null;
    }
}

/**
 * Load top 10 lists data from API and populate both lists
 */
async function loadBeatportTop10Lists() {
    try {
        console.log('🏆 Loading top 10 lists data...');
        const signal = getBeatportContentSignal();
        const response = await fetch('/api/beatport/homepage/top-10-lists', signal ? { signal } : undefined);
        const data = await response.json();

        if (data.success) {
            console.log(`🎵 Loaded ${data.beatport_count} Beatport Top 10 + ${data.hype_count} Hype Top 10 tracks`);

            // Populate both lists
            populateBeatportTop10List(data.beatport_top10);
            populateHypeTop10List(data.hype_top10);
            return true;
        } else {
            console.error('Failed to load top 10 lists:', data.error);
            showTop10ListsError(data.error || 'No data available');
            return false;
        }
    } catch (error) {
        if (error && error.name === 'AbortError') return false;
        console.error('Error loading top 10 lists:', error);
        showTop10ListsError('Failed to load top 10 lists');
        return false;
    }
}

/**
 * Clean track/artist text for proper spacing
 */
function cleanTrackText(text) {
    if (!text) return text;

    // Fix common spacing issues
    text = text.replace(/([a-z$!@#%&*])([A-Z])/g, '$1 $2');  // Add space between lowercase/symbols and uppercase
    text = text.replace(/([a-zA-Z]),([a-zA-Z])/g, '$1, $2');  // Add space after comma
    text = text.replace(/([a-zA-Z])(Mix|Remix|Extended|Version)\b/g, '$1 $2');  // Fix mix types
    text = text.replace(/\s+/g, ' ');  // Collapse multiple spaces
    text = text.trim();

    return text;
}

/**
 * Populate Beatport Top 10 list with data
 */
function populateBeatportTop10List(tracks) {
    const container = document.getElementById('beatport-top10-list');
    if (!container || !tracks || tracks.length === 0) return;

    // Generate HTML for the tracks
    let tracksHtml = `
        <div class="beatport-top10-list-header">
            <h3 class="beatport-top10-list-title">🎵 Beatport Top 10</h3>
            <p class="beatport-top10-list-subtitle">Most popular tracks on Beatport</p>
        </div>
        <div class="beatport-top10-tracks">
    `;

    tracks.forEach((track, index) => {
        // Clean the text data before injection
        const cleanTitle = cleanTrackText(track.title || 'Unknown Title');
        const cleanArtist = cleanTrackText(track.artist || 'Unknown Artist');
        const cleanLabel = cleanTrackText(track.label || 'Unknown Label');

        tracksHtml += `
            <div class="beatport-top10-card" data-url="${track.url || '#'}">
                <div class="beatport-top10-card-rank">${track.rank || index + 1}</div>
                <div class="beatport-top10-card-artwork">
                    ${track.artwork_url ?
                `<img src="${track.artwork_url}" alt="${cleanTitle}" loading="lazy">` :
                '<div class="beatport-top10-card-placeholder">🎵</div>'
            }
                </div>
                <div class="beatport-top10-card-info">
                    <h4 class="beatport-top10-card-title">${cleanTitle}</h4>
                    <p class="beatport-top10-card-artist">${cleanArtist}</p>
                    <p class="beatport-top10-card-label">${cleanLabel}</p>
                </div>
            </div>
        `;
    });

    tracksHtml += '</div>';
    container.innerHTML = tracksHtml;
}

/**
 * Populate Hype Top 10 list with data
 */
function populateHypeTop10List(tracks) {
    const container = document.getElementById('beatport-hype10-list');
    if (!container || !tracks || tracks.length === 0) return;

    // Generate HTML for the tracks
    let tracksHtml = `
        <div class="beatport-hype10-list-header">
            <h3 class="beatport-hype10-list-title">🔥 Hype Top 10</h3>
            <p class="beatport-hype10-list-subtitle">Editor's trending picks</p>
        </div>
        <div class="beatport-hype10-tracks">
    `;

    tracks.forEach((track, index) => {
        // Clean the text data before injection
        const cleanTitle = cleanTrackText(track.title || 'Unknown Title');
        const cleanArtist = cleanTrackText(track.artist || 'Unknown Artist');
        const cleanLabel = cleanTrackText(track.label || 'Unknown Label');

        tracksHtml += `
            <div class="beatport-hype10-card" data-url="${track.url || '#'}">
                <div class="beatport-hype10-card-rank">${track.rank || index + 1}</div>
                <div class="beatport-hype10-card-artwork">
                    ${track.artwork_url ?
                `<img src="${track.artwork_url}" alt="${cleanTitle}" loading="lazy">` :
                '<div class="beatport-hype10-card-placeholder">🔥</div>'
            }
                </div>
                <div class="beatport-hype10-card-info">
                    <h4 class="beatport-hype10-card-title">${cleanTitle}</h4>
                    <p class="beatport-hype10-card-artist">${cleanArtist}</p>
                    <p class="beatport-hype10-card-label">${cleanLabel}</p>
                </div>
            </div>
        `;
    });

    tracksHtml += '</div>';
    container.innerHTML = tracksHtml;
}

/**
 * Show error message for top 10 lists
 */
function showTop10ListsError(errorMessage) {
    const beatportContainer = document.getElementById('beatport-top10-list');
    const hypeContainer = document.getElementById('beatport-hype10-list');

    const errorHtml = `
        <div class="beatport-top10-error">
            <h3>❌ Error Loading Data</h3>
            <p>${errorMessage}</p>
        </div>
    `;

    if (beatportContainer) beatportContainer.innerHTML = errorHtml;
    if (hypeContainer) hypeContainer.innerHTML = errorHtml;
}

/**
 * Load top 10 releases data from API and populate the list
 */
async function loadBeatportTop10Releases() {
    try {
        console.log('💿 Loading top 10 releases data...');
        const signal = getBeatportContentSignal();
        const response = await fetch('/api/beatport/homepage/top-10-releases-cards', signal ? { signal } : undefined);
        const data = await response.json();

        if (data.success) {
            console.log(`💿 Loaded ${data.releases_count} Top 10 Releases`);
            populateBeatportTop10Releases(data.releases);
            return true;
        } else {
            console.error('Failed to load top 10 releases:', data.error);
            showTop10ReleasesError(data.error || 'No data available');
            return false;
        }
    } catch (error) {
        if (error && error.name === 'AbortError') return false;
        console.error('Error loading top 10 releases:', error);
        showTop10ReleasesError('Failed to load top 10 releases');
        return false;
    }
}

/**
 * Populate Top 10 Releases list with data
 */
function populateBeatportTop10Releases(releases) {
    const container = document.getElementById('beatport-releases-top10-list');
    if (!container || !releases || releases.length === 0) return;

    // Generate HTML for the releases
    let releasesHtml = `
        <div class="beatport-releases-top10-tracks">
    `;

    releases.forEach((release, index) => {
        releasesHtml += `
            <div class="beatport-releases-top10-card" data-url="${release.url || '#'}" data-bg-image="${release.image_url || ''}">
                <div class="beatport-releases-top10-card-rank">${release.rank || index + 1}</div>
                <div class="beatport-releases-top10-card-artwork">
                    ${release.image_url ?
                `<img src="${release.image_url}" alt="${release.title}" loading="lazy">` :
                '<div class="beatport-releases-top10-card-placeholder">💿</div>'
            }
                </div>
                <div class="beatport-releases-top10-card-info">
                    <h4 class="beatport-releases-top10-card-title">${release.title || 'Unknown Title'}</h4>
                    <p class="beatport-releases-top10-card-artist">${release.artist || 'Unknown Artist'}</p>
                    <p class="beatport-releases-top10-card-label">${release.label || 'Unknown Label'}</p>
                </div>
            </div>
        `;
    });

    releasesHtml += '</div>';
    container.innerHTML = releasesHtml;

    // Set background images for cards
    const cards = container.querySelectorAll('.beatport-releases-top10-card[data-bg-image]');
    cards.forEach(card => {
        const bgImage = card.getAttribute('data-bg-image');
        if (bgImage) {
            // Transform image URL from 95x95 to 500x500 for higher quality background
            const highResImage = bgImage.replace('/image_size/95x95/', '/image_size/500x500/');
            card.style.backgroundImage = `linear-gradient(rgba(0,0,0,0.7), rgba(0,0,0,0.8)), url('${highResImage}')`;
            card.style.backgroundSize = 'cover';
            card.style.backgroundPosition = 'center';
        }
    });

    // Add click handlers for individual release discovery
    const releaseCards = container.querySelectorAll('.beatport-releases-top10-card[data-url]');
    releaseCards.forEach((card, index) => {
        card.addEventListener('click', () => handleBeatportReleaseCardClick(card, releases[index]));
        card.style.cursor = 'pointer';
    });
}

/**
 * Show error message for top 10 releases
 */
function showTop10ReleasesError(errorMessage) {
    const container = document.getElementById('beatport-releases-top10-list');

    const errorHtml = `
        <div class="beatport-releases-top10-error">
            <h3>❌ Error Loading Releases</h3>
            <p>${errorMessage}</p>
        </div>
    `;

    if (container) container.innerHTML = errorHtml;
}

/**
 * Handle click on individual Top 10 Release card - create discovery process for single release
 */
async function handleBeatportReleaseCardClick(cardElement, release) {
    if (_beatportModalOpening) return;
    _beatportModalOpening = true;

    console.log(`💿 Individual release card clicked: ${release.title} by ${release.artist}`);

    if (!release.url || release.url === '#') {
        _beatportModalOpening = false;
        showToast('No release URL available', 'error');
        return;
    }

    try {
        showToast(`Loading ${release.title}...`, 'info');
        showLoadingOverlay(`Getting tracks from ${release.title}...`);

        // Fetch structured release metadata for direct download modal
        console.log(`🎵 Fetching release metadata: ${release.url}`);
        const response = await fetch('/api/beatport/release-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ release_url: release.url })
        });

        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error(data.error || 'No tracks found in this release');
        }

        console.log(`✅ Got ${data.tracks.length} tracks from ${data.album.name}`);

        // Format artists as array of strings for compatibility with download modal
        const formattedTracks = data.tracks.map(track => ({
            ...track,
            artists: track.artists.map(a => typeof a === 'object' ? a.name : a)
        }));

        const virtualPlaylistId = `beatport_release_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const playlistName = data.album.name;

        // Open download modal directly - same as clicking an album on the Artists page
        await openDownloadMissingModalForArtistAlbum(
            virtualPlaylistId,
            playlistName,
            formattedTracks,
            data.album,
            data.artist,
            false
        );

        // Register Beatport download bubble for releases (albums, EPs, singles)
        const releaseImage = (data.album.images && data.album.images.length > 0) ? data.album.images[0].url : (release.image_url || '');
        registerBeatportDownload(playlistName, releaseImage, virtualPlaylistId);

        hideLoadingOverlay();
        _beatportModalOpening = false;
        console.log(`✅ Opened download modal for ${playlistName}`);

    } catch (error) {
        console.error(`❌ Error handling release click for ${release.title}:`, error);
        hideLoadingOverlay();
        _beatportModalOpening = false;
        showToast(`Error loading ${release.title}: ${error.message}`, 'error');
    }
}

/**
 * Convert scraped Beatport tracks into download-modal-compatible format and open the modal.
 * Used by all chart/playlist handlers (Top 100, Hype 100, Featured Charts, DJ Charts, genre charts).
 * Charts open as compilations — each track is searched independently on Soulseek.
 */
// Guard against multiple rapid clicks opening duplicate modals
let _beatportModalOpening = false;

/**
 * Enrich tracks via a single batch request to the backend.
 * Progress is reported via WebSocket (beatport:enrich_progress) and updates the loading overlay.
 * Returns the enriched tracks array.
 */
async function _enrichTracksWithProgress(tracks, chartName) {
    const enrichmentId = `enrich_${Date.now()}_${Math.random().toString(36).substr(2, 6)}`;

    try {
        const resp = await fetch('/api/beatport/enrich-tracks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks, enrichment_id: enrichmentId })
        });
        const data = await resp.json();

        // Synchronous path — all tracks were cached, results returned inline
        if (data.success && data.tracks) {
            return data.tracks;
        }

        // Async path — poll for progress until done
        if (data.success && data.async) {
            while (true) {
                await new Promise(r => setTimeout(r, 800));
                try {
                    const progressResp = await fetch(`/api/beatport/enrich-progress/${enrichmentId}?_=${Date.now()}`);
                    const progress = await progressResp.json();
                    if (!progress.success) break;

                    // Update loading overlay with live progress
                    const overlayText = document.querySelector('#loading-overlay .loading-message');
                    if (overlayText) {
                        overlayText.textContent = `Fetching track metadata... (${progress.completed}/${progress.total}) ${progress.current_track || ''}`;
                    }

                    if (progress.done) {
                        if (progress.tracks) {
                            return progress.tracks;
                        }
                        console.warn('⚠️ Async enrichment failed:', progress.error);
                        return tracks;
                    }
                } catch (pollErr) {
                    console.warn('⚠️ Progress poll error:', pollErr);
                }
            }
        }

        console.warn('⚠️ Enrichment failed, returning original tracks');
        return tracks;
    } catch (e) {
        console.warn('⚠️ Failed to enrich tracks:', e);
        return tracks;
    }
}

function parseBeatportDuration(raw) {
    if (!raw) return 0;
    if (typeof raw === 'string' && raw.includes(':')) {
        const parts = raw.split(':');
        return (parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10)) * 1000 || 0;
    }
    return (parseInt(raw, 10) || 0) * 1000;
}

function openBeatportChartAsDownloadModal(tracks, chartName, chartImage) {
    // Note: callers already guard against double-clicks via _beatportModalOpening.
    // Reset the flag here so the modal can open even after fast (cached) enrichment.
    _beatportModalOpening = false;

    const albumObj = {
        id: `beatport_chart_${Date.now()}`,
        name: chartName,
        album_type: 'compilation',
        images: chartImage ? [{ url: chartImage }] : [],
        total_tracks: tracks.length
    };

    const formattedTracks = tracks.map((track, index) => {
        // Use per-track release metadata if available (from JSON extraction)
        const hasRelease = track.release_name && track.release_name.length > 0;
        const trackAlbum = hasRelease ? {
            id: `beatport_release_${track.release_id || index}`,
            name: cleanTrackText(track.release_name),
            album_type: 'single',
            images: track.release_image ? [{ url: track.release_image }] : [],
            release_date: track.release_date || '',
            total_tracks: 1
        } : albumObj;

        // Combine title + mix_name
        let trackName = cleanTrackText(track.title || 'Unknown Title');
        if (track.mix_name && track.mix_name.toLowerCase() !== 'original mix') {
            trackName = `${trackName} (${cleanTrackText(track.mix_name)})`;
        }

        // Split combined artist string into individual names for proper folder structure
        const rawArtist = cleanTrackText(track.artist || 'Unknown Artist');
        const artistList = rawArtist.includes(',')
            ? rawArtist.split(',').map(a => a.trim()).filter(a => a)
            : [rawArtist];

        return {
            id: `beatport_chart_${index}`,
            name: trackName,
            artists: artistList,
            duration_ms: parseBeatportDuration(track.duration),
            track_number: index + 1,
            disc_number: 1,
            album: trackAlbum
        };
    });

    const virtualPlaylistId = `beatport_chart_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    // Compilation artist
    const artistObj = { id: 'beatport_various', name: 'Various Artists' };

    openDownloadMissingModalForArtistAlbum(
        virtualPlaylistId,
        chartName,
        formattedTracks,
        albumObj,
        artistObj,
        false,
        'playlist'
    );

    // Register Beatport download bubble
    registerBeatportDownload(chartName, chartImage, virtualPlaylistId);
}

/**
 * Handle click on individual chart card - open download modal directly
 */
async function handleBeatportChartCardClick(cardElement, chart) {
    console.log(`📊 Individual chart card clicked: ${chart.name} by ${chart.creator}`);

    if (!chart.url || chart.url === '') {
        showToast('No chart URL available', 'error');
        return;
    }

    try {
        const chartName = `${chart.name} - ${chart.creator}`;
        showToast(`Loading ${chart.name}...`, 'info');
        showLoadingOverlay(`Scraping ${chart.name}...`);

        const response = await fetch('/api/beatport/chart/extract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                chart_url: chart.url,
                chart_name: `Featured Chart: ${chart.name}`,
                limit: 100,
                enrich: false
            })
        });

        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error('No tracks found in this chart');
        }

        console.log(`✅ Fetched ${data.tracks.length} raw tracks from ${chart.name}, enriching...`);
        const enrichedTracks = await _enrichTracksWithProgress(data.tracks, chartName);

        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(enrichedTracks, chartName, chart.image);

    } catch (error) {
        console.error(`❌ Error handling chart click for ${chart.name}:`, error);
        hideLoadingOverlay();
        showToast(`Error loading ${chart.name}: ${error.message}`, 'error');
    }
}

/**
 * Handle click on individual DJ chart card - open download modal directly
 */
async function handleBeatportDJChartCardClick(cardElement, chart) {
    console.log(`🎧 Individual DJ chart card clicked: ${chart.name} by ${chart.creator}`);

    if (!chart.url || chart.url === '') {
        showToast('No DJ chart URL available', 'error');
        return;
    }

    try {
        const chartName = `${chart.name} - ${chart.creator}`;
        showToast(`Loading ${chart.name}...`, 'info');
        showLoadingOverlay(`Scraping ${chart.name}...`);

        const response = await fetch('/api/beatport/chart/extract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                chart_url: chart.url,
                chart_name: `DJ Chart: ${chart.name}`,
                limit: 100,
                enrich: false
            })
        });

        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error('No tracks found in this DJ chart');
        }

        console.log(`✅ Fetched ${data.tracks.length} raw tracks from ${chart.name}, enriching...`);
        const enrichedTracks = await _enrichTracksWithProgress(data.tracks, chartName);

        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(enrichedTracks, chartName, chart.image);

    } catch (error) {
        console.error(`❌ Error handling DJ chart click for ${chart.name}:`, error);
        hideLoadingOverlay();
        showToast(`Error loading ${chart.name}: ${error.message}`, 'error');
    }
}

/**
 * Handle click on Beatport Top 100 button - open download modal directly
 */
async function handleBeatportTop100Click() {
    if (_beatportModalOpening) return;
    _beatportModalOpening = true;
    setTimeout(() => { _beatportModalOpening = false; }, 2000);

    console.log('💯 Beatport Top 100 button clicked');

    try {
        showLoadingOverlay('Scraping Beatport Top 100...');

        // Fetch track list without enrichment (fast)
        const response = await fetch('/api/beatport/top-100?enrich=false', { method: 'GET' });
        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error('No tracks found in Beatport Top 100');
        }

        console.log(`✅ Fetched ${data.tracks.length} tracks, enriching one-by-one...`);

        // Enrich one-by-one with live progress
        const enrichedTracks = await _enrichTracksWithProgress(data.tracks, 'Beatport Top 100');

        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(enrichedTracks, 'Beatport Top 100', null);

    } catch (error) {
        console.error('❌ Error handling Beatport Top 100 click:', error);
        hideLoadingOverlay();
        showToast(`Error loading Beatport Top 100: ${error.message}`, 'error');
    }
}

/**
 * Handle click on Hype Top 100 button - open download modal directly
 */
async function handleHypeTop100Click() {
    if (_beatportModalOpening) return;
    _beatportModalOpening = true;
    setTimeout(() => { _beatportModalOpening = false; }, 2000);

    console.log('🔥 Hype Top 100 button clicked');

    try {
        showLoadingOverlay('Scraping Hype Top 100...');

        // Fetch track list without enrichment (fast)
        const response = await fetch('/api/beatport/hype-top-100?enrich=false', { method: 'GET' });
        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error('No tracks found in Hype Top 100');
        }

        console.log(`✅ Fetched ${data.tracks.length} tracks, enriching one-by-one...`);

        // Enrich one-by-one with live progress
        const enrichedTracks = await _enrichTracksWithProgress(data.tracks, 'Hype Top 100');

        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(enrichedTracks, 'Hype Top 100', null);

    } catch (error) {
        console.error('❌ Error handling Hype Top 100 click:', error);
        hideLoadingOverlay();
        showToast(`Error loading Hype Top 100: ${error.message}`, 'error');
    }
}

// ================================= //
// GENRE BROWSER MODAL FUNCTIONS    //
// ================================= //

// Cache for genre browser data to avoid re-loading
let genreBrowserCache = {
    genres: null,
    imagesLoaded: false,
    lastLoaded: null,
    imageLoadingActive: false,
    imageWorkers: null
};

function initializeGenreBrowserModal() {
    console.log('🎵 Initializing Genre Browser Modal...');

    // Browse by Genre button click handler
    const browseByGenreBtn = document.getElementById('browse-by-genre-btn');
    if (browseByGenreBtn) {
        browseByGenreBtn.addEventListener('click', () => {
            console.log('🎵 Browse by Genre button clicked');
            openGenreBrowserModal();
        });
    }

    // Modal close button handler
    const modalCloseBtn = document.getElementById('genre-browser-modal-close');
    if (modalCloseBtn) {
        modalCloseBtn.addEventListener('click', closeGenreBrowserModal);
    }

    // Click outside modal to close
    const modalOverlay = document.getElementById('genre-browser-modal');
    if (modalOverlay) {
        modalOverlay.addEventListener('click', (e) => {
            if (e.target === modalOverlay) {
                closeGenreBrowserModal();
            }
        });
    }

    // Search functionality
    const searchInput = document.getElementById('genre-browser-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            filterGenreBrowserCards(e.target.value);
        });
    }

    // ESC key to close modal
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isGenreBrowserModalOpen()) {
            closeGenreBrowserModal();
        }
    });

    console.log('✅ Genre Browser Modal initialized');
}

function openGenreBrowserModal() {
    console.log('🎵 Opening Genre Browser Modal...');

    const modal = document.getElementById('genre-browser-modal');
    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden'; // Prevent background scrolling

        // Check cache before loading genres
        if (genreBrowserCache.genres && genreBrowserCache.genres.length > 0) {
            console.log('💾 Using cached genres data');
            displayCachedGenres();
        } else {
            console.log('🔄 No cached data, loading genres...');
            loadGenreBrowserGenres();
        }

        console.log('✅ Genre Browser Modal opened');
    }
}

function closeGenreBrowserModal() {
    console.log('🎵 Closing Genre Browser Modal...');

    const modal = document.getElementById('genre-browser-modal');
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = ''; // Restore scrolling

        // Clear search input but keep the genre data cached
        const searchInput = document.getElementById('genre-browser-search');
        if (searchInput) {
            searchInput.value = '';
            // Also reset the display filter to show all genres
            filterGenreBrowserCards('');
        }

        // Pause image loading workers if they're running
        if (genreBrowserCache.imageLoadingActive) {
            console.log('⏸️ Pausing image loading workers...');
            genreBrowserCache.imageLoadingActive = false;
        }

        console.log('✅ Genre Browser Modal closed (data preserved in cache)');
    }
}

function isGenreBrowserModalOpen() {
    const modal = document.getElementById('genre-browser-modal');
    return modal && modal.classList.contains('active');
}

async function loadGenreBrowserGenres() {
    console.log('🔍 Loading genres for Genre Browser Modal...');

    const genresGrid = document.getElementById('genre-browser-genres-grid');
    if (!genresGrid) {
        console.error('❌ Genre browser grid not found');
        return;
    }

    // Show loading state
    genresGrid.innerHTML = `
        <div class="genre-browser-loading-container">
            <div class="genre-browser-loading-spinner"></div>
            <p class="genre-browser-loading-text">🔍 Discovering current Beatport genres...</p>
        </div>
    `;

    try {
        // First, fetch genres quickly without images
        console.log('🚀 Fetching genres without images for fast loading...');
        const fastResponse = await fetch('/api/beatport/genres');
        if (!fastResponse.ok) {
            throw new Error(`API returned ${fastResponse.status}: ${fastResponse.statusText}`);
        }

        const fastData = await fastResponse.json();
        const genres = fastData.genres || [];

        if (genres.length === 0) {
            genresGrid.innerHTML = `
                <div class="genre-browser-loading-container">
                    <p style="color: rgba(255, 255, 255, 0.7);">⚠️ No genres available</p>
                    <button onclick="loadGenreBrowserGenres()" style="margin-top: 10px; padding: 10px 20px; border: 1px solid rgba(255, 255, 255, 0.3); background: rgba(20, 20, 20, 0.8); color: white; border-radius: 8px; cursor: pointer;">🔄 Retry</button>
                </div>
            `;
            return;
        }

        // Filter out unwanted genres (section titles, etc.)
        const filteredGenres = genres.filter(genre => {
            const name = genre.name.toLowerCase().trim();
            const unwantedGenres = [
                'open format',
                'electronic',
                'genres',
                'browse',
                'charts',
                'new releases',
                'trending',
                'featured',
                'popular'
            ];

            const isUnwanted = unwantedGenres.includes(name);
            if (isUnwanted) {
                console.log(`🚫 Filtered out unwanted genre: "${genre.name}"`);
            }
            return !isUnwanted;
        });

        console.log(`📋 Filtered genres: ${genres.length} → ${filteredGenres.length} (removed ${genres.length - filteredGenres.length} unwanted)`);

        // Generate genre cards dynamically (without images first)
        const genreCardsHTML = filteredGenres.map(genre => `
            <div class="genre-browser-card genre-browser-card-fallback"
                 data-genre-slug="${genre.slug}"
                 data-genre-id="${genre.id}"
                 data-genre-name="${genre.name}"
                 data-url="${genre.url}">
                <div class="genre-browser-card-image">🎵</div>
                <div class="genre-browser-card-content">
                    <h3 class="genre-browser-card-title">${genre.name}</h3>
                    <p class="genre-browser-card-subtitle">Top 10 & Top 100 Charts</p>
                </div>
            </div>
        `).join('');

        genresGrid.innerHTML = genreCardsHTML;

        // Add click event listeners to genre cards
        addGenreBrowserCardClickListeners();

        // Cache the filtered genres data
        genreBrowserCache.genres = filteredGenres;
        genreBrowserCache.lastLoaded = new Date();
        genreBrowserCache.imagesLoaded = false;

        console.log(`✅ Loaded ${filteredGenres.length} Beatport genres for modal (fast mode)`);
        console.log(`💾 Cached ${filteredGenres.length} genres for future use`);
        showToast(`Loaded ${filteredGenres.length} genres for browsing`, 'success');

        // Now fetch images progressively in the background
        if (filteredGenres.length > 5) {
            console.log('🖼️ Loading genre images progressively for modal...');
            loadGenreBrowserImagesProgressively(filteredGenres);
        }

    } catch (error) {
        console.error('❌ Error loading genres for modal:', error);
        genresGrid.innerHTML = `
            <div class="genre-browser-loading-container">
                <p style="color: rgba(255, 255, 255, 0.7);">❌ Failed to load genres: ${error.message}</p>
                <button onclick="loadGenreBrowserGenres()" style="margin-top: 10px; padding: 10px 20px; border: 1px solid rgba(255, 255, 255, 0.3); background: rgba(20, 20, 20, 0.8); color: white; border-radius: 8px; cursor: pointer;">🔄 Retry</button>
            </div>
        `;
        showToast(`Error loading genres: ${error.message}`, 'error');
    }
}

function displayCachedGenres() {
    console.log('💾 Displaying cached genres...');

    const genresGrid = document.getElementById('genre-browser-genres-grid');
    if (!genresGrid) {
        console.error('❌ Genre browser grid not found');
        return;
    }

    const genres = genreBrowserCache.genres;
    if (!genres || genres.length === 0) {
        console.error('❌ No cached genres available');
        return;
    }

    // Generate genre cards from cached data
    const genreCardsHTML = genres.map(genre => `
        <div class="genre-browser-card genre-browser-card-fallback"
             data-genre-slug="${genre.slug}"
             data-genre-id="${genre.id}"
             data-genre-name="${genre.name}"
             data-url="${genre.url}">
            <div class="genre-browser-card-image">🎵</div>
            <div class="genre-browser-card-content">
                <h3 class="genre-browser-card-title">${genre.name}</h3>
                <p class="genre-browser-card-subtitle">Top 10 & Top 100 Charts</p>
            </div>
        </div>
    `).join('');

    genresGrid.innerHTML = genreCardsHTML;

    // Add click event listeners to genre cards
    addGenreBrowserCardClickListeners();

    console.log(`✅ Displayed ${genres.length} cached genres instantly`);

    // Handle image loading based on current state
    if (genreBrowserCache.imagesLoaded) {
        console.log('🖼️ Images already loaded, restoring them...');
        restoreCachedImages(genres);
    } else if (!genreBrowserCache.imageLoadingActive && genres.length > 5) {
        // Resume or start image loading
        const cachedCount = genres.filter(g => g.imageUrl).length;
        if (cachedCount > 0) {
            console.log(`🔄 Resuming image loading (${cachedCount}/${genres.length} already cached)...`);
            restoreCachedImages(genres); // Show already cached images
        } else {
            console.log('🖼️ Starting fresh image loading for cached genres...');
        }
        loadGenreBrowserImagesProgressively(genres);
    } else {
        console.log('📷 Image loading in progress, showing cached images...');
        restoreCachedImages(genres);
    }
}

function restoreCachedImages(genres) {
    // Restore images that were already loaded in previous sessions
    genres.forEach(genre => {
        if (genre.imageUrl) {
            const genreCard = document.querySelector(
                `.genre-browser-card[data-genre-slug="${genre.slug}"][data-genre-id="${genre.id}"]`
            );

            if (genreCard) {
                const imageElement = genreCard.querySelector('.genre-browser-card-image');
                if (imageElement) {
                    imageElement.innerHTML = `<img src="${genre.imageUrl}" alt="${genre.name}" loading="lazy" style="width: 100%; height: 100%; object-fit: cover;">`;
                    genreCard.classList.remove('genre-browser-card-fallback');
                }
            }
        }
    });
}

async function loadGenreBrowserImagesProgressively(genres) {
    // Load genre images with 2 concurrent workers for faster loading
    // Only process genres that don't already have cached images
    const imageQueue = genres.filter(genre => !genre.imageUrl);
    let imagesLoaded = 0;
    const maxWorkers = 2;

    // Mark loading as active
    genreBrowserCache.imageLoadingActive = true;

    console.log(`🖼️ Starting progressive image loading for modal with ${maxWorkers} workers for ${imageQueue.length} remaining genres (${genres.length - imageQueue.length} already cached)`);

    // If all images are already cached, mark as complete
    if (imageQueue.length === 0) {
        console.log('✅ All images already cached, marking as complete');
        genreBrowserCache.imagesLoaded = true;
        genreBrowserCache.imageLoadingActive = false;
        return;
    }

    // Function to process a single image
    async function processImage(genre) {
        try {
            // Fetch individual genre image from backend
            const response = await fetch(`/api/beatport/genre-image/${genre.slug}/${genre.id}`);

            if (response.ok) {
                const data = await response.json();

                if (data.success && data.image_url) {
                    // Cache the image URL in the genre object
                    genre.imageUrl = data.image_url;

                    // Find the genre card in the modal
                    const genreCard = document.querySelector(
                        `.genre-browser-card[data-genre-slug="${genre.slug}"][data-genre-id="${genre.id}"]`
                    );

                    if (genreCard) {
                        const imageElement = genreCard.querySelector('.genre-browser-card-image');
                        if (imageElement) {
                            // Replace the fallback emoji with the actual image
                            imageElement.innerHTML = `<img src="${data.image_url}" alt="${genre.name}" loading="lazy" style="width: 100%; height: 100%; object-fit: cover;">`;
                            genreCard.classList.remove('genre-browser-card-fallback');

                            console.log(`✅ Loaded and cached image for ${genre.name} in modal`);
                        }
                    }
                }
            }

            imagesLoaded++;
            console.log(`📷 Progress: ${imagesLoaded}/${genres.length} images loaded for modal`);

        } catch (error) {
            console.log(`⚠️ Could not load image for ${genre.name} in modal: ${error.message}`);
            imagesLoaded++;
        }
    }

    // Worker function to process images from the queue
    async function worker() {
        while (imageQueue.length > 0 && genreBrowserCache.imageLoadingActive) {
            const genre = imageQueue.shift();
            if (genre) {
                await processImage(genre);
                // Small delay to prevent overwhelming the server
                await new Promise(resolve => setTimeout(resolve, 100));
            }

            // Check if we should pause
            if (!genreBrowserCache.imageLoadingActive) {
                console.log('⏸️ Worker paused - modal closed');
                break;
            }
        }
    }

    // Start the workers
    const workers = [];
    for (let i = 0; i < maxWorkers; i++) {
        workers.push(worker());
    }

    // Wait for all workers to complete
    await Promise.all(workers);

    // Check if loading was completed or paused
    if (genreBrowserCache.imageLoadingActive) {
        // Completed successfully
        genreBrowserCache.imagesLoaded = true;
        genreBrowserCache.imageLoadingActive = false;
        console.log(`🎉 Completed loading all genre images for modal (${imagesLoaded}/${genres.length})`);
        console.log(`💾 Marked images as loaded in cache`);
    } else {
        // Was paused
        console.log(`⏸️ Image loading paused (${imagesLoaded}/${genres.length} completed)`);
        console.log(`💾 Partial progress saved in cache`);
    }
}

function filterGenreBrowserCards(searchTerm) {
    const genreCards = document.querySelectorAll('.genre-browser-card');
    const searchLower = searchTerm.toLowerCase();

    genreCards.forEach(card => {
        const genreName = card.dataset.genreName?.toLowerCase() || '';
        const shouldShow = genreName.includes(searchLower);

        card.style.display = shouldShow ? 'block' : 'none';
    });

    console.log(`🔍 Filtered genre cards with search term: "${searchTerm}"`);
}

// === GENRE BROWSER CARD CLICK HANDLERS ===

function addGenreBrowserCardClickListeners() {
    const genreCards = document.querySelectorAll('.genre-browser-card');
    genreCards.forEach(card => {
        card.addEventListener('click', () => {
            const genreSlug = card.dataset.genreSlug;
            const genreId = card.dataset.genreId;
            const genreName = card.dataset.genreName;

            console.log(`🎵 Genre card clicked: ${genreName} (${genreSlug})`);
            handleGenreBrowserCardClick(genreSlug, genreId, genreName);
        });
    });

    console.log(`🔗 Added click listeners to ${genreCards.length} genre browser cards`);
}

async function handleGenreBrowserCardClick(genreSlug, genreId, genreName) {
    console.log(`🎠 Loading hero slider for ${genreName}...`);

    try {
        // Show the genre page view
        showGenrePageView(genreSlug, genreId, genreName);

        // Load the hero slider data
        // Load hero slider, Top 10 lists, and Top 10 releases in parallel
        await Promise.all([
            loadGenreHeroSlider(genreSlug, genreId, genreName),
            loadGenreTop10Lists(genreSlug, genreId, genreName),
            loadGenreTop10Releases(genreSlug, genreId, genreName)
        ]);

    } catch (error) {
        console.error(`❌ Error loading genre page for ${genreName}:`, error);
        showToast(`Error loading ${genreName}: ${error.message}`, 'error');

        // Return to genre list on error
        showGenreListView();
    }
}

function showGenrePageView(genreSlug, genreId, genreName) {
    console.log(`🎯 Showing genre page view for ${genreName}`);

    // CRITICAL: Stop all other slider auto-play to prevent conflicts
    if (typeof beatportRebuildSliderState !== 'undefined' && beatportRebuildSliderState.autoPlayInterval) {
        clearInterval(beatportRebuildSliderState.autoPlayInterval);
        console.log('🛑 Stopped main slider auto-play to prevent conflicts');
    }

    const modal = document.getElementById('genre-browser-modal');
    if (!modal) return;

    // Hide genre list elements
    const searchSection = modal.querySelector('.genre-browser-search-section');
    const genresSection = modal.querySelector('.genre-browser-genres-section');

    if (searchSection) searchSection.style.display = 'none';
    if (genresSection) genresSection.style.display = 'none';

    // Create or show genre page content
    let genrePageContent = modal.querySelector('.genre-page-content');
    if (!genrePageContent) {
        genrePageContent = document.createElement('div');
        genrePageContent.className = 'genre-page-content';
        genrePageContent.innerHTML = `
            <div class="genre-page-header">
                <button class="genre-back-button" id="genre-back-button">
                    <span class="back-icon">←</span> Back to Genres
                </button>
                <h2 class="genre-page-title"></h2>
            </div>
            <div class="genre-hero-slider-container" id="genre-hero-slider-container">
                <div class="genre-loading-container">
                    <div class="genre-loading-spinner"></div>
                    <p class="genre-loading-text">🎠 Loading hero releases...</p>
                </div>
            </div>
            <div class="genre-nav-buttons-section">
                <div class="genre-nav-buttons-container">
                    <button class="beatport-nav-button" id="genre-top100-btn">
                        <span class="beatport-nav-icon top100-icon"></span>
                        <span class="beatport-nav-text">Beatport Top 100</span>
                    </button>
                </div>
            </div>
            <div class="genre-top10-lists-container" id="genre-top10-lists-container">
                <div class="genre-top10-loading-container">
                    <div class="genre-loading-spinner"></div>
                    <p class="genre-loading-text">🎵 Loading Top 10 lists...</p>
                </div>
            </div>
            <div class="genre-top10-releases-container" id="genre-top10-releases-container">
                <div class="genre-top10-releases-loading-container">
                    <div class="genre-loading-spinner"></div>
                    <p class="genre-loading-text">💿 Loading Top 10 releases...</p>
                </div>
            </div>
        `;

        modal.querySelector('.genre-browser-modal-content').appendChild(genrePageContent);

        // Add back button listener
        const backButton = genrePageContent.querySelector('#genre-back-button');
        if (backButton) {
            backButton.addEventListener('click', showGenreListView);
        }

        // Add genre top 100 button listener
        const genreTop100Button = genrePageContent.querySelector('#genre-top100-btn');
        if (genreTop100Button) {
            genreTop100Button.addEventListener('click', () => {
                handleGenreTop100Click(genreSlug, genreId, genreName);
            });
        }
    }

    // Update title and show genre page
    const titleElement = genrePageContent.querySelector('.genre-page-title');
    if (titleElement) titleElement.textContent = genreName;

    genrePageContent.style.display = 'block';

    // Store current genre info for potential back navigation
    genrePageContent.dataset.genreSlug = genreSlug;
    genrePageContent.dataset.genreId = genreId;
    genrePageContent.dataset.genreName = genreName;
}

function showGenreListView() {
    console.log(`🔙 Returning to genre list view`);

    // Clean up genre hero slider
    if (window.genreHeroSliderState && window.genreHeroSliderState.autoPlayInterval) {
        clearInterval(window.genreHeroSliderState.autoPlayInterval);
        console.log('🧹 Cleaned up genre hero slider auto-play');
    }

    // CRITICAL: Restart main slider auto-play
    if (typeof beatportRebuildSliderState !== 'undefined' && !beatportRebuildSliderState.autoPlayInterval) {
        if (typeof startBeatportRebuildSliderAutoPlay === 'function') {
            startBeatportRebuildSliderAutoPlay();
            console.log('🔄 Restarted main slider auto-play');
        }
    }

    const modal = document.getElementById('genre-browser-modal');
    if (!modal) return;

    // Show genre list elements
    const searchSection = modal.querySelector('.genre-browser-search-section');
    const genresSection = modal.querySelector('.genre-browser-genres-section');
    const genrePageContent = modal.querySelector('.genre-page-content');

    if (searchSection) searchSection.style.display = 'block';
    if (genresSection) genresSection.style.display = 'block';
    if (genrePageContent) genrePageContent.style.display = 'none';
}

async function loadGenreHeroSlider(genreSlug, genreId, genreName) {
    console.log(`🎠 Loading hero slider data for ${genreName}...`);

    const container = document.getElementById('genre-hero-slider-container');
    if (!container) return;

    try {
        // Show loading state
        container.innerHTML = `
            <div class="genre-loading-container">
                <div class="genre-loading-spinner"></div>
                <p class="genre-loading-text">🎠 Loading ${genreName} hero releases...</p>
            </div>
        `;

        // Fetch hero slider data from API
        const response = await fetch(`/api/beatport/genre/${genreSlug}/${genreId}/hero`);
        if (!response.ok) {
            throw new Error(`API returned ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (!data.success || !data.releases || data.releases.length === 0) {
            throw new Error(data.message || 'No hero releases found');
        }

        console.log(`✅ Loaded ${data.count} hero releases for ${genreName} (cached: ${data.cached})`);

        // Create hero slider HTML
        const heroSliderHTML = createGenreHeroSliderHTML(data.releases, genreName);
        container.innerHTML = heroSliderHTML;

        // Add click handlers to individual releases (for future download functionality)
        addGenreHeroReleaseClickHandlers(data.releases);

        showToast(`Loaded ${data.count} ${genreName} releases`, 'success');

    } catch (error) {
        console.error(`❌ Error loading hero slider for ${genreName}:`, error);

        container.innerHTML = `
            <div class="genre-error-container">
                <p class="genre-error-text">❌ Failed to load ${genreName} releases</p>
                <p class="genre-error-details">${error.message}</p>
                <button class="genre-retry-button" onclick="loadGenreHeroSlider('${genreSlug}', '${genreId}', '${genreName}')">
                    🔄 Retry
                </button>
            </div>
        `;

        throw error;
    }
}

function createGenreHeroSliderHTML(releases, genreName) {
    const slidesHTML = releases.map((release, index) => {
        // Convert relative URL to absolute URL
        const absoluteUrl = release.url.startsWith('http')
            ? release.url
            : `https://www.beatport.com${release.url}`;

        return `
        <div class="beatport-rebuild-slide ${index === 0 ? 'active' : ''}"
             data-slide="${index}"
             data-url="${absoluteUrl}"
             data-image="${release.image_url}"
             style="--slide-bg-image: url('${release.image_url}')">
            <div class="beatport-rebuild-slide-background">
                <div class="beatport-rebuild-slide-gradient"></div>
            </div>
            <div class="beatport-rebuild-slide-content">
                <div class="beatport-rebuild-track-info">
                    <h2 class="beatport-rebuild-track-title">${release.title}</h2>
                    <p class="beatport-rebuild-artist-name">${release.artists_string}</p>
                    <p class="beatport-rebuild-album-name">${release.label || genreName + ' Hero Release'}</p>
                </div>
            </div>
        </div>`;
    }).join('');

    const indicatorsHTML = releases.map((_, index) => `
        <button class="beatport-rebuild-indicator ${index === 0 ? 'active' : ''}" data-slide="${index}"></button>
    `).join('');

    return `
        <div class="beatport-rebuild-slider-container">
            <div class="beatport-rebuild-slider" id="genre-hero-slider">
                <div class="beatport-rebuild-slider-track" id="genre-hero-slider-track">
                    ${slidesHTML}
                </div>

                <!-- Slider Navigation -->
                <div class="beatport-rebuild-slider-nav">
                    <button class="beatport-rebuild-nav-btn beatport-rebuild-prev-btn" id="genre-hero-prev-btn">‹</button>
                    <button class="beatport-rebuild-nav-btn beatport-rebuild-next-btn" id="genre-hero-next-btn">›</button>
                </div>

                <!-- Slider Indicators -->
                <div class="beatport-rebuild-slider-indicators">
                    ${indicatorsHTML}
                </div>
            </div>
        </div>
    `;
}

function addGenreHeroReleaseClickHandlers(releases) {
    // Clear any existing intervals first
    if (window.genreHeroSliderState && window.genreHeroSliderState.autoPlayInterval) {
        clearInterval(window.genreHeroSliderState.autoPlayInterval);
        console.log('🧹 Cleared previous genre hero auto-play interval');
    }

    // CRITICAL: Clear ALL possible conflicting intervals
    if (typeof beatportRebuildSliderState !== 'undefined' && beatportRebuildSliderState.autoPlayInterval) {
        clearInterval(beatportRebuildSliderState.autoPlayInterval);
        console.log('🛑 Cleared main rebuild slider auto-play interval');
    }

    // Initialize global slider state for genre hero slider
    window.genreHeroSliderState = {
        currentSlide: 0,
        totalSlides: releases.length,
        autoPlayInterval: null
    };

    console.log(`🎠 Initializing genre hero slider with ${releases.length} slides`);

    // Set up navigation button handlers
    const prevBtn = document.getElementById('genre-hero-prev-btn');
    const nextBtn = document.getElementById('genre-hero-next-btn');

    if (prevBtn) {
        prevBtn.addEventListener('click', () => {
            window.genreHeroSliderState.currentSlide = window.genreHeroSliderState.currentSlide > 0
                ? window.genreHeroSliderState.currentSlide - 1
                : window.genreHeroSliderState.totalSlides - 1;
            updateGenreHeroSlide(window.genreHeroSliderState.currentSlide);
            console.log(`⬅️ Previous: Moving to slide ${window.genreHeroSliderState.currentSlide}`);
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener('click', () => {
            window.genreHeroSliderState.currentSlide = (window.genreHeroSliderState.currentSlide + 1) % window.genreHeroSliderState.totalSlides;
            updateGenreHeroSlide(window.genreHeroSliderState.currentSlide);
            console.log(`➡️ Next: Moving to slide ${window.genreHeroSliderState.currentSlide}`);
        });
    }

    // Set up indicator handlers
    const indicators = document.querySelectorAll('#genre-hero-slider .beatport-rebuild-indicator');
    indicators.forEach((indicator, index) => {
        indicator.addEventListener('click', () => {
            window.genreHeroSliderState.currentSlide = index;
            updateGenreHeroSlide(index);
            console.log(`🎯 Indicator: Jumping to slide ${index}`);
        });
    });

    // Set up individual slide click handlers (like the main hero slider)
    const slides = document.querySelectorAll('#genre-hero-slider .beatport-rebuild-slide[data-url]');
    console.log(`🔗 Found ${slides.length} slides to set up click handlers for`);

    slides.forEach((slide, index) => {
        const releaseUrl = slide.getAttribute('data-url');
        if (releaseUrl && releaseUrl !== '#' && releaseUrl !== '') {
            const release = releases[index];
            if (release) {
                // Ensure we use the absolute URL and match the expected data structure
                const releaseData = {
                    url: releaseUrl, // This is already the absolute URL from data-url
                    title: release.title || 'Unknown Title',
                    artist: release.artists_string || 'Unknown Artist', // handleBeatportReleaseCardClick expects 'artist'
                    label: release.label || 'Unknown Label',
                    image_url: release.image_url || '',
                    // Include all original data for completeness
                    artists_string: release.artists_string,
                    type: release.type,
                    source: release.source,
                    badges: release.badges || []
                };

                slide.addEventListener('click', async (event) => {
                    // Prevent navigation button clicks from triggering this
                    if (event.target.closest('.beatport-rebuild-nav-btn') ||
                        event.target.closest('.beatport-rebuild-indicator')) {
                        return;
                    }

                    console.log(`🎵 Genre hero slide clicked: ${releaseData.title} by ${releaseData.artist}`);

                    // Use the exact same functionality as the main hero slider
                    await handleBeatportReleaseCardClick(slide, releaseData);
                });

                slide.style.cursor = 'pointer';
            }
        }
    });

    // Ensure first slide is active BEFORE starting auto-play
    updateGenreHeroSlide(0);

    // Delay auto-play start to let DOM settle
    setTimeout(() => {
        startGenreHeroSliderAutoPlay();
    }, 100);

    // Pause on hover
    const sliderContainer = document.querySelector('#genre-hero-slider');
    if (sliderContainer) {
        sliderContainer.addEventListener('mouseenter', () => {
            if (window.genreHeroSliderState.autoPlayInterval) {
                clearInterval(window.genreHeroSliderState.autoPlayInterval);
                console.log('⏸️ Paused auto-play on hover');
            }
        });

        sliderContainer.addEventListener('mouseleave', () => {
            // Delay restart to avoid rapid state changes
            setTimeout(() => {
                startGenreHeroSliderAutoPlay();
            }, 100);
            console.log('▶️ Resumed auto-play after hover');
        });
    }

    console.log(`✅ Set up slider functionality for ${releases.length} genre hero releases`);
}

function updateGenreHeroSlide(slideIndex) {
    if (!window.genreHeroSliderState) {
        console.error('❌ Genre hero slider state not initialized');
        return;
    }

    // First update the state
    window.genreHeroSliderState.currentSlide = slideIndex;

    // Update slide visibility - use the exact same logic as main slider
    const slides = document.querySelectorAll('#genre-hero-slider .beatport-rebuild-slide');
    console.log(`🔄 Updating slide to index ${slideIndex}, found ${slides.length} slides`);

    if (slideIndex >= slides.length || slideIndex < 0) {
        console.error(`❌ Invalid slide index ${slideIndex}, max is ${slides.length - 1}`);
        return;
    }

    slides.forEach((slide, index) => {
        slide.classList.remove('active', 'prev', 'next');

        if (index === slideIndex) {
            slide.classList.add('active');
            console.log(`✅ Activated slide ${index}: ${slide.getAttribute('data-slide')} - Title: ${slide.querySelector('.beatport-rebuild-track-title')?.textContent}`);
        } else if (index < slideIndex) {
            slide.classList.add('prev');
        } else {
            slide.classList.add('next');
        }
    });

    // Update indicators
    const indicators = document.querySelectorAll('#genre-hero-slider .beatport-rebuild-indicator');
    indicators.forEach((indicator, index) => {
        indicator.classList.toggle('active', index === slideIndex);
    });

    console.log(`Genre slide updated to: ${window.genreHeroSliderState.currentSlide}`);
}

function startGenreHeroSliderAutoPlay() {
    if (!window.genreHeroSliderState) {
        console.error('❌ Cannot start auto-play: Genre hero slider state not initialized');
        return;
    }

    // Clear any existing intervals first
    if (window.genreHeroSliderState.autoPlayInterval) {
        clearInterval(window.genreHeroSliderState.autoPlayInterval);
        console.log('🧹 Cleared existing auto-play interval');
    }

    window.genreHeroSliderState.autoPlayInterval = setInterval(() => {
        if (!window.genreHeroSliderState) {
            console.error('❌ Auto-play fired but state is gone, clearing interval');
            clearInterval(window.genreHeroSliderState.autoPlayInterval);
            return;
        }

        const currentSlide = window.genreHeroSliderState.currentSlide;
        const totalSlides = window.genreHeroSliderState.totalSlides;
        const nextSlide = (currentSlide + 1) % totalSlides;

        console.log(`⏰ Auto-play: Current=${currentSlide}, Total=${totalSlides}, Next=${nextSlide}`);

        // Validate the next slide index
        if (nextSlide >= 0 && nextSlide < totalSlides) {
            updateGenreHeroSlide(nextSlide);
        } else {
            console.error(`❌ Invalid nextSlide calculated: ${nextSlide}, resetting to 0`);
            updateGenreHeroSlide(0);
        }
    }, 5000); // 5 second intervals like the main slider

    console.log(`▶️ Started auto-play for genre hero slider (${window.genreHeroSliderState.totalSlides} slides)`);
}

/**
 * Load Top 10 lists for a specific genre (Beatport + Hype)
 */
async function loadGenreTop10Lists(genreSlug, genreId, genreName) {
    console.log(`🎵 Loading Top 10 lists for ${genreName}...`);

    const container = document.getElementById('genre-top10-lists-container');
    if (!container) {
        console.error('❌ Genre Top 10 lists container not found');
        return;
    }

    try {
        const response = await fetch(`/api/beatport/genre/${genreSlug}/${genreId}/top-10-lists`);
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || 'Failed to load Top 10 lists');
        }

        console.log(`✅ Loaded ${data.beatport_count} Beatport + ${data.hype_count} Hype Top 10 tracks for ${genreName}`);

        // Generate HTML using exact same structure as main page (but unique IDs)
        const top10ListsHTML = createGenreTop10ListsHTML(data, genreName);
        container.innerHTML = top10ListsHTML;

        // Add container-level click handlers exactly like main page
        addGenreTop10ClickHandlers();

        console.log(`✅ Successfully populated genre Top 10 lists for ${genreName}`);

    } catch (error) {
        console.error(`❌ Error loading Top 10 lists for ${genreName}:`, error);

        // Show error state
        container.innerHTML = `
            <div class="genre-top10-error">
                <h3>❌ Error Loading Top 10 Lists</h3>
                <p>Could not load Top 10 tracks for ${genreName}</p>
                <p class="error-detail">${error.message}</p>
            </div>
        `;
    }
}

/**
 * Create HTML for genre Top 10 lists (exact structure as main page, unique IDs)
 */
function createGenreTop10ListsHTML(data, genreName) {
    const { beatport_top10, hype_top10, has_hype_section } = data;

    // Use exact same structure as main page but with genre-specific IDs
    let html = `
        <div class="beatport-top10-section">
            <div class="beatport-top10-header">
                <h2 class="beatport-top10-title">🏆 ${genreName} Top 10 Lists</h2>
                <p class="beatport-top10-subtitle">Current trending ${genreName.toLowerCase()} tracks</p>
            </div>

            <div class="beatport-top10-container"${!has_hype_section ? ' style="grid-template-columns: 1fr; justify-items: center; max-width: 700px;"' : ''}>
                <!-- Beatport Top 10 List (same classes, unique ID) -->
                <div class="beatport-top10-list" id="genre-beatport-top10-list">
                    <div class="beatport-top10-list-header">
                        <h3 class="beatport-top10-list-title">🎵 Beatport Top 10</h3>
                        <p class="beatport-top10-list-subtitle">Most popular ${genreName.toLowerCase()} tracks</p>
                    </div>
                    <div class="beatport-top10-tracks">
    `;

    // Add Beatport Top 10 tracks (same classes as main page)
    beatport_top10.forEach((track, index) => {
        const cleanTitle = cleanTrackText(track.title || 'Unknown Title');
        const cleanArtist = cleanTrackText(track.artist || 'Unknown Artist');
        const cleanLabel = cleanTrackText(track.label || 'Unknown Label');

        html += `
            <div class="beatport-top10-card" data-url="${track.url || '#'}">
                <div class="beatport-top10-card-rank">${track.rank || index + 1}</div>
                <div class="beatport-top10-card-artwork">
                    ${track.artwork_url ?
                `<img src="${track.artwork_url}" alt="${cleanTitle}" loading="lazy">` :
                '<div class="beatport-top10-card-placeholder">🎵</div>'
            }
                </div>
                <div class="beatport-top10-card-info">
                    <h4 class="beatport-top10-card-title">${cleanTitle}</h4>
                    <p class="beatport-top10-card-artist">${cleanArtist}</p>
                    <p class="beatport-top10-card-label">${cleanLabel}</p>
                </div>
            </div>
        `;
    });

    html += `
                    </div>
                </div>
    `;

    // Add Hype Top 10 section (same classes, unique ID)
    if (has_hype_section && hype_top10.length > 0) {
        html += `
                <!-- Hype Top 10 List (same classes, unique ID) -->
                <div class="beatport-hype10-list" id="genre-beatport-hype10-list">
                    <div class="beatport-hype10-list-header">
                        <h3 class="beatport-hype10-list-title">🔥 Hype Top 10</h3>
                        <p class="beatport-hype10-list-subtitle">Editor's trending ${genreName.toLowerCase()} picks</p>
                    </div>
                    <div class="beatport-hype10-tracks">
        `;

        // Add Hype Top 10 tracks (same classes as main page)
        hype_top10.forEach((track, index) => {
            const cleanTitle = cleanTrackText(track.title || 'Unknown Title');
            const cleanArtist = cleanTrackText(track.artist || 'Unknown Artist');
            const cleanLabel = cleanTrackText(track.label || 'Unknown Label');

            html += `
                <div class="beatport-hype10-card" data-url="${track.url || '#'}">
                    <div class="beatport-hype10-card-rank">${track.rank || index + 1}</div>
                    <div class="beatport-hype10-card-artwork">
                        ${track.artwork_url ?
                    `<img src="${track.artwork_url}" alt="${cleanTitle}" loading="lazy">` :
                    '<div class="beatport-hype10-card-placeholder">🔥</div>'
                }
                    </div>
                    <div class="beatport-hype10-card-info">
                        <h4 class="beatport-hype10-card-title">${cleanTitle}</h4>
                        <p class="beatport-hype10-card-artist">${cleanArtist}</p>
                        <p class="beatport-hype10-card-label">${cleanLabel}</p>
                    </div>
                </div>
            `;
        });

        html += `
                    </div>
                </div>
        `;
    }
    // No else block - completely hide hype section when no hype tracks available

    html += `
            </div>
        </div>
    `;

    return html;
}

/**
 * Add container-level click handlers for genre Top 10 lists (exact parity with main page)
 */
function addGenreTop10ClickHandlers() {
    console.log('🔗 Adding container-level click handlers for genre Top 10 lists...');

    // Add container-level click handler for Beatport Top 10 (exact match to main page)
    const beatportContainer = document.getElementById('genre-beatport-top10-list');
    if (beatportContainer) {
        beatportContainer.addEventListener('click', () => {
            console.log('🎵 Genre Beatport Top 10 container clicked');
            handleGenreBeatportTop10Click();
        });
        console.log('✅ Added Beatport Top 10 container click handler');
    }

    // Add container-level click handler for Hype Top 10 (exact match to main page)
    const hypeContainer = document.getElementById('genre-beatport-hype10-list');
    if (hypeContainer) {
        hypeContainer.addEventListener('click', () => {
            console.log('🔥 Genre Hype Top 10 container clicked');
            handleGenreHypeTop10Click();
        });
        console.log('✅ Added Hype Top 10 container click handler');
    }

    console.log(`✅ Set up container-level click handlers for genre Top 10 lists`);
}

/**
 * Handle genre Beatport Top 10 container click (exact parity with main page)
 */
async function handleGenreBeatportTop10Click() {
    console.log('🎵 Handling Genre Beatport Top 10 click');

    // Get the actual genre name from the page title
    const genreName = document.querySelector('.genre-page-title')?.textContent?.trim() || 'Genre';

    // Use actual genre name in chart title
    await handleGenreChartClick('genre_beatport_top10', `${genreName} Beatport Top 10`, 'genre_beatport_top10');
}

/**
 * Handle genre Hype Top 10 container click (exact parity with main page)
 */
async function handleGenreHypeTop10Click() {
    console.log('🔥 Handling Genre Hype Top 10 click');

    // Get the actual genre name from the page title
    const genreName = document.querySelector('.genre-page-title')?.textContent?.trim() || 'Genre';

    // Use actual genre name in chart title
    await handleGenreChartClick('genre_hype_top10', `${genreName} Hype Top 10`, 'genre_hype_top10');
}

/**
 * Handle genre chart click (based on main page handleRebuildChartClick)
 */
async function handleGenreChartClick(trackDataKey, chartName, chartType) {
    if (_beatportModalOpening) return;
    _beatportModalOpening = true;
    setTimeout(() => { _beatportModalOpening = false; }, 2000);

    try {
        // Extract track data from DOM cards
        const trackData = await getGenrePageTrackData(trackDataKey);
        if (!trackData || trackData.length === 0) {
            throw new Error(`No track data found for ${chartName}`);
        }

        console.log(`✅ Got ${trackData.length} tracks from ${chartName}, enriching one-by-one...`);
        showLoadingOverlay(`Fetching track metadata... (0/${trackData.length})`);

        const enrichedTracks = await _enrichTracksWithProgress(trackData, chartName);

        console.log(`✅ Enriched ${enrichedTracks.length} tracks`);
        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(enrichedTracks, chartName, null);

    } catch (error) {
        hideLoadingOverlay();
        console.error(`❌ Error handling ${chartName} click:`, error);
        showToast(`Error loading ${chartName}: ${error.message}`, 'error');
    }
}

/**
 * Extract track data from genre page DOM (based on main page getRebuildPageTrackData)
 */
async function getGenrePageTrackData(trackDataKey) {
    console.log(`🔍 Extracting ${trackDataKey} data from genre page DOM`);

    let containerSelector, cardSelector;
    if (trackDataKey === 'genre_beatport_top10') {
        containerSelector = '#genre-beatport-top10-list';
        cardSelector = '.beatport-top10-card[data-url]';
    } else if (trackDataKey === 'genre_hype_top10') {
        containerSelector = '#genre-beatport-hype10-list';
        cardSelector = '.beatport-hype10-card[data-url]';
    } else {
        throw new Error(`Unknown track data key: ${trackDataKey}`);
    }

    const container = document.querySelector(containerSelector);
    if (!container) {
        throw new Error(`Container ${containerSelector} not found`);
    }

    const trackCards = container.querySelectorAll(cardSelector);
    if (trackCards.length === 0) {
        throw new Error(`No track cards found in ${containerSelector}`);
    }

    // Extract track data from DOM cards (exact same pattern as main page)
    const tracks = Array.from(trackCards).map(card => {
        const title = card.querySelector('.beatport-top10-card-title, .beatport-hype10-card-title')?.textContent?.trim() || 'Unknown Title';
        const artist = card.querySelector('.beatport-top10-card-artist, .beatport-hype10-card-artist')?.textContent?.trim() || 'Unknown Artist';
        const label = card.querySelector('.beatport-top10-card-label, .beatport-hype10-card-label')?.textContent?.trim() || 'Unknown Label';
        const url = card.getAttribute('data-url') || '';
        const rank = card.querySelector('.beatport-top10-card-rank, .beatport-hype10-card-rank')?.textContent?.trim() || '';

        return {
            title: title,
            artist: artist,
            label: label,
            url: url,
            rank: rank
        };
    });

    console.log(`📋 Extracted ${tracks.length} tracks from ${containerSelector}`);
    return tracks;
}

/**
 * Handle genre-specific Top 100 button click - create discovery process for genre top 100 tracks
 */
async function handleGenreTop100Click(genreSlug, genreId, genreName) {
    if (_beatportModalOpening) return;
    _beatportModalOpening = true;
    setTimeout(() => { _beatportModalOpening = false; }, 2000);

    console.log(`💯 Genre Top 100 button clicked for ${genreName}`);

    const chartName = `${genreName} Top 100`;

    try {
        showLoadingOverlay(`Scraping ${chartName}...`);

        // Use the genre tracks endpoint without enrichment
        const response = await fetch(`/api/beatport/genre/${genreSlug}/${genreId}/tracks?enrich=false`, { method: 'GET' });
        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error(`No tracks found in ${chartName}`);
        }

        console.log(`✅ Fetched ${data.tracks.length} tracks, enriching one-by-one...`);

        // Enrich one-by-one with live progress
        const enrichedTracks = await _enrichTracksWithProgress(data.tracks, chartName);

        hideLoadingOverlay();
        openBeatportChartAsDownloadModal(enrichedTracks, chartName, null);

    } catch (error) {
        console.error(`❌ Error handling ${chartName} click:`, error);
        hideLoadingOverlay();
        showToast(`Error loading ${chartName}: ${error.message}`, 'error');
    }
}

/**
 * Load Top 10 releases for a specific genre
 */
async function loadGenreTop10Releases(genreSlug, genreId, genreName) {
    console.log(`💿 Loading Top 10 releases for ${genreName}...`);

    const container = document.getElementById('genre-top10-releases-container');
    if (!container) {
        console.error('❌ Genre Top 10 releases container not found');
        return;
    }

    try {
        const response = await fetch(`/api/beatport/genre/${genreSlug}/${genreId}/top-10-releases`);
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || 'Failed to load Top 10 releases');
        }

        console.log(`💿 Loaded ${data.releases.length} Top 10 releases for ${genreName}`);
        createGenreTop10ReleasesHTML(data.releases, genreName);

    } catch (error) {
        console.error(`❌ Error loading Top 10 releases for ${genreName}:`, error);
        showGenreTop10ReleasesError(error.message || 'Failed to load Top 10 releases');
    }
}

/**
 * Create HTML for genre Top 10 releases section (exact parity with main page)
 */
function createGenreTop10ReleasesHTML(releases, genreName) {
    const container = document.getElementById('genre-top10-releases-container');
    if (!container || !releases || releases.length === 0) return;

    // Create section with unique ID but exact same structure as main page
    const sectionHtml = `
        <div class="beatport-releases-top10-section">
            <div class="beatport-releases-top10-header">
                <h2 class="beatport-releases-top10-title">💿 Top 10 ${genreName} Releases</h2>
                <p class="beatport-releases-top10-subtitle">Most popular albums and EPs for ${genreName}</p>
            </div>
            <div class="beatport-releases-top10-container">
                <div class="beatport-releases-top10-list" id="genre-beatport-releases-top10-list">
                    ${createGenreTop10ReleasesCardsHTML(releases)}
                </div>
            </div>
        </div>
    `;

    container.innerHTML = sectionHtml;

    // Add background images and click handlers
    addGenreTop10ReleasesInteractivity(releases);
}

/**
 * Create release cards HTML for genre Top 10 releases
 */
function createGenreTop10ReleasesCardsHTML(releases) {
    let cardsHtml = '<div class="beatport-releases-top10-tracks">';

    releases.forEach((release, index) => {
        cardsHtml += `
            <div class="beatport-releases-top10-card" data-url="${release.url || '#'}" data-bg-image="${release.image_url || ''}">
                <div class="beatport-releases-top10-card-rank">${release.rank || index + 1}</div>
                <div class="beatport-releases-top10-card-artwork">
                    ${release.image_url ?
                `<img src="${release.image_url}" alt="${release.title}" loading="lazy">` :
                '<div class="beatport-releases-top10-card-placeholder">💿</div>'
            }
                </div>
                <div class="beatport-releases-top10-card-info">
                    <h4 class="beatport-releases-top10-card-title">${release.title || 'Unknown Title'}</h4>
                    <p class="beatport-releases-top10-card-artist">${release.artist || 'Unknown Artist'}</p>
                    <p class="beatport-releases-top10-card-label">${release.label || 'Unknown Label'}</p>
                </div>
            </div>
        `;
    });

    cardsHtml += '</div>';
    return cardsHtml;
}

/**
 * Add interactivity to genre Top 10 releases cards
 */
function addGenreTop10ReleasesInteractivity(releases) {
    const container = document.getElementById('genre-beatport-releases-top10-list');
    if (!container) return;

    // Set background images for cards
    const cards = container.querySelectorAll('.beatport-releases-top10-card[data-bg-image]');
    cards.forEach(card => {
        const bgImage = card.getAttribute('data-bg-image');
        if (bgImage) {
            // Transform image URL from 95x95 to 500x500 for higher quality background
            const highResImage = bgImage.replace('/image_size/95x95/', '/image_size/500x500/');
            card.style.backgroundImage = `linear-gradient(rgba(0,0,0,0.7), rgba(0,0,0,0.8)), url('${highResImage}')`;
            card.style.backgroundSize = 'cover';
            card.style.backgroundPosition = 'center';
        }
    });

    // Add click handlers for individual release discovery (exact same pattern as main page)
    const releaseCards = container.querySelectorAll('.beatport-releases-top10-card[data-url]');
    releaseCards.forEach((card, index) => {
        card.addEventListener('click', () => handleGenreReleaseCardClick(card, releases[index]));
        card.style.cursor = 'pointer';
    });
}

/**
 * Handle click on individual genre Top 10 Release card (exact parity with main page)
 */
async function handleGenreReleaseCardClick(cardElement, release) {
    if (_beatportModalOpening) return;
    _beatportModalOpening = true;

    console.log(`💿 Individual genre release card clicked: ${release.title} by ${release.artist}`);

    if (!release.url || release.url === '#') {
        _beatportModalOpening = false;
        showToast('No release URL available', 'error');
        return;
    }

    try {
        showToast(`Loading ${release.title}...`, 'info');
        showLoadingOverlay(`Getting tracks from ${release.title}...`);

        // Fetch structured release metadata for direct download modal
        console.log(`🎵 Fetching release metadata: ${release.url}`);
        const response = await fetch('/api/beatport/release-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ release_url: release.url })
        });

        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            throw new Error(data.error || 'No tracks found in this release');
        }

        console.log(`✅ Got ${data.tracks.length} tracks from ${data.album.name}`);

        const formattedTracks = data.tracks.map(track => ({
            ...track,
            artists: track.artists.map(a => typeof a === 'object' ? a.name : a)
        }));

        const virtualPlaylistId = `beatport_release_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const playlistName = data.album.name;

        await openDownloadMissingModalForArtistAlbum(
            virtualPlaylistId,
            playlistName,
            formattedTracks,
            data.album,
            data.artist,
            false
        );

        hideLoadingOverlay();
        _beatportModalOpening = false;
        console.log(`✅ Opened download modal for ${playlistName}`);

    } catch (error) {
        console.error(`❌ Error handling release click for ${release.title}:`, error);
        hideLoadingOverlay();
        _beatportModalOpening = false;
        showToast(`Error loading ${release.title}: ${error.message}`, 'error');
    }
}

/**
 * Show error message for genre Top 10 releases
 */
function showGenreTop10ReleasesError(errorMessage) {
    const container = document.getElementById('genre-top10-releases-container');

    const errorHtml = `
        <div class="beatport-releases-top10-section">
            <div class="beatport-releases-top10-header">
                <h2 class="beatport-releases-top10-title">💿 Top 10 Releases</h2>
                <p class="beatport-releases-top10-subtitle">Error loading releases</p>
            </div>
            <div class="beatport-releases-top10-container">
                <div class="beatport-releases-top10-error">
                    <h3>❌ Error Loading Releases</h3>
                    <p>${errorMessage}</p>
                </div>
            </div>
        </div>
    `;

    if (container) container.innerHTML = errorHtml;
}

// Initialize the Genre Browser Modal when the page loads
document.addEventListener('DOMContentLoaded', () => {
    initializeGenreBrowserModal();
});

// ============ Plex Music Library Selection ============

async function loadPlexMusicLibraries() {
    try {
        const response = await fetch('/api/plex/music-libraries');
        const data = await response.json();

        if (data.success && data.libraries && data.libraries.length > 0) {
            const selector = document.getElementById('plex-music-library');
            const container = document.getElementById('plex-library-selector-container');

            // Clear existing options
            selector.innerHTML = '';

            // Add options for each library
            data.libraries.forEach(library => {
                const option = document.createElement('option');
                option.value = library.title;
                option.textContent = library.title;

                // Mark the currently selected library
                if (library.title === data.current || library.title === data.selected) {
                    option.selected = true;
                }

                selector.appendChild(option);
            });

            // Show the container
            container.style.display = 'block';
        } else {
            // Hide if no libraries found or not connected
            document.getElementById('plex-library-selector-container').style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading Plex music libraries:', error);
        document.getElementById('plex-library-selector-container').style.display = 'none';
    }
}

async function selectPlexLibrary() {
    const selector = document.getElementById('plex-music-library');
    const selectedLibrary = selector.value;

    if (!selectedLibrary) return;

    try {
        const response = await fetch('/api/plex/select-music-library', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                library_name: selectedLibrary
            })
        });

        const data = await response.json();

        if (data.success) {
            console.log(`Plex music library switched to: ${selectedLibrary}`);
        } else {
            console.error('Failed to switch library:', data.error);
            alert(`Failed to switch library: ${data.error}`);
        }
    } catch (error) {
        console.error('Error selecting Plex library:', error);
        alert('Error selecting library. Please try again.');
    }
}

// ============ Jellyfin User Selection ============

async function loadJellyfinUsers() {
    try {
        const response = await fetch('/api/jellyfin/users');
        const data = await response.json();

        if (data.success && data.users && data.users.length > 0) {
            const selector = document.getElementById('jellyfin-user');
            const container = document.getElementById('jellyfin-user-selector-container');

            // Clear existing options
            selector.innerHTML = '';

            // Add options for each user
            data.users.forEach(user => {
                const option = document.createElement('option');
                option.value = user.name;
                option.textContent = user.name;

                // Mark the currently selected user
                if (user.name === data.current || user.name === data.selected) {
                    option.selected = true;
                }

                selector.appendChild(option);
            });

            // Show the container
            container.style.display = 'block';
        } else {
            // Hide if no users found or not connected
            document.getElementById('jellyfin-user-selector-container').style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading Jellyfin users:', error);
        document.getElementById('jellyfin-user-selector-container').style.display = 'none';
    }
}

async function selectJellyfinUser() {
    const selector = document.getElementById('jellyfin-user');
    const selectedUser = selector.value;

    if (!selectedUser) return;

    try {
        const response = await fetch('/api/jellyfin/select-user', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                username: selectedUser
            })
        });

        const data = await response.json();

        if (data.success) {
            console.log(`Jellyfin user switched to: ${selectedUser}`);
            // Refresh library dropdown for the new user
            loadJellyfinMusicLibraries();
        } else {
            console.error('Failed to switch user:', data.error);
            alert(`Failed to switch user: ${data.error}`);
        }
    } catch (error) {
        console.error('Error selecting Jellyfin user:', error);
        alert('Error selecting user. Please try again.');
    }
}

// ============ Jellyfin Music Library Selection ============

async function loadJellyfinMusicLibraries() {
    try {
        const response = await fetch('/api/jellyfin/music-libraries');
        const data = await response.json();

        if (data.success && data.libraries && data.libraries.length > 0) {
            const selector = document.getElementById('jellyfin-music-library');
            const container = document.getElementById('jellyfin-library-selector-container');

            // Clear existing options
            selector.innerHTML = '';

            // Add options for each library
            data.libraries.forEach(library => {
                const option = document.createElement('option');
                option.value = library.title;
                option.textContent = library.title;

                // Mark the currently selected library
                if (library.title === data.current || library.title === data.selected) {
                    option.selected = true;
                }

                selector.appendChild(option);
            });

            // Show the container
            container.style.display = 'block';
        } else {
            // Hide if no libraries found or not connected
            document.getElementById('jellyfin-library-selector-container').style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading Jellyfin music libraries:', error);
        document.getElementById('jellyfin-library-selector-container').style.display = 'none';
    }
}

async function selectJellyfinLibrary() {
    const selector = document.getElementById('jellyfin-music-library');
    const selectedLibrary = selector.value;

    if (!selectedLibrary) return;

    try {
        const response = await fetch('/api/jellyfin/select-music-library', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                library_name: selectedLibrary
            })
        });

        const data = await response.json();

        if (data.success) {
            console.log(`Jellyfin music library switched to: ${selectedLibrary}`);
        } else {
            console.error('Failed to switch library:', data.error);
            alert(`Failed to switch library: ${data.error}`);
        }
    } catch (error) {
        console.error('Error selecting Jellyfin library:', error);
        alert('Error selecting library. Please try again.');
    }
}

// ============ Navidrome Music Folder Selection ============

async function loadNavidromeMusicFolders() {
    try {
        const response = await fetch('/api/navidrome/music-folders');
        const data = await response.json();

        if (data.success && data.folders && data.folders.length > 0) {
            const selector = document.getElementById('navidrome-music-folder');
            const container = document.getElementById('navidrome-folder-selector-container');

            selector.innerHTML = '<option value="">All Libraries</option>';

            data.folders.forEach(folder => {
                const option = document.createElement('option');
                option.value = folder.title;
                option.textContent = folder.title;

                if (folder.title === data.current || folder.title === data.selected) {
                    option.selected = true;
                }

                selector.appendChild(option);
            });

            container.style.display = 'block';
        } else {
            document.getElementById('navidrome-folder-selector-container').style.display = 'none';
        }
    } catch (error) {
        console.error('Error loading Navidrome music folders:', error);
        document.getElementById('navidrome-folder-selector-container').style.display = 'none';
    }
}

async function selectNavidromeMusicFolder() {
    const selector = document.getElementById('navidrome-music-folder');
    const selectedFolder = selector.value;

    try {
        const response = await fetch('/api/navidrome/select-music-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_name: selectedFolder })
        });

        const data = await response.json();

        if (data.success) {
            showToast(data.message, 'success');
        } else {
            console.error('Failed to set music folder:', data.error);
            showToast(`Failed to set music folder: ${data.error}`, 'error', 'set-media');
        }
    } catch (error) {
        console.error('Error selecting Navidrome music folder:', error);
        showToast('Error selecting music folder. Please try again.', 'error', 'set-media');
    }
}

// ============================================

