// argv[0]: JSON {name, fallback_name, folder, description, track_ids}
// The final name and description are set in the creating call. Renaming a playlist right after
// creating it does not stick: Music.app keeps (and iCloud syncs) the name it was created with.
function run(argv) {
  const req = JSON.parse(argv[0]);
  const Music = Application('Music');
  const lib = Music.libraryPlaylists[0];
  const exists = (n) => Music.playlists.whose({name: n}).length > 0;

  let name = req.name;
  if (exists(name)) {
    name = req.fallback_name;
    let i = 2;
    while (exists(name)) { name = `${req.fallback_name} ${i}`; i += 1; }
  }

  let folder = null;
  const candidates = Music.playlists.whose({name: req.folder});
  for (let i = 0; i < candidates.length; i += 1) {
    if (candidates[i].class() === 'folderPlaylist') { folder = candidates[i]; break; }
  }
  if (!folder) folder = Music.make({new: 'folderPlaylist', withProperties: {name: req.folder}});

  const playlist = Music.make({
    new: 'playlist', at: folder, withProperties: {name: name, description: req.description || ''},
  });
  const pid = playlist.persistentID();
  // stderr survives a timeout kill, so Python can delete exactly this playlist if we're cut off.
  console.log('MUSIC_AGENT_PLAYLIST_ID=' + pid);
  const missing = [];
  try {
    for (const id of req.track_ids) {
      const hits = lib.tracks.whose({persistentID: id});
      if (hits.length === 0) { missing.push(id); continue; }
      Music.duplicate(hits[0], {to: playlist});
    }
    if (missing.length === req.track_ids.length) {
      throw new Error('None of the requested tracks are in your library.');
    }
  } catch (e) {
    Music.delete(playlist); // never leave a half-built or empty playlist behind
    throw e;
  }
  return JSON.stringify({
    name: name,
    persistent_id: pid,
    track_count: playlist.tracks.length,
    missing_ids: missing,
  });
}
