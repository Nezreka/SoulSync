OverviewThis plan will guide you through implementing the "Correct Failed Matches" feature using the hybrid model we discussed. The automated download process will continue uninterrupted, and a button will appear as soon as a track fails, allowing you to choose when to address the failures.All changes will be made within the sync.py file.Step 1: Add State Tracking for Failed DownloadsFirst, we need a list in our DownloadMissingTracksModal class to keep track of tracks that have permanently failed after all automated retries.Location: sync.py -> DownloadMissingTracksModal class -> __init__ method.Action: Add the following line inside the __init__ method, near the other state tracking variables.# In DownloadMissingTracksModal.__init__

        # ... existing state tracking variables ...
        self.download_in_progress = False
        
        # --- ADD THIS LINE ---
        self.permanently_failed_tracks = [] 
        # --- END OF ADDITION ---

        print(f"📊 Total tracks: {self.total_tracks}")
Step 2: Add the "Correct Failed Matches" Button to the UINext, we'll add the new button to the modal's UI. It will be hidden by default and will only appear when there's at least one failed track to correct.Location: sync.py -> DownloadMissingTracksModal class -> create_buttons method.Action: Add the code for the new button within the create_buttons method, right before the "Close" button.# In DownloadMissingTracksModal.create_buttons

        # ... existing button code ...
        layout = QHBoxLayout(button_frame)
        layout.setSpacing(15)
        layout.setContentsMargins(0, 10, 0, 0)

        # --- ADD THE NEW BUTTON DEFINITION HERE ---
        self.correct_failed_btn = QPushButton("🔧 Correct Failed Matches")
        self.correct_failed_btn.setFixedSize(220, 40) # Slightly wider for counter text
        self.correct_failed_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffc107; /* Amber color */
                color: #000000;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
                padding: 10px 20px;
            }
            QPushButton:hover {
                background-color: #ffca28;
            }
        """)
        self.correct_failed_btn.clicked.connect(self.on_correct_failed_matches_clicked)
        self.correct_failed_btn.hide() # Initially hidden
        # --- END OF ADDITION ---
        
        # Begin Search button
        self.begin_search_btn = QPushButton("Begin Search")
        # ... existing code for other buttons ...

        layout.addStretch()
        layout.addWidget(self.begin_search_btn)
        layout.addWidget(self.cancel_btn)
        # --- ADD THE BUTTON TO THE LAYOUT ---
        layout.addWidget(self.correct_failed_btn)
        # --- END OF ADDITION ---
        layout.addWidget(self.close_btn)
        
        return button_frame
Step 3: Update Failure Handling LogicNow, we need to modify the method that handles a permanently failed download. It will now add the failed track to our new list and update the "Correct Failed Matches" button.Location: sync.py -> DownloadMissingTracksModal class.Action: Find the on_parallel_track_failed method and replace the entire method with the version below.# --- REPLACE this entire method in DownloadMissingTracksModal ---

    def on_parallel_track_failed(self, download_index, reason):
        """Handle failure of a parallel track download"""
        print(f"❌ Parallel download {download_index + 1} failed: {reason}")
        
        if hasattr(self, 'parallel_search_tracking') and download_index in self.parallel_search_tracking:
            track_info = self.parallel_search_tracking[download_index]
            
            # --- NEW LOGIC TO TRACK PERMANENT FAILURES ---
            # Add the failed track to our list for manual correction
            if track_info not in self.permanently_failed_tracks:
                self.permanently_failed_tracks.append(track_info)
            self.update_failed_matches_button() # Update the button visibility and count
            # --- END OF NEW LOGIC ---

        self.on_parallel_track_completed(download_index, False)
Action: Now, add the new helper method that controls the button's visibility and text. Paste this new method anywhere inside the DownloadMissingTracksModal class.# --- ADD this new method to DownloadMissingTracksModal ---

    def update_failed_matches_button(self):
        """Shows, hides, and updates the counter on the 'Correct Failed Matches' button."""
        count = len(self.permanently_failed_tracks)
        if count > 0:
            self.correct_failed_btn.setText(f"🔧 Correct {count} Failed Match{'es' if count > 1 else ''}")
            self.correct_failed_btn.show()
        else:
            self.correct_failed_btn.hide()
Step 4: Create the ManualMatchModal ClassThis is the largest step. We need to create the new modal that will handle the manual search-and-download process. This is a completely new class, designed to meet your specifications for styling and functionality.Location: sync.pyAction: First, add these imports to the top of your sync.py file if they don't already exist.# At the top of sync.py with other imports
from PyQt6.QtWidgets import QLineEdit
from core.soulseek_client import TrackResult
Action: Now, copy the entire ManualMatchModal class definition below and paste it into sync.py. A good place is right before the DownloadMissingTracksModal class definition begins.# --- PASTE THIS ENTIRE NEW CLASS into sync.py ---

class ManualMatchModal(QDialog):
    """Modal for manually searching and downloading a failed track."""
    
    track_resolved = pyqtSignal(object)

    def __init__(self, failed_tracks, parent_modal):
        super().__init__(parent_modal)
        self.parent_modal = parent_modal
        self.soulseek_client = parent_modal.parent_page.soulseek_client
        self.downloads_page = parent_modal.downloads_page
        
        self.failed_tracks = list(failed_tracks) # Use a copy of the list
        self.current_track_info = None
        self.search_worker = None
        
        self.setWindowTitle("Manual Track Correction")
        self.setMinimumSize(900, 700)
        self.setup_ui()
        self.load_next_track()

    def setup_ui(self):
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; }
            QLabel { color: #ffffff; font-size: 14px; }
            QPushButton {
                background-color: #1db954; color: #000000; border: none;
                border-radius: 6px; font-size: 13px; font-weight: bold;
                padding: 10px 20px; min-width: 80px;
            }
            QPushButton:hover { background-color: #1ed760; }
            QPushButton:disabled { background-color: #404040; color: #888888; }
            QLineEdit {
                background: #404040; border: 1px solid #606060; border-radius: 6px;
                padding: 10px; color: #ffffff; font-size: 13px;
            }
            QScrollArea { border: none; }
        """)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(15)

        info_frame = QFrame()
        info_frame.setStyleSheet("background-color: #2d2d2d; border-radius: 8px; padding: 15px;")
        info_layout = QVBoxLayout(info_frame)
        self.info_label = QLabel("Loading track...")
        self.info_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        self.info_label.setWordWrap(True)
        info_layout.addWidget(self.info_label)
        self.main_layout.addWidget(info_frame)

        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.returnPressed.connect(self.perform_manual_search)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.perform_manual_search)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.search_btn)
        self.main_layout.addLayout(search_layout)

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_widget = QWidget()
        self.results_layout = QVBoxLayout(self.results_widget)
        self.results_layout.setSpacing(8)
        self.results_scroll.setWidget(self.results_widget)
        self.main_layout.addWidget(self.results_scroll, 1)

    def load_next_track(self):
        self.clear_results()
        if not self.failed_tracks:
            QMessageBox.information(self, "Complete", "All failed tracks have been addressed.")
            self.accept()
            return

        self.current_track_info = self.failed_tracks[0]
        spotify_track = self.current_track_info['spotify_track']
        artist = spotify_track.artists[0] if spotify_track.artists else "Unknown"
        
        self.info_label.setText(f"Could not find: <b>{spotify_track.name}</b><br>by {artist}")
        self.search_input.setText(f"{artist} {spotify_track.name}")
        
        # Display cached results first, as requested
        cached_candidates = self.current_track_info.get('candidates', [])
        if cached_candidates:
            self.results_layout.addWidget(QLabel("Showing results from initial search. Or, perform a new search above."))
            for result in cached_candidates:
                self.results_layout.addWidget(self.create_result_widget(result))
        else:
            self.perform_manual_search() # If no cache, search automatically

    def perform_manual_search(self):
        query = self.search_input.text().strip()
        if not query: return
        self.clear_results()
        
        self.results_layout.addWidget(QLabel(f"Searching for '{query}'..."))
        self.search_btn.setText("Searching...")
        self.search_btn.setEnabled(False)

        worker = self.parent_modal.start_search_worker_parallel(
            query, [query], self.current_track_info['spotify_track'], 
            self.current_track_info['track_index'], self.current_track_info['table_index'], 
            0, self.current_track_info['download_index']
        )
        worker.signals.search_completed.connect(self.on_manual_search_completed)
        worker.signals.search_failed.connect(self.on_manual_search_failed)

    def on_manual_search_completed(self, results, query):
        self.search_btn.setText("Search")
        self.search_btn.setEnabled(True)
        self.clear_results()

        if not results:
            self.results_layout.addWidget(QLabel("No results found for this query."))
            return

        for result in results:
            self.results_layout.addWidget(self.create_result_widget(result))

    def on_manual_search_failed(self, query, error):
        self.search_btn.setText("Search")
        self.search_btn.setEnabled(True)
        self.clear_results()
        self.results_layout.addWidget(QLabel(f"Search failed: {error}"))

    def create_result_widget(self, result):
        widget = QFrame()
        widget.setStyleSheet("background-color: #3a3a3a; border-radius: 6px; padding: 10px;")
        layout = QHBoxLayout(widget)
        
        # Display filename and path structure
        path_parts = result.filename.replace('\\', '/').split('/')
        filename = path_parts[-1]
        path_structure = '/'.join(path_parts[:-1])
        
        info_text = f"<b>{filename}</b><br><i style='color:#aaaaaa;'>{path_structure}</i><br>Quality: {result.quality.upper()}, Size: {result.size // 1024} KB"
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        
        select_btn = QPushButton("Select")
        select_btn.setFixedWidth(100)
        select_btn.clicked.connect(lambda: self.on_selection_made(result))
        
        layout.addWidget(info_label, 1)
        layout.addWidget(select_btn)
        return widget

    def on_selection_made(self, slskd_result):
        print(f"Manual selection made: {slskd_result.filename}")
        
        # This starts the download via the main modal's infrastructure
        self.parent_modal.start_validated_download_parallel(
            slskd_result, 
            self.current_track_info['spotify_track'], 
            self.current_track_info['track_index'], 
            self.current_track_info['table_index'], 
            self.current_track_info['download_index']
        )
        
        self.track_resolved.emit(self.current_track_info)
        
        self.failed_tracks.pop(0)
        self.load_next_track()

    def clear_results(self):
        while self.results_layout.count():
            child = self.results_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
