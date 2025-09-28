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

    def scrape_genre_hype_picks(self, genre: Dict, limit: int = 50) -> List[Dict]:
        """Scrape hype picks for a specific genre - FIXED VERSION"""
        tracks = []

        # Try multiple hype-related URLs
        hype_urls_to_try = [
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/hype-100",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/hype",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}/hype-10",
            f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"  # Main page as fallback
        ]

        for hype_url in hype_urls_to_try:
            print(f"   🔥 Trying hype URL: {hype_url}")
            soup = self.get_page(hype_url)
            if soup:
                # First try direct track extraction
                tracks = self.extract_tracks_from_page(soup, f"{genre['name']} Hype Picks", limit)

                if len(tracks) >= 10:  # Good result
                    print(f"   ✅ Found {len(tracks)} hype tracks from {hype_url}")
                    break
                elif len(tracks) > 0:
                    print(f"   ⚠️ Only found {len(tracks)} hype tracks, trying next URL...")
                else:
                    print(f"   ❌ No hype tracks found at {hype_url}")

                    # If main page, try to find hype section
                    if hype_url.endswith(genre['id']):
                        print(f"   🔍 Searching for hype section on main genre page...")
                        hype_section_tracks = self.find_hype_section_on_genre_page(soup, genre, limit)
                        if hype_section_tracks:
                            tracks = hype_section_tracks
                            print(f"   ✅ Found {len(tracks)} tracks in hype section")
                            break

        return tracks[:limit]

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

    def scrape_genre_staff_picks(self, genre: Dict, limit: int = 50) -> List[Dict]:
        """Scrape staff picks for a specific genre - FIXED VERSION"""
        genre_url = f"{self.base_url}/genre/{genre['slug']}/{genre['id']}"

        soup = self.get_page(genre_url)
        if not soup:
            return []

        tracks = []

        # Method 1: Look for editorial/staff pick sections directly
        editorial_sections = [
            'staff pick', 'editorial', 'featured', 'editor pick',
            'beatport picks', 'weekend pick', 'best new', 'exclusives'
        ]

        for section_name in editorial_sections:
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
                            break  # Found staff picks, stop looking

        # Method 2: If no direct sections found, look for editorial chart collections
        if not tracks:
            print(f"   🔍 No direct staff picks section found, checking editorial charts...")

            chart_links = soup.find_all('a', href=re.compile(r'/chart/'))
            editorial_charts = []

            for chart_link in chart_links[:10]:  # Limit to first 10 charts
                chart_name = chart_link.get_text(strip=True)
                chart_href = chart_link.get('href', '')

                # Filter for editorial-style chart names
                if any(keyword in chart_name.lower() for keyword in
                      ['best new', 'weekend pick', 'editor', 'staff', 'beatport picks', 'exclusive']):
                    editorial_charts.append((chart_name, chart_href))

            print(f"   📊 Found {len(editorial_charts)} editorial charts")

            # Extract tracks from editorial charts
            for chart_name, chart_href in editorial_charts[:3]:  # Limit to 3 charts
                if len(tracks) >= limit:
                    break

                print(f"   📊 Processing editorial chart: {chart_name}")
                chart_url = urljoin(self.base_url, chart_href)
                remaining_limit = limit - len(tracks)
                chart_tracks = self.extract_tracks_from_chart(chart_url, chart_name, remaining_limit)

                if chart_tracks:
                    tracks.extend(chart_tracks)
                    print(f"   ✅ Added {len(chart_tracks)} tracks from {chart_name}")

        return tracks[:limit]

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