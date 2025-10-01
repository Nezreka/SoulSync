#!/usr/bin/env python3
"""
Unified Beatport Scraper - Reliable Artist & Track Name Extraction
Focused on extracting clean artist and track names for virtual playlists
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import re
from urllib.parse import urljoin
from typing import Dict, List, Optional
import concurrent.futures
from threading import Lock

class BeatportUnifiedScraper:
    def __init__(self):
        self.base_url = "https://beatport.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.results_lock = Lock()

        # Dynamic genres - will be populated by scraping homepage
        self.all_genres = []

    def clean_text(self, text):
        """Clean and normalize text from HTML elements"""
        if not text:
            return text

        # Fix common spacing issues
        text = re.sub(r'([a-z$!@#%&*])([A-Z])', r'\1 \2', text)  # Add space between lowercase/symbols and uppercase
        text = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', text)  # Add space between letter and number
        text = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', text)  # Add space between number and letter
        text = re.sub(r'([a-zA-Z]),([a-zA-Z])', r'\1, \2', text)  # Add space after comma
        text = re.sub(r'([a-zA-Z])Mix\b', r'\1 Mix', text)  # Fix "hitMix" -> "hit Mix"
        text = re.sub(r'([a-zA-Z])Remix\b', r'\1 Remix', text)  # Fix "hitRemix" -> "hit Remix"
        text = re.sub(r'([a-zA-Z])Extended\b', r'\1 Extended', text)  # Fix "hitExtended" -> "hit Extended"
        text = re.sub(r'([a-zA-Z])Version\b', r'\1 Version', text)  # Fix "hitVersion" -> "hit Version"
        text = re.sub(r'\s+', ' ', text)  # Collapse multiple spaces
        text = text.strip()

        return text

        # Comprehensive fallback genres based on current Beatport dropdown (39 genres)
        self.fallback_genres = [
            # Electronic genres
            {'name': '140 / Deep Dubstep / Grime', 'slug': '140-deep-dubstep-grime', 'id': '95', 'url': f'{self.base_url}/genre/140-deep-dubstep-grime/95'},
            {'name': 'Afro House', 'slug': 'afro-house', 'id': '89', 'url': f'{self.base_url}/genre/afro-house/89'},
            {'name': 'Amapiano', 'slug': 'amapiano', 'id': '98', 'url': f'{self.base_url}/genre/amapiano/98'},
            {'name': 'Ambient / Experimental', 'slug': 'ambient-experimental', 'id': '100', 'url': f'{self.base_url}/genre/ambient-experimental/100'},
            {'name': 'Bass / Club', 'slug': 'bass-club', 'id': '85', 'url': f'{self.base_url}/genre/bass-club/85'},
            {'name': 'Bass House', 'slug': 'bass-house', 'id': '91', 'url': f'{self.base_url}/genre/bass-house/91'},
            {'name': 'Brazilian Funk', 'slug': 'brazilian-funk', 'id': '101', 'url': f'{self.base_url}/genre/brazilian-funk/101'},
            {'name': 'Breaks / Breakbeat / UK Bass', 'slug': 'breaks-breakbeat-uk-bass', 'id': '9', 'url': f'{self.base_url}/genre/breaks-breakbeat-uk-bass/9'},
            {'name': 'Dance / Pop', 'slug': 'dance-pop', 'id': '39', 'url': f'{self.base_url}/genre/dance-pop/39'},
            {'name': 'Deep House', 'slug': 'deep-house', 'id': '12', 'url': f'{self.base_url}/genre/deep-house/12'},
            {'name': 'DJ Tools', 'slug': 'dj-tools', 'id': '16', 'url': f'{self.base_url}/genre/dj-tools/16'},
            {'name': 'Downtempo', 'slug': 'downtempo', 'id': '63', 'url': f'{self.base_url}/genre/downtempo/63'},
            {'name': 'Drum & Bass', 'slug': 'drum-bass', 'id': '1', 'url': f'{self.base_url}/genre/drum-bass/1'},
            {'name': 'Dubstep', 'slug': 'dubstep', 'id': '18', 'url': f'{self.base_url}/genre/dubstep/18'},
            {'name': 'Electro (Classic / Detroit / Modern)', 'slug': 'electro-classic-detroit-modern', 'id': '94', 'url': f'{self.base_url}/genre/electro-classic-detroit-modern/94'},
            {'name': 'Electronica', 'slug': 'electronica', 'id': '3', 'url': f'{self.base_url}/genre/electronica/3'},
            {'name': 'Funky House', 'slug': 'funky-house', 'id': '81', 'url': f'{self.base_url}/genre/funky-house/81'},
            {'name': 'Hard Dance / Hardcore / Neo Rave', 'slug': 'hard-dance-hardcore-neo-rave', 'id': '8', 'url': f'{self.base_url}/genre/hard-dance-hardcore-neo-rave/8'},
            {'name': 'Hard Techno', 'slug': 'hard-techno', 'id': '2', 'url': f'{self.base_url}/genre/hard-techno/2'},
            {'name': 'House', 'slug': 'house', 'id': '5', 'url': f'{self.base_url}/genre/house/5'},
            {'name': 'Indie Dance', 'slug': 'indie-dance', 'id': '37', 'url': f'{self.base_url}/genre/indie-dance/37'},
            {'name': 'Jackin House', 'slug': 'jackin-house', 'id': '97', 'url': f'{self.base_url}/genre/jackin-house/97'},
            {'name': 'Mainstage', 'slug': 'mainstage', 'id': '96', 'url': f'{self.base_url}/genre/mainstage/96'},
            {'name': 'Melodic House & Techno', 'slug': 'melodic-house-techno', 'id': '90', 'url': f'{self.base_url}/genre/melodic-house-techno/90'},
            {'name': 'Minimal / Deep Tech', 'slug': 'minimal-deep-tech', 'id': '14', 'url': f'{self.base_url}/genre/minimal-deep-tech/14'},
            {'name': 'Nu Disco / Disco', 'slug': 'nu-disco-disco', 'id': '50', 'url': f'{self.base_url}/genre/nu-disco-disco/50'},
            {'name': 'Organic House', 'slug': 'organic-house', 'id': '93', 'url': f'{self.base_url}/genre/organic-house/93'},
            {'name': 'Progressive House', 'slug': 'progressive-house', 'id': '15', 'url': f'{self.base_url}/genre/progressive-house/15'},
            {'name': 'Psy-Trance', 'slug': 'psy-trance', 'id': '13', 'url': f'{self.base_url}/genre/psy-trance/13'},
            {'name': 'Tech House', 'slug': 'tech-house', 'id': '11', 'url': f'{self.base_url}/genre/tech-house/11'},
            {'name': 'Techno (Peak Time / Driving)', 'slug': 'techno-peak-time-driving', 'id': '6', 'url': f'{self.base_url}/genre/techno-peak-time-driving/6'},
            {'name': 'Techno (Raw / Deep / Hypnotic)', 'slug': 'techno-raw-deep-hypnotic', 'id': '92', 'url': f'{self.base_url}/genre/techno-raw-deep-hypnotic/92'},
            {'name': 'Trance (Main Floor)', 'slug': 'trance-main-floor', 'id': '7', 'url': f'{self.base_url}/genre/trance-main-floor/7'},
            {'name': 'Trance (Raw / Deep / Hypnotic)', 'slug': 'trance-raw-deep-hypnotic', 'id': '99', 'url': f'{self.base_url}/genre/trance-raw-deep-hypnotic/99'},
            {'name': 'Trap / Future Bass', 'slug': 'trap-future-bass', 'id': '38', 'url': f'{self.base_url}/genre/trap-future-bass/38'},
            {'name': 'UK Garage / Bassline', 'slug': 'uk-garage-bassline', 'id': '86', 'url': f'{self.base_url}/genre/uk-garage-bassline/86'},
            # Open Format genres
            {'name': 'African', 'slug': 'african', 'id': '102', 'url': f'{self.base_url}/genre/african/102'},
            {'name': 'Caribbean', 'slug': 'caribbean', 'id': '103', 'url': f'{self.base_url}/genre/caribbean/103'},
            {'name': 'Hip-Hop', 'slug': 'hip-hop', 'id': '105', 'url': f'{self.base_url}/genre/hip-hop/105'},
            {'name': 'Latin', 'slug': 'latin', 'id': '106', 'url': f'{self.base_url}/genre/latin/106'},
            {'name': 'Pop', 'slug': 'pop', 'id': '107', 'url': f'{self.base_url}/genre/pop/107'},
            {'name': 'R&B', 'slug': 'rb', 'id': '108', 'url': f'{self.base_url}/genre/rb/108'}
        ]

    def get_page(self, url: str) -> Optional[BeautifulSoup]:
        """Fetch and parse a page with error handling"""
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'html.parser')
        except requests.RequestException as e:
            print(f"❌ Error fetching {url}: {e}")
            return None

    def clean_artist_track_data(self, raw_artist: str, raw_title: str) -> Dict[str, str]:
        """Clean and separate artist and track data reliably"""
        if not raw_artist or not raw_title:
            return {'artist': raw_artist or 'Unknown Artist', 'title': raw_title or 'Unknown Title'}

        # Clean artist name - remove extra whitespace and common artifacts
        artist = re.sub(r'\s+', ' ', raw_artist.strip())

        # Clean title and properly format mix information
        title = raw_title.strip()

        # Fix common concatenation issues in titles
        concatenation_fixes = [
            (r'(.+?)(Extended Mix?)$', r'\1 (\2)'),
            (r'(.+?)(Original Mix?)$', r'\1 (\2)'),
            (r'(.+?)(Radio Edit?)$', r'\1 (\2)'),
            (r'(.+?)(Club Mix?)$', r'\1 (\2)'),
            (r'(.+?)(Vocal Mix?)$', r'\1 (\2)'),
            (r'(.+?)(Instrumental?)$', r'\1 (\2)'),
            (r'(.+?)(Remix?)$', r'\1 (\2)'),
            (r'(.+?)(Edit?)$', r'\1 (\2)'),
            (r'(.+?)(Extended)$', r'\1 (\2 Mix)'),
            (r'(.+?)(Version)$', r'\1 (\2)')
        ]

        for pattern, replacement in concatenation_fixes:
            match = re.match(pattern, title, re.IGNORECASE)
            if match:
                title = re.sub(pattern, replacement, title, flags=re.IGNORECASE)
                break

        # Remove duplicate spaces
        title = re.sub(r'\s+', ' ', title)

        return {
            'artist': artist,
            'title': title
        }

    def discover_genres_from_homepage(self) -> List[Dict]:
        """Dynamically discover all genres from Beatport homepage dropdown"""
        print("🔍 Discovering genres from Beatport homepage...")

        try:
            soup = self.get_page(self.base_url)
            if not soup:
                print("❌ Could not fetch homepage")
                return self.fallback_genres

            genres = []

            # Method 1: Look for genres dropdown menu (multiple selectors)
            potential_dropdowns = [
                soup.find('div', {'id': 'genres-dropdown-menu'}),
                soup.find('div', class_=re.compile(r'genres.*dropdown', re.I)),
                soup.find('nav', class_=re.compile(r'genres', re.I)),
                soup.find('div', class_=re.compile(r'dropdown.*genres', re.I)),
                soup.find('ul', class_=re.compile(r'genres', re.I)),
                soup.find('div', {'data-testid': 'genres-dropdown'}),
                soup.find('div', {'aria-label': re.compile(r'genres', re.I)})
            ]

            for dropdown in potential_dropdowns:
                if dropdown:
                    print(f"✅ Found potential genres dropdown: {dropdown.name} with class {dropdown.get('class')}")
                    # Extract genre links from dropdown - look for the specific pattern
                    genre_links = dropdown.find_all('a', href=re.compile(r'/genre/[^/]+/\d+'))

                    if genre_links:
                        print(f"🔗 Found {len(genre_links)} genre links in dropdown")
                        for link in genre_links:
                            href = link.get('href', '')
                            # Get text content, handling nested elements
                            name_text = link.get_text(strip=True)

                            # Clean up the name - remove "New" tags and extra whitespace
                            name = re.sub(r'\s*New\s*', '', name_text).strip()

                            if href and name and len(name) > 1:  # Filter out empty or single char names
                                # Parse URL: /genre/house/5 -> slug='house', id='5'
                                url_parts = href.strip('/').split('/')
                                if len(url_parts) >= 3 and url_parts[0] == 'genre':
                                    slug = url_parts[1]
                                    genre_id = url_parts[2]

                                    genres.append({
                                        'name': name,
                                        'slug': slug,
                                        'id': genre_id,
                                        'url': urljoin(self.base_url, href)
                                    })

                        if genres:
                            print(f"🎯 Successfully extracted {len(genres)} genres from dropdown")
                            break  # Stop after first successful dropdown

            # Method 2: Look for any genre links on the page
            if not genres:
                print("🔍 Dropdown not found, searching for genre links...")
                all_genre_links = soup.find_all('a', href=re.compile(r'/genre/[^/]+/\d+'))
                print(f"🔗 Found {len(all_genre_links)} potential genre links on page")

                seen_genres = set()
                for link in all_genre_links:
                    href = link.get('href', '')
                    name = link.get_text(strip=True)

                    if href and name and len(name) > 1 and href not in seen_genres:
                        url_parts = href.strip('/').split('/')
                        if len(url_parts) >= 3:
                            slug = url_parts[1]
                            genre_id = url_parts[2]

                            genres.append({
                                'name': name,
                                'slug': slug,
                                'id': genre_id,
                                'url': urljoin(self.base_url, href)
                            })
                            seen_genres.add(href)

            # Method 3: Try to find a genres page link and scrape from there
            if not genres:
                print("🔍 Searching for genres page...")
                genres_page_link = soup.find('a', href=re.compile(r'/genres$')) or \
                                 soup.find('a', href=re.compile(r'/browse.*genre', re.I))

                if genres_page_link:
                    genres_page_url = urljoin(self.base_url, genres_page_link['href'])
                    print(f"🔗 Found genres page: {genres_page_url}")
                    genres_soup = self.get_page(genres_page_url)

                    if genres_soup:
                        genre_links = genres_soup.find_all('a', href=re.compile(r'/genre/[^/]+/\d+'))
                        print(f"🔗 Found {len(genre_links)} genre links on genres page")

                        seen_genres = set()
                        for link in genre_links:
                            href = link.get('href', '')
                            name = link.get_text(strip=True)

                            if href and name and len(name) > 1 and href not in seen_genres:
                                url_parts = href.strip('/').split('/')
                                if len(url_parts) >= 3:
                                    slug = url_parts[1]
                                    genre_id = url_parts[2]

                                    genres.append({
                                        'name': name,
                                        'slug': slug,
                                        'id': genre_id,
                                        'url': urljoin(self.base_url, href)
                                    })
                                    seen_genres.add(href)

            # Remove duplicates and sort
            if genres:
                unique_genres = {}
                for genre in genres:
                    key = f"{genre['slug']}-{genre['id']}"
                    if key not in unique_genres:
                        unique_genres[key] = genre

                final_genres = list(unique_genres.values())
                final_genres.sort(key=lambda x: x['name'])

                print(f"✅ Discovered {len(final_genres)} unique genres from homepage")
                return final_genres
            else:
                print("⚠️ No genres found, using fallback list")
                return self.fallback_genres

        except Exception as e:
            print(f"❌ Error discovering genres: {e}")
            return self.fallback_genres

    def discover_chart_sections(self) -> Dict[str, List[Dict]]:
        """Dynamically discover chart sections from homepage"""
        print("🔍 Discovering chart sections from Beatport homepage...")

        soup = self.get_page(self.base_url)
        if not soup:
            return {}

        chart_sections = {
            'top_charts': [],
            'staff_picks': [],
            'other_sections': []
        }

        # Method 1: Find H2 section headings
        print("   📋 Finding H2 section headings...")
        h2_headings = soup.find_all('h2')

        for heading in h2_headings:
            text = heading.get_text(strip=True)
            if text and len(text) > 1:
                section_info = {
                    'title': text,
                    'type': self._classify_chart_section(text),
                    'element_type': 'h2'
                }

                # Categorize into our three main groups
                category = self._categorize_chart_section(text)
                chart_sections[category].append(section_info)
                print(f"      Found: '{text}' -> {category}")

        # Method 2: Find specific chart links
        print("   🔗 Finding chart page links...")
        chart_links = []

        # Look for the specific links we discovered
        known_chart_links = [
            {'text_pattern': r'View Beatport top 100 tracks', 'expected_href': '/top-100'},
            {'text_pattern': r'View Hype top 100 tracks', 'expected_href': '/hype-100'},
            {'text_pattern': r'View Beatport top 100 releases', 'expected_href': '/top-100-releases'}
        ]

        for link_info in known_chart_links:
            link = soup.find('a', string=re.compile(link_info['text_pattern'], re.I))
            if link:
                href = link.get('href', '')
                chart_links.append({
                    'title': link.get_text(strip=True),
                    'href': href,
                    'full_url': urljoin(self.base_url, href),
                    'expected': link_info['expected_href'],
                    'matches_expected': href == link_info['expected_href']
                })
                print(f"      Found: '{link.get_text(strip=True)}' -> {href}")

        # Method 3: Count individual DJ charts
        print("   🎧 Counting individual DJ charts...")
        dj_chart_links = soup.find_all('a', href=re.compile(r'/chart/'))
        individual_dj_charts = []

        for i, chart_link in enumerate(dj_chart_links[:10]):  # Show first 10
            href = chart_link.get('href', '')
            text = chart_link.get_text(strip=True)
            if text and href:
                individual_dj_charts.append({
                    'title': text,
                    'href': href,
                    'full_url': urljoin(self.base_url, href)
                })

        print(f"      Found {len(dj_chart_links)} individual DJ charts")

        return {
            'sections': chart_sections,
            'chart_links': chart_links,
            'individual_dj_charts': individual_dj_charts,
            'summary': {
                'top_charts_sections': len(chart_sections['top_charts']),
                'staff_picks_sections': len(chart_sections['staff_picks']),
                'other_sections': len(chart_sections['other_sections']),
                'main_chart_links': len(chart_links),
                'individual_dj_charts': len(dj_chart_links)
            }
        }

    def _classify_chart_section(self, text: str) -> str:
        """Classify what type of chart section this is"""
        text_lower = text.lower()

        if any(word in text_lower for word in ['top 100', 'top 10', 'beatport top', 'hype top']):
            return 'ranking_chart'
        elif any(word in text_lower for word in ['dj chart', 'artist chart']):
            return 'curated_chart'
        elif any(word in text_lower for word in ['featured', 'staff', 'editorial']):
            return 'editorial_chart'
        elif any(word in text_lower for word in ['hype pick', 'trending']):
            return 'trending_chart'
        elif any(word in text_lower for word in ['new release', 'latest']):
            return 'new_content'
        else:
            return 'other'

    def _categorize_chart_section(self, text: str) -> str:
        """Categorize section into our three main UI categories"""
        text_lower = text.lower()

        # Top Charts: ranking/algorithmic content
        if any(phrase in text_lower for phrase in ['top 100', 'top 10', 'beatport top', 'hype top', 'top tracks', 'top releases']):
            return 'top_charts'

        # Staff Picks: human-curated content
        elif any(phrase in text_lower for phrase in ['dj chart', 'featured chart', 'staff pick', 'hype pick', 'editorial']):
            return 'staff_picks'

        # Other: everything else
        else:
            return 'other_sections'

    def get_genre_image(self, genre_url: str) -> Optional[str]:
        """Extract a representative image from genre page slideshow"""
        try:
            soup = self.get_page(genre_url)
            if not soup:
                return None

            # Look for hero release slideshow images
            hero_images = soup.find_all('img', src=re.compile(r'geo-media\.beatport\.com/image_size/'))

            if hero_images:
                # Get the first high-quality image
                for img in hero_images:
                    src = img.get('src', '')
                    if '1050x508' in src or '500x500' in src:
                        return src

                # Fallback to any geo-media image
                return hero_images[0].get('src', '')

            return None

        except Exception as e:
            print(f"⚠️ Could not get image for {genre_url}: {e}")
            return None

    def discover_genres_with_images(self, include_images: bool = False) -> List[Dict]:
        """Discover genres and optionally include representative images"""
        genres = self.discover_genres_from_homepage()

        if include_images:
            print("🖼️ Fetching genre images...")
            for i, genre in enumerate(genres[:10]):  # Limit to first 10 for demo
                print(f"📷 Getting image for {genre['name']} ({i+1}/{min(10, len(genres))})")

                # Check if genre has URL
                if 'url' in genre and genre['url']:
                    image_url = self.get_genre_image(genre['url'])
                    genre['image_url'] = image_url
                else:
                    print(f"   ⚠️ No URL available for {genre['name']}, skipping image")
                    genre['image_url'] = None

                # Small delay to be respectful
                time.sleep(0.5)

        return genres

    def extract_release_data_from_card(self, release_card) -> Optional[Dict]:
        """Extract data from a release card element (for homepage sections)"""
        try:
            # Get release link and name
            link_elem = release_card.select_one('a[href*="/release/"]')
            if not link_elem:
                return None

            release_url = urljoin(self.base_url, link_elem.get('href'))

            # Extract release name
            name_elem = release_card.select_one('[class*="ReleaseName"], [class*="release-name"]')
            if not name_elem:
                # Try to get from link text
                name_elem = release_card.select_one('a[href*="/release/"]')

            release_name = name_elem.get_text(strip=True) if name_elem else "Unknown Release"

            # Extract artists
            artist_elems = release_card.select('[href*="/artist/"]')
            artists = []
            for artist_elem in artist_elems:
                artist_name = artist_elem.get_text(strip=True)
                if artist_name and artist_name not in artists:
                    artists.append(artist_name)

            # Extract label
            label_elem = release_card.select_one('[href*="/label/"]')
            label = label_elem.get_text(strip=True) if label_elem else "Unknown Label"

            # Extract image
            img_elem = release_card.select_one('img')
            image_url = img_elem.get('src') if img_elem else None

            # Extract price
            price_elem = release_card.select_one('[class*="price"], [class*="Price"]')
            price = price_elem.get_text(strip=True) if price_elem else None

            # Check for badges (EXCLUSIVE, HYPE, etc.)
            badges = []
            badge_elems = release_card.select('[class*="badge"], [class*="Badge"], .hype, .exclusive')
            for badge in badge_elems:
                badge_text = badge.get_text(strip=True).upper()
                if badge_text and badge_text not in badges:
                    badges.append(badge_text)

            return {
                'title': release_name,
                'artist': ', '.join(artists) if artists else "Unknown Artist",
                'artists': artists,
                'label': label,
                'url': release_url,
                'image_url': image_url,
                'price': price,
                'badges': badges,
                'type': 'release'
            }

        except Exception as e:
            print(f"❌ Error extracting release data: {e}")
            return None

    def extract_chart_data_from_card(self, chart_card) -> Optional[Dict]:
        """Extract data from a chart card element (for homepage sections)"""
        try:
            # Get chart link and name
            link_elem = chart_card.select_one('a[href*="/chart/"]')
            if not link_elem:
                return None

            chart_url = urljoin(self.base_url, link_elem.get('href'))

            # Extract chart name from link text or card content
            chart_name = link_elem.get_text(strip=True)
            if not chart_name:
                name_elem = chart_card.select_one('[class*="ChartName"], [class*="chart-name"], [class*="title"]')
                chart_name = name_elem.get_text(strip=True) if name_elem else "Unknown Chart"

            # Extract artist/curator
            artist_elems = chart_card.select('[href*="/artist/"]')
            curators = []
            for artist_elem in artist_elems:
                curator_name = artist_elem.get_text(strip=True)
                if curator_name and curator_name not in curators:
                    curators.append(curator_name)

            # Extract image
            img_elem = chart_card.select_one('img')
            image_url = img_elem.get('src') if img_elem else None

            # Extract price/value
            price_elem = chart_card.select_one('[class*="price"], [class*="Price"]')
            price = price_elem.get_text(strip=True) if price_elem else None

            return {
                'title': chart_name,
                'artist': ', '.join(curators) if curators else "Beatport",
                'curators': curators,
                'url': chart_url,
                'image_url': image_url,
                'price': price,
                'type': 'chart'
            }

        except Exception as e:
            print(f"❌ Error extracting chart data: {e}")
            return None

    def extract_tracks_from_page(self, soup: BeautifulSoup, list_name: str, limit: int = 100) -> List[Dict]:
        """Extract tracks from any Beatport page using reliable selectors"""
        tracks = []

        if not soup:
            return tracks

        # Find all track links on the page
        track_links = soup.find_all('a', href=re.compile(r'/track/'))

        print(f"   Found {len(track_links)} track links on {list_name}")

        for i, link in enumerate(track_links[:limit]):
            if len(tracks) >= limit:
                break

            try:
                # Get track title
                raw_title = link.get_text(separator=' ', strip=True)
                if not raw_title:
                    continue

                # Find artist - try multiple robust approaches
                artist_text = None

                # Method 1: Look for common artist element patterns
                parent = link.parent
                for level in range(5):  # Check up to 5 parent levels
                    if parent:
                        # Try multiple artist class patterns that Beatport commonly uses
                        artist_selectors = [
                            'span[class*="artist"]',
                            'div[class*="artist"]',
                            'a[class*="artist"]',
                            '[data-testid*="artist"]',
                            'span[class*="Artist"]',
                            'div[class*="Artist"]',
                            'span:contains("by")',
                        ]

                        for selector in artist_selectors:
                            artist_elem = parent.select_one(selector)
                            if artist_elem:
                                candidate_text = artist_elem.get_text(strip=True)
                                # Filter out obvious non-artist text
                                if candidate_text and len(candidate_text) > 1 and not any(word in candidate_text.lower() for word in ['track', 'release', 'chart', 'page', 'beatport']):
                                    artist_text = candidate_text
                                    break

                        if artist_text:
                            break
                        parent = parent.parent
                    else:
                        break

                # Method 2: Look for artist links near the track link
                if not artist_text and link.parent:
                    # Look for artist links (href containing /artist/)
                    artist_links = link.parent.find_all('a', href=re.compile(r'/artist/'))
                    if artist_links:
                        artist_text = artist_links[0].get_text(strip=True)

                # Method 3: Parse from title if it contains " - " pattern
                if not artist_text and ' - ' in raw_title:
                    # Sometimes artist and title are combined
                    parts = raw_title.split(' - ', 1)
                    if len(parts) == 2:
                        artist_text = parts[0].strip()
                        raw_title = parts[1].strip()

                # Method 4: Look for any text element that might be an artist in the container
                if not artist_text and link.parent and link.parent.parent:
                    container = link.parent.parent
                    # Look for any element that might contain artist info
                    all_text_elements = container.find_all(['span', 'div', 'a'])
                    for elem in all_text_elements:
                        text = elem.get_text(strip=True)
                        # Heuristic: artist names are typically 1-50 chars, not the same as title
                        if text and 1 < len(text) < 50 and text != raw_title and not any(word in text.lower() for word in ['track', 'release', 'chart', 'page', 'beatport', 'add', 'play', 'buy']):
                            artist_text = text
                            break

                # Clean the data
                cleaned_data = self.clean_artist_track_data(artist_text, raw_title)

                track_data = {
                    'position': len(tracks) + 1,
                    'artist': cleaned_data['artist'],
                    'title': cleaned_data['title'],
                    'list_name': list_name,
                    'url': urljoin(self.base_url, link['href'])
                }

                tracks.append(track_data)

            except Exception as e:
                continue

        return tracks

    def scrape_top_100(self, limit: int = 100) -> List[Dict]:
        """Scrape Beatport Top 100"""
        print("\n🔥 Scraping Beatport Top 100...")

        soup = self.get_page(f"{self.base_url}/top-100")
        tracks = self.extract_tracks_from_page(soup, "Top 100", limit)

        print(f"✅ Extracted {len(tracks)} tracks from Top 100")
        return tracks

    def scrape_new_releases(self, limit: int = 40) -> List[Dict]:
        """Scrape individual tracks from Beatport New Releases using JSON extraction - ENHANCED"""
        print("\n🆕 Scraping Beatport New Releases (individual tracks)...")

        # Step 1: Get release URLs from homepage cards
        release_urls = self.extract_new_releases_urls(limit)
        if not release_urls:
            return []

        # Step 2: Extract individual tracks from each release
        all_tracks = []
        for i, release_url in enumerate(release_urls):
            print(f"\n📀 Processing release {i+1}/{len(release_urls)}")
            tracks = self.extract_tracks_from_release_json(release_url)
            if tracks:
                all_tracks.extend(tracks)

            # Add small delay between requests to be respectful
            import time
            time.sleep(0.5)

        print(f"✅ Extracted {len(all_tracks)} individual tracks from {len(release_urls)} releases")
        return all_tracks

    def extract_new_releases_urls(self, limit: int) -> List[str]:
        """Extract release URLs from New Releases cards on homepage"""
        soup = self.get_page(self.base_url)
        if not soup:
            return []

        # Find New Releases section using data-testid
        release_cards = soup.select('[data-testid="new-releases"]')
        print(f"   Found {len(release_cards)} release cards in New Releases section")

        release_urls = []
        for i, card in enumerate(release_cards[:limit]):
            # Look for artwork anchor link
            artwork_link = card.select_one('a.artwork')
            if not artwork_link:
                # Try other common selectors for release links
                artwork_link = card.select_one('a[href*="/release/"]')

            if artwork_link and artwork_link.get('href'):
                href = artwork_link.get('href')
                # Ensure full URL
                if href.startswith('/'):
                    href = self.base_url + href
                release_urls.append(href)
                print(f"   {i+1}. Found release URL: {href}")

        return release_urls

    def extract_tracks_from_release_json(self, release_url: str) -> List[Dict]:
        """Extract individual tracks from a release page using JSON data"""
        print(f"🎵 Extracting tracks from: {release_url}")

        soup = self.get_page(release_url)
        if not soup:
            return []

        # Extract JSON object from page
        json_obj = self.extract_json_object_from_release_page(soup)
        if not json_obj:
            print("   ❌ No JSON data found")
            return []

        # Filter tracks for this specific release
        release_tracks = self.filter_tracks_for_specific_release(json_obj, release_url)
        if not release_tracks:
            print("   ❌ No matching tracks found")
            return []

        # Convert to our standard format
        converted_tracks = []
        for i, track_data in enumerate(release_tracks):
            track = self.convert_release_json_to_track_format(track_data, release_url, len(converted_tracks) + 1)
            if track:
                converted_tracks.append(track)

        print(f"   ✅ Extracted {len(converted_tracks)} tracks")
        return converted_tracks

    def extract_json_object_from_release_page(self, soup):
        """Extract the main JSON object from a release page"""
        script_tags = soup.find_all('script')

        for script in script_tags:
            if script.string:
                script_content = script.string.strip()

                # Look for Next.js JSON data
                if script_content.startswith('{') and any(keyword in script_content for keyword in ['tracks', 'release']):
                    try:
                        import json
                        json_obj = json.loads(script_content)
                        return json_obj
                    except json.JSONDecodeError:
                        continue

        return None

    def filter_tracks_for_specific_release(self, json_obj: Dict, release_url: str) -> List[Dict]:
        """Filter tracks to only include those from the specific release"""
        # Extract release ID from URL (e.g., /release/capoeira-feat-jessica-gaspar/5361445)
        release_parts = release_url.split('/')
        release_id = release_parts[-1] if release_parts else None

        try:
            # Navigate to the correct path: props.pageProps.dehydratedState.queries[1].state.data.results
            queries = json_obj.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])

            if len(queries) >= 2:
                results = queries[1].get('state', {}).get('data', {}).get('results', [])

                # Filter tracks that match our release ID
                matching_tracks = []
                for track in results:
                    if isinstance(track, dict):
                        track_release_id = None
                        if 'release' in track and isinstance(track['release'], dict):
                            track_release_id = str(track['release'].get('id', ''))

                        if track_release_id == release_id:
                            matching_tracks.append(track)

                return matching_tracks

        except Exception as e:
            print(f"   ❌ Error filtering tracks: {e}")

        return []

    def convert_release_json_to_track_format(self, track_data: Dict, release_url: str, position: int):
        """Convert JSON track data from release page to our standard track format"""
        try:
            if not isinstance(track_data, dict):
                return None

            # Extract title
            title = track_data.get('title') or track_data.get('name', 'Unknown Title')

            # Extract artists
            artist = 'Unknown Artist'
            if 'artists' in track_data and isinstance(track_data['artists'], list):
                artist_names = []
                for artist_obj in track_data['artists']:
                    if isinstance(artist_obj, dict) and 'name' in artist_obj:
                        artist_names.append(artist_obj['name'])
                    elif isinstance(artist_obj, str):
                        artist_names.append(artist_obj)
                if artist_names:
                    artist = ', '.join(artist_names)

            # Extract metadata
            bpm = track_data.get('bpm')
            key_data = track_data.get('key')
            key = key_data.get('name') if isinstance(key_data, dict) else None
            genre_data = track_data.get('genre')
            genre = genre_data.get('name') if isinstance(genre_data, dict) else None
            duration = track_data.get('duration') or track_data.get('length')
            price = track_data.get('price')

            # Get label from release data
            label = 'Unknown Label'
            if 'release' in track_data and isinstance(track_data['release'], dict):
                release_data = track_data['release']
                if 'label' in release_data and isinstance(release_data['label'], dict):
                    label = release_data['label'].get('name', 'Unknown Label')

            # Get track URL if available
            track_url = release_url  # Default to release URL
            if 'slug' in track_data and 'id' in track_data:
                track_url = f"{self.base_url}/track/{track_data['slug']}/{track_data['id']}"

            track = {
                'position': position,
                'title': title,
                'artist': artist,
                'list_name': 'New Releases',
                'url': track_url,
                'label': label,
                'bpm': bpm,
                'key': key,
                'genre': genre,
                'duration': duration,
                'price': price,
                'type': 'track'
            }

            return track

        except Exception as e:
            print(f"   ❌ Error converting track data: {e}")
            return None

    def extract_individual_tracks_from_release_url(self, release_url: str, source_name: str) -> List[Dict]:
        """Extract individual tracks from a release URL using JSON method - used for Top 10/100"""
        try:
            # Get the release page
            soup = self.get_page(release_url)
            if not soup:
                return []

            # Try JSON extraction method (same as New Releases/Hype Picks)
            if hasattr(self, 'extract_json_object_from_release_page') and hasattr(self, 'filter_tracks_for_specific_release'):
                # Use existing JSON extraction methods
                json_obj = self.extract_json_object_from_release_page(soup)
                if json_obj:
                    release_tracks = self.filter_tracks_for_specific_release(json_obj, release_url)
                    if release_tracks and hasattr(self, 'convert_release_json_to_track_format'):
                        converted_tracks = []
                        for i, track_data in enumerate(release_tracks):
                            track = self.convert_release_json_to_track_format(track_data, release_url, i+1)
                            if track:
                                # Update the list_name to reflect the source
                                track['list_name'] = source_name
                                converted_tracks.append(track)
                        return converted_tracks

            # Fallback: try the general track extraction method
            tracks = self.extract_tracks_from_page(soup, source_name, 50)
            return tracks

        except Exception as e:
            print(f"      ❌ Error extracting tracks from {release_url}: {e}")
            return []

    def scrape_multiple_releases(self, release_urls: List[str], source_name: str = "General Release Scraper") -> List[Dict]:
        """
        General scraper function - takes list of release URLs and extracts all tracks

        Args:
            release_urls: List of Beatport release URLs to scrape
            source_name: Name to use as source identifier for tracks

        Returns:
            List of track dictionaries with title, artist, label, etc.
        """
        print(f"\n🎯 SCRAPING {len(release_urls)} RELEASE URLs")
        print("=" * 60)

        all_tracks = []

        for i, release_url in enumerate(release_urls, 1):
            print(f"\n📀 Processing release {i}/{len(release_urls)}: {release_url}")

            try:
                tracks = self.extract_individual_tracks_from_release_url(release_url, source_name)
                if tracks:
                    all_tracks.extend(tracks)
                    print(f"   ✅ Found {len(tracks)} tracks")

                    # Show first few tracks for verification
                    for j, track in enumerate(tracks[:3], 1):
                        title = track.get('title', 'Unknown')
                        artist = track.get('artist', 'Unknown')
                        label = track.get('label', 'Unknown')
                        print(f"      Track {j}: '{title}' by '{artist}' [{label}]")

                    if len(tracks) > 3:
                        print(f"      ... and {len(tracks) - 3} more tracks")
                else:
                    print(f"   ❌ No tracks found")

            except Exception as e:
                print(f"   ❌ Error processing release: {e}")
                continue

            # Small delay between requests to be respectful
            if i < len(release_urls):
                time.sleep(0.5)

        print(f"\n" + "=" * 60)
        print(f"🎉 SCRAPING COMPLETE")
        print(f"   Total releases processed: {len(release_urls)}")
        print(f"   Total tracks extracted: {len(all_tracks)}")

        return all_tracks

    def scrape_hype_top_100(self, limit: int = 100) -> List[Dict]:
        """Scrape Beatport Hype Top 100 - Fixed URL based on parser discovery"""
        print("\n🔥 Scraping Beatport Hype Top 100...")

        # Use the correct URL discovered by parser
        soup = self.get_page(f"{self.base_url}/hype-100")
        if soup:
            tracks = self.extract_tracks_from_page(soup, "Hype Top 100", limit)
            print(f"✅ Extracted {len(tracks)} tracks from Hype Top 100")
            return tracks
        else:
            print("⚠️ Could not access /hype-100, trying homepage Hype Picks section...")
            # Fallback to homepage section
            soup = self.get_page(self.base_url)
            if soup:
                hype_heading = soup.find(['h1', 'h2', 'h3'], string=re.compile(r'Hype Picks', re.I))
                if hype_heading:
                    section_container = hype_heading.find_parent()
                    if section_container:
                        content_area = section_container.find_next_sibling()
                        if content_area:
                            tracks = self.extract_tracks_from_page(content_area, "Hype Top 100", limit)
                        else:
                            tracks = self.extract_tracks_from_page(section_container, "Hype Top 100", limit)
                    else:
                        tracks = []
                else:
                    tracks = []
            else:
                tracks = []

            print(f"✅ Extracted {len(tracks)} tracks from Hype Top 100 (fallback)")
            return tracks

    def extract_releases_from_page(self, soup: BeautifulSoup, list_name: str, limit: int = 100) -> List[Dict]:
        """Extract releases from Beatport Top 100 Releases page using table structure"""
        releases = []

        if not soup:
            return releases

        # Find table rows - each track/release is in a table row
        table_rows = soup.find_all('div', class_=re.compile(r'Table-style__TableRow'))
        print(f"   Found {len(table_rows)} table rows on {list_name}")

        for i, row in enumerate(table_rows[:limit]):
            if len(releases) >= limit:
                break

            try:
                # Find release title using the specific CSS class
                title_element = row.find('span', class_=re.compile(r'Tables-shared-style__ReleaseName'))
                if not title_element:
                    if len(releases) < 5:
                        print(f"   ⚠️ Row {i+1}: No release title found")
                    continue

                release_title = title_element.get_text(strip=True)
                if not release_title:
                    if len(releases) < 5:
                        print(f"   ⚠️ Row {i+1}: Empty release title")
                    continue

                # Find the release URL from the title link
                title_link = title_element.find_parent('a')
                if not title_link:
                    # Look for any release link in this row
                    title_link = row.find('a', href=re.compile(r'/release/'))

                release_href = title_link.get('href', '') if title_link else ''

                # Find artist links in this row
                artists = []
                artist_links = row.find_all('a', href=re.compile(r'/artist/'))
                for artist_link in artist_links:
                    artist_name = artist_link.get_text(strip=True)
                    if artist_name and artist_name not in artists:
                        artists.append(artist_name)

                # Combine artists or use fallback
                if artists:
                    artist_text = ", ".join(artists)
                else:
                    artist_text = "Various Artists"

                release_data = {
                    'position': len(releases) + 1,
                    'artist': artist_text,
                    'title': release_title,
                    'list_name': list_name,
                    'url': urljoin(self.base_url, release_href) if release_href else '',
                    'type': 'release'
                }

                releases.append(release_data)

                # Debug print for first few items
                if len(releases) <= 5:
                    print(f"   Release {len(releases)}: '{release_title}' by '{artist_text}' (found {len(artists)} artists)")

            except Exception as e:
                print(f"   ⚠️ Error extracting row {i+1}: {e}")
                continue

        print(f"   Successfully extracted {len(releases)} releases from {len(table_rows)} rows")
        return releases

    def scrape_top_100_releases(self, limit: int = 100) -> List[Dict]:
        """Scrape Beatport Top 100 Releases - Extract individual tracks using URL crawling"""
        print("\n📊 Scraping Beatport Top 100 Releases...")

        # Step 1: Extract release URLs from Top 100 page
        soup = self.get_page(f"{self.base_url}/top-100-releases")
        if not soup:
            print("   ❌ Could not access /top-100-releases page")
            return []

        # Look for rows with release links (Top 100 uses [class*="row"] elements, not tables)
        table_rows = soup.select('tr')
        if not table_rows:
            # Top 100 page uses row-based layout, not table structure
            table_rows = soup.select('[class*="row"]')

        print(f"   Found {len(table_rows)} rows on Top 100 page")

        release_urls = []
        urls_found = 0

        for i, row in enumerate(table_rows):
            # Look for release link in this row
            link_elem = row.select_one('a[href*="/release/"]')
            if link_elem and link_elem.get('href'):
                release_url = urljoin(self.base_url, link_elem.get('href'))
                release_urls.append(release_url)
                urls_found += 1
                print(f"   {urls_found}. Found Top 100 release URL: {release_url}")

                # Stop when we've found enough URLs
                if urls_found >= limit:
                    break

        if not release_urls:
            print("   ❌ No Top 100 release URLs found")
            return []

        # Step 2: Crawl each release URL to extract individual tracks
        all_individual_tracks = []
        for i, release_url in enumerate(release_urls):
            print(f"   Processing Top 100 release {i+1}/{len(release_urls)}: {release_url}")

            # Extract individual tracks from this release
            tracks = self.extract_individual_tracks_from_release_url(release_url, "Top 100 Releases")
            if tracks:
                print(f"   ✅ Found {len(tracks)} individual tracks")
                all_individual_tracks.extend(tracks)
            else:
                print(f"   ❌ No tracks found")

            # Add delay between requests to be respectful
            if i < len(release_urls) - 1:
                time.sleep(0.5)

        print(f"✅ Extracted {len(all_individual_tracks)} individual tracks from {len(release_urls)} Top 100 releases")
        return all_individual_tracks

    def scrape_dj_charts(self, limit: int = 20) -> List[Dict]:
        """Scrape Beatport DJ Charts from homepage section - Improved reliability"""
        print("\n🎧 Scraping Beatport DJ Charts...")

        soup = self.get_page(self.base_url)
        if not soup:
            return []

        charts = []

        # Method 1: Find DJ Charts H2 section on homepage
        dj_charts_heading = soup.find(['h1', 'h2', 'h3'], string=re.compile(r'DJ Charts', re.I))
        if dj_charts_heading:
            print("   Found DJ Charts section heading")
            # Get the section content after the heading
            section_container = dj_charts_heading.find_parent()
            if section_container:
                content_area = section_container.find_next_sibling()
                if content_area:
                    # Look for individual chart links within this section
                    chart_links = content_area.find_all('a', href=re.compile(r'/chart/'))
                    print(f"   Found {len(chart_links)} individual DJ chart links")

                    for chart_link in chart_links[:limit]:
                        chart_name = chart_link.get_text(strip=True)
                        chart_href = chart_link.get('href', '')

                        if chart_name and chart_href:
                            # Add this chart info to our results
                            chart_info = {
                                'position': len(charts) + 1,
                                'artist': 'Various Artists',  # DJ charts are compilations
                                'title': chart_name,
                                'list_name': 'DJ Charts',
                                'url': urljoin(self.base_url, chart_href),
                                'chart_name': chart_name,
                                'chart_type': 'dj_chart'
                            }
                            charts.append(chart_info)

        # Method 2: If no section found, look for chart links across entire homepage
        if not charts:
            print("   ⚠️ DJ Charts section not found, scanning entire homepage...")
            all_chart_links = soup.find_all('a', href=re.compile(r'/chart/'))
            print(f"   Found {len(all_chart_links)} total chart links on homepage")

            for chart_link in all_chart_links[:limit]:
                chart_name = chart_link.get_text(strip=True)
                chart_href = chart_link.get('href', '')

                if chart_name and chart_href and len(chart_name) > 3:  # Filter out very short names
                    chart_info = {
                        'position': len(charts) + 1,
                        'artist': 'Various Artists',
                        'title': chart_name,
                        'list_name': 'DJ Charts',
                        'url': urljoin(self.base_url, chart_href),
                        'chart_name': chart_name,
                        'chart_type': 'dj_chart'
                    }
                    charts.append(chart_info)

        print(f"✅ Extracted {len(charts)} DJ charts")
        return charts

    def scrape_featured_charts(self, limit: int = 20) -> List[Dict]:
        """Scrape Beatport Featured Charts from homepage section - FIXED"""
        print("\n📊 Scraping Beatport Featured Charts...")

        soup = self.get_page(self.base_url)
        if not soup:
            return []

        # Find Featured Charts section using data-testid
        chart_cards = soup.select('[data-testid="featured-charts"]')
        print(f"   Found {len(chart_cards)} chart cards in Featured Charts section")

        charts = []
        for i, card in enumerate(chart_cards[:limit]):
            chart_data = self.extract_chart_data_from_card(card)
            if chart_data:
                # Convert to track format for compatibility
                track_data = {
                    'position': i + 1,
                    'artist': chart_data['artist'],
                    'title': chart_data['title'],
                    'list_name': 'Featured Charts',
                    'url': chart_data['url'],
                    'chart_name': chart_data['title'],
                    'chart_type': 'featured',
                    'curators': chart_data.get('curators', []),
                    'image_url': chart_data.get('image_url'),
                    'price': chart_data.get('price'),
                    'type': 'chart'
                }
                charts.append(track_data)

        print(f"✅ Extracted {len(charts)} charts from Featured Charts")
        return charts

    def scrape_hype_picks_homepage(self, limit: int = 40) -> List[Dict]:
        """Scrape individual tracks from Beatport Hype Picks using JSON extraction - ENHANCED"""
        print("\n🔥 Scraping Beatport Hype Picks (individual tracks)...")

        # Step 1: Get release URLs from homepage cards
        release_urls = self.extract_hype_picks_urls(limit)
        if not release_urls:
            return []

        # Step 2: Extract individual tracks from each release
        all_tracks = []
        for i, release_url in enumerate(release_urls):
            print(f"\n📀 Processing release {i+1}/{len(release_urls)}")
            tracks = self.extract_tracks_from_hype_picks_release_json(release_url)
            if tracks:
                all_tracks.extend(tracks)

            # Add small delay between requests to be respectful
            import time
            time.sleep(0.5)

        print(f"✅ Extracted {len(all_tracks)} individual tracks from {len(release_urls)} hype picks releases")
        return all_tracks

    def extract_hype_picks_urls(self, limit: int) -> List[str]:
        """Extract release URLs from Hype Picks cards on homepage"""
        soup = self.get_page(self.base_url)
        if not soup:
            return []

        # Find Hype Picks section using data-testid
        hype_cards = soup.select('[data-testid="hype-picks"]')
        print(f"   Found {len(hype_cards)} hype picks cards in section")

        release_urls = []
        for i, card in enumerate(hype_cards[:limit]):
            # Look for artwork anchor link
            artwork_link = card.select_one('a.artwork')
            if not artwork_link:
                # Try other common selectors for release links
                artwork_link = card.select_one('a[href*="/release/"]')

            if artwork_link and artwork_link.get('href'):
                href = artwork_link.get('href')
                # Ensure full URL
                if href.startswith('/'):
                    href = self.base_url + href
                release_urls.append(href)
                print(f"   {i+1}. Found release URL: {href}")

        return release_urls

    def extract_tracks_from_hype_picks_release_json(self, release_url: str) -> List[Dict]:
        """Extract individual tracks from a hype picks release page using JSON data"""
        print(f"🎵 Extracting tracks from: {release_url}")

        soup = self.get_page(release_url)
        if not soup:
            return []

        # Extract JSON object from page (same method as New Releases)
        json_obj = self.extract_json_object_from_release_page(soup)
        if not json_obj:
            print("   ❌ No JSON data found")
            return []

        # Filter tracks for this specific release (same method as New Releases)
        release_tracks = self.filter_tracks_for_specific_release(json_obj, release_url)
        if not release_tracks:
            print("   ❌ No matching tracks found")
            return []

        # Convert to our standard format (with Hype Picks branding)
        converted_tracks = []
        for i, track_data in enumerate(release_tracks):
            track = self.convert_hype_picks_json_to_track_format(track_data, release_url, len(converted_tracks) + 1)
            if track:
                converted_tracks.append(track)

        print(f"   ✅ Extracted {len(converted_tracks)} tracks")
        return converted_tracks

    def convert_hype_picks_json_to_track_format(self, track_data: Dict, release_url: str, position: int):
        """Convert JSON track data from hype picks release page to our standard track format"""
        try:
            if not isinstance(track_data, dict):
                return None

            # Extract title
            title = track_data.get('title') or track_data.get('name', 'Unknown Title')

            # Extract artists
            artist = 'Unknown Artist'
            if 'artists' in track_data and isinstance(track_data['artists'], list):
                artist_names = []
                for artist_obj in track_data['artists']:
                    if isinstance(artist_obj, dict) and 'name' in artist_obj:
                        artist_names.append(artist_obj['name'])
                    elif isinstance(artist_obj, str):
                        artist_names.append(artist_obj)
                if artist_names:
                    artist = ', '.join(artist_names)

            # Extract metadata
            bpm = track_data.get('bpm')
            key_data = track_data.get('key')
            key = key_data.get('name') if isinstance(key_data, dict) else None
            genre_data = track_data.get('genre')
            genre = genre_data.get('name') if isinstance(genre_data, dict) else None
            duration = track_data.get('duration') or track_data.get('length')
            price = track_data.get('price')

            # Get label from release data
            label = 'Unknown Label'
            if 'release' in track_data and isinstance(track_data['release'], dict):
                release_data = track_data['release']
                if 'label' in release_data and isinstance(release_data['label'], dict):
                    label = release_data['label'].get('name', 'Unknown Label')

            # Get track URL if available
            track_url = release_url  # Default to release URL
            if 'slug' in track_data and 'id' in track_data:
                track_url = f"{self.base_url}/track/{track_data['slug']}/{track_data['id']}"

            track = {
                'position': position,
                'title': title,
                'artist': artist,
                'list_name': 'Hype Picks',
                'url': track_url,
                'label': label,
                'bpm': bpm,
                'key': key,
                'genre': genre,
                'duration': duration,
                'price': price,
                'badges': ['HYPE'],  # Keep the HYPE badge
                'type': 'track',
                'hype': True  # Maintain hype flag
            }

            return track

        except Exception as e:
            print(f"   ❌ Error converting track data: {e}")
            return None

    def scrape_homepage_top10_lists(self) -> Dict[str, List[Dict]]:
        """Scrape Top 10 Lists from homepage - Beatport Top 10 and Hype Top 10"""
        print("\n🏆 Scraping Top 10 Lists from homepage...")

        soup = self.get_page(self.base_url)
        if not soup:
            return {"beatport_top10": [], "hype_top10": []}

        # Extract Beatport Top 10 tracks
        beatport_top10_items = soup.select('[data-testid="top-10-item"]')
        print(f"   Found {len(beatport_top10_items)} Beatport Top 10 items")

        beatport_tracks = []
        for i, item in enumerate(beatport_top10_items, 1):
            try:
                track_data = self.extract_track_from_top10_item(item, i, "Beatport Top 10")
                if track_data:
                    beatport_tracks.append(track_data)
            except Exception as e:
                print(f"   ❌ Error extracting Beatport track {i}: {e}")

        # Extract Hype Top 10 tracks
        hype_top10_items = soup.select('[data-testid="hype-top-10-item"]')
        print(f"   Found {len(hype_top10_items)} Hype Top 10 items")

        hype_tracks = []
        for i, item in enumerate(hype_top10_items, 1):
            try:
                track_data = self.extract_track_from_top10_item(item, i, "Hype Top 10")
                if track_data:
                    hype_tracks.append(track_data)
            except Exception as e:
                print(f"   ❌ Error extracting Hype track {i}: {e}")

        print(f"✅ Extracted {len(beatport_tracks)} Beatport Top 10 + {len(hype_tracks)} Hype Top 10 tracks")

        return {
            "beatport_top10": beatport_tracks,
            "hype_top10": hype_tracks
        }

    def extract_track_from_top10_item(self, item, rank, list_name):
        """Extract track data from a top 10 list item"""
        try:
            # Get the track URL
            link_elem = item.select_one('a[href*="/track/"]')
            track_url = ""
            if link_elem and link_elem.get('href'):
                track_url = f"https://www.beatport.com{link_elem.get('href')}"

            # Extract track title
            title = "Unknown Title"
            title_selectors = [
                '[class*="ItemName"]',
                '[class*="TrackName"]',
                '[class*="track-name"]',
                'a[href*="/track/"]'
            ]

            for selector in title_selectors:
                title_elem = item.select_one(selector)
                if title_elem:
                    title = self.clean_text(title_elem.get_text(strip=True))
                    if title and title != "Unknown Title":
                        break

            # Extract artist name
            artist = "Unknown Artist"
            artist_selectors = [
                '[class*="Artists"]',
                '[class*="artist"]',
                '[class*="Artist"]',
                '[class*="ItemArtist"]',
                'a[href*="/artist/"]'
            ]

            for selector in artist_selectors:
                artist_elem = item.select_one(selector)
                if artist_elem:
                    artist = self.clean_text(artist_elem.get_text(strip=True))
                    if artist and artist != "Unknown Artist":
                        break

            # Extract label name
            label = "Unknown Label"
            label_selectors = [
                '[class*="Label"]',
                '[class*="label"]',
                '[class*="ItemLabel"]',
                'a[href*="/label/"]'
            ]

            for selector in label_selectors:
                label_elem = item.select_one(selector)
                if label_elem:
                    label = self.clean_text(label_elem.get_text(strip=True))
                    if label and label != "Unknown Label":
                        break

            # Extract artwork if available
            artwork_url = ""
            img_elem = item.select_one('img')
            if img_elem and img_elem.get('src'):
                artwork_url = img_elem.get('src')

            return {
                "rank": rank,
                "title": title,
                "artist": artist,
                "label": label,
                "url": track_url,
                "artwork_url": artwork_url,
                "list_name": list_name
            }

        except Exception as e:
            print(f"Error extracting track data: {e}")
            return None

    def scrape_homepage_top10_releases(self) -> List[Dict]:
        """Scrape Top 10 Releases from homepage - FIXED VERSION"""
        print("\n💿 FIXED: Scraping Top 10 Releases from homepage...")

        soup = self.get_page(self.base_url)
        if not soup:
            print("   ❌ Could not get homepage")
            return []

        # Extract Top 10 Releases items - EXACT same as test script
        top10_releases_items = soup.select('[data-testid="top-10-releases-item"]')
        print(f"   FOUND {len(top10_releases_items)} Top 10 Releases items")

        if len(top10_releases_items) == 0:
            print("   ❌ No items found - trying alternatives")
            return []

        releases = []
        for i, item in enumerate(top10_releases_items, 1):
            try:
                # Use the SAME function name as the test script
                release_data = self.extract_release_from_item_FIXED(item, i)
                if release_data:
                    releases.append(release_data)
                    print(f"   ✅ {i}. {release_data['artist']} - {release_data['title']}")
                else:
                    print(f"   ❌ {i}. No data extracted")
            except Exception as e:
                print(f"   ❌ Error extracting release {i}: {e}")

        print(f"✅ FINAL: Extracted {len(releases)} Top 10 Releases")
        return releases

    def extract_release_from_item_FIXED(self, item, rank):
        """Extract release data from a list item - EXACT COPY FROM WORKING TEST SCRIPT"""
        try:
            # Get the release URL
            link_elem = item.select_one('a[href*="/release/"]')
            release_url = ""
            if link_elem and link_elem.get('href'):
                release_url = f"https://www.beatport.com{link_elem.get('href')}"

            # Extract release title
            title = "Unknown Title"
            # Try multiple selectors for title
            title_selectors = [
                '[class*="ItemName"]',
                '[class*="ReleaseName"]',
                '[class*="release-name"]',
                '[class*="TrackName"]',
                '[class*="track-name"]',
                'a[href*="/release/"]',
                'h3', 'h4', 'h5',
                '[class*="title"]',
                '[class*="Title"]'
            ]

            for selector in title_selectors:
                title_elem = item.select_one(selector)
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    if title and title != "Unknown Title" and len(title) > 2:
                        break

            # Extract artist name - try multiple approaches
            artist = "Unknown Artist"
            artist_selectors = [
                '[class*="Artists"]',
                '[class*="artist"]',
                '[class*="Artist"]',
                '[class*="ItemArtist"]',
                'a[href*="/artist/"]',
                '[class*="by"]',
                '[class*="By"]'
            ]

            for selector in artist_selectors:
                artist_elem = item.select_one(selector)
                if artist_elem:
                    artist = artist_elem.get_text(strip=True)
                    if artist and artist != "Unknown Artist" and len(artist) > 1:
                        break

            # Extract label name - try multiple approaches
            label = "Unknown Label"
            label_selectors = [
                '[class*="Label"]',
                '[class*="label"]',
                '[class*="ItemLabel"]',
                'a[href*="/label/"]',
                '[class*="imprint"]',
                '[class*="Imprint"]'
            ]

            for selector in label_selectors:
                label_elem = item.select_one(selector)
                if label_elem:
                    label = label_elem.get_text(strip=True)
                    if label and label != "Unknown Label" and len(label) > 2:
                        break

            # Extract image URL - this is important for releases
            image_url = ""
            image_selectors = [
                'img[src]',
                'img[data-src]',
                'img[data-lazy]',
                '[style*="background-image"]',
                'picture img',
                '.artwork img',
                '[class*="artwork"] img',
                '[class*="Artwork"] img',
                '[class*="image"] img',
                '[class*="Image"] img'
            ]

            for selector in image_selectors:
                img_elem = item.select_one(selector)
                if img_elem:
                    # Try different image source attributes
                    img_src = (img_elem.get('src') or
                              img_elem.get('data-src') or
                              img_elem.get('data-lazy') or
                              img_elem.get('data-original'))

                    if img_src and img_src.startswith(('http', '//')):
                        image_url = img_src
                        break
                    elif img_src and img_src.startswith('/'):
                        image_url = f"https://www.beatport.com{img_src}"
                        break

            return {
                "rank": rank,
                "title": title,
                "artist": artist,
                "label": label,
                "url": release_url,
                "image_url": image_url,
                "list_name": "Top 10 Releases"
            }

        except Exception as e:
            print(f"Error extracting release data: {e}")
            return None

    def extract_release_from_top10_item(self, item, rank):
        """Extract release data from a top 10 releases item"""
        try:
            # Get the release URL
            link_elem = item.select_one('a[href*="/release/"]')
            release_url = ""
            if link_elem and link_elem.get('href'):
                release_url = f"https://www.beatport.com{link_elem.get('href')}"

            # Extract release title
            title = "Unknown Title"
            title_selectors = [
                '[class*="ItemName"]',
                '[class*="ReleaseName"]',
                '[class*="release-name"]',
                '[class*="TrackName"]',
                '[class*="track-name"]',
                'a[href*="/release/"]',
                'h3', 'h4', 'h5',
                '[class*="title"]',
                '[class*="Title"]'
            ]

            for selector in title_selectors:
                title_elem = item.select_one(selector)
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    if title and title != "Unknown Title" and len(title) > 2:
                        break

            # Extract artist name
            artist = "Unknown Artist"
            artist_selectors = [
                '[class*="Artists"]',
                '[class*="artist"]',
                '[class*="Artist"]',
                '[class*="ItemArtist"]',
                'a[href*="/artist/"]',
                '[class*="by"]',
                '[class*="By"]'
            ]

            for selector in artist_selectors:
                artist_elem = item.select_one(selector)
                if artist_elem:
                    artist = artist_elem.get_text(strip=True)
                    if artist and artist != "Unknown Artist" and len(artist) > 1:
                        break

            # Extract label name
            label = "Unknown Label"
            label_selectors = [
                '[class*="Label"]',
                '[class*="label"]',
                '[class*="ItemLabel"]',
                'a[href*="/label/"]',
                '[class*="imprint"]',
                '[class*="Imprint"]'
            ]

            for selector in label_selectors:
                label_elem = item.select_one(selector)
                if label_elem:
                    label = label_elem.get_text(strip=True)
                    if label and label != "Unknown Label" and len(label) > 2:
                        break

            # Extract image URL - important for releases
            image_url = ""
            image_selectors = [
                'img[src]',
                'img[data-src]',
                'img[data-lazy]',
                '[style*="background-image"]',
                'picture img',
                '.artwork img',
                '[class*="artwork"] img',
                '[class*="Artwork"] img',
                '[class*="image"] img',
                '[class*="Image"] img'
            ]

            for selector in image_selectors:
                img_elem = item.select_one(selector)
                if img_elem:
                    img_src = (img_elem.get('src') or
                              img_elem.get('data-src') or
                              img_elem.get('data-lazy') or
                              img_elem.get('data-original'))

                    if img_src and img_src.startswith(('http', '//')):
                        image_url = img_src
                        break
                    elif img_src and img_src.startswith('/'):
                        image_url = f"https://www.beatport.com{img_src}"
                        break

            return {
                "rank": rank,
                "title": title,
                "artist": artist,
                "label": label,
                "url": release_url,
                "image_url": image_url,
                "list_name": "Top 10 Releases"
            }

        except Exception as e:
            print(f"Error extracting release data: {e}")
            return None

    def scrape_new_on_beatport_hero(self, limit: int = 10) -> List[Dict]:
        """Scrape the 'New on Beatport' hero slideshow from homepage using data-testid standard"""
        print("\n🎯 Scraping 'New on Beatport' hero slideshow...")

        soup = self.get_page(self.base_url)
        if not soup:
            return []

        tracks = []

        # Method 1 (PRIMARY): Use data-testid standard like all other rebuild functions
        hero_items = soup.select('[data-testid="new-on-beatport"]')
        if hero_items:
            print(f"   ✅ Found {len(hero_items)} items using data-testid='new-on-beatport'")
            for i, item in enumerate(hero_items[:limit]):
                track_data = self._extract_track_from_slide(item, f"Hero Item {i+1}")
                if track_data and track_data.get('url'):
                    tracks.append(track_data)

        # Method 2 (FALLBACK): Look for the specific wrapper class (legacy support)
        if len(tracks) < 5:
            hero_wrapper = soup.find('div', class_='Homepage-style__NewOnBeatportWrapper-sc-deeb4244-2 iyIchZ')
            if hero_wrapper:
                print("   ✅ Found Homepage NewOnBeatportWrapper (fallback)")
                tracks.extend(self._extract_from_hero_wrapper(hero_wrapper, limit))

        # Method 3 (FALLBACK): Look for carousel with aria attributes
        if len(tracks) < 5:
            carousel = soup.find('div', {'aria-roledescription': 'carousel', 'aria-label': 'Carousel'})
            if carousel:
                print("   ✅ Found carousel with aria-roledescription and aria-label (fallback)")
                additional_tracks = self._extract_from_carousel(carousel, limit)
                # Merge without duplicates
                existing_urls = {track.get('url') for track in tracks}
                for track in additional_tracks:
                    if track.get('url') not in existing_urls:
                        tracks.append(track)

        # Method 4 (LAST RESORT): Look for individual slide items more broadly
        if len(tracks) < 5:
            print("   🔍 Looking for individual carousel items (last resort)...")
            carousel_items = soup.find_all(['div', 'article'], class_=re.compile(r'carousel.*item|item.*carousel|slide', re.I))
            print(f"   Found {len(carousel_items)} potential carousel items")

            for i, item in enumerate(carousel_items[:limit * 2]):  # Check more items
                track_data = self._extract_track_from_slide(item, f"Carousel Item {i+1}")
                if track_data and track_data.get('url'):
                    # Check for duplicate URLs
                    existing_urls = {track.get('url') for track in tracks}
                    if track_data['url'] not in existing_urls:
                        tracks.append(track_data)

        print(f"   📊 Extracted {len(tracks)} tracks from New on Beatport hero")
        return tracks[:limit]

    def _extract_from_hero_wrapper(self, wrapper, limit: int) -> List[Dict]:
        """Extract tracks from the specific NewOnBeatportWrapper"""
        tracks = []

        # Method 1: Look for all release/track links within the wrapper
        release_links = wrapper.find_all('a', href=re.compile(r'/release/|/track/'))

        seen_urls = set()
        for i, link in enumerate(release_links):
            href = link.get('href')
            if href and href not in seen_urls:
                seen_urls.add(href)

                # Find the parent container that likely contains all track info
                parent = link.find_parent(['div', 'article', 'section'])
                if parent:
                    track_data = self._extract_track_from_slide(parent, f"Hero Release {i+1}")
                    if track_data:
                        tracks.append(track_data)

        # Method 2: If not enough tracks, try broader slide detection
        if len(tracks) < 5:
            slides = wrapper.find_all(['div', 'article', 'section'], class_=re.compile(r'slide|item|card', re.I))

            for i, slide in enumerate(slides[:limit]):
                track_data = self._extract_track_from_slide(slide, f"Hero Slide {i+1}")
                if track_data:
                    # Check for duplicates by URL
                    url = track_data.get('url')
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        tracks.append(track_data)

        # Method 3: If still not enough, try finding all elements with images
        if len(tracks) < 5:
            image_containers = wrapper.find_all(['div', 'figure'], recursive=True)

            for i, container in enumerate(image_containers):
                if container.find('img') and container.find('a'):
                    track_data = self._extract_track_from_slide(container, f"Hero Image {i+1}")
                    if track_data:
                        url = track_data.get('url')
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            tracks.append(track_data)
                            if len(tracks) >= limit:
                                break

        return tracks

    def _extract_from_carousel(self, carousel, limit: int) -> List[Dict]:
        """Extract tracks from carousel element"""
        tracks = []

        # Look for individual slides within carousel
        slides = carousel.find_all(['div', 'article', 'li'], class_=re.compile(r'slide|item|card', re.I))

        if not slides:
            # Try alternative selectors
            slides = carousel.find_all(['div', 'article'], recursive=True)
            slides = [s for s in slides if s.find('a') or s.find('img') or 'track' in str(s.get('class', '')).lower()]

        for i, slide in enumerate(slides[:limit]):
            track_data = self._extract_track_from_slide(slide, f"Carousel Slide {i+1}")
            if track_data:
                tracks.append(track_data)

        return tracks

    def _extract_from_hero_element(self, element, limit: int) -> List[Dict]:
        """Extract tracks from general hero element"""
        tracks = []

        # Look for any trackable items
        items = element.find_all(['div', 'article', 'a'], recursive=True)
        track_items = []

        for item in items:
            # Filter for elements likely to contain track info
            if (item.find('img') or
                'track' in str(item.get('class', '')).lower() or
                'release' in str(item.get('class', '')).lower() or
                item.get('href', '').count('/') > 2):
                track_items.append(item)

        for i, item in enumerate(track_items[:limit]):
            track_data = self._extract_track_from_slide(item, f"Hero Item {i+1}")
            if track_data:
                tracks.append(track_data)

        return tracks

    def _extract_track_from_slide(self, slide, context: str) -> Optional[Dict]:
        """Extract track information from a slide/item element"""
        try:
            track_data = {}

            # Extract image
            img = slide.find('img')
            if img:
                track_data['image_url'] = img.get('src') or img.get('data-src')
                track_data['alt_text'] = img.get('alt', '')

            # Extract link URL
            link = slide.find('a')
            if link:
                href = link.get('href')
                if href:
                    track_data['url'] = urljoin(self.base_url, href)

            # Enhanced title/track name extraction
            title_selectors = [
                'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                '[class*="title"]', '[class*="name"]', '[class*="track"]',
                '[data-testid*="title"]', '[data-testid*="name"]',
                # Beatport-specific selectors
                '[class*="TrackTitle"]', '[class*="ReleaseTitle"]',
                '[class*="Title"]', 'span:contains(".")'
            ]

            for selector in title_selectors:
                title_elem = slide.select_one(selector)
                if title_elem and title_elem.get_text(strip=True):
                    title_text = title_elem.get_text(strip=True)
                    # Filter out common non-title text
                    if title_text not in ['New on Beatport', 'Previous slide', 'Next slide', 'EXCLUSIVE', 'HYPE']:
                        track_data['title'] = title_text
                        break

            # Enhanced artist extraction
            artist_selectors = [
                '[class*="artist"]', '[class*="by"]', '[class*="author"]',
                '[data-testid*="artist"]', '[data-testid*="by"]',
                # Beatport-specific selectors
                '[class*="Artist"]', '[class*="Label"]'
            ]

            for selector in artist_selectors:
                artist_elem = slide.select_one(selector)
                if artist_elem and artist_elem.get_text(strip=True):
                    track_data['artist'] = artist_elem.get_text(strip=True)
                    break

            # Extract any text content for analysis
            all_text = slide.get_text(strip=True)
            if all_text:
                track_data['raw_text'] = all_text[:400]  # More chars for analysis

            # Try to parse title and artist from raw text if not found
            if not track_data.get('title') or not track_data.get('artist'):
                parsed_data = self._parse_title_artist_from_raw_text(all_text)
                if parsed_data.get('title') and not track_data.get('title'):
                    track_data['title'] = parsed_data['title']
                if parsed_data.get('artist') and not track_data.get('artist'):
                    track_data['artist'] = parsed_data['artist']

            # FALLBACK: Extract title from URL slug if still no title/artist found
            if (not track_data.get('title') or not track_data.get('artist')) and track_data.get('url'):
                url_data = self._extract_title_artist_from_url(track_data['url'])
                if url_data.get('title') and not track_data.get('title'):
                    track_data['title'] = url_data['title']
                if url_data.get('artist') and not track_data.get('artist'):
                    track_data['artist'] = url_data.get('artist', 'Various Artists')

            # Apply final cleaning to all extracted data
            if track_data.get('title'):
                track_data['title'] = self.clean_beatport_text(self._clean_title(track_data['title']))
            if track_data.get('artist'):
                track_data['artist'] = self.clean_beatport_text(self._clean_artist(track_data['artist']))

            # Extract all class names for debugging
            classes = slide.get('class', [])
            if classes:
                track_data['element_classes'] = ' '.join(classes)

            # Filter out empty/invalid tracks
            title = track_data.get('title', '').strip()
            artist = track_data.get('artist', '').strip()

            # Skip tracks with no title/artist or generic values
            if (not title or not artist or
                title.lower() in ['no title', 'unknown title', 'unknown', ''] or
                artist.lower() in ['no artist', 'unknown artist', 'unknown', 'various artists', '']):
                print(f"   ❌ {context}: Filtered out invalid track - '{title}' by '{artist}'")
                return None

            # Only return if we found meaningful data
            if track_data.get('url') or track_data.get('image_url'):
                track_data['source'] = f"New on Beatport Hero - {context}"
                track_data['scraped_at'] = time.time()
                print(f"   ✅ {context}: {title} - {artist}")
                return track_data
            else:
                print(f"   ❌ {context}: No usable data found")
                return None

        except Exception as e:
            print(f"   ❌ Error extracting from {context}: {e}")
            return None

    def _extract_title_artist_from_url(self, url: str) -> Dict[str, str]:
        """Extract title and artist from Beatport URL slug as fallback"""
        result = {}

        try:
            # Extract the slug from URL like: https://beatport.com/release/gods-window-pt-1/5291662
            if '/release/' in url:
                parts = url.split('/release/')
                if len(parts) > 1:
                    slug_part = parts[1].split('/')[0]  # Get "gods-window-pt-1"

                    # Convert slug to title (replace hyphens with spaces, title case)
                    title = slug_part.replace('-', ' ').title()

                    # Clean up common patterns
                    title = title.replace(' Pt ', ' Pt. ')
                    title = title.replace(' Ep', ' EP')
                    title = title.replace(' Feat ', ' feat. ')
                    title = title.replace(' Vs ', ' vs. ')
                    title = title.replace(' Remix', ' Remix')

                    result['title'] = title

            elif '/track/' in url:
                parts = url.split('/track/')
                if len(parts) > 1:
                    slug_part = parts[1].split('/')[0]
                    title = slug_part.replace('-', ' ').title()
                    result['title'] = title

        except Exception as e:
            pass  # Silently handle URL extraction errors

        return result

    def _parse_title_artist_from_raw_text(self, raw_text: str) -> Dict[str, str]:
        """Parse title and artist from raw text using patterns"""
        result = {}

        if not raw_text:
            return result

        # Remove common Beatport UI elements
        text = raw_text.replace('New on Beatport', '').replace('Previous slide', '').replace('Next slide', '')
        text = text.replace('EXCLUSIVE', '').replace('HYPE', '').replace('PlayAdd to queueAdd to playlist', '')

        # Pattern 1: Look for track title followed by artist names (common Beatport pattern)
        # Example: "Gods window, Pt. 1Thakzin,Thandazo,Xelimpilo"
        lines = [line.strip() for line in text.split('\n') if line.strip()]

        for i, line in enumerate(lines):
            # Look for lines that might contain title and artists
            if len(line) > 5 and '$' not in line and 'Music' in line:
                # This might be a title line
                # Check if the next part contains artist names
                words = line.split()
                for j in range(1, len(words)):
                    potential_title = ' '.join(words[:j])
                    potential_artists = ' '.join(words[j:])

                    # Check if we have a reasonable title and artist split
                    if (len(potential_title) > 2 and len(potential_artists) > 2 and
                        ',' in potential_artists):  # Artists often comma-separated
                        result['title'] = potential_title
                        result['artist'] = potential_artists.split(',')[0]  # First artist
                        break

                if result.get('title'):
                    break

        # Pattern 2: Look for specific patterns in the text
        patterns = [
            # Pattern: "Title"Artist1,Artist2 (with capital letter start for artist)
            r'([A-Za-z\'\s\(\)][^,]{2,40})([A-Z][a-z][^,]{2,}(?:,[A-Z][^,]+)*)',
            # Pattern: Look for quoted titles
            r'"([^"]+)"([^$]+)',
            r"'([^']+)'([^$]+)",
            # Pattern: Title followed by artist names (looser)
            r'([A-Za-z\'\s\(\)][^,]{2,25})\s+([A-Z][a-z][A-Za-z\s]{2,25})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match and not result.get('title'):
                potential_title = match.group(1).strip()
                potential_artist = match.group(2).strip()

                # Additional validation
                if (len(potential_title) > 2 and len(potential_artist) > 2 and
                    not potential_title.endswith('Music') and
                    not potential_artist.startswith('$')):
                    result['title'] = potential_title
                    result['artist'] = potential_artist.split(',')[0]  # First artist
                    break

        # Pattern 3: Handle concatenated cases like "Come to MeDarius Syrossian"
        if not result.get('title') and not result.get('artist'):
            # Look for cases where title+artist are concatenated
            concatenated_pattern = r'([A-Za-z\'\s\(\)][^A-Z]{3,25})([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'
            match = re.search(concatenated_pattern, text)
            if match:
                potential_title = match.group(1).strip()
                potential_artist = match.group(2).strip()

                # Make sure it looks reasonable
                if (len(potential_title) > 2 and len(potential_artist) > 2 and
                    ' ' in potential_artist and  # Artist should have space (first + last name)
                    not potential_title.endswith('Music')):
                    result['title'] = potential_title
                    result['artist'] = potential_artist

        # Clean up results
        if result.get('title'):
            # Clean title - preserve common music characters
            title = result['title']
            title = re.sub(r'[^\w\s\(\)\-\.\'\&]', ' ', title)
            title = re.sub(r'\s+', ' ', title).strip()
            result['title'] = title

        if result.get('artist'):
            # Clean artist - handle multiple artists and remove label names
            artist = result['artist']

            # Remove common label/publisher suffixes
            label_patterns = [
                r'\s*Music\s*$', r'\s*Records?\s*$', r'\s*Recordings?\s*$',
                r'\s*Entertainment\s*$', r'\s*Productions?\s*$',
                r'\s*Label\s*$', r'elrow\s*Music\s*$',
                r'Happy\s*Techno\s*Music\s*$', r'In\s*It\s*Together\s*Records?\s*$'
            ]

            for pattern in label_patterns:
                artist = re.sub(pattern, '', artist, flags=re.IGNORECASE)

            # Take only the first artist if comma-separated
            if ',' in artist:
                artist = artist.split(',')[0].strip()

            # Clean special characters but preserve common artist name characters
            artist = re.sub(r'[^\w\s\-\.\'\&]', ' ', artist)
            artist = re.sub(r'\s+', ' ', artist).strip()

            # Remove trailing/leading words that don't look like artist names
            words = artist.split()
            cleaned_words = []
            for word in words:
                # Skip words that are clearly not part of artist names
                if word.lower() not in ['music', 'records', 'record', 'entertainment',
                                      'productions', 'production', 'label', 'remix',
                                      'featuring', 'feat', 'ft']:
                    cleaned_words.append(word)
                else:
                    break  # Stop at first label-like word

            if cleaned_words:
                result['artist'] = ' '.join(cleaned_words)
            else:
                result['artist'] = artist  # Fallback to original if all words filtered

        return result

    def _clean_title(self, title: str) -> str:
        """Clean and standardize track title"""
        if not title:
            return title

        # Remove common suffixes that get attached
        title = re.sub(r'(Darius\s+Syrossian.*|Happy\s+Techno.*|Ron\s*$)', '', title, flags=re.IGNORECASE)

        # Clean title - preserve common music characters
        title = re.sub(r'[^\w\s\(\)\-\.\'\&]', ' ', title)
        title = re.sub(r'\s+', ' ', title).strip()

        # Remove trailing words that don't belong in titles
        words = title.split()
        cleaned_words = []
        for word in words:
            # Stop at artist names or label words
            if (word[0].isupper() and len(word) > 2 and
                word.lower() not in ['the', 'of', 'and', 'in', 'on', 'at', 'to', 'for', 'pt']):
                # This might be an artist name starting
                break
            cleaned_words.append(word)

        if cleaned_words:
            return ' '.join(cleaned_words)
        return title

    def _clean_artist(self, artist: str) -> str:
        """Clean and standardize artist name"""
        if not artist:
            return artist

        # Remove common label/publisher suffixes
        label_patterns = [
            r'\s*Music\s*$', r'\s*Records?\s*$', r'\s*Recordings?\s*$',
            r'\s*Entertainment\s*$', r'\s*Productions?\s*$',
            r'\s*Label\s*$', r'elrow\s*Music\s*$',
            r'Happy\s*Techno\s*Music\s*$', r'In\s*It\s*Together\s*Records?\s*$',
            r'Musicelrow\s*Music\s*$', r'Freenzy\s*Musicelrow\s*Music\s*$'
        ]

        for pattern in label_patterns:
            artist = re.sub(pattern, '', artist, flags=re.IGNORECASE)

        # Take only the first artist if comma-separated
        if ',' in artist:
            artist = artist.split(',')[0].strip()

        # Clean special characters but preserve common artist name characters
        artist = re.sub(r'[^\w\s\-\.\'\&]', ' ', artist)
        artist = re.sub(r'\s+', ' ', artist).strip()

        # Remove trailing/leading words that don't look like artist names
        words = artist.split()
        cleaned_words = []
        for word in words:
            # Skip words that are clearly not part of artist names
            if word.lower() not in ['music', 'records', 'record', 'entertainment',
                                  'productions', 'production', 'label', 'remix',
                                  'featuring', 'feat', 'ft', 'musicelrow', 'elrow',
                                  'freenzy', 'happy', 'techno']:
                cleaned_words.append(word)
            else:
                break  # Stop at first label-like word

        if cleaned_words:
            return ' '.join(cleaned_words)
        return artist

    def clean_beatport_text(self, text: str) -> str:
        """Clean Beatport track/artist text for proper spacing"""
        if not text:
            return text

        # Fix common spacing issues
        text = re.sub(r'([a-z$!@#%&*])([A-Z])', r'\1 \2', text)  # Add space between lowercase/symbols and uppercase
        text = re.sub(r'([a-zA-Z]),([a-zA-Z])', r'\1, \2', text)  # Add space after comma
        text = re.sub(r'([a-zA-Z])(Mix|Remix|Extended|Version)\b', r'\1 \2', text)  # Fix mix types
        text = re.sub(r'\s+', ' ', text)  # Collapse multiple spaces
        text = text.strip()

        return text

    def scrape_top_10_releases_homepage(self, limit: int = 10) -> List[Dict]:
        """Scrape Top 10 Releases from homepage - Extract individual tracks using URL crawling"""
        print("\n🔟 Scraping Top 10 Releases from homepage...")

        soup = self.get_page(self.base_url)
        if not soup:
            return []

        # Step 1: Extract release URLs from Top 10 section
        release_items = soup.select('[data-testid="top-10-releases-item"]')
        print(f"   Found {len(release_items)} release items in Top 10 Releases section")

        release_urls = []
        for i, item in enumerate(release_items[:limit]):
            # Extract release URL
            link_elem = item.select_one('a[href*="/release/"]')
            if link_elem and link_elem.get('href'):
                release_url = urljoin(self.base_url, link_elem.get('href'))
                release_urls.append(release_url)
                print(f"   {i+1}. Found Top 10 release URL: {release_url}")

        if not release_urls:
            print("   ❌ No Top 10 release URLs found")
            return []

        # Step 2: Crawl each release URL to extract individual tracks
        all_individual_tracks = []
        for i, release_url in enumerate(release_urls):
            print(f"   Processing Top 10 release {i+1}/{len(release_urls)}: {release_url}")

            # Extract individual tracks from this release
            tracks = self.extract_individual_tracks_from_release_url(release_url, "Top 10 Releases")
            if tracks:
                print(f"   ✅ Found {len(tracks)} individual tracks")
                all_individual_tracks.extend(tracks)
            else:
                print(f"   ❌ No tracks found")

            # Add delay between requests to be respectful
            if i < len(release_urls) - 1:
                time.sleep(0.5)

        print(f"✅ Extracted {len(all_individual_tracks)} individual tracks from {len(release_urls)} Top 10 releases")
        return all_individual_tracks

    def scrape_genre_charts(self, genre: Dict, limit: int = 100) -> List[Dict]:
        """Scrape charts for a specific genre (default: top tracks)"""
        tracks = []

        # First try dedicated top chart page URLs that might have more tracks
        # Based on actual Beatport URL patterns from genre pages
        chart_urls_to_try = [
            f"{self.base_url}/genre/{genre['slug']}/tracks",  # Most likely pattern
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/tracks",
            f"{self.base_url}/genre/{genre['slug']}/top-100",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/top-100",
            f"{self.base_url}/genre/{genre['slug']}/featured",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/featured",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"  # Fallback to main page
        ]

        for chart_url in chart_urls_to_try:
            print(f"   🎯 Trying chart URL: {chart_url}")
            soup = self.get_page(chart_url)
            if soup:
                tracks = self.extract_tracks_from_page(soup, f"{genre['name']} Top 100", limit)
                if tracks and len(tracks) >= min(limit, 50):  # If we got a decent number of tracks
                    print(f"   ✅ Successfully extracted {len(tracks)} tracks from {chart_url}")
                    break
                elif tracks:
                    print(f"   ⚠️ Only found {len(tracks)} tracks at {chart_url}, trying next URL...")
                else:
                    print(f"   ❌ No tracks found at {chart_url}")

        return tracks

    def scrape_genre_top_10(self, genre: Dict) -> List[Dict]:
        """Scrape top 10 tracks for a specific genre"""
        return self.scrape_genre_charts(genre, limit=10)

    def scrape_genre_releases(self, genre: Dict, limit: int = 100) -> List[Dict]:
        """Scrape top releases for a specific genre"""
        releases = []

        # Try dedicated release page URLs that might have more releases
        # Based on the successful tracks pattern (genre/slug/id/top-100)
        release_urls_to_try = [
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/releases/top-100",  # Try this pattern first
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/top-100-releases",  # Alternative
            f"{self.base_url}/genre/{genre['slug']}/releases/top-100",
            f"{self.base_url}/genre/{genre['slug']}/releases",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/releases",
            f"{self.base_url}/genre/{genre['slug']}/top-releases",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/top-releases",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"  # Fallback to main page
        ]

        for release_url in release_urls_to_try:
            print(f"   🎯 Trying release URL: {release_url}")
            soup = self.get_page(release_url)
            if soup:
                # Try to find releases section on the page
                releases = self.extract_releases_from_page(soup, f"{genre['name']} Top Releases", limit)

                # If no releases found with release extraction, try track extraction
                if not releases:
                    print(f"   ⚠️ No releases found with release method, trying track method for {genre['name']}")
                    releases = self.extract_tracks_from_page(soup, f"{genre['name']} Top Releases", limit)
                    # Mark these as releases
                    for release in releases:
                        release['type'] = 'release'

                if releases and len(releases) >= min(limit, 30):  # If we got a decent number of releases
                    print(f"   ✅ Successfully extracted {len(releases)} releases from {release_url}")
                    break
                elif releases:
                    print(f"   ⚠️ Only found {len(releases)} releases at {release_url}, trying next URL...")
                else:
                    print(f"   ❌ No releases found at {release_url}")

        return releases

    def scrape_genre_hype_top_10(self, genre: Dict) -> List[Dict]:
        """Scrape hype top 10 tracks for a specific genre"""
        return self.scrape_genre_hype_charts(genre, limit=10)

    def scrape_genre_hype_charts(self, genre: Dict, limit: int = 100) -> List[Dict]:
        """Scrape hype charts for a specific genre"""
        tracks = []

        # Based on actual Beatport structure, try the correct hype URLs
        hype_urls_to_try = [
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/hype-100",  # Actual hype-100 URL
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/hype-10",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/hype",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"  # Fallback to main page
        ]

        for hype_url in hype_urls_to_try:
            print(f"   🔥 Trying hype URL: {hype_url}")
            soup = self.get_page(hype_url)
            if soup:
                # Use the new dedicated hype extraction method
                tracks = self.extract_hype_tracks_from_beatport_page(soup, f"{genre['name']} Hype Charts", limit)
                if tracks and len(tracks) >= min(limit, 10):  # If we got a decent number of tracks
                    print(f"   ✅ Successfully extracted {len(tracks)} hype tracks from {hype_url}")
                    break
                elif tracks:
                    print(f"   ⚠️ Only found {len(tracks)} hype tracks at {hype_url}, trying next URL...")
                else:
                    print(f"   ❌ No hype tracks found at {hype_url}")

        # If no dedicated hype page found, try main genre page for hype content
        if not tracks:
            print(f"   🔍 No dedicated hype page found, looking for hype content on main page...")
            genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"
            soup = self.get_page(genre_url)
            if soup:
                tracks = self.extract_hype_tracks_from_beatport_page(soup, f"{genre['name']} Hype Charts", limit)

        return tracks

    def scrape_genre_hype_picks(self, genre: Dict, limit: int = 100) -> List[Dict]:
        """Scrape individual tracks from Genre Hype Picks using JSON extraction - ENHANCED (same pattern as Latest Releases)"""
        print(f"\n🔥 Scraping {genre['name']} Hype Picks (individual tracks)...")

        # Step 1: Get release URLs from genre Hype Picks carousel (same logic as Latest Releases)
        release_urls = self.extract_genre_hype_picks_urls(genre, limit)
        if not release_urls:
            return []

        # Step 2: Extract individual tracks from each release (same method as Latest Releases)
        all_tracks = []
        for i, release_url in enumerate(release_urls):
            print(f"\n🔥 Processing {genre['name']} hype pick {i+1}/{len(release_urls)}")
            tracks = self.extract_tracks_from_release_json(release_url)
            if tracks:
                # Update list_name to match genre context
                for track in tracks:
                    track['list_name'] = f"Genre {genre['name']} Hype Picks"
                all_tracks.extend(tracks)

            # Add small delay between requests to be respectful (same as Latest Releases)
            import time
            time.sleep(0.5)

        print(f"✅ Extracted {len(all_tracks)} individual tracks from {len(release_urls)} {genre['name']} hype picks")
        return all_tracks

    def extract_genre_hype_picks_urls(self, genre: Dict, limit: int) -> List[str]:
        """Extract release URLs from Hype Picks carousel on genre page (same pattern as Latest Releases)"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"
        soup = self.get_page(genre_url)
        if not soup:
            return []

        # Find Hype Picks GridSlider container (equivalent to Latest Releases approach)
        gridsliders = soup.select('[class*="GridSlider-style__Wrapper"]')
        hype_container = None

        for container in gridsliders:
            h2 = container.select_one('h2')
            if h2 and 'hype' in h2.get_text().lower() and 'pick' in h2.get_text().lower():
                hype_container = container
                print(f"   Found Hype Picks section: '{h2.get_text().strip()}'")
                break

        if not hype_container:
            print(f"   ❌ Could not find Hype Picks section for {genre['name']}")
            return []

        # Extract release URLs from ALL releases in Hype Picks section (same as Latest Releases)
        release_links = hype_container.select('a[href*="/release/"]')
        print(f"   Found {len(release_links)} release links in Hype Picks section")

        release_urls = []
        seen_urls = set()

        # Process ALL links but stop when we reach the limit of unique URLs (same as Latest Releases)
        for i, link in enumerate(release_links):
            href = link.get('href')
            if href:
                # Ensure full URL (same as Latest Releases)
                if href.startswith('/'):
                    href = self.base_url + href

                # Avoid duplicates (same as Latest Releases logic)
                if href not in seen_urls:
                    release_urls.append(href)
                    seen_urls.add(href)
                    print(f"   {len(release_urls)}. Found hype pick URL: {href}")

                    # Stop when we reach the desired number of unique releases
                    if len(release_urls) >= limit:
                        break

        return release_urls

    def find_hype_section_on_genre_page(self, soup, genre: Dict, limit: int) -> List[Dict]:
        """Find and extract tracks from hype section on main genre page"""
        tracks = []

        # Look for headings containing "hype"
        hype_headings = soup.find_all(['h1', 'h2', 'h3', 'h4'],
                                     string=re.compile(r'hype', re.I))

        for heading in hype_headings:
            print(f"   📝 Found hype heading: {heading.get_text(strip=True)}")

            # Get the section after this heading
            section_container = heading.find_parent()
            if section_container:
                # Look for tracks in the next sibling or current container
                content_areas = [
                    section_container.find_next_sibling(),
                    section_container
                ]

                for content_area in content_areas:
                    if content_area:
                        section_tracks = self.extract_tracks_from_page(
                            content_area, f"{genre['name']} Hype Picks", limit
                        )
                        if section_tracks:
                            tracks.extend(section_tracks)
                            if len(tracks) >= limit:
                                break

                if tracks:
                    break

        return tracks

    def extract_comprehensive_hype_picks(self, soup: BeautifulSoup, list_name: str, limit: int) -> List[Dict]:
        """Extract hype picks using multiple methods to get full 50 tracks"""
        tracks = []

        # Method 1: Get releases from Hype Picks carousel and then get their tracks
        carousel_releases = self.extract_hype_picks_from_carousel(soup, list_name, limit)

        # For each release, try to get individual tracks from it
        for release in carousel_releases:
            if len(tracks) >= limit:
                break

            # Try to get tracks from this release
            release_tracks = self.get_tracks_from_hype_release(release['url'], release['artist'], limit - len(tracks))
            tracks.extend(release_tracks)

        # Method 2: Look for individual HYPE labeled tracks on the page
        if len(tracks) < limit:
            hype_labeled = self.extract_hype_labeled_tracks(soup, list_name, limit - len(tracks))
            # Avoid duplicates
            for track in hype_labeled:
                if not any(existing['url'] == track['url'] for existing in tracks):
                    tracks.append(track)
                    if len(tracks) >= limit:
                        break

        # Method 3: Look for hype picks section specifically
        if len(tracks) < limit:
            section_tracks = self.find_hype_picks_section(soup, list_name, limit - len(tracks))
            for track in section_tracks:
                if not any(existing['url'] == track['url'] for existing in tracks):
                    tracks.append(track)
                    if len(tracks) >= limit:
                        break

        return tracks

    def get_tracks_from_hype_release(self, release_url: str, release_artist: str, limit: int) -> List[Dict]:
        """Get individual tracks from a hype release"""
        tracks = []

        if not release_url:
            return tracks

        try:
            soup = self.get_page(release_url)
            if soup:
                # Look for track listings on release page
                track_items = soup.find_all(class_=re.compile(r'Track.*Item|Lists.*Item'))

                for item in track_items[:limit]:
                    try:
                        # Extract track title
                        title_link = item.find('a', href=re.compile(r'/track/'))
                        if not title_link:
                            continue

                        track_title = title_link.get_text(separator=' ', strip=True)
                        track_url = urljoin(self.base_url, title_link['href'])

                        # Use release artist as fallback
                        artist_container = item.find(class_=re.compile(r'ArtistNames|artist'))
                        if artist_container:
                            artist_links = artist_container.find_all('a', href=re.compile(r'/artist/'))
                            artists = [link.get_text(strip=True) for link in artist_links]
                            artist_text = ', '.join(artists) if artists else release_artist
                        else:
                            artist_text = release_artist

                        track_data = {
                            'position': len(tracks) + 1,
                            'artist': artist_text,
                            'title': track_title,
                            'list_name': "Hype Picks",
                            'url': track_url,
                            'hype_labeled': True
                        }

                        tracks.append(track_data)
                        print(f"   🎵 Release Track: {artist_text} - {track_title}")

                    except Exception:
                        continue

        except Exception:
            pass

        return tracks

    def find_hype_picks_section(self, soup: BeautifulSoup, list_name: str, limit: int) -> List[Dict]:
        """Find hype picks section on page"""
        tracks = []

        # Look for hype picks sections on genre page
        hype_sections = [
            'hype pick', 'hype picks', 'trending pick', 'hot pick',
            'featured hype', 'hype selection'
        ]

        for section_name in hype_sections:
            section_heading = soup.find(['h1', 'h2', 'h3', 'h4'],
                string=re.compile(rf'{section_name}', re.I))

            if section_heading:
                print(f"   📝 Found hype picks section: {section_heading.get_text(strip=True)}")
                section_container = section_heading.find_parent()
                if section_container:
                    content_area = section_container.find_next_sibling()
                    if content_area:
                        section_tracks = self.extract_tracks_from_page(
                            content_area, f"{list_name}", limit
                        )
                        if section_tracks:
                            tracks.extend(section_tracks)
                            if len(tracks) >= limit:
                                break

        return tracks

    def extract_hype_labeled_tracks(self, soup: BeautifulSoup, list_name: str, limit: int = 50) -> List[Dict]:
        """Extract tracks that have HYPE labels or tags on the page"""
        tracks = []

        if not soup:
            return tracks

        print(f"   🔍 Looking for HYPE labeled tracks on page...")

        # Look for elements containing "HYPE" text
        hype_elements = soup.find_all(text=re.compile(r'HYPE', re.I))

        for hype_element in hype_elements[:limit * 2]:  # Check more elements than needed
            if len(tracks) >= limit:
                break

            try:
                # Find the parent container that might contain track info
                parent = hype_element.parent
                track_container = None

                # Walk up the DOM tree to find a suitable container
                for level in range(5):
                    if parent:
                        # Look for track links in this container
                        track_links = parent.find_all('a', href=re.compile(r'/track/'))
                        if track_links:
                            track_container = parent
                            break
                        parent = parent.parent
                    else:
                        break

                if track_container and track_links:
                    # Extract track info from the first track link in this container
                    for link in track_links[:1]:  # Just take the first track from each HYPE container
                        try:
                            raw_title = link.get_text(separator=' ', strip=True)
                            if not raw_title or len(raw_title) < 2:
                                continue

                            # Try to find artist info in the same container
                            artist_text = None

                            # Look for artist links in the same container
                            artist_links = track_container.find_all('a', href=re.compile(r'/artist/'))
                            if artist_links:
                                artist_text = artist_links[0].get_text(strip=True)

                            # If no artist link found, look for text elements that might be artists
                            if not artist_text:
                                text_elements = track_container.find_all(['span', 'div'])
                                for elem in text_elements:
                                    text = elem.get_text(strip=True)
                                    # Heuristic: artist names are typically short and don't contain certain words
                                    if (text and 2 < len(text) < 50 and text != raw_title and
                                        not any(word in text.lower() for word in ['hype', 'track', 'release', 'exclusive', 'beatport', '$'])):
                                        artist_text = text
                                        break

                            # Clean the data
                            cleaned_data = self.clean_artist_track_data(artist_text, raw_title)

                            track_data = {
                                'position': len(tracks) + 1,
                                'artist': cleaned_data['artist'],
                                'title': cleaned_data['title'],
                                'list_name': list_name,
                                'url': urljoin(self.base_url, link['href']),
                                'hype_labeled': True  # Mark as hype track
                            }

                            # Avoid duplicates
                            if not any(existing['url'] == track_data['url'] for existing in tracks):
                                tracks.append(track_data)
                                print(f"   🔥 Found HYPE track: {track_data['artist']} - {track_data['title']}")

                        except Exception as e:
                            continue

            except Exception as e:
                continue

        print(f"   ✅ Extracted {len(tracks)} HYPE labeled tracks")
        return tracks

    def extract_hype_tracks_from_beatport_page(self, soup: BeautifulSoup, list_name: str, limit: int = 100) -> List[Dict]:
        """Extract hype tracks from Beatport page using actual HTML structure"""
        tracks = []

        if not soup:
            return tracks

        print(f"   🔍 Extracting hype tracks from Beatport page...")

        # Method 1: Extract from Hype Picks carousel (release cards with HYPE badges)
        hype_picks_tracks = self.extract_hype_picks_from_carousel(soup, list_name, limit)
        tracks.extend(hype_picks_tracks)

        # Method 2: Extract from Hype Top 10 list format
        if len(tracks) < limit:
            hype_list_tracks = self.extract_hype_from_track_list(soup, list_name, limit - len(tracks))
            tracks.extend(hype_list_tracks)

        # Method 3: Extract from Hype Top 100 table format
        if len(tracks) < limit:
            hype_table_tracks = self.extract_hype_from_track_table(soup, list_name, limit - len(tracks))
            tracks.extend(hype_table_tracks)

        print(f"   ✅ Extracted {len(tracks)} hype tracks using actual Beatport structure")
        return tracks[:limit]

    def extract_hype_picks_from_carousel(self, soup: BeautifulSoup, list_name: str, limit: int) -> List[Dict]:
        """Extract hype picks from carousel format (release cards with HYPE badges)"""
        tracks = []

        # Look for release cards with HYPE badges in carousel
        hype_badges = soup.find_all('div', text='HYPE')

        for badge in hype_badges[:limit]:
            try:
                # Find the release card container
                release_card = badge.find_parent(class_=re.compile(r'ReleaseCard.*Wrapper'))
                if not release_card:
                    continue

                # Extract release title
                release_title_elem = release_card.find(class_=re.compile(r'ReleaseName'))
                if not release_title_elem:
                    continue

                release_title = release_title_elem.get_text(strip=True)

                # Extract artists from ArtistNames container
                artist_container = release_card.find(class_=re.compile(r'ArtistNames'))
                artists = []
                if artist_container:
                    artist_links = artist_container.find_all('a', href=re.compile(r'/artist/'))
                    artists = [link.get_text(strip=True) for link in artist_links]

                artist_text = ', '.join(artists) if artists else 'Unknown Artist'

                # Get release URL
                release_link = release_card.find('a', href=re.compile(r'/release/'))
                release_url = urljoin(self.base_url, release_link['href']) if release_link else ''

                track_data = {
                    'position': len(tracks) + 1,
                    'artist': artist_text,
                    'title': release_title,
                    'list_name': f"{list_name} - Hype Picks",
                    'url': release_url,
                    'hype_labeled': True
                }

                tracks.append(track_data)
                print(f"   🔥 Hype Pick: {artist_text} - {release_title}")

            except Exception as e:
                continue

        return tracks

    def extract_hype_from_track_list(self, soup: BeautifulSoup, list_name: str, limit: int) -> List[Dict]:
        """Extract hype tracks from track list format (Lists-shared-style__Item containers)"""
        tracks = []

        # Look for track list items in the format shown in example
        track_items = soup.find_all(class_=re.compile(r'Lists-shared-style__Item'))

        for i, item in enumerate(track_items[:limit]):
            try:
                # Extract track number
                track_number_elem = item.find(class_=re.compile(r'ItemNumber'))
                position = track_number_elem.get_text(strip=True) if track_number_elem else str(i + 1)

                # Extract track title
                title_link = item.find('a', href=re.compile(r'/track/'))
                if not title_link:
                    continue

                title_elem = title_link.find(class_=re.compile(r'ItemName'))
                if not title_elem:
                    title_elem = title_link

                track_title = title_elem.get_text(separator=' ', strip=True)

                # Extract artists
                artist_container = item.find(class_=re.compile(r'ArtistNames'))
                artists = []
                if artist_container:
                    artist_links = artist_container.find_all('a', href=re.compile(r'/artist/'))
                    artists = [link.get_text(strip=True) for link in artist_links]

                artist_text = ', '.join(artists) if artists else 'Unknown Artist'

                # Get track URL
                track_url = urljoin(self.base_url, title_link['href']) if title_link else ''

                track_data = {
                    'position': position,
                    'artist': artist_text,
                    'title': track_title,
                    'list_name': f"{list_name} - Hype Top 10",
                    'url': track_url,
                    'hype_labeled': True
                }

                tracks.append(track_data)
                print(f"   🎵 Hype Track {position}: {artist_text} - {track_title}")

            except Exception as e:
                continue

        return tracks

    def extract_hype_from_track_table(self, soup: BeautifulSoup, list_name: str, limit: int) -> List[Dict]:
        """Extract hype tracks from table format (Table-style__TableRow containers)"""
        tracks = []

        # Look for table rows in the format shown in example
        table_rows = soup.find_all(class_=re.compile(r'Table-style__TableRow'))

        for i, row in enumerate(table_rows[:limit]):
            try:
                # Skip header rows
                if row.get('role') == 'columnheader':
                    continue

                # Extract track number from artwork container
                track_no_elem = row.find(class_=re.compile(r'TrackNo'))
                position = track_no_elem.get_text(strip=True) if track_no_elem else str(i + 1)

                # Extract track title
                title_link = row.find('a', href=re.compile(r'/track/'))
                if not title_link:
                    continue

                title_elem = title_link.find(class_=re.compile(r'ReleaseName'))
                if not title_elem:
                    title_elem = title_link

                track_title = title_elem.get_text(separator=' ', strip=True)

                # Extract artists
                artist_container = row.find(class_=re.compile(r'ArtistNames'))
                artists = []
                if artist_container:
                    artist_links = artist_container.find_all('a', href=re.compile(r'/artist/'))
                    artists = [link.get_text(strip=True) for link in artist_links]

                artist_text = ', '.join(artists) if artists else 'Unknown Artist'

                # Get track URL
                track_url = urljoin(self.base_url, title_link['href']) if title_link else ''

                track_data = {
                    'position': position,
                    'artist': artist_text,
                    'title': track_title,
                    'list_name': f"{list_name} - Hype Top 100",
                    'url': track_url,
                    'hype_labeled': True
                }

                tracks.append(track_data)
                print(f"   📊 Hype Track {position}: {artist_text} - {track_title}")

            except Exception as e:
                continue

        return tracks

    def scrape_genre_staff_picks(self, genre: Dict, limit: int = 100) -> List[Dict]:
        """Scrape individual tracks from Genre Staff Picks using JSON extraction - ENHANCED (same pattern as Latest Releases)"""
        print(f"\n📝 Scraping {genre['name']} Staff Picks (individual tracks)...")

        # Step 1: Get release URLs from genre Staff Picks carousel (same logic as Latest Releases)
        release_urls = self.extract_genre_staff_picks_urls(genre, limit)
        if not release_urls:
            return []

        # Step 2: Extract individual tracks from each release (same method as Latest Releases)
        all_tracks = []
        for i, release_url in enumerate(release_urls):
            print(f"\n📝 Processing {genre['name']} staff pick {i+1}/{len(release_urls)}")
            tracks = self.extract_tracks_from_release_json(release_url)
            if tracks:
                # Update list_name to match genre context
                for track in tracks:
                    track['list_name'] = f"Genre {genre['name']} Staff Picks"
                all_tracks.extend(tracks)

            # Add small delay between requests to be respectful (same as Latest Releases)
            import time
            time.sleep(0.5)

        print(f"✅ Extracted {len(all_tracks)} individual tracks from {len(release_urls)} {genre['name']} staff picks")
        return all_tracks

    def extract_genre_staff_picks_urls(self, genre: Dict, limit: int) -> List[str]:
        """Extract release URLs from Staff Picks carousel on genre page (same pattern as Latest Releases)"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"
        soup = self.get_page(genre_url)
        if not soup:
            return []

        # Find Staff Picks GridSlider container (equivalent to Latest Releases approach)
        gridsliders = soup.select('[class*="GridSlider-style__Wrapper"]')
        staff_container = None

        for container in gridsliders:
            h2 = container.select_one('h2')
            if h2 and 'staff' in h2.get_text().lower() and 'pick' in h2.get_text().lower():
                staff_container = container
                print(f"   Found Staff Picks section: '{h2.get_text().strip()}'")
                break

        if not staff_container:
            print(f"   ❌ Could not find Staff Picks section for {genre['name']}")
            return []

        # Extract release URLs from ALL releases in Staff Picks section (same as Latest Releases)
        release_links = staff_container.select('a[href*="/release/"]')
        print(f"   Found {len(release_links)} release links in Staff Picks section")

        release_urls = []
        seen_urls = set()

        # Process ALL links but stop when we reach the limit of unique URLs (same as Latest Releases)
        for i, link in enumerate(release_links):
            href = link.get('href')
            if href:
                # Ensure full URL (same as Latest Releases)
                if href.startswith('/'):
                    href = self.base_url + href

                # Avoid duplicates (same as Latest Releases logic)
                if href not in seen_urls:
                    release_urls.append(href)
                    seen_urls.add(href)
                    print(f"   {len(release_urls)}. Found staff pick URL: {href}")

                    # Stop when we reach the desired number of unique releases
                    if len(release_urls) >= limit:
                        break

        return release_urls

    def scrape_genre_latest_releases(self, genre: Dict, limit: int = 100) -> List[Dict]:
        """Scrape individual tracks from Genre Latest Releases using JSON extraction - ENHANCED (same pattern as homepage)"""
        print(f"\n🆕 Scraping {genre['name']} Latest Releases (individual tracks)...")

        # Step 1: Get release URLs from genre Latest Releases carousel (same logic as homepage)
        release_urls = self.extract_genre_latest_releases_urls(genre, limit)
        if not release_urls:
            return []

        # Step 2: Extract individual tracks from each release (same method as homepage)
        all_tracks = []
        for i, release_url in enumerate(release_urls):
            print(f"\n📀 Processing {genre['name']} latest release {i+1}/{len(release_urls)}")
            tracks = self.extract_tracks_from_release_json(release_url)
            if tracks:
                # Update list_name to match genre context
                for track in tracks:
                    track['list_name'] = f"Genre {genre['name']} Latest"
                all_tracks.extend(tracks)

            # Add small delay between requests to be respectful (same as homepage)
            import time
            time.sleep(0.5)

        print(f"✅ Extracted {len(all_tracks)} individual tracks from {len(release_urls)} latest {genre['name']} releases")
        return all_tracks

    def extract_genre_latest_releases_urls(self, genre: Dict, limit: int) -> List[str]:
        """Extract release URLs from Latest Releases carousel on genre page (same pattern as homepage)"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"
        soup = self.get_page(genre_url)
        if not soup:
            return []

        # Find Latest Releases GridSlider container (equivalent to homepage's data-testid="new-releases")
        gridsliders = soup.select('[class*="GridSlider-style__Wrapper"]')
        latest_container = None

        for container in gridsliders:
            h2 = container.select_one('h2')
            if h2 and 'latest' in h2.get_text().lower() and 'release' in h2.get_text().lower():
                latest_container = container
                print(f"   Found Latest Releases section: '{h2.get_text().strip()}'")
                break

        if not latest_container:
            print(f"   ❌ Could not find Latest Releases section for {genre['name']}")
            return []

        # Extract release URLs from ALL releases in Latest Releases section (same as homepage gets all cards)
        release_links = latest_container.select('a[href*="/release/"]')
        print(f"   Found {len(release_links)} release links in Latest Releases section")

        release_urls = []
        seen_urls = set()

        # Process ALL links but stop when we reach the limit of unique URLs (same as homepage)
        for i, link in enumerate(release_links):
            href = link.get('href')
            if href:
                # Ensure full URL (same as homepage)
                if href.startswith('/'):
                    href = self.base_url + href

                # Avoid duplicates (same as homepage logic)
                if href not in seen_urls:
                    release_urls.append(href)
                    seen_urls.add(href)
                    print(f"   {len(release_urls)}. Found latest release URL: {href}")

                    # Stop when we reach the desired number of unique releases
                    if len(release_urls) >= limit:
                        break

        return release_urls

    def scrape_genre_new_charts(self, genre: Dict, limit: int = 100) -> List[Dict]:
        """Scrape NEW CHARTS COLLECTION - Returns list of charts, not individual tracks"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"

        soup = self.get_page(genre_url)
        if not soup:
            return []

        charts = []
        chart_links = soup.find_all('a', href=re.compile(r'/chart/'))

        print(f"   🔍 Found {len(chart_links)} chart links on genre page")

        for chart_link in chart_links[:limit]:
            chart_name = chart_link.get_text(strip=True)
            chart_href = chart_link.get('href', '')

            if chart_name and chart_href and len(chart_name) > 3:
                # Create chart metadata entry (not individual tracks)
                chart_info = {
                    'position': len(charts) + 1,
                    'artist': 'Various Artists',  # Charts are compilations
                    'title': chart_name,
                    'list_name': f"{genre['name']} New Charts",
                    'url': urljoin(self.base_url, chart_href),
                    'chart_name': chart_name,
                    'chart_type': 'new_chart',
                    'genre': genre['name']
                }
                charts.append(chart_info)

                print(f"   📊 Chart {len(charts)}: {chart_name}")

        print(f"   ✅ Found {len(charts)} charts in New Charts Collection")
        return charts[:limit]

    def extract_tracks_from_chart(self, chart_url: str, chart_name: str, limit: int) -> List[Dict]:
        """Extract individual tracks from a chart page - OPTIMIZED FOR CHART PAGES"""
        tracks = []

        try:
            soup = self.get_page(chart_url)
            if not soup:
                return tracks

            print(f"   🔍 Extracting tracks from chart page: {chart_url}")
            print(f"   📋 Chart name: {chart_name}")

            # DEBUG: Check page title to confirm we're on the right page
            page_title = soup.find('title')
            if page_title:
                print(f"   📄 Page title: {page_title.get_text(strip=True)}")

            # DEBUG: Look for the chart title on the page
            chart_title_elem = soup.find(['h1', 'h2'], string=re.compile(chart_name.split(':')[0], re.I))
            if chart_title_elem:
                print(f"   ✅ Found chart title on page: {chart_title_elem.get_text(strip=True)}")
            else:
                print(f"   ⚠️ Chart title '{chart_name}' not found on page")

            # Method 1: Try chart-specific table extraction first (most reliable for chart pages)
            tracks = self.extract_tracks_from_chart_table(soup, chart_name, limit)

            if len(tracks) >= 10:
                print(f"   ✅ Chart table extraction found {len(tracks)} tracks")
                return tracks

            # Method 2: Fallback to general page extraction
            print(f"   ⚠️ Chart table extraction found {len(tracks)} tracks, trying general extraction...")
            general_tracks = self.extract_tracks_from_page(soup, f"New Chart: {chart_name}", limit)

            if len(general_tracks) > len(tracks):
                tracks = general_tracks
                print(f"   ✅ General extraction found {len(tracks)} tracks")

            # Method 3: Last resort - generic table extraction
            if len(tracks) < 10:
                print(f"   ⚠️ Still low track count, trying generic table extraction...")
                table_tracks = self.extract_tracks_from_table_format(soup, chart_name, limit)
                if len(table_tracks) > len(tracks):
                    tracks = table_tracks
                    print(f"   ✅ Generic table extraction found {len(tracks)} tracks")

            print(f"   📊 Final result: {len(tracks)} tracks extracted from {chart_name}")
            return tracks

        except Exception as e:
            print(f"   ❌ Error extracting tracks from chart {chart_name}: {e}")
            return []

    def extract_tracks_from_chart_table(self, soup, chart_name: str, limit: int) -> List[Dict]:
        """Extract tracks from Beatport chart table structure (tracks-table class)"""
        tracks = []

        print(f"   🔍 DEBUG: Looking for tracks-table container...")

        # Look for the tracks table container
        tracks_table = soup.find(class_=re.compile(r'tracks-table'))
        if not tracks_table:
            print(f"   ⚠️ No tracks-table container found")
            # Debug: Let's see what table classes ARE available
            all_tables = soup.find_all(['table', 'div'], class_=re.compile(r'table|Table', re.I))
            print(f"   🔍 DEBUG: Found {len(all_tables)} table-like elements")
            for i, table in enumerate(all_tables[:5]):
                classes = table.get('class', [])
                print(f"      Table {i+1}: {' '.join(classes)}")
            return tracks

        print(f"   ✅ Found tracks-table container with classes: {tracks_table.get('class', [])}")

        # Find all track rows using data-testid or table row classes
        track_rows_testid = tracks_table.find_all(['div', 'tr'], attrs={'data-testid': 'tracks-table-row'})
        track_rows_class = tracks_table.find_all(class_=re.compile(r'Table.*Row.*tracks-table'))
        track_rows_generic = tracks_table.find_all(class_=re.compile(r'Table.*Row'))

        print(f"   🔍 DEBUG: Track rows found:")
        print(f"      - By data-testid='tracks-table-row': {len(track_rows_testid)}")
        print(f"      - By class pattern 'Table.*Row.*tracks-table': {len(track_rows_class)}")
        print(f"      - By generic 'Table.*Row': {len(track_rows_generic)}")

        # Use the best available option
        track_rows = track_rows_testid or track_rows_class or track_rows_generic

        if not track_rows:
            print(f"   ❌ No track rows found in any format")
            return tracks

        print(f"   🔍 Using {len(track_rows)} track rows for extraction")

        for i, row in enumerate(track_rows[:limit]):
            try:
                # Skip header rows
                if row.get('role') == 'columnheader':
                    continue

                # Find track title link - look for the specific structure
                title_cell = row.find(class_=re.compile(r'cell.*title|title.*cell'))
                if not title_cell:
                    # Fallback: look for any cell with track links
                    title_cell = row

                track_link = title_cell.find('a', href=re.compile(r'/track/'))
                if not track_link:
                    continue

                # Extract track title from the ReleaseName span or link text
                title_span = track_link.find(class_=re.compile(r'ReleaseName'))
                if title_span:
                    track_title = title_span.get_text(separator=' ', strip=True)
                else:
                    track_title = track_link.get_text(separator=' ', strip=True)

                track_url = urljoin(self.base_url, track_link['href'])

                # Extract artists from ArtistNames container
                artists = []
                artist_container = row.find(class_=re.compile(r'ArtistNames'))
                if artist_container:
                    artist_links = artist_container.find_all('a', href=re.compile(r'/artist/'))
                    artists = [link.get_text(strip=True) for link in artist_links]

                artist_text = ', '.join(artists) if artists else 'Unknown Artist'

                # DEBUG: Print track details for first few
                if len(tracks) < 3:
                    print(f"   🔍 DEBUG Track {len(tracks)+1}:")
                    print(f"      Title: '{track_title}'")
                    print(f"      Artist: '{artist_text}'")
                    print(f"      URL: {track_url}")
                    print(f"      Track link href: {track_link.get('href', 'NO HREF')}")

                # Extract track number if available
                track_no_elem = row.find(class_=re.compile(r'TrackNo'))
                position = track_no_elem.get_text(strip=True) if track_no_elem else str(len(tracks) + 1)

                track_data = {
                    'position': position,
                    'artist': artist_text,
                    'title': track_title,
                    'list_name': f"Chart: {chart_name}",
                    'url': track_url,
                    'chart_source': chart_name
                }

                tracks.append(track_data)

                # Debug output for first few tracks
                if len(tracks) <= 5:
                    print(f"   🎵 Track {len(tracks)}: {artist_text} - {track_title}")

            except Exception as e:
                print(f"   ⚠️ Error parsing track row {i+1}: {e}")
                continue

        print(f"   ✅ Chart table extraction completed: {len(tracks)} tracks found")
        return tracks

    def extract_tracks_from_table_format(self, soup, chart_name: str, limit: int) -> List[Dict]:
        """Extract tracks from table format (for charts that use table layout)"""
        tracks = []

        # Look for table rows containing track data
        table_rows = soup.find_all('tr') + soup.find_all('div', class_=re.compile(r'Table.*Row|track.*row', re.I))

        print(f"   🔍 Found {len(table_rows)} potential table rows")

        for i, row in enumerate(table_rows[:limit]):
            try:
                # Skip header rows
                if row.name == 'tr' and row.find('th'):
                    continue

                # Look for track links
                track_links = row.find_all('a', href=re.compile(r'/track/'))
                if not track_links:
                    continue

                track_link = track_links[0]
                track_title = track_link.get_text(separator=' ', strip=True)
                track_url = urljoin(self.base_url, track_link['href'])

                # Look for artist information
                artist_text = 'Unknown Artist'

                # Try multiple methods to find artist
                artist_links = row.find_all('a', href=re.compile(r'/artist/'))
                if artist_links:
                    artists = [link.get_text(strip=True) for link in artist_links]
                    artist_text = ', '.join(artists)

                track_data = {
                    'position': len(tracks) + 1,
                    'artist': artist_text,
                    'title': track_title,
                    'list_name': f"New Chart: {chart_name}",
                    'url': track_url,
                    'chart_source': chart_name
                }

                tracks.append(track_data)

                if len(tracks) <= 3:  # Debug first few
                    print(f"   🎵 Track {len(tracks)}: {artist_text} - {track_title}")

            except Exception as e:
                continue

        return tracks

    def discover_genre_page_sections(self, genre: Dict) -> Dict:
        """Analyze a genre page to discover all available sections"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"

        print(f"🔍 Discovering sections for {genre['name']} genre page...")

        soup = self.get_page(genre_url)
        if not soup:
            return {}

        sections = {
            'top_tracks': [],
            'top_releases': [],
            'staff_picks': [],
            'latest_releases': [],
            'new_charts': [],
            'other_sections': []
        }

        # Find all section headings
        headings = soup.find_all(['h1', 'h2', 'h3', 'h4'])

        for heading in headings:
            text = heading.get_text(strip=True).lower()

            if any(keyword in text for keyword in ['top 100', 'top 10', 'chart']):
                sections['top_tracks'].append(heading.get_text(strip=True))
            elif any(keyword in text for keyword in ['release', 'album', 'ep']):
                sections['top_releases'].append(heading.get_text(strip=True))
            elif any(keyword in text for keyword in ['staff', 'editor', 'pick', 'featured']):
                sections['staff_picks'].append(heading.get_text(strip=True))
            elif any(keyword in text for keyword in ['latest', 'new', 'recent']):
                sections['latest_releases'].append(heading.get_text(strip=True))
            elif 'chart' in text:
                sections['new_charts'].append(heading.get_text(strip=True))
            else:
                sections['other_sections'].append(heading.get_text(strip=True))

        # Count DJ/artist charts
        chart_links = soup.find_all('a', href=re.compile(r'/chart/'))
        sections['chart_count'] = len(chart_links)

        print(f"✅ Discovered sections for {genre['name']}:")
        for section_type, items in sections.items():
            if items and section_type != 'chart_count':
                print(f"   • {section_type}: {len(items)} sections")
        print(f"   • Individual charts found: {sections['chart_count']}")

        return sections

    def scrape_all_genres(self, tracks_per_genre: int = 100, max_workers: int = 5, include_images: bool = False) -> Dict[str, List[Dict]]:
        """Scrape all genres in parallel"""
        # Discover genres dynamically if not already done
        if not self.all_genres:
            self.all_genres = self.discover_genres_with_images(include_images=include_images)

        print(f"\n🎵 Scraping {len(self.all_genres)} genres...")

        all_results = {}
        completed = 0

        def scrape_single_genre(genre):
            nonlocal completed

            print(f"🎯 Scraping {genre['name']}...")
            tracks = self.scrape_genre_charts(genre, tracks_per_genre)

            with self.results_lock:
                if tracks:  # Only store genres that have tracks
                    all_results[genre['name']] = tracks
                completed += 1
                print(f"✅ {genre['name']}: {len(tracks)} tracks ({completed}/{len(self.all_genres)} complete)")

            return genre['name'], tracks

        # Use ThreadPoolExecutor for parallel processing
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all genre scraping tasks
            future_to_genre = {executor.submit(scrape_single_genre, genre): genre for genre in self.all_genres}

            # Wait for completion
            for future in concurrent.futures.as_completed(future_to_genre):
                genre = future_to_genre[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"❌ Error processing {genre['name']}: {e}")

        return all_results

    def test_data_quality(self, tracks: List[Dict]) -> Dict:
        """Test the quality of extracted data"""
        if not tracks:
            return {'quality_score': 0, 'issues': ['No tracks found']}

        issues = []
        valid_tracks = 0

        for track in tracks:
            if track.get('artist') and track.get('title'):
                if track['artist'] != 'Unknown Artist' and track['title'] != 'Unknown Title':
                    valid_tracks += 1
            else:
                issues.append(f"Missing data in track {track.get('position', '?')}")

        quality_score = (valid_tracks / len(tracks)) * 100 if tracks else 0

        return {
            'quality_score': quality_score,
            'total_tracks': len(tracks),
            'valid_tracks': valid_tracks,
            'issues': issues[:5]  # Show first 5 issues
        }