Step 5: Integrate the New ModalFinally, we need to connect the "Correct Failed Matches" button to open our new modal and create a method to handle the track_resolved signal that the new modal will emit.Location: sync.py -> DownloadMissingTracksModal class.Action: Add these two new methods anywhere inside the DownloadMissingTracksModal class.# --- ADD these two new methods to DownloadMissingTracksModal ---

    def on_correct_failed_matches_clicked(self):
        """Opens the modal to manually correct failed downloads."""
        if not self.permanently_failed_tracks:
            return

        # Create and show the modal
        manual_modal = ManualMatchModal(self.permanently_failed_tracks, self)
        manual_modal.track_resolved.connect(self.on_manual_match_resolved)
        manual_modal.exec()

    def on_manual_match_resolved(self, resolved_track_info):
        """
        Handles a track being successfully resolved by the ManualMatchModal.
        """
        # The download has already been started by the manual modal.
        # We just need to update our internal state.
        
        # Find the original failed track in our list and remove it
        original_failed_track = next((t for t in self.permanently_failed_tracks if t['download_index'] == resolved_track_info['download_index']), None)
        if original_failed_track:
            self.permanently_failed_tracks.remove(original_failed_track)
        
        # Update the button counter
        self.update_failed_matches_button()
This comprehensive plan implements the entire feature exactly as you specified. It tracks failures, displays a button to correct them, and provides a new, well-styled interface for you to manually select the correct download from either the cached results or a new search. The selected track is then processed just like any other normal matched download.