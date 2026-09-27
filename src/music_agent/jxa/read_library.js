// Bulk-read every library track as a JSON array. One Apple Event per property, not per track.
function run(argv) {
  const Music = Application('Music');
  const tracks = Music.libraryPlaylists[0].tracks;
  let ids;
  try {
    ids = tracks.persistentID();
  } catch (e) {
    if (e.errorNumber === -1728) return '[]'; // empty library
    throw e;
  }
  const names = tracks.name(), artists = tracks.artist(), albums = tracks.album();
  const genres = tracks.genre(), durations = tracks.duration(), added = tracks.dateAdded();
  const counts = tracks.playedCount(), played = tracks.playedDate();
  const iso = (d) => (d ? d.toISOString() : null);
  return JSON.stringify(ids.map((id, i) => ({
    persistent_id: id,
    name: names[i] || '',
    artist: artists[i] || '',
    album: albums[i] || '',
    genre: genres[i] || '',
    duration_s: durations[i] || 0,
    date_added: iso(added[i]),
    played_count: counts[i] || 0,
    played_date: iso(played[i]),
  })));
}