def test_dynamic_genre_discovery():
    """Test the dynamic genre discovery functionality"""
    print("🚀 Dynamic Genre Discovery Test")
    print("=" * 80)

    scraper = BeatportUnifiedScraper()

    # Test genre discovery
    print("\n🔍 TEST 1: Genre Discovery")
    genres = scraper.discover_genres_from_homepage()

    print(f"\n✅ Discovered {len(genres)} genres:")
    for i, genre in enumerate(genres[:10]):  # Show first 10
        print(f"   {i+1:2}. {genre['name']} -> {genre['slug']} (ID: {genre['id']})")
        if 'url' in genre:
            print(f"       URL: {genre['url']}")

    if len(genres) > 10:
        print(f"   ... and {len(genres) - 10} more genres")

    # Test with images (limit to 3 for demo)
    print("\n📷 TEST 2: Genre Discovery with Images (Sample)")
    genres_with_images = scraper.discover_genres_with_images(include_images=True)

    print(f"\n🖼️ Sample genres with images:")
    for genre in genres_with_images[:3]:
        print(f"   • {genre['name']}: {genre.get('image_url', 'No image')}")

    # Test a few genre scrapes
    print("\n🎵 TEST 3: Sample Genre Chart Scraping")
    sample_genres = genres[:3]

    for genre in sample_genres:
        print(f"\n🎯 Testing {genre['name']}...")
        tracks = scraper.scrape_genre_charts(genre, limit=3)
        if tracks:
            print(f"   ✅ Found {len(tracks)} tracks:")
            for track in tracks:
                print(f"      • {track['artist']} - {track['title']}")
        else:
            print(f"   ❌ No tracks found")

    return genres

