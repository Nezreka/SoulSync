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
                raw_title = link.get_text(strip=True)
                if not raw_title:
                    continue

                # Find artist - try multiple approaches
                artist_text = None

                # Method 1: Look for artist class in parent hierarchy
                parent = link.parent
                for level in range(4):  # Check up to 4 parent levels
                    if parent:
                        artist_elem = parent.find(class_='heGYqE')
                        if artist_elem:
                            artist_text = artist_elem.get_text(strip=True)
                            break
                        parent = parent.parent
                    else:
                        break

                # Method 2: If no artist found, look in surrounding elements
                if not artist_text and link.parent:
                    # Check siblings
                    for sibling in link.parent.find_all():
                        if 'heGYqE' in str(sibling.get('class', [])):
                            artist_text = sibling.get_text(strip=True)
                            break

                # Method 3: If still no artist, try broader search in parent container
                if not artist_text and link.parent and link.parent.parent:
                    container = link.parent.parent
                    artist_elem = container.find(class_='heGYqE')
                    if artist_elem:
                        artist_text = artist_elem.get_text(strip=True)

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
        """Scrape Beatport New Releases from homepage section"""
        print("\n🆕 Scraping Beatport New Releases...")

        # Parse from homepage New Releases section (H2 heading)
        soup = self.get_page(self.base_url)
        if not soup:
            return []

        # Find the New Releases H2 section
        new_releases_heading = soup.find(['h1', 'h2', 'h3'], string=re.compile(r'New Releases', re.I))
        if new_releases_heading:
            # Get the section content after the heading
            section_container = new_releases_heading.find_parent()
            if section_container:
                # Look for the next sibling or content area
                content_area = section_container.find_next_sibling()
                if content_area:
                    tracks = self.extract_tracks_from_page(content_area, "New Releases", limit)
                else:
                    # Fallback: search in parent container
                    tracks = self.extract_tracks_from_page(section_container, "New Releases", limit)
            else:
                tracks = []
        else:
            print("⚠️ New Releases section not found, scanning entire homepage...")
            tracks = self.extract_tracks_from_page(soup, "New Releases", limit)

        print(f"✅ Extracted {len(tracks)} tracks from New Releases")
        return tracks

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
        """Scrape Beatport Top 100 Releases - Try both track and release approaches"""
        print("\n📊 Scraping Beatport Top 100 Releases...")

        # Use the correct URL discovered by parser
        soup = self.get_page(f"{self.base_url}/top-100-releases")
        if soup:
            # First try the same approach as hype-100 (looking for tracks)
            tracks = self.extract_tracks_from_page(soup, "Top 100 New Releases", limit)
            if tracks and len(tracks) > 10:
                print(f"✅ Extracted {len(tracks)} tracks from Top 100 New Releases (track method)")
                return tracks
            else:
                print(f"⚠️ Track method found {len(tracks)} tracks, trying release method...")
                # Fallback to release extraction
                releases = self.extract_releases_from_page(soup, "Top 100 New Releases", limit)
                print(f"✅ Extracted {len(releases)} releases from Top 100 New Releases (release method)")
                return releases
        else:
            print("⚠️ Could not access /top-100-releases, trying homepage Top 10 Releases section...")
            # Fallback to homepage section
            soup = self.get_page(self.base_url)
            if soup:
                releases_heading = soup.find(['h1', 'h2', 'h3'], string=re.compile(r'Top.*Releases', re.I))
                if releases_heading:
                    section_container = releases_heading.find_parent()
                    if section_container:
                        content_area = section_container.find_next_sibling()
                        if content_area:
                            tracks = self.extract_tracks_from_page(content_area, "Top 100 New Releases", limit)
                        else:
                            tracks = self.extract_tracks_from_page(section_container, "Top 100 New Releases", limit)
                    else:
                        tracks = []
                else:
                    tracks = []
            else:
                tracks = []

            print(f"✅ Extracted {len(tracks)} tracks from Top 100 New Releases (fallback)")
            return tracks

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
        """Scrape Beatport Featured Charts from homepage section - Improved reliability"""
        print("\n📊 Scraping Beatport Featured Charts...")

        soup = self.get_page(self.base_url)
        if not soup:
            return []

        tracks = []

        # Method 1: Find Featured Charts H2 section on homepage
        featured_heading = soup.find(['h1', 'h2', 'h3'], string=re.compile(r'Featured Charts', re.I))
        if featured_heading:
            print("   Found Featured Charts section heading")
            section_container = featured_heading.find_parent()
            if section_container:
                content_area = section_container.find_next_sibling()
                if content_area:
                    # Look for chart items within this section
                    chart_items = content_area.find_all('a', href=re.compile(r'/chart/'))
                    print(f"   Found {len(chart_items)} featured chart items")

                    for chart_item in chart_items[:limit]:
                        chart_name = chart_item.get_text(strip=True)
                        chart_href = chart_item.get('href', '')

                        if chart_name and chart_href:
                            # Extract additional info if available (artist, price, etc.)
                            chart_container = chart_item.find_parent()
                            artist_name = "Beatport Editorial"

                            # Try to find artist name in the container
                            if chart_container:
                                # Look for artist info near the chart name
                                potential_artist = chart_container.find_next(string=True)
                                if potential_artist and len(potential_artist.strip()) > 2:
                                    artist_name = potential_artist.strip()

                            track_info = {
                                'position': len(tracks) + 1,
                                'artist': artist_name,
                                'title': chart_name,
                                'list_name': 'Featured Charts',
                                'url': urljoin(self.base_url, chart_href),
                                'chart_name': chart_name,
                                'chart_type': 'featured'
                            }
                            tracks.append(track_info)

        # Method 2: Look for other editorial/featured sections if main section not found
        if not tracks:
            print("   ⚠️ Featured Charts section not found, looking for staff picks or editorial sections...")

            # Look for staff picks or other editorial content
            editorial_headings = soup.find_all(['h1', 'h2', 'h3'],
                string=re.compile(r'staff.*pick|editorial|hype.*pick|weekend.*pick|exclusives.*only', re.I))

            for heading in editorial_headings:
                section_name = heading.get_text(strip=True)
                print(f"   Found editorial section: {section_name}")

                section_container = heading.find_parent()
                if section_container:
                    content_area = section_container.find_next_sibling()
                    if content_area:
                        # Try to extract tracks from this section
                        section_tracks = self.extract_tracks_from_page(content_area, section_name, 5)
                        for track in section_tracks:
                            track['chart_type'] = 'featured'
                            track['chart_name'] = section_name
                        tracks.extend(section_tracks)

                        if len(tracks) >= limit:
                            break

        print(f"✅ Extracted {len(tracks)} items from Featured Charts")
        return tracks

    def scrape_genre_charts(self, genre: Dict, limit: int = 100) -> List[Dict]:
        """Scrape charts for a specific genre (default: top tracks)"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"

        soup = self.get_page(genre_url)
        tracks = self.extract_tracks_from_page(soup, f"{genre['name']} Top 100", limit)

        return tracks

    def scrape_genre_top_10(self, genre: Dict) -> List[Dict]:
        """Scrape top 10 tracks for a specific genre"""
        return self.scrape_genre_charts(genre, limit=10)

    def scrape_genre_releases(self, genre: Dict, limit: int = 100) -> List[Dict]:
        """Scrape top releases for a specific genre"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"

        soup = self.get_page(genre_url)
        if not soup:
            return []

        # Try to find releases section on genre page
        releases = self.extract_releases_from_page(soup, f"{genre['name']} Top Releases", limit)

        # If no releases found with release extraction, try track extraction
        if not releases:
            print(f"   ⚠️ No releases found with release method, trying track method for {genre['name']}")
            releases = self.extract_tracks_from_page(soup, f"{genre['name']} Top Releases", limit)
            # Mark these as releases
            for release in releases:
                release['type'] = 'release'

        return releases

    def scrape_genre_staff_picks(self, genre: Dict, limit: int = 50) -> List[Dict]:
        """Scrape staff picks for a specific genre"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"

        soup = self.get_page(genre_url)
        if not soup:
            return []

        tracks = []

        # Look for staff picks, editorial, or featured sections on genre page
        staff_sections = [
            'staff pick', 'editorial', 'featured', 'editor', 'hype pick',
            'weekend pick', 'best new', 'exclusives'
        ]

        for section_name in staff_sections:
            # Find section headings that match staff pick patterns
            section_heading = soup.find(['h1', 'h2', 'h3', 'h4'],
                string=re.compile(rf'{section_name}', re.I))

            if section_heading:
                print(f"   📝 Found staff picks section: {section_heading.get_text(strip=True)}")
                section_container = section_heading.find_parent()
                if section_container:
                    content_area = section_container.find_next_sibling()
                    if content_area:
                        section_tracks = self.extract_tracks_from_page(
                            content_area, f"{genre['name']} Staff Picks", limit
                        )
                        if section_tracks:
                            tracks.extend(section_tracks)
                            break  # Found staff picks, no need to continue

        # If no specific staff picks section found, try to find any editorial content
        if not tracks:
            print(f"   🔍 No specific staff picks section found, looking for editorial content...")
            # Look for DJ charts or featured charts on the genre page
            chart_links = soup.find_all('a', href=re.compile(r'/chart/'))
            for chart_link in chart_links[:10]:  # Limit to first 10 charts
                chart_name = chart_link.get_text(strip=True)
                if chart_name and len(chart_name) > 3:
                    track_info = {
                        'position': len(tracks) + 1,
                        'artist': 'Various Artists',
                        'title': chart_name,
                        'list_name': f"{genre['name']} Staff Picks",
                        'url': urljoin(self.base_url, chart_link.get('href', '')),
                        'chart_type': 'staff_pick'
                    }
                    tracks.append(track_info)
                    if len(tracks) >= limit:
                        break

        return tracks

    def scrape_genre_latest_releases(self, genre: Dict, limit: int = 50) -> List[Dict]:
        """Scrape latest releases for a specific genre"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"

        soup = self.get_page(genre_url)
        if not soup:
            return []

        # Look for latest releases, new releases, or recent sections
        latest_sections = ['latest', 'new releases', 'recent', 'newest']
        tracks = []

        for section_name in latest_sections:
            section_heading = soup.find(['h1', 'h2', 'h3', 'h4'],
                string=re.compile(rf'{section_name}', re.I))

            if section_heading:
                print(f"   🕒 Found latest releases section: {section_heading.get_text(strip=True)}")
                section_container = section_heading.find_parent()
                if section_container:
                    content_area = section_container.find_next_sibling()
                    if content_area:
                        section_tracks = self.extract_tracks_from_page(
                            content_area, f"Latest {genre['name']} Releases", limit
                        )
                        if section_tracks:
                            tracks.extend(section_tracks)
                            break

        # If no specific latest section found, try releases extraction
        if not tracks:
            print(f"   🔍 No specific latest releases section found, trying general releases...")
            tracks = self.scrape_genre_releases(genre, limit)

        return tracks

    def scrape_genre_new_charts(self, genre: Dict, limit: int = 50) -> List[Dict]:
        """Scrape new charts (DJ/artist curated) for a specific genre"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"

        soup = self.get_page(genre_url)
        if not soup:
            return []

        tracks = []

        # Look for DJ charts, artist charts, or curated content on genre page
        chart_links = soup.find_all('a', href=re.compile(r'/chart/'))

        for chart_link in chart_links[:limit]:
            chart_name = chart_link.get_text(strip=True)
            chart_href = chart_link.get('href', '')

            if chart_name and chart_href and len(chart_name) > 3:
                # Extract additional info if available (artist name, etc.)
                chart_container = chart_link.find_parent()
                artist_name = "Various Artists"

                # Try to find artist info near the chart
                if chart_container:
                    # Look for artist links in the same container
                    artist_link = chart_container.find('a', href=re.compile(r'/artist/'))
                    if artist_link:
                        artist_name = artist_link.get_text(strip=True)

                chart_info = {
                    'position': len(tracks) + 1,
                    'artist': artist_name,
                    'title': chart_name,
                    'list_name': f"New {genre['name']} Charts",
                    'url': urljoin(self.base_url, chart_href),
                    'chart_type': 'new_chart'
                }
                tracks.append(chart_info)

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

    # Test improved chart sections first
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