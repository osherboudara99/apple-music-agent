// Bulk-read every library track, one Apple Event per property (not per track).
// Returns columns plus a second read of the ids; Python rejects the read if anything is
// misaligned (a track added or removed mid-read would otherwise shift values onto wrong ids).
function run(argv) {
  const Music = Application('Music');
  const tracks = Music.libraryPlaylists[0].tracks;
  const empty = {ids: [], names: [], artists: [], albums: [], genres: [], durations: [],
                 added: [], counts: [], played: [], ids_after: []};
  let ids;
  try {
    ids = tracks.persistentID();
  } catch (e) {
    if (e.errorNumber === -1728) return JSON.stringify(empty); // empty library
    throw e;
  }
  const iso = (d) => (d ? d.toISOString() : null);
  return JSON.stringify({
    ids: ids,
    names: tracks.name(),
    artists: tracks.artist(),
    albums: tracks.album(),
    genres: tracks.genre(),
    durations: tracks.duration(),
    added: tracks.dateAdded().map(iso),
    counts: tracks.playedCount(),
    played: tracks.playedDate().map(iso),
    ids_after: tracks.persistentID(),
  });
}