def test_improved_chart_sections():
    """Test the improved chart section discovery and scraping"""
    print("🚀 Testing Improved Chart Section Discovery & Scraping")
    print("=" * 80)

    scraper = BeatportUnifiedScraper()

    # Test 1: Chart Section Discovery
    print("\n🔍 TEST 1: Chart Section Discovery")
    chart_discovery = scraper.discover_chart_sections()

    print(f"\n📊 Discovery Results:")
    summary = chart_discovery.get('summary', {})
    print(f"   • Top Charts sections: {summary.get('top_charts_sections', 0)}")
    print(f"   • Staff Picks sections: {summary.get('staff_picks_sections', 0)}")
    print(f"   • Other sections: {summary.get('other_sections', 0)}")
    print(f"   • Main chart links: {summary.get('main_chart_links', 0)}")
    print(f"   • Individual DJ charts: {summary.get('individual_dj_charts', 0)}")

    # Test 2: New/Improved Scraping Methods
    print("\n🔥 TEST 2: Improved Chart Scraping Methods")

    # Test Hype Top 100 (fixed URL)
    print("\n2a. Testing Hype Top 100 (fixed URL)...")
    hype_tracks = scraper.scrape_hype_top_100(limit=5)
    if hype_tracks:
        print(f"   ✅ Found {len(hype_tracks)} tracks:")
        for track in hype_tracks[:3]:
            print(f"      • {track['artist']} - {track['title']}")
    else:
        print("   ❌ No tracks found")

    # Test Top 100 Releases (new method)
    print("\n2b. Testing Top 100 Releases (new method)...")
    releases_tracks = scraper.scrape_top_100_releases(limit=5)
    if releases_tracks:
        print(f"   ✅ Found {len(releases_tracks)} tracks:")
        for track in releases_tracks[:3]:
            print(f"      • {track['artist']} - {track['title']}")
    else:
        print("   ❌ No tracks found")

    # Test Improved New Releases
    print("\n2c. Testing Improved New Releases...")
    new_releases = scraper.scrape_new_releases(limit=5)
    if new_releases:
        print(f"   ✅ Found {len(new_releases)} tracks:")
        for track in new_releases[:3]:
            print(f"      • {track['artist']} - {track['title']}")
    else:
        print("   ❌ No tracks found")

    # Test Improved DJ Charts
    print("\n2d. Testing Improved DJ Charts...")
    dj_charts = scraper.scrape_dj_charts(limit=5)
    if dj_charts:
        print(f"   ✅ Found {len(dj_charts)} charts:")
        for chart in dj_charts[:3]:
            print(f"      • {chart['title']} by {chart['artist']}")
    else:
        print("   ❌ No charts found")

    # Test Improved Featured Charts
    print("\n2e. Testing Improved Featured Charts...")
    featured_charts = scraper.scrape_featured_charts(limit=5)
    if featured_charts:
        print(f"   ✅ Found {len(featured_charts)} items:")
        for item in featured_charts[:3]:
            print(f"      • {item['title']} by {item['artist']}")
    else:
        print("   ❌ No items found")

    return {
        'chart_discovery': chart_discovery,
        'hype_top_100': hype_tracks,
        'top_100_releases': releases_tracks,
        'new_releases': new_releases,
        'dj_charts': dj_charts,
        'featured_charts': featured_charts
    }

