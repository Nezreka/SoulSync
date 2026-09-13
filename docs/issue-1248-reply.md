Thanks for the logs. Found a problem where SoulSync could hold onto old Navidrome track IDs and use them when updating playlists. I've fixed that and added checks so failed updates aren't reported as successful. Also added cleanup for confirmed duplicate library entries.

Once you're on a build with the fix, run a normal database update, then resync the playlists. No database reset needed. The fixes passed testing here, but let me know how it goes on your setup and if the library still times out. Sorry for the hassle, appreciate you being patient.