def main():
    """Test the unified Beatport scraper"""
    print("🚀 Beatport Unified Scraper - Improved Chart Discovery")
    print("=" * 80)

    scraper = BeatportUnifiedScraper()

    # Test New on Beatport Hero first
    print("\n🎯 NEW ON BEATPORT HERO TEST")
    hero_tracks = scraper.scrape_new_on_beatport_hero(limit=10)
    if hero_tracks:
        print(f"✅ Successfully extracted {len(hero_tracks)} tracks from hero slideshow")
        for i, track in enumerate(hero_tracks[:3]):  # Show first 3
            print(f"   {i+1}. {track.get('title', 'No title')} - {track.get('artist', 'No artist')}")
            print(f"      URL: {track.get('url', 'No URL')}")
            print(f"      Classes: {track.get('element_classes', 'No classes')}")
    else:
        print("❌ No tracks found in hero slideshow")

    # Test improved chart sections
    print("\n🆕 IMPROVED CHART SECTIONS TEST")
    improved_results = test_improved_chart_sections()

    # Test dynamic genre discovery (existing)
    print("\n\n🆕 DYNAMIC GENRE DISCOVERY TEST")
    discovered_genres = test_dynamic_genre_discovery()

    # Update scraper with discovered genres
    scraper.all_genres = discovered_genres

    # Test 1: Top 100
    print("\n📊 TEST 1: Top 100 Chart")
    top_100 = scraper.scrape_top_100(limit=10)  # Test with 10 for now

    if top_100:
        print(f"\n✅ Top 100 Sample (showing first 5):")
        for track in top_100[:5]:
            print(f"   {track['position']}. {track['artist']} - {track['title']}")

        quality = scraper.test_data_quality(top_100)
        print(f"\n📈 Data Quality: {quality['quality_score']:.1f}% ({quality['valid_tracks']}/{quality['total_tracks']} tracks)")
    else:
        print("❌ Failed to extract Top 100")

    # Test 2: Sample of discovered genres
    print("\n🎵 TEST 2: Dynamic Genre Charts Sample")
    test_genres = scraper.all_genres[:5]  # Test first 5 discovered genres

    print(f"Testing {len(test_genres)} dynamically discovered genres...")

    genre_results = {}
    for genre in test_genres:
        tracks = scraper.scrape_genre_charts(genre, limit=5)  # 5 tracks per genre for testing
        if tracks:
            genre_results[genre['name']] = tracks
            print(f"\n🎯 {genre['name']} Top 5:")
            for track in tracks[:3]:
                print(f"   • {track['artist']} - {track['title']}")

    # Test 3: Full genre scraping (smaller sample)
    print("\n🚀 TEST 3: Full Multi-Genre Scraping")
    print("Testing parallel scraping of 10 genres...")

    sample_genres = scraper.all_genres[:10]
    scraper.all_genres = sample_genres  # Temporarily limit for testing

    all_genre_results = scraper.scrape_all_genres(tracks_per_genre=5, max_workers=3)

    # Results summary
    print("\n" + "=" * 80)
    print("📋 FINAL RESULTS SUMMARY")
    print("=" * 80)

    total_tracks = len(top_100) if top_100 else 0
    total_genres = len(all_genre_results)
    total_genre_tracks = sum(len(tracks) for tracks in all_genre_results.values())

    print(f"• Top 100 tracks extracted: {total_tracks}")
    print(f"• Genres successfully scraped: {total_genres}")
    print(f"• Total genre tracks: {total_genre_tracks}")
    print(f"• Grand total tracks: {total_tracks + total_genre_tracks}")

    # Data quality assessment
    all_tracks = (top_100 or []) + [track for tracks in all_genre_results.values() for track in tracks]
    if all_tracks:
        overall_quality = scraper.test_data_quality(all_tracks)
        print(f"\n📊 OVERALL DATA QUALITY")
        print(f"• Quality Score: {overall_quality['quality_score']:.1f}%")
        print(f"• Valid Tracks: {overall_quality['valid_tracks']}/{overall_quality['total_tracks']}")

        if overall_quality['issues']:
            print(f"• Issues Found: {len(overall_quality['issues'])}")

    # Save results
    results = {
        'top_100': top_100,
        'genre_charts': all_genre_results,
        'available_genres': [genre['name'] for genre in scraper.all_genres],
        'summary': {
            'total_genres_available': len(scraper.all_genres),
            'genres_tested': total_genres,
            'total_tracks_extracted': total_tracks + total_genre_tracks,
            'data_quality_score': overall_quality['quality_score'] if all_tracks else 0
        }
    }

    try:
        with open('beatport_unified_results.json', 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Results saved to beatport_unified_results.json")
    except Exception as e:
        print(f"❌ Failed to save results: {e}")

    # Virtual playlist possibilities
    if overall_quality['quality_score'] > 70:
        print(f"\n🎉 SUCCESS! Ready for virtual playlist creation")
        print(f"📱 You can now create playlists for:")
        print(f"   • Beatport Top 100")
        for genre_name in list(all_genre_results.keys())[:5]:
            print(f"   • {genre_name} Top 100")
        if len(all_genre_results) > 5:
            print(f"   • ...and {len(all_genre_results) - 5} more genres!")

        print(f"\n🔧 Integration Notes:")
        print(f"   • Artist and title data is clean and ready")
        print(f"   • {total_genres} genres confirmed working")
        print(f"   • Data quality: {overall_quality['quality_score']:.1f}%")
    else:
        print(f"\n⚠️  Data quality needs improvement ({overall_quality['quality_score']:.1f}%)")
        print(f"💡 Consider refining extraction methods")


if __name__ == "__main__":
    main()